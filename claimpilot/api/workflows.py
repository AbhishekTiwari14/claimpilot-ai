from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from claimpilot.api.intake import RequestKey
from claimpilot.api.knowledge import Actor
from claimpilot.storage.contractors import digest
from claimpilot.storage.intake import IntakeError

router = APIRouter(prefix="/api/v1/claims/{claim_id}/workflows", tags=["claim workflows"])


class StartWorkflow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0, strict=True)


class ResumeWorkflow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pause_id: str = Field(min_length=1, max_length=96)
    action: Literal["retry", "approve", "reject"]
    expected_version: int = Field(ge=0, strict=True)
    proposal_id: UUID | None = None
    proposal_version: int | None = Field(default=None, ge=1, strict=True)
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ""


def graph_config(actor, claim_id, workflow_id):
    return {"configurable": {"thread_id": f"{digest(actor.tenant_id)}:{claim_id}:{workflow_id}"}}


def current_pause(snapshot):
    return next(iter(snapshot.interrupts), None)


def view(snapshot) -> dict:
    if not snapshot.values:
        raise IntakeError(404, "workflow_not_found", "Workflow not found.")
    pause = current_pause(snapshot)
    state = {key: value for key, value in snapshot.values.items() if key not in ("initiator", "fingerprint")}
    return state | {
        "pause_id": pause.id if pause else None,
        "execution_error": "node_execution_failed" if any(task.error for task in snapshot.tasks) else None,
    }


def invoke(graph, config, value):
    try:
        graph.invoke(value, config, durability="sync")
    except Exception:
        raise IntakeError(503, "workflow_execution_failed", "Inspect the workflow or retry with the same key.") from None


@router.post("")
def start(claim_id: UUID, payload: StartWorkflow, actor: Actor, key: RequestKey, request: Request) -> dict:
    from claimpilot.agents.workflows import workflow_graph

    app = request.app.state
    with workflow_graph(app, actor, mutate=True) as graph:
        workflow_id = app.workflow_store.start_workflow(actor, claim_id, key, payload.expected_version)
        config = graph_config(actor, claim_id, workflow_id)
        state = graph.get_state(config)
        if not state.values:
            invoke(graph, config, {"workflow_id": workflow_id, "claim_id": str(claim_id),
                                  "initiator": actor.operator_id, "attempt": 0,
                                  "claim_version": payload.expected_version})
        elif state.next and not current_pause(state):
            invoke(graph, config, None)
        return view(graph.get_state(config))


@router.get("/{workflow_id}")
def inspect(claim_id: UUID, workflow_id: UUID, actor: Actor, request: Request) -> dict:
    from claimpilot.agents.workflows import workflow_graph

    app = request.app.state
    app.workflow_store.snapshot(actor, claim_id)
    with workflow_graph(app, actor, mutate=False) as graph:
        return view(graph.get_state(graph_config(actor, claim_id, workflow_id)))


@router.post("/{workflow_id}/resume")
def resume(claim_id: UUID, workflow_id: UUID, payload: ResumeWorkflow, actor: Actor,
           key: RequestKey, request: Request) -> dict:
    from langgraph.types import Command
    from claimpilot.agents.workflows import workflow_graph

    app = request.app.state
    config = graph_config(actor, claim_id, workflow_id)
    with workflow_graph(app, actor, mutate=True) as graph:
        claim = app.workflow_store.snapshot(actor, claim_id).claim
        state = graph.get_state(config)
        previous = view(state)
        matches = previous["pause_id"] == payload.pause_id
        if matches:
            if previous["status"] == "awaiting_approval":
                if (payload.action not in ("approve", "reject") or not payload.reason
                    or str(payload.proposal_id) != previous["proposal_id"]
                    or payload.proposal_version != previous["proposal_version"]
                    or payload.expected_version != previous["claim_version"]):
                    raise IntakeError(409, "decision_mismatch", "Specify the paused proposal, versions and decision reason.")
            elif payload.action != "retry" or payload.expected_version != claim.version:
                raise IntakeError(409, "retry_mismatch", "Retry using the current claim version.")
        replay = app.workflow_store.reserve_resume(actor, workflow_id, key, payload, matches)
        if matches:
            choice = {"action": payload.action, "reason": payload.reason, "reviewer_id": actor.operator_id}
            invoke(graph, config, Command(resume={payload.pause_id: choice}))
        elif replay and state.next and not current_pause(state):
            invoke(graph, config, None)
        return view(graph.get_state(config))
