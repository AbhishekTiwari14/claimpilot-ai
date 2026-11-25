import json

from pydantic import BaseModel, ConfigDict, Field, field_validator

from claimpilot.prompts.claim_extraction import CLAIM_EXTRACTION_PROMPT, CLAIM_EXTRACTION_VERSION
from claimpilot.schemas.intake import MAX_REPORT_LENGTH, IntakeExtraction
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
    if report != SAMPLE_REPORT:
        raise LLMError("offline_input_unsupported")
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


def extract_claim(report: str, llm: LLMService) -> IntakeExtraction:
    text = ClaimReport(text=report).text
    return llm.structured(
        IntakeExtraction,
        system_prompt=CLAIM_EXTRACTION_PROMPT,
        user_payload=json.dumps({"customer_report": text}, ensure_ascii=False),
        operation=CLAIM_EXTRACTION_VERSION,
        offline_result=lambda: _offline_extraction(text),
        validate=lambda result: result.verify_source(text),
    )
