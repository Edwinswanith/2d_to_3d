"""Versioned contracts for local feasibility experiments, not production acceptance."""

from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Number = Annotated[Decimal, Field(allow_inf_nan=False)]
Nonnegative = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
Unit = Literal["mm", "in", "degree", "count"]
Source = Literal["native", "a", "b", "escalation"]
Kind = Literal["diameter", "linear", "radius", "angle", "count", "note"]
DecisionStatus = Literal[
    "AGREED",
    "NATIVE_CONFIRMED",
    "A_ONLY",
    "B_ONLY",
    "SINGLE_SOURCE",
    "CONFLICT",
    "UNREAD",
    "RESOLVED_BY_ESCALATION",
]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class Box(Contract):
    """Page coordinates in points; pages are one-based."""

    page: int = Field(ge=1)
    x0: float = Field(ge=0, allow_inf_nan=False)
    y0: float = Field(ge=0, allow_inf_nan=False)
    x1: float = Field(gt=0, allow_inf_nan=False)
    y1: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.x1 <= self.x0 or self.y1 <= self.y0:
            raise ValueError("Box must have positive width and height")
        return self

    def overlap(self, other: "Box") -> float:
        if self.page != other.page:
            return 0
        intersection = max(0, min(self.x1, other.x1) - max(self.x0, other.x0)) * max(
            0, min(self.y1, other.y1) - max(self.y0, other.y0)
        )
        union = (self.x1 - self.x0) * (self.y1 - self.y0)
        union += (other.x1 - other.x0) * (other.y1 - other.y0) - intersection
        return intersection / union


class Value(Contract):
    value: Number | None = None
    unit: Unit | None = None
    kind: Kind
    tolerance_minus: Nonnegative | None = None
    tolerance_plus: Nonnegative | None = None

    @model_validator(mode="after")
    def compatible_unit(self) -> Self:
        expected = {
            "diameter": {"mm", "in"},
            "radius": {"mm", "in"},
            "linear": {"mm", "in"},
            "angle": {"degree"},
            "count": {"count"},
            "note": set(),
        }
        if self.unit is not None and self.unit not in expected[self.kind]:
            raise ValueError(f"Unit {self.unit} is incompatible with {self.kind}")
        if self.kind == "count" and self.value is not None:
            if self.value < 1 or self.value != self.value.to_integral_value():
                raise ValueError("Counts must be positive integers")
        return self

    def signature(self) -> tuple[object, ...]:
        """Exact source-value equivalence, not manufacturing tolerance matching."""
        scale = Decimal("25.4") if self.unit == "in" else Decimal(1)
        return (
            self.kind,
            "mm" if self.unit == "in" else self.unit,
            self.value * scale if self.value is not None else None,
            self.tolerance_minus * scale if self.tolerance_minus is not None else None,
            self.tolerance_plus * scale if self.tolerance_plus is not None else None,
        )


class Observation(Value):
    id: str = Field(min_length=1)
    source: Source
    source_version: str = Field(min_length=1)
    raw_text: str = Field(min_length=1)
    box: Box
    geometry_driving: bool = True


class Decision(Contract):
    status: DecisionStatus
    accepted: bool
    selected_reading: str | None = None
    reason: str


class Requirement(Value):
    id: str = Field(min_length=1)
    raw_text: str = Field(min_length=1)
    box: Box
    feature: str | None = None
    geometry_driving: bool = True
    text_status: Literal["ACCEPTED", "REVIEW"] = "REVIEW"
    interpretation_status: Literal["SUPPORTED", "UNKNOWN"] = "UNKNOWN"
    association_status: Literal["VALIDATED", "UNKNOWN"] = "UNKNOWN"
    evidence: tuple[Observation, ...] = ()

    def signature(self) -> tuple[object, ...]:
        if self.kind == "note":
            return ("note", " ".join(self.raw_text.split()))
        return super().signature()


class EvalCase(Contract):
    schema_version: Literal["eval-v1"] = "eval-v1"
    id: str = Field(min_length=1)
    group: str = Field(min_length=1)
    quality: Literal["clean", "scan"]
    split: Literal["development", "held_out"] = "development"
    synthetic: bool
    truth: tuple[Requirement, ...]
    predictions: tuple[Requirement, ...]
    latency_seconds: Nonnegative = Decimal(0)
    cost_usd: Nonnegative = Decimal(0)

    @model_validator(mode="after")
    def unique_requirements(self) -> Self:
        for collection in (self.truth, self.predictions):
            if len({item.id for item in collection}) != len(collection):
                raise ValueError("Duplicate requirement IDs within a case")
        for target in self.truth:
            if target.kind != "note" and (target.value is None or target.unit is None):
                raise ValueError("Numeric ground truth requires a value and explicit unit")
        return self
