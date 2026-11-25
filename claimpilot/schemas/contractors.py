from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Trade = Literal[
    "water_restoration", "fire_restoration", "storm_damage", "roofing", "plumbing",
    "electrical", "general_restoration",
]
Urgency = Literal["low", "medium", "high", "emergency"]
ProposalStatus = Literal["awaiting_approval", "requires_intervention", "unsuccessful"]
ContractorID = Annotated[str, StringConstraints(pattern=r"^CT-[0-9]{3}$")]


class Contractor(BaseModel):
    id: ContractorID
    name: str = Field(min_length=1, max_length=100)
    specializations: list[Trade]
    service_areas: list[str]
    zip_codes: list[Annotated[str, StringConstraints(pattern=r"^[0-9]{5}$")]]
    opens_utc: int = Field(ge=0, le=23)
    closes_utc: int = Field(ge=1, le=24)
    available: bool
    capacity: int = Field(ge=0)
    arrival_minutes: int = Field(gt=0)
    rating: float = Field(ge=0, le=5)
    acceptance_rate: float = Field(ge=0, le=1)
    completion_rate: float = Field(ge=0, le=1)
    estimated_cost_usd: int = Field(ge=0)
    emergency_capable: bool
    active: bool
    credentialed: bool
    insured: bool
    synthetic: Literal[True] = True

    @model_validator(mode="after")
    def valid_hours(self) -> "Contractor":
        if self.closes_utc <= self.opens_utc:
            raise ValueError("Use a same-day operating window in UTC")
        return self


class UrgencyAssessment(BaseModel):
    level: Urgency
    mandatory_escalation: bool
    reasons: list[str]


class NoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SearchArgs(NoArgs):
    limit: int = Field(default=3, ge=1, le=5, description="Maximum candidates")


class CandidateArgs(NoArgs):
    contractor_id: ContractorID = Field(description="ID returned by search")


class SearchResults(BaseModel):
    candidate_ids: list[ContractorID]


class Availability(BaseModel):
    contractor_id: ContractorID
    eligible: bool
    capacity: int


class Arrival(BaseModel):
    contractor_id: ContractorID
    minutes: int


class Performance(BaseModel):
    contractor_id: ContractorID
    rating: float
    acceptance_rate: float
    completion_rate: float
    simulated_cost_usd: int


class RequirementSource(BaseModel):
    chunk_id: str
    filename: str
    section: str | None
    excerpt: str


class DispatchRequirements(BaseModel):
    approval_required: Literal[True] = True
    acknowledgement_limit_minutes: int
    arrival_limit_minutes: int | None
    sources: list[RequirementSource]
    provenance: Literal["validated_operations", "synthetic_default"] = "validated_operations"


class ProposalDraft(BaseModel):
    contractor_id: ContractorID
    alternative_ids: list[ContractorID]
    arrival_minutes: int
    fingerprints: dict[str, str]
    status: Literal["awaiting_approval"] = "awaiting_approval"


class Proposal(BaseModel):
    id: UUID
    run_id: UUID
    claim_id: UUID
    claim_version: int
    version: int
    urgency: UrgencyAssessment
    required_specialization: Trade | None
    recommended_contractor_id: ContractorID | None = None
    alternative_contractor_ids: list[ContractorID] = Field(default_factory=list)
    estimated_arrival_minutes: int | None = None
    requirements: DispatchRequirements | None = None
    evidence: list[str] = Field(default_factory=list)
    explanation: str
    uncertainties: list[str] = Field(default_factory=list)
    status: ProposalStatus
    human_intervention_required: Literal[True] = True
    stale: bool = False
    created_at: datetime


class RunView(BaseModel):
    id: UUID
    claim_id: UUID
    status: Literal["running", "completed"]
    provider: Literal["offline_fixture", "groq"]
    model: str
    proposal: Proposal | None = None


class RecommendationRequest(NoArgs):
    expected_version: int = Field(ge=0)


class ToolTrace(BaseModel):
    iteration: int
    call_id: str | None
    tool: str
    arguments: dict
    result: dict
    duration_ms: float
    success: bool
    attempts: int


class ModelTrace(BaseModel):
    iteration: int
    feedback_call_ids: list[str]
    requested_call_ids: list[str]
    usage: dict[str, int]
    duration_ms: float
    retries: int = 0
    error: str | None = None


class AgentTrace(BaseModel):
    run_id: UUID
    model_turns: list[ModelTrace] = Field(default_factory=list)
    tools: list[ToolTrace] = Field(default_factory=list)
    recommendation_status: ProposalStatus | None = None
    decision_summary: str | None = None
