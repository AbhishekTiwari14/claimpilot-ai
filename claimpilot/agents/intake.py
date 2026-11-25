import json

from pydantic import BaseModel, ConfigDict, Field, field_validator

from claimpilot.agents.intake_fixtures import OFFLINE_TURNS
from claimpilot.prompts.claim_extraction import (
    CLAIM_EXTRACTION_PROMPT, CLAIM_EXTRACTION_VERSION, CONVERSATION_CONTEXT,
)
from claimpilot.schemas.intake import CLAIM_FIELDS, MAX_REPORT_LENGTH, ClaimField, IntakeExtraction
from claimpilot.services.llm import LLMError, LLMService

SAMPLE_REPORT = (
    "I found water damage in the kitchen at 12 Example Lane yesterday morning. "
    "I think the dishwasher leaked. Water is still pooling. My policy ID is SYN-123."
)


class ClaimReport(BaseModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)

    text: str = Field(min_length=1, max_length=MAX_REPORT_LENGTH)

    @field_validator("text")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("The report must contain text")
        return value


def _offline_extraction(report: str) -> dict:
    if report in OFFLINE_TURNS:
        return dict.fromkeys(CLAIM_FIELDS) | {"conflicts": []} | OFFLINE_TURNS[report]
    if report == SAMPLE_REPORT:
        return {
            "damage_type": "water damage",
            "suspected_cause": "I think the dishwasher leaked",
            "affected_areas": ["kitchen"],
            "incident_time": "yesterday morning",
            "property_location": "12 Example Lane",
            "policy_id": "SYN-123",
            "ongoing_hazards": ["Water is still pooling"],
            "conflicts": [],
        }
    raise LLMError("offline_input_unsupported")


def extract_claim(
    report: str, llm: LLMService, *, pending_field: ClaimField | None = None
) -> IntakeExtraction:
    text = ClaimReport(text=report).text
    payload = {"customer_report": text}
    prompt = CLAIM_EXTRACTION_PROMPT
    operation = CLAIM_EXTRACTION_VERSION
    if pending_field is not None:
        payload["pending_field"] = pending_field
        prompt += CONVERSATION_CONTEXT
        operation = "claim-turn-v1"
    return llm.structured(
        IntakeExtraction,
        system_prompt=prompt,
        user_payload=json.dumps(payload, ensure_ascii=False),
        operation=operation,
        offline_result=lambda: _offline_extraction(text),
        validate=lambda result: result.verify_source(text),
    )
