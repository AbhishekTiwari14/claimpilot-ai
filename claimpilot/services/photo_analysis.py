import json
import re

from claimpilot.agents.photo_fixtures import offline_observation
from claimpilot.prompts.photo_analysis import PHOTO_ANALYSIS_PROMPT, PHOTO_ANALYSIS_VERSION
from claimpilot.schemas.conversation import ClaimState
from claimpilot.schemas.photos import AnalysisView, DamageReport, PhotoFollowUp
from claimpilot.services.llm import LLMService
from claimpilot.storage.photos import AnalysisAttempt

ROOMS = re.compile(r"\b(kitchen|bathroom|bedroom|basement|garage|attic|hallway|living room|roof)\b")
DAMAGE_WORDS = {
    "fire_related": re.compile(r"\b(fire|smoke|soot|burns?|burned|scorch\w*)\b"),
    "water_related": re.compile(r"\b(water|flood\w*|leak\w*)\b"),
}


def analyze_photos(attempt: AnalysisAttempt, context: str | None, llm: LLMService) -> DamageReport:
    photo_ids = [photo_id for photo_id, _ in attempt.images]

    def verify(report: DamageReport) -> None:
        if [item.image_id for item in report.observations] != photo_ids:
            raise ValueError("Vision observations must match the supplied image identifiers")

    return llm.structured(
        DamageReport, system_prompt=PHOTO_ANALYSIS_PROMPT,
        user_payload=json.dumps({
            "customer_context": context,
            "reported_claim_details": {
                field: (value if isinstance(value, str) else ", ".join(value))[:120]
                for field, value in attempt.claim_context.items()
            },
        }, ensure_ascii=False),
        operation=PHOTO_ANALYSIS_VERSION, images=attempt.images, validate=verify,
        offline_result=lambda: {
            "observations": [
                offline_observation(photo_id, image_hash)
                for photo_id, image_hash in zip(photo_ids, attempt.original_hashes, strict=True)
            ],
            "batch_limitations": [],
        },
    )


def photo_follow_ups(claim: ClaimState, analyses: list[AnalysisView]) -> list[PhotoFollowUp]:
    if claim.status == "human_assistance_required":
        return []
    area = claim.facts.get("affected_areas")
    customer_rooms = set(ROOMS.findall(str(area.value).casefold())) if area else set()
    damage = claim.facts.get("damage_type")
    customer_types = {
        category for category, pattern in DAMAGE_WORDS.items()
        if damage and pattern.search(str(damage.value).casefold())
    }
    questions = []
    seen = set()
    for analysis in analyses:
        if analysis.result is None:
            continue
        for item in analysis.result.observations:
            if item.image_id in seen:
                continue
            seen.add(item.image_id)
            if item.image_quality != "usable":
                kind = "image_limitation"
                question = "Can you share a clearer photo that shows the part of the property you reported?"
            else:
                rooms = set(ROOMS.findall(" ".join(item.visible.affected_areas).casefold()))
                types = set(DAMAGE_WORDS) & set(item.visible.observed_damage_types)
                if customer_rooms and rooms and not (customer_rooms & rooms):
                    kind = "possible_disagreement"
                    question = (
                        f"You reported {', '.join(sorted(customer_rooms))}. This photo may show "
                        f"{', '.join(sorted(rooms))}. Which area does it show, and is that area also affected?"
                    )
                elif customer_types and types and not (customer_types & types):
                    kind = "possible_disagreement"
                    question = "The photo may show a different kind of damage. Can you describe what it shows?"
                elif item.follow_up_questions:
                    kind = "observation_question"
                    question = "Can you describe what the photo shows and whether the damage is still changing?"
                else:
                    continue
            questions.append(PhotoFollowUp(photo_id=item.image_id, kind=kind, question=question))
            if len(questions) >= 3:
                return questions
    return questions
