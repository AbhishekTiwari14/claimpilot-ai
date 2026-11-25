from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from claimpilot.api.intake import RequestKey
from claimpilot.api.knowledge import Actor
from claimpilot.schemas.contractors import AgentTrace, Contractor, ContractorID, Proposal, RecommendationRequest, RunView
from claimpilot.storage.contractors import ContractorStore

router = APIRouter(prefix="/api/v1", tags=["contractor recommendations"])


def contractor_store(request: Request) -> ContractorStore:
    return request.app.state.contractor_store


Store = Annotated[ContractorStore, Depends(contractor_store)]


@router.get("/contractors")
def list_contractors(actor: Actor, store: Store) -> list[Contractor]:
    return store.contractors(actor)


@router.get("/contractors/{contractor_id}")
def get_contractor(contractor_id: ContractorID, actor: Actor, store: Store) -> Contractor:
    return store.contractor(actor, contractor_id)


@router.post("/claims/{claim_id}/contractor-recommendations")
def recommend(claim_id: UUID, payload: RecommendationRequest, actor: Actor, store: Store,
              key: RequestKey, request: Request, response: Response) -> RunView:
    from claimpilot.agents.contractors import run_recommendation

    state = request.app.state
    result = run_recommendation(
        store, state.knowledge, state.llm, state.settings, actor, claim_id, key, payload.expected_version,
    )
    response.headers["Location"] = f"/api/v1/claims/{claim_id}/contractor-recommendations/{result.id}"
    return result


@router.get("/claims/{claim_id}/contractor-recommendations/{run_id}")
def run(claim_id: UUID, run_id: UUID, actor: Actor, store: Store) -> RunView:
    return store.run(actor, claim_id, run_id)


@router.get("/claims/{claim_id}/contractor-recommendations/{run_id}/trace")
def run_trace(claim_id: UUID, run_id: UUID, actor: Actor, store: Store) -> AgentTrace:
    return store.trace(actor, claim_id, run_id)


@router.get("/claims/{claim_id}/dispatch-proposals")
def proposals(claim_id: UUID, actor: Actor, store: Store) -> list[Proposal]:
    return store.proposals(actor, claim_id)
