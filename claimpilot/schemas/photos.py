from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)]
DamageType = Literal[
    "water_related", "fire_related", "surface_discoloration", "cracking", "material_loss",
    "deformation", "debris", "other",
]
Trade = Literal[
    "water_restoration", "fire_restoration", "carpentry", "roofing", "electrical",
    "plumbing", "general_restoration",
]
PhotoSafetyCue = Literal[
    "visible_debris", "visible_sharp_edges", "possible_surface_growth", "visible_exposed_wiring",
    "visible_standing_water", "visible_loose_material",
]
BatchLimitation = Literal["views_may_show_different_areas", "findings_vary_between_views", "overlap_unclear"]


class VisionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class VisibleDamage(VisionModel):
    observed_damage_types: list[DamageType] = Field(max_length=8)
    affected_areas: list[ShortText] = Field(max_length=8)
    materials: list[ShortText] = Field(max_length=8)
    visible_indicators: list[ShortText] = Field(max_length=8)


class DamageInterpretation(VisionModel):
    apparent_severity: Literal["minor", "moderate", "severe", "unknown"]
    potential_safety_concerns: list[PhotoSafetyCue] = Field(max_length=5)
    suggested_specializations: list[Trade] = Field(max_length=4)


class PhotoObservation(VisionModel):
    image_id: Annotated[str, StringConstraints(min_length=36, max_length=36)]
    image_quality: Literal["usable", "blurry", "irrelevant", "insufficient"]
    visible: VisibleDamage
    interpretation: DamageInterpretation
    limitations: list[ShortText] = Field(min_length=1, max_length=6)
    follow_up_questions: list[ShortText] = Field(max_length=5)

    @model_validator(mode="after")
    def unsupported_image_has_no_findings(self) -> "PhotoObservation":
        if self.image_quality in ("irrelevant", "insufficient"):
            if any(self.visible.model_dump().values()) or (
                self.interpretation.apparent_severity != "unknown"
                or self.interpretation.potential_safety_concerns
                or self.interpretation.suggested_specializations
            ):
                raise ValueError("An unsupported image cannot have damage findings")
        return self


class DamageReport(VisionModel):
    observations: list[PhotoObservation] = Field(min_length=1, max_length=3)
    batch_limitations: list[BatchLimitation] = Field(max_length=3)

    @model_validator(mode="after")
    def batch_limitations_require_multiple_photos(self) -> "DamageReport":
        if len(self.observations) == 1 and self.batch_limitations:
            raise ValueError("A single photo cannot have cross-photo limitations")
        return self


class PhotoMetadata(BaseModel):
    id: UUID
    claim_id: UUID
    media_type: Literal["image/jpeg"] = "image/jpeg"
    width: int
    height: int
    size_bytes: int
    created_at: datetime


class AnalyzePhotos(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    photo_ids: list[UUID] = Field(min_length=1, max_length=3)
    context: ShortText | None = None

    @field_validator("photo_ids")
    @classmethod
    def unique_ids(cls, values: list[UUID]) -> list[UUID]:
        if len(set(values)) != len(values):
            raise ValueError("Provide distinct photo IDs")
        return values


class AnalysisView(BaseModel):
    id: UUID
    claim_id: UUID
    photo_ids: list[UUID]
    status: Literal["pending", "completed", "failed", "invalidated"]
    provider: Literal["offline_fixture", "groq"]
    model: str
    attempts: int
    result: DamageReport | None = None
    error_code: str | None = None


class PhotoFollowUp(BaseModel):
    photo_id: UUID
    kind: Literal["possible_disagreement", "image_limitation", "observation_question"]
    question: str
