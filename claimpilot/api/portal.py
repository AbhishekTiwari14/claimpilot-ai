from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from claimpilot.api.intake import Actor as ClaimantActor, Store
from claimpilot.api.knowledge import Actor as OperatorActor
from claimpilot.api.workflows import graph_config, view
from claimpilot.schemas.conversation import ClaimView, Claimant, IntakeStatus
from claimpilot.schemas.contractors import UrgencyAssessment
from claimpilot.schemas.photos import AnalysisView
from claimpilot.schemas.contractors import RequirementSource
from claimpilot.services.contractor_urgency import classify_urgency

router = APIRouter(prefix="/api/v1", tags=["portal reads"])
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


class ClaimSummary(BaseModel):
    claim_id: UUID
    conversation_id: UUID
    status: IntakeStatus
    version: int
    damage_type: str | list[str] | None = None
    dispatch_status: str | None = None


class OperatorClaim(BaseModel):
    claim: ClaimView
    urgency: UrgencyAssessment
    analyses: list[AnalysisView]
    workflow_ids: list[UUID]
    policy_version: str | None = None


def list_scoped(store, tenant, claimant, limit, offset):
    sql = """SELECT c.*, (SELECT status FROM dispatches WHERE claim_id=c.id
                           ORDER BY rowid DESC LIMIT 1) AS dispatch_status
             FROM claims c WHERE c.tenant_id=?"""
    params = [tenant]
    if claimant is not None:
        sql += " AND c.claimant_id=?"
        params.append(claimant)
    with store._connection() as db:
        rows = db.execute(sql + " ORDER BY c.rowid DESC LIMIT ? OFFSET ?", (*params, limit, offset)).fetchall()
    result = []
    for row in rows:
        claim = store._view(row)
        damage = claim.facts.get("damage_type")
        result.append(ClaimSummary(
            claim_id=claim.claim_id, conversation_id=claim.conversation_id, status=claim.status,
            version=claim.version, damage_type=damage.value if damage else None, dispatch_status=row["dispatch_status"],
        ))
    return result


@router.get("/customer/claims")
def customer_claims(actor: ClaimantActor, store: Store, limit: Limit = 50, offset: Offset = 0) -> list[ClaimSummary]:
    return list_scoped(store, actor.tenant_id, actor.claimant_id, limit, offset)


@router.get("/operator/claims")
def operator_claims(actor: OperatorActor, store: Store, limit: Limit = 50, offset: Offset = 0) -> list[ClaimSummary]:
    return list_scoped(store, actor.tenant_id, None, limit, offset)


@router.get("/operator/claims/{claim_id}")
def operator_claim(claim_id: UUID, actor: OperatorActor, store: Store, request: Request) -> OperatorClaim:
    snapshot = request.app.state.workflow_store.snapshot(actor, claim_id)
    with store._connection() as db:
        row = db.execute("SELECT * FROM claims WHERE tenant_id=? AND id=?", (actor.tenant_id, str(claim_id))).fetchone()
        workflow_ids = [record[0] for record in db.execute(
            """SELECT response_json FROM idempotency_requests WHERE tenant_id=? AND route=?
               AND response_json IS NOT NULL ORDER BY id DESC LIMIT 50""",
            (actor.tenant_id, f"workflow:start:{claim_id}"),
        )]
        binding = db.execute("SELECT policy_version FROM claim_policy_bindings WHERE claim_id=?",
                             (str(claim_id),)).fetchone()
    owner = Claimant(tenant_id=actor.tenant_id, claimant_id=row["claimant_id"])
    return OperatorClaim(
        claim=store._view(row), analyses=store.analyses(owner, claim_id), workflow_ids=workflow_ids,
        urgency=classify_urgency(snapshot.facts, snapshot.photos,
                                intake_safety=snapshot.claim.assistance_reason == "immediate_safety"),
        policy_version=binding["policy_version"] if binding else None,
    )


@router.get("/operator/claims/{claim_id}/workflows/{workflow_id}/policy-evidence")
def policy_evidence(claim_id: UUID, workflow_id: UUID, actor: OperatorActor, request: Request) -> list[RequirementSource]:
    from claimpilot.agents.workflows import workflow_graph

    app = request.app.state
    store = app.workflow_store
    snapshot = store.snapshot(actor, claim_id)
    with workflow_graph(app, actor, mutate=False) as graph:
        state = graph.get_state(graph_config(actor, claim_id, workflow_id))
        references = list(dict.fromkeys(view(state).get("policy_citations", [])))
    if not references or state.values.get("fingerprint") != snapshot.fingerprint:
        return []
    scope, claimant, _ = store.claim_policy(actor, claim_id)
    if scope is None:
        return []
    with store._connection() as db:
        chunks = db.execute(
            f"""SELECT c.chunk_id,d.filename,c.section,c.text AS excerpt FROM knowledge_chunks c
                JOIN knowledge_documents d ON c.document_id=d.id AND c.generation=d.generation
                WHERE d.tenant_id=? AND d.document_type='policy' AND d.policy_id=? AND d.policy_version=?
                  AND d.subject_claimant_id IN ('', ?) AND d.status='ready' AND d.deleted_at IS NULL
                  AND c.chunk_id IN ({','.join('?' for _ in references)})""",
            (actor.tenant_id, scope.policy_id, scope.policy_version, claimant, *references),
        )
        by_id = {row["chunk_id"]: RequirementSource.model_validate(dict(row)) for row in chunks}
    return [by_id[reference] for reference in references if reference in by_id]
