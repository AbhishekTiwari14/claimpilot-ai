from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, computed_field, field_validator

from claimpilot.schemas.intake import ClaimField, MAX_REPORT_LENGTH

IntakeStatus = Literal[
    "collecting_information",
    "awaiting_clarification",
    "awaiting_customer_confirmation",
    "intake_completed",
    "human_assistance_required",
]
REQUIRED_FIELDS: tuple[ClaimField, ...] = (
    "damage_type", "affected_areas", "property_location", "incident_time", "policy_id",
    "ongoing_hazards",
)


class Claimant(BaseModel):
    tenant_id: str = Field(min_length=1)
    claimant_id: str = Field(min_length=1)


class MessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    message: str = Field(min_length=1, max_length=MAX_REPORT_LENGTH)
    expected_version: int = Field(ge=0)
    replace_failed_message: StrictBool = False

    @field_validator("message")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message must contain text")
        return value


class ConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    expected_version: int = Field(ge=0)
    confirmed: StrictBool


class Evidence(BaseModel):
    value: str | list[str]
    message_id: UUID
    status: Literal["candidate", "customer_confirmed"] = "candidate"


class ClaimState(BaseModel):
    claim_id: UUID
    conversation_id: UUID
    version: int = 0
    status: IntakeStatus = "collecting_information"
    facts: dict[ClaimField, Evidence] = Field(default_factory=dict)
    conflicts: dict[ClaimField, list[Evidence]] = Field(default_factory=dict)
    pending_field: ClaimField | None = "damage_type"
    no_progress_turns: int = 0
    confirmed_at: datetime | None = None
    assistance_reason: Literal["immediate_safety", "clarification_exhausted"] | None = None

    @computed_field
    @property
    def missing_fields(self) -> list[ClaimField]:
        return [field for field in REQUIRED_FIELDS if field not in self.facts]

    @computed_field
    @property
    def summary(self) -> dict[ClaimField, str | list[str]] | None:
        if self.status in ("awaiting_customer_confirmation", "intake_completed"):
            return {field: evidence.value for field, evidence in self.facts.items()}
        return None


class ConversationMessage(BaseModel):
    id: UUID
    sequence: int
    role: Literal["customer", "assistant"]
    text: str
    created_at: datetime


class ClaimView(ClaimState):
    pending_message_id: UUID | None = None
    processing_error: str | None = None


class IntakeTurn(BaseModel):
    claim: ClaimView
    reply: ConversationMessage


class ConversationHistory(BaseModel):
    conversation_id: UUID
    claim_id: UUID
    messages: list[ConversationMessage]
    next_after: int | None = None
