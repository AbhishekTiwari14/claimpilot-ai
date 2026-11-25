from contextlib import closing, contextmanager
from operator import add
import sqlite3
from typing import Annotated, Literal, TypedDict
from uuid import UUID

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from langsmith import tracing_context

from claimpilot.agents.contractors import run_recommendation
from claimpilot.services.contractor_urgency import classify_urgency
from claimpilot.services.knowledge_answers import assess_claim
from claimpilot.storage.intake import IntakeError
from claimpilot.storage.dispatch import MAX_ATTEMPTS


class Transition(TypedDict):
    node: str
    status: str
    reason: str | None


class WorkflowState(TypedDict, total=False):
    workflow_id: str
    claim_id: str
    initiator: str
    claim_version: int
    attempt: int
    fingerprint: str
    status: str
    reason: str | None
    missing_fields: list[str]
    policy_outcome: str | None
    policy_citations: list[str]
    contractor_run_id: str | None
    proposal_id: str | None
    proposal_version: int | None
    dispatch_citations: list[str]
    dispatch_id: str | None
    transitions: Annotated[list[Transition], add]


def step(node: str, status: str, reason: str | None = None, **fields) -> dict:
    return {"status": status, "reason": reason, "transitions": [
        {"node": node, "status": status, "reason": reason}
    ], **fields}


@contextmanager
def workflow_graph(app, actor, *, mutate: bool):
    store = app.workflow_store
    # A separate transaction serializes graph commands across threads and processes.
    with closing(sqlite3.connect(store.path.with_suffix(".workflow-lock.sqlite3"), timeout=1)) as gate:
        if mutate:
            try:
                gate.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as error:
                if error.sqlite_errorcode == sqlite3.SQLITE_BUSY:
                    raise IntakeError(409, "workflow_busy", "Retry the workflow request with the same key.") from None
                raise
        with SqliteSaver.from_conn_string(
            str(store.path.with_suffix(".checkpoints.sqlite3")),
        ) as saver, tracing_context(enabled=False):
            saver.serde = JsonPlusSerializer(allowed_msgpack_modules=())
            yield build_graph(app, actor).compile(checkpointer=saver)


