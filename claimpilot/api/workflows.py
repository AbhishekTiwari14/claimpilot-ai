from datetime import datetime
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


class SendDispatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposal_id: UUID
    proposal_version: int = Field(ge=1, strict=True)


class ContractorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: Literal["accepted", "rejected"]


class ProcessSLA(BaseModel):
    dispatch_id: UUID


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
            elif previous["status"] not in ("requires_intervention", "awaiting_information"):
                raise IntakeError(409, "dispatch_endpoint_required", "Use the dispatch endpoints for this pause.")
            elif payload.action != "retry" or payload.expected_version != claim.version:
                raise IntakeError(409, "retry_mismatch", "Retry using the current claim version.")
        replay = app.workflow_store.reserve_resume(actor, workflow_id, key, payload, matches)
        if matches:
            choice = {"action": payload.action, "reason": payload.reason, "reviewer_id": actor.operator_id}
            invoke(graph, config, Command(resume={payload.pause_id: choice}))
        elif replay and state.next and not current_pause(state):
            invoke(graph, config, None)
        return view(graph.get_state(config))


def dispatch_summary(app, actor, claim_id, workflow_id, state):
    return {"workflow": view(state), "dispatches": app.workflow_store.dispatch_history(actor, claim_id, workflow_id)}


@router.get("/{workflow_id}/dispatch")
def dispatch_status(claim_id: UUID, workflow_id: UUID, actor: Actor, request: Request) -> dict:
    from claimpilot.agents.workflows import workflow_graph

    app = request.app.state
    app.workflow_store.snapshot(actor, claim_id)
    with workflow_graph(app, actor, mutate=False) as graph:
        return dispatch_summary(app, actor, claim_id, workflow_id,
                                graph.get_state(graph_config(actor, claim_id, workflow_id)))


def dispatch_command(claim_id, workflow_id, actor, app, key, payload, *, action, dispatch_id=None):
    from langgraph.types import Command
    from claimpilot.agents.workflows import workflow_graph

    config = graph_config(actor, claim_id, workflow_id)
    with workflow_graph(app, actor, mutate=True) as graph:
        app.workflow_store.snapshot(actor, claim_id)
        state = graph.get_state(config)
        current = view(state)
        if action == "send":
            matches = (current["status"] == "approved" and current["proposal_id"] == str(payload.proposal_id)
                       and current["proposal_version"] == payload.proposal_version)
            if matches and not current_pause(state) and not state.next:
                # Pre-dispatch versions of the workflow ended at approval.
                graph.update_state(config, {}, as_node="approval")
                invoke(graph, config, None)
                state = graph.get_state(config)
        else:
            history = app.workflow_store.dispatch_history(actor, claim_id, workflow_id)
            record = next((row for row in history if row["id"] == str(dispatch_id)), None)
            if record is None:
                raise IntakeError(404, "dispatch_not_found", "Dispatch not found.")
            matches = current["status"] == "awaiting_acknowledgement" and current["dispatch_id"] == str(dispatch_id)
            if (matches and action == "sla" and record["status"] == "awaiting_acknowledgement"
                and app.workflow_store.now() < datetime.fromisoformat(record["acknowledgement_deadline"])):
                return dispatch_summary(app, actor, claim_id, workflow_id, state)
        pause = current_pause(state)
        scope = "send" if action == "send" else f"{'sla' if action == 'sla' else 'response'}:{dispatch_id}"
        replay = app.workflow_store.reserve_resume(actor, workflow_id, f"dispatch:{scope}:{key}", payload,
                                                   matches and pause is not None)
        if matches and pause:
            invoke(graph, config, Command(resume={pause.id: {"action": action}}))
        elif replay and state.next and not pause:
            invoke(graph, config, None)
        return dispatch_summary(app, actor, claim_id, workflow_id, graph.get_state(config))


@router.post("/{workflow_id}/dispatch")
def send_dispatch(claim_id: UUID, workflow_id: UUID, payload: SendDispatch, actor: Actor,
                  key: RequestKey, request: Request) -> dict:
    return dispatch_command(claim_id, workflow_id, actor, request.app.state, key, payload, action="send")


@router.post("/{workflow_id}/dispatch/{dispatch_id}/response")
def respond_to_dispatch(claim_id: UUID, workflow_id: UUID, dispatch_id: UUID, payload: ContractorResponse,
                        actor: Actor, key: RequestKey, request: Request) -> dict:
    return dispatch_command(claim_id, workflow_id, actor, request.app.state, key, payload,
                            action=payload.outcome, dispatch_id=dispatch_id)


@router.post("/{workflow_id}/dispatch/{dispatch_id}/sla")
def process_sla(claim_id: UUID, workflow_id: UUID, dispatch_id: UUID, actor: Actor,
                key: RequestKey, request: Request) -> dict:
    return dispatch_command(claim_id, workflow_id, actor, request.app.state, key,
                            ProcessSLA(dispatch_id=dispatch_id), action="sla", dispatch_id=dispatch_id)
