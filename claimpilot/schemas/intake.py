import re
from typing import Annotated, Literal, get_args

from pydantic import AfterValidator, BaseModel, ConfigDict, computed_field, model_validator

ClaimField = Literal[
    "damage_type",
    "suspected_cause",
    "affected_areas",
    "incident_time",
    "property_location",
    "policy_id",
    "ongoing_hazards",
]
CLAIM_FIELDS = get_args(ClaimField)
MAX_REPORT_LENGTH = 4_000
ZIP = re.compile(r"(?<![\w-])([0-9]{5})(?:-[0-9]{4})?(?![\w-])")
LEADING_HOUSE_NUMBER = re.compile(r"^\s*[0-9]{5}\s+(?!(?:or|and)\b)\w", re.I)


def service_zip(location: str | list[str] | None) -> str | None:
    if not isinstance(location, str):
        return None
    house_number = LEADING_HOUSE_NUMBER.match(location)
    matches = {match[1] for match in ZIP.finditer(location)
               if not house_number or match.start() >= house_number.end()}
    return next(iter(matches)) if len(matches) == 1 else None


def validate_excerpt(value: str) -> str:
    if not 0 < len(value) <= 500 or value != value.strip():
        raise ValueError("Expected a nonempty, bounded excerpt with no surrounding whitespace")
    return value


Excerpt = Annotated[str, AfterValidator(validate_excerpt)]


class Conflict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    field: ClaimField
    statements: list[Excerpt]


class IntakeExtraction(BaseModel):
    """Verbatim candidate evidence from a single customer report."""

    model_config = ConfigDict(
        extra="forbid", strict=True, hide_input_in_errors=True, revalidate_instances="always"
    )

    damage_type: Excerpt | None
    suspected_cause: Excerpt | None
    affected_areas: list[Excerpt] | None
    incident_time: Excerpt | None
    property_location: Excerpt | None
    policy_id: Excerpt | None
    ongoing_hazards: list[Excerpt] | None
    conflicts: list[Conflict]

    @computed_field
    @property
    def missing_information(self) -> list[ClaimField]:
        return [field for field in CLAIM_FIELDS if getattr(self, field) is None]

    @model_validator(mode="after")
    def validate_candidates(self) -> "IntakeExtraction":
        for excerpts in (self.affected_areas, self.ongoing_hazards):
            if excerpts is not None and not 1 <= len(excerpts) <= 10:
                raise ValueError("Expected between one and ten excerpts or null")

        fields = set()
        for conflict in self.conflicts:
            if conflict.field in fields or getattr(self, conflict.field) is not None:
                raise ValueError("A conflicting field must appear once and have no resolved value")
            if not 2 <= len(conflict.statements) <= 5 or len(set(conflict.statements)) != len(
                conflict.statements
            ):
                raise ValueError("A conflict needs two to five distinct statements")
            fields.add(conflict.field)
        return self

    def verify_source(self, report: str) -> None:
        if self.policy_id is not None:
            identifier = re.escape(self.policy_id)
            if not re.search(rf"(?<!\w)(?<!\w[./-]){identifier}(?!\w|[./-]\w)", report):
                raise ValueError("The policy ID must match a complete identifier in the report")

        excerpts = []
        for field in CLAIM_FIELDS:
            value = getattr(self, field)
            if value is not None:
                excerpts.extend(value if isinstance(value, list) else [value])
        for conflict in self.conflicts:
            excerpts.extend(conflict.statements)

        for excerpt in excerpts:
            if not re.search(rf"(?<!\w){re.escape(excerpt)}(?!\w)", report):
                raise ValueError("Extracted evidence does not occur verbatim in the report")
