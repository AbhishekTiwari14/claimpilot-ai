from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Identifier = Annotated[str, StringConstraints(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")]
Kind = Literal["policy", "operations"]


class Operator(BaseModel):
    tenant_id: Identifier
    operator_id: Identifier


class Scope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_type: Kind
    policy_id: Identifier | None = None
    policy_version: Identifier | None = None

    @model_validator(mode="after")
    def exact_policy_scope(self) -> "Scope":
        if self.document_type == "policy":
            if not self.policy_id or not self.policy_version:
                raise ValueError("Policy ID and version are required together")
        elif self.policy_id or self.policy_version:
            raise ValueError("Operational documents have no policy scope")
        return self


class DocumentInput(Scope):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)]
    subject_claimant_id: Identifier | None = None
    document_version: int = Field(default=1, ge=1, le=1_000_000)

    @model_validator(mode="after")
    def private_policy_only(self) -> "DocumentInput":
        if self.subject_claimant_id and self.document_type != "policy":
            raise ValueError("A claimant subject requires policy scope")
        return self


class Section(BaseModel):
    page: int | None = None
    section: str | None = None
    text: str


class Document(DocumentInput):
    id: UUID
    filename: str
    content_hash: str
    format: Literal["pdf", "docx", "txt", "md"]
    status: Literal["pending", "indexing", "ready", "failed"]
    generation: int
    indexing_config: dict | None
    error_code: str | None
    created_at: str


class SearchRequest(Scope):
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=600)]
    top_k: int | None = Field(default=None, ge=1, le=12)


class Evidence(BaseModel):
    chunk_id: str
    document_id: UUID
    filename: str
    title: str
    document_version: int
    content_hash: str
    document_type: Kind
    policy_id: str | None
    policy_version: str | None
    page: int | None
    section: str | None
    text: str
    distance: float


Outcome = Literal["answered", "potentially_covered", "potentially_excluded", "conflicting_evidence", "insufficient_evidence"]


class EvidenceChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_number: int = Field(ge=1, le=12)
    category: Literal["answer", "coverage", "exclusion", "deductible", "limitation"]


class AnswerSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    outcome: Literal["potentially_covered", "potentially_excluded", "conflicting_evidence", "insufficient_evidence"]
    sources: list[EvidenceChoice] = Field(max_length=8)


class QuestionSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    outcome: Literal["answered", "conflicting_evidence", "insufficient_evidence"]
    source_numbers: list[Annotated[int, Field(ge=1, le=12)]] = Field(max_length=8)


class Citation(BaseModel):
    category: Literal["answer", "coverage", "exclusion", "deductible", "limitation"]
    excerpt: str
    source: Evidence


class GroundedAnswer(BaseModel):
    answer: str
    outcome: Outcome
    insufficient_evidence: bool
    citations: list[Citation]
    retrieved: list[Evidence]
    uncertainties: list[str]
    recommended_human_review: bool
    provider: Literal["offline_fixture", "groq"]


class PolicyBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy_version: Identifier


class PolicyAssessment(BaseModel):
    claim_id: UUID
    policy_id: str | None
    policy_version: str | None
    assessment: GroundedAnswer
    coverage_clauses: list[Citation]
    exclusions: list[Citation]
    deductibles: list[Citation]
    limitations: list[Citation]
