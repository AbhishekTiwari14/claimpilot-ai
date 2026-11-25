import re

from claimpilot.schemas.contractors import Trade, UrgencyAssessment
from claimpilot.services.intake import immediate_safety_concern

ZIP = re.compile(r"(?<![\w-])([0-9]{5})(?:-[0-9]{4})?(?![\w-])")
ACTIVE = re.compile(r"\b(?:still (?:leaking|pooling|flooding)|active (?:leak|water intrusion)|ongoing flooding|water is rising|burst pipe)\b", re.I)
NEGATED = re.compile(r"\b(?:no|not|without|stopped|no longer)\b", re.I)


def service_zip(location: str) -> str | None:
    matches = set(ZIP.findall(location))
    return next(iter(matches)) if len(matches) == 1 else None


def classify_urgency(facts: dict, photos: list, *, intake_safety: bool = False) -> UrgencyAssessment:
    hazard = str(facts.get("ongoing_hazards", ""))
    damage = str(facts.get("damage_type", ""))
    cause = str(facts.get("suspected_cause", ""))
    photos = [item for item in photos if item.image_quality in ("usable", "blurry")]
    if intake_safety or any(immediate_safety_concern(value) for value in (hazard, damage, cause)):
        return UrgencyAssessment(level="emergency", mandatory_escalation=True, reasons=["reported_immediate_hazard"])
    if any("visible_exposed_wiring" in item.interpretation.potential_safety_concerns for item in photos):
        return UrgencyAssessment(level="emergency", mandatory_escalation=True, reasons=["unconfirmed_photo_electrical_hazard"])
    if any(ACTIVE.search(part) and not NEGATED.search(part) for part in re.split(r"[.!?;,]|\bbut\b", hazard, flags=re.I)):
        return UrgencyAssessment(level="high", mandatory_escalation=False, reasons=["reported_active_damage"])
    if any(item.interpretation.apparent_severity == "severe" or
           "visible_standing_water" in item.interpretation.potential_safety_concerns for item in photos):
        return UrgencyAssessment(level="high", mandatory_escalation=False, reasons=["unconfirmed_photo_extent"])
    if re.search(r"\b(?:water|fire|smoke|storm|wind|flood|roof)\b", damage, re.I):
        return UrgencyAssessment(level="medium", mandatory_escalation=False, reasons=["reported_restoration_peril"])
    return UrgencyAssessment(level="low", mandatory_escalation=False, reasons=["routine_observed_damage"])


def specialization(facts: dict) -> Trade | None:
    text = str(facts.get("damage_type", "")).casefold()
    patterns: tuple[tuple[str, Trade], ...] = (
        (r"\b(?:fire|smoke|soot|burn\w*)\b", "fire_restoration"),
        (r"\b(?:electrical|wiring|outlet)\b", "electrical"),
        (r"\b(?:roof|roofing|shingles?)\b", "roofing"),
        (r"\b(?:flood\w*|water|moisture)\b", "water_restoration"),
        (r"\b(?:storm|wind|hail)\b", "storm_damage"),
        (r"\b(?:plumbing|(?:burst|broken) pipe)\b", "plumbing"),
        (r"\b(?:surface|drywall|crack\w*|cosmetic|minor damage)\b", "general_restoration"),
    )
    found: set[Trade] = {trade for pattern, trade in patterns if re.search(pattern, text)}
    if "roofing" in found:
        found.discard("storm_damage")
    if found - {"general_restoration"}:
        found.discard("general_restoration")
    return next(iter(found)) if len(found) == 1 else None
