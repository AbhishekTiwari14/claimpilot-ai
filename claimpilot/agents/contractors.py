from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import datetime, timezone
import json
import re
import sqlite3
from threading import BoundedSemaphore
from time import monotonic
from uuid import UUID, uuid4

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langsmith import tracing_context
from pydantic import BaseModel, ValidationError

from claimpilot.agents.contractor_fixtures import offline_contractor_turn
from claimpilot.config import Settings
from claimpilot.prompts.contractors import CONTRACTOR_PROMPT
from claimpilot.schemas.contractors import AgentTrace, DispatchRequirements, ModelTrace, Proposal, ProposalDraft, RunView, ToolTrace
from claimpilot.schemas.knowledge import Operator
from claimpilot.services.contractor_tools import ContractorTools
from claimpilot.services.contractor_urgency import classify_urgency, service_zip, specialization
from claimpilot.services.knowledge_answers import assess_claim
from claimpilot.services.knowledge_index import KnowledgeIndex
from claimpilot.services.llm import LLMError, LLMService
from claimpilot.storage.contractors import ContractorStore
from claimpilot.storage.intake import IntakeError

CALL_ID = re.compile(r"^[A-Za-z0-9_-]{1,96}$")
DETAIL_TOOLS = {"check_availability", "get_estimated_arrival", "get_contractor_performance"}
EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="contractor-tool")
SLOTS = BoundedSemaphore(4)


def invoke_local(tool, args):
    with tracing_context(enabled=False):
        return tool.invoke(args)


def timed_read(tool, args: dict, timeout: float) -> tuple[BaseModel | None, str | None, int]:
    deadline = monotonic() + timeout
    for attempt in (1, 2):
        if not SLOTS.acquire(blocking=False):
            return None, "tool_busy", attempt - 1
        try:
            future = EXECUTOR.submit(invoke_local, tool, args)
        except RuntimeError:
            SLOTS.release()
            return None, "tool_unavailable", attempt - 1
        future.add_done_callback(lambda _: SLOTS.release())
        try:
            return future.result(timeout=max(0, deadline - monotonic())), None, attempt
        except TimeoutError:
            future.cancel()
            return None, "tool_timeout", attempt
        except IntakeError as error:
            if error.status != 503:
                return None, error.code, attempt
        except sqlite3.OperationalError:
            pass
        except Exception:
            return None, "tool_error", attempt
        if attempt == 2 or monotonic() >= deadline:
            return None, "tool_unavailable", attempt
    raise RuntimeError("Unreachable tool retry state")


def safe_result(result: BaseModel) -> dict:
    if isinstance(result, DispatchRequirements):
        return result.model_dump(exclude={"sources"}) | {"source_sections": [s.section for s in result.sources]}
    return result.model_dump(exclude={"fingerprints"})