def build_graph(app, actor):
    store = app.workflow_store

    def intake(state: WorkflowState):
        snapshot = store.snapshot(actor, UUID(state["claim_id"]))
        claim = snapshot.claim
        urgency = classify_urgency(snapshot.facts, snapshot.photos,
                                   intake_safety=claim.assistance_reason == "immediate_safety")
        details = dict(claim_version=claim.version, fingerprint=snapshot.fingerprint,
                       missing_fields=claim.missing_fields, policy_outcome=None, policy_citations=[],
                       contractor_run_id=None, proposal_id=None, proposal_version=None, dispatch_citations=[])
        if urgency.mandatory_escalation or claim.status == "human_assistance_required":
            reason = "immediate_hazard" if urgency.mandatory_escalation else "intake_needs_assistance"
            return step("intake", "requires_intervention", reason, **details)
        if state["attempt"] == 0 and claim.version != state["claim_version"]:
            return step("intake", "requires_intervention", "stale_version", **details)
        if claim.status != "intake_completed" or claim.missing_fields:
            return step("intake", "awaiting_information", "confirmed_intake_required", **details)
        return step("intake", "assessing_policy", **details)

    def policy(state: WorkflowState):
        try:
            assessment = assess_claim(app.knowledge, app.llm, actor, UUID(state["claim_id"])).assessment
        except IntakeError as error:
            return step("policy", "requires_intervention", error.code)
        outcome = assessment.outcome
        fields = dict(policy_outcome=outcome, policy_citations=[c.source.chunk_id for c in assessment.citations])
        if outcome != "potentially_covered":
            return step("policy", "requires_intervention", outcome, **fields)
        return step("policy", "recommending", **fields)

    def contractor(state: WorkflowState):
        claim_id = UUID(state["claim_id"])
        snapshot = store.snapshot(actor, claim_id)
        if snapshot.fingerprint != state["fingerprint"]:
            return step("contractor", "requires_intervention", "claim_evidence_changed")
        failed = store.failed(actor, claim_id)
        if len(failed) >= MAX_ATTEMPTS:
            return step("contractor", "requires_intervention", "reassignment_limit")
        try:
            run = run_recommendation(
                store, app.knowledge, app.llm, app.settings, actor.model_copy(update={"operator_id": state["initiator"]}),
                claim_id, f'workflow:{state["workflow_id"]}:{state["attempt"]}', state["claim_version"],
                policy_outcome=state["policy_outcome"], excluded=failed, allow_sla_defaults=True,
            )
        except IntakeError as error:
            return step("contractor", "requires_intervention", error.code)
        proposal = run.proposal
        fields = {"contractor_run_id": str(run.id)}
        if proposal is None or proposal.status != "awaiting_approval":
            reason = "no_eligible_contractors" if proposal and "no_eligible_contractors" in proposal.uncertainties else "contractor_review_required"
            return step("contractor", "requires_intervention", reason, **fields)
        return step("contractor", "awaiting_approval", proposal_id=str(proposal.id), proposal_version=proposal.version,
                    dispatch_citations=[c.chunk_id for c in proposal.requirements.sources], **fields)

    def review(state: WorkflowState):
        interrupt({"status": state["status"], "reason": state["reason"]})
        return step("review", "checking_intake", attempt=state["attempt"] + 1)

    def approval(state: WorkflowState):
        choice = interrupt({"status": "awaiting_approval", "proposal_id": state["proposal_id"],
                            "proposal_version": state["proposal_version"], "claim_version": state["claim_version"]})
        outcome = store.decide(actor, state, choice)
        if outcome in ("approve", "reject"):
            return step("approval", "approved" if outcome == "approve" else "rejected")
        return step("approval", "requires_intervention", outcome)

    def dispatch(state: WorkflowState):
        interrupt({"status": "approved", "proposal_id": state["proposal_id"]})
        try:
            dispatch_id = store.send(actor, state, store.now())
        except IntakeError as error:
            return step("dispatch", "requires_intervention", error.code)
        return step("dispatch", "awaiting_acknowledgement", dispatch_id=dispatch_id)

    def acknowledgement(state: WorkflowState):
        choice = interrupt({"status": "awaiting_acknowledgement", "dispatch_id": state["dispatch_id"]})
        status = store.resolve_dispatch(actor, state, choice["action"], store.now())
        if status == "accepted":
            return step("acknowledgement", "accepted")
        return step("acknowledgement", "checking_intake", status, attempt=state["attempt"] + 1)

    graph = StateGraph(WorkflowState)
    graph.add_node("intake", intake)
    graph.add_node("policy", policy)
    graph.add_node("contractor", contractor)
    graph.add_node("review", review)
    graph.add_node("approval", approval)
    graph.add_node("dispatch", dispatch)
    graph.add_node("acknowledgement", acknowledgement)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", lambda s: "policy" if s["status"] == "assessing_policy" else "review",
                                ["policy", "review"])
    graph.add_conditional_edges("policy", lambda s: "contractor" if s["status"] == "recommending" else "review",
                                ["contractor", "review"])
    graph.add_conditional_edges("contractor", lambda s: "approval" if s["status"] == "awaiting_approval" else "review",
                                ["approval", "review"])
    graph.add_edge("review", "intake")
    graph.add_conditional_edges("approval", lambda s: {
        "requires_intervention": "review", "approved": "dispatch", "rejected": END,
    }[s["status"]], ["review", "dispatch", END])
    graph.add_conditional_edges("dispatch", lambda s: "acknowledgement" if s["status"] == "awaiting_acknowledgement" else "review",
                                ["acknowledgement", "review"])
    graph.add_conditional_edges("acknowledgement", lambda s: END if s["status"] == "accepted" else "intake",
                                [END, "intake"])
    return graph
