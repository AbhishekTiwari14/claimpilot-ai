import re
from datetime import datetime, timezone
from uuid import UUID

from claimpilot.schemas.conversation import ClaimState, Evidence
from claimpilot.schemas.intake import CLAIM_FIELDS, ClaimField, IntakeExtraction, service_zip

WELCOME = "Tell me what happened and which parts of your property were damaged."
SAFETY_REPLY = (
    "If anyone is in immediate danger, get to a safe place if you can and contact local "
    "emergency services. Your intake needs human assistance before it can continue."
)
CONFIRM_REPLY = "Please review the structured claim summary and explicitly confirm it, or tell me what to correct."
COMPLETED_REPLY = "Your confirmation was recorded. Claim intake is complete."
ASSISTANCE_REPLY = "Your information has been saved. Human assistance is needed to finish this intake."

FIELD_LABELS: dict[ClaimField, str] = {
    "damage_type": "type of damage",
    "affected_areas": "affected areas",
    "property_location": "property location",
    "incident_time": "incident time",
    "policy_id": "policy ID",
    "ongoing_hazards": "current hazards",
    "suspected_cause": "suspected cause",
}
QUESTIONS: dict[ClaimField, str] = {
    "damage_type": "What kind of damage did you notice?",
    "affected_areas": "Which rooms or parts of the property were affected?",
    "property_location": "What is the five-digit ZIP code for the damaged property's location? You can include the street address.",
    "incident_time": "When did you first notice the damage?",
    "policy_id": "What is your insurance policy ID?",
    "ongoing_hazards": "Are there any ongoing hazards or unsafe conditions at the property?",
    "suspected_cause": "What do you think caused the damage?",
}
CORRECTION_MARKER = re.compile(r"^\s*(?:correction\b|actually\b|i (?:meant|need to correct)\b)", re.I)
EMERGENCY = re.compile(
    r"\b(?:gas leak|leaking gas|(?:smell(?:s|ing)?|odor) (?:of |like )?(?:natural )?gas|gas (?:smell|odor)|"
    r"carbon monoxide (?:alarm|leak)|(?:the )?(?:house|building|property|room|kitchen|roof) is on fire|"
    r"active fire|fire (?:is )?(?:spreading|burning)|(?:there(?:'s| is)|i see) (?:a )?fire\b|"
    r"smoke (?:is )?filling|live (?:electrical )?wires?|sparking (?:outlet|wires?)|"
    r"(?:exposed|downed) (?:electrical |power )?(?:wires?|wiring|lines?)|"
    r"(?:outlet|wires?) (?:is |are )?sparking|electrical (?:fire|sparks)|"
    r"(?:structural|building|roof|ceiling|wall) collapse|(?:roof|ceiling|wall) (?:is )?collapsing|"
    r"immediate danger|(?:someone|person|people|child) (?:is |are )?trapped)\b",
    re.I,
)
NEGATION = re.compile(r"\b(?:no|not|never|don't|do not|without)\b", re.I)
UNKNOWN_VALUE = re.compile(r"^(?:unknown|unsure|not sure|i (?:do not|don't) know|n/a)[.!?]*$", re.I)


def immediate_safety_concern(report: str) -> bool:
    # A conservative screen for explicit emergency language. Check each clause independently.
    for clause in re.split(r"[.!?;,\n]|\b(?:but|however|and)\b", report, flags=re.I):
        for signal in EMERGENCY.finditer(clause):
            if not NEGATION.search(clause[:signal.start()]):
                return True
    return False


def _identity(value: str | list[str]) -> frozenset[str]:
    values = [value] if isinstance(value, str) else value
    return frozenset(" ".join(item.casefold().strip(" .!?").split()) for item in values)


def _add_evidence(options: list[Evidence], evidence: Evidence) -> None:
    if all(_identity(item.value) != _identity(evidence.value) for item in options):
        options.append(evidence)


def _answer_present(value: str | list[str] | None) -> bool:
    values = [value] if isinstance(value, str) else value
    return bool(values and all(not UNKNOWN_VALUE.fullmatch(item) for item in values))


def _follow_up(state: ClaimState) -> str:
    field = state.pending_field
    if field is None:
        return CONFIRM_REPLY
    if field in state.conflicts:
        options = [
            ", ".join(evidence.value) if isinstance(evidence.value, list) else evidence.value
            for evidence in state.conflicts[field][-2:]
        ]
        if len(options) < 2:
            return f"Please clarify the {FIELD_LABELS[field]}. {QUESTIONS[field]}"
        return f'Which {FIELD_LABELS[field]} is correct: "{options[0]}" or "{options[1]}"?'
    if field == "incident_time" and "affected_areas" in state.facts:
        areas = state.facts["affected_areas"].value
        if isinstance(areas, list):
            return f"When did you first notice the damage in the areas you mentioned ({', '.join(areas)})?"
    return QUESTIONS[field]


def apply_turn(
    state: ClaimState, message_id: UUID, report: str, extraction: IntakeExtraction | None,
    *, safety_concern: bool,
) -> str:
    state.version += 1
    if safety_concern:
        state.status = "human_assistance_required"
        state.assistance_reason = "immediate_safety"
        state.pending_field = None
        return SAFETY_REPLY
    if extraction is None:
        raise ValueError("An extraction is required for routine intake")

    previous_facts = dict(state.facts)
    previous_conflicts = {field: list(values) for field, values in state.conflicts.items()}
    for conflict in extraction.conflicts:
        original = state.facts.pop(conflict.field, None)
        alternatives = state.conflicts.setdefault(conflict.field, [])
        if original is not None:
            _add_evidence(alternatives, original)
        for statement in conflict.statements:
            _add_evidence(alternatives, Evidence(value=statement, message_id=message_id))

    explicit_correction = bool(CORRECTION_MARKER.match(report))
    for field in CLAIM_FIELDS:
        value = getattr(extraction, field)
        if field == "property_location" and service_zip(value) is None:
            prior = state.facts.get(field)
            if prior and (service_zip(prior.value) is None or (explicit_correction and value is not None)):
                state.facts.pop(field)
            continue
        if not _answer_present(value):
            continue
        evidence = Evidence(value=value, message_id=message_id)
        prior = state.facts.get(field)
        alternatives = state.conflicts.get(field)
        if explicit_correction or (alternatives is not None and state.pending_field == field):
            state.facts[field] = evidence
            state.conflicts.pop(field, None)
        elif alternatives is not None:
            _add_evidence(alternatives, evidence)
        elif prior is None:
            state.facts[field] = evidence
        elif _identity(prior.value) != _identity(value):
            state.conflicts[field] = [state.facts.pop(field), evidence]

    if state.facts == previous_facts and state.conflicts == previous_conflicts:
        state.no_progress_turns += 1
    else:
        state.no_progress_turns = 0

    if state.no_progress_turns >= 3 and (state.conflicts or state.missing_fields):
        state.status = "human_assistance_required"
        state.assistance_reason = "clarification_exhausted"
        state.pending_field = None
        return ASSISTANCE_REPLY
    if state.conflicts:
        state.status = "awaiting_clarification"
        state.pending_field = next(iter(state.conflicts))
    elif state.missing_fields:
        state.status = "collecting_information"
        state.pending_field = state.missing_fields[0]
    else:
        state.status = "awaiting_customer_confirmation"
        state.pending_field = None
    return _follow_up(state)


def confirm_state(state: ClaimState) -> None:
    state.version += 1
    state.status = "intake_completed"
    state.confirmed_at = datetime.now(timezone.utc)
    for evidence in state.facts.values():
        evidence.status = "customer_confirmed"