def run_recommendation(
    store: ContractorStore, knowledge: KnowledgeIndex, llm: LLMService, settings: Settings,
    actor: Operator, claim_id: UUID, key: str, expected_version: int,
) -> RunView:
    offline = settings.claimpilot_llm_provider == "offline"
    reserved = store.reserve_run(
        actor, claim_id, key, expected_version, provider="offline_fixture" if offline else "groq",
        model="offline-fixture" if offline else settings.groq_model,
    )
    if isinstance(reserved, RunView):
        return reserved
    run_id, snapshot = reserved
    trace = AgentTrace(run_id=run_id)
    facts = snapshot.facts
    urgency = classify_urgency(facts, snapshot.photos, intake_safety=snapshot.claim.assistance_reason == "immediate_safety")
    zip_code = service_zip(str(facts.get("property_location", "")))
    trade = specialization(facts)
    proposal = Proposal(
        id=uuid4(), run_id=run_id, claim_id=claim_id, claim_version=expected_version,
        version=0, urgency=urgency, required_specialization=trade, created_at=datetime.now(timezone.utc),
        explanation="An operator must review the available evidence.", status="requires_intervention",
    )
    reason = None
    if urgency.mandatory_escalation:
        reason = "mandatory_safety_escalation"
    elif snapshot.claim.version != expected_version:
        reason = "stale_claim_version"
    elif snapshot.claim.status != "intake_completed":
        reason = "confirmed_intake_required"
    elif zip_code is None:
        reason = "usable_service_zip_required"
    elif trade is None:
        reason = "trade_uncertain_or_conflicting"
    else:
        damage_types = {kind for item in snapshot.photos if item.image_quality == "usable"
                        for kind in item.visible.observed_damage_types}
        if (trade == "water_restoration" and damage_types & {"fire_related", "water_related"} == {"fire_related"}
            or trade == "fire_restoration" and damage_types & {"fire_related", "water_related"} == {"water_related"}):
            reason = "unconfirmed_photo_peril_disagreement"
    if reason:
        proposal.uncertainties.append(reason)
        if urgency.mandatory_escalation:
            proposal.explanation = "A potential immediate hazard requires human safety review."
        return store.finish_run(actor, proposal, trace, snapshot, {})

    policy_outcome = None
    if snapshot.binding:
        try:
            policy_outcome = assess_claim(knowledge, llm, actor, claim_id).assessment.outcome
        except IntakeError:
            proposal.uncertainties.append("policy_assessment_unavailable")
    if policy_outcome == "conflicting_evidence":
        proposal.uncertainties.append("policy_evidence_conflicts")
        return store.finish_run(actor, proposal, trace, snapshot, {})

    tools = ContractorTools(store, knowledge, actor, zip_code, trade, urgency.level)
    definitions = tools.definitions()
    tool_map = {tool.name: tool for tool in definitions}
    context = json.dumps({
        "confirmed_report": {field: str(facts[field])[:120] for field in (
            "damage_type", "suspected_cause", "affected_areas", "ongoing_hazards"
        ) if field in facts},
        "unconfirmed_photo_signals": [
            {"severity": item.interpretation.apparent_severity,
             "safety_cues": item.interpretation.potential_safety_concerns,
             "observed_damage_types": item.visible.observed_damage_types}
            for item in snapshot.photos[:3]
        ],
        "urgency": urgency.level, "required_specialization": trade,
        "preliminary_policy_assessment": policy_outcome,
    })
    messages = [SystemMessage(content=CONTRACTOR_PROMPT), HumanMessage(content=context)]
    seen_ids: set[str] = set()
    seen_calls: set[tuple[str, str]] = set()
    invalid = 0
    draft = None
    stop_after_feedback = False
    deadline = monotonic() + settings.claimpilot_agent_deadline_seconds

    for iteration in range(1, settings.claimpilot_agent_max_iterations + 1):
        if monotonic() >= deadline:
            proposal.uncertainties.append("agent_deadline")
            break
        feedback = [message.tool_call_id for message in messages if isinstance(message, ToolMessage)]
        progress = {
            "search_done": any(step.tool == "search_contractors" and step.success for step in trace.tools),
            "requirements_done": tools.requirements is not None,
            "candidate_checks": {
                contractor_id: sorted(name for name, item in tools.observations if item == contractor_id)
                for contractor_id in sorted(tools.discovered)
            },
        }
        checks = progress["candidate_checks"]
        ready = [candidate for candidate, confirmed in checks.items()
                 if DETAIL_TOOLS <= set(confirmed) and tools.requirements is not None]
        if stop_after_feedback:
            offered_names = set(tool_map)
            progress["phase"] = "final_result_stop"
        elif ready:
            offered_names = {"propose_dispatch"}
            progress["ready_candidates"] = ready
        else:
            offered_names = {"get_dispatch_requirements"} if tools.requirements is None else set()
            if not tools.discovered:
                offered_names.add("search_contractors")
            else:
                focus = max(checks, key=lambda candidate: len(checks[candidate]))
                if checks[focus]:
                    progress["candidate_to_finish"] = focus
                offered_names.update(DETAIL_TOOLS - set(checks[focus]))
        progress["allowed_next_tools"] = sorted(offered_names) if not stop_after_feedback else []
        messages[0] = SystemMessage(content=CONTRACTOR_PROMPT + "\nVerified progress: " + json.dumps(progress))
        offered = [tool for tool in definitions if tool.name in offered_names]
        start = monotonic()
        try:
            turn = llm.tool_turn(
                messages, offered, offline_turn=offline_contractor_turn,
                tool_choice="auto" if stop_after_feedback else "required",
            )
        except LLMError as error:
            trace.model_turns.append(ModelTrace(
                iteration=iteration, feedback_call_ids=feedback, requested_call_ids=[], usage={},
                duration_ms=round((monotonic() - start) * 1000, 1), retries=error.retries, error=error.code,
            ))
            proposal.uncertainties.append(f"model_{error.code}")
            break
        message = turn.message
        calls = [(call, False) for call in message.tool_calls] + [
            (call, True) for call in message.invalid_tool_calls
        ]
        ids = [call.get("id") or "" for call, _ in calls]
        valid_ids = all(isinstance(value, str) and CALL_ID.fullmatch(value) for value in ids)
        usage = {key: value for key, value in (message.usage_metadata or {}).items()
                 if key in ("input_tokens", "output_tokens", "total_tokens") and isinstance(value, int)}
        trace.model_turns.append(ModelTrace(
            iteration=iteration, feedback_call_ids=feedback, requested_call_ids=ids if valid_ids else [],
            usage=usage, duration_ms=round((monotonic() - start) * 1000, 1), retries=turn.retries,
        ))
        if not calls or stop_after_feedback:
            break
        if not valid_ids or len(set(ids)) != len(ids) or seen_ids.intersection(ids):
            proposal.uncertainties.append("invalid_or_reused_call_id")
            break
        seen_ids.update(ids)
        messages.append(AIMessage(content="", tool_calls=message.tool_calls, invalid_tool_calls=message.invalid_tool_calls))
        for call, malformed in calls:
            if len(trace.tools) >= settings.claimpilot_agent_max_tool_calls:
                proposal.uncertainties.append("tool_budget_exhausted")
                stop_after_feedback = True
                break
            name = call.get("name", "")
            tool = tool_map.get(name)
            public_name = name if tool else "unknown_tool"
            args = {}
            error = None
            result = None
            attempts = 0
            tool_start = monotonic()
            if stop_after_feedback:
                error = "tool_loop_stopped"
            elif tool is None:
                error = "unknown_tool"
            elif malformed:
                error = "invalid_arguments"
            else:
                try:
                    args = tool.args_schema.model_validate(call["args"]).model_dump()
                except ValidationError:
                    error = "invalid_arguments"
            if not error:
                signature = public_name, json.dumps(args, sort_keys=True)
                if signature in seen_calls:
                    error = "repeated_tool_call"
                elif name not in offered_names:
                    error = "tool_not_offered"
                else:
                    seen_calls.add(signature)
                    remaining = min(settings.claimpilot_agent_tool_timeout_seconds, deadline - monotonic())
                    if remaining <= 0:
                        error = "agent_deadline"
                    else:
                        result, error, attempts = timed_read(tool, args, remaining)
            if result is not None and error is None:
                tools.observe(name, result)
                body = safe_result(result)
                if isinstance(result, ProposalDraft):
                    draft = result
                    stop_after_feedback = True
                if name == "search_contractors" and not body["candidate_ids"] and not tools.discovered:
                    proposal.uncertainties.append("no_eligible_contractors")
                    stop_after_feedback = True
            else:
                error = error or "tool_error"
                body = {"error": error}
                invalid += error in ("unknown_tool", "invalid_arguments", "repeated_tool_call", "not_discovered",
                                     "tool_not_offered")
                if error not in proposal.uncertainties:
                    proposal.uncertainties.append(error)
                if invalid >= 2:
                    stop_after_feedback = True
            trace.tools.append(ToolTrace(
                iteration=iteration, call_id=call["id"], tool=public_name, arguments=args,
                result=body, duration_ms=round((monotonic() - tool_start) * 1000, 1),
                attempts=attempts, success=error is None,
            ))
            messages.append(ToolMessage(content=json.dumps(body, separators=(",", ":")),
                                        tool_call_id=call["id"], name=public_name))
        # A provider request may only follow a fully paired assistant tool message.
        if trace.tools[-len(ids):] and {step.call_id for step in trace.tools[-len(ids):]} != set(ids):
            break
    else:
        if draft is None:
            proposal.uncertainties.append("iteration_limit")

    proposal.requirements = tools.requirements
    if draft is not None:
        proposal.recommended_contractor_id = draft.contractor_id
        proposal.alternative_contractor_ids = draft.alternative_ids
        proposal.estimated_arrival_minutes = draft.arrival_minutes
        proposal.status = "awaiting_approval"
        proposal.evidence = ["search_contractors", "check_availability", "get_estimated_arrival",
                             "get_contractor_performance", "get_dispatch_requirements"]
        proposal.explanation = (
            f"{draft.contractor_id} passed the trade, service area, credential, capacity and SLA checks. "
            f"The recorded arrival estimate is {draft.arrival_minutes} minutes."
        )
    elif "no_eligible_contractors" in proposal.uncertainties:
        proposal.explanation = "No eligible contractor was available for the confirmed service area and trade."
    elif not trace.tools or tools.discovered:
        proposal.status = "unsuccessful"
        proposal.explanation = "The run ended without enough verified evidence to recommend a contractor."
    return store.finish_run(actor, proposal, trace, snapshot, draft.fingerprints if draft else {})
