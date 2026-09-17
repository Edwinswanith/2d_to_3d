"""What the drawing requires, kept separate from what the AI proposes to build.

`accepted_source_contract.json` (a `SourceContract`) is the drawing's own accepted meaning: a
feature, a quantity, a value with its permitted limits, the axial span or position it applies
to, and the datum it is measured from — not a bare number interchangeable with any other length
of the same magnitude. The repair loop may change `candidate_spec.json`; it must never touch
this file. Initially these are hand-authored and reviewed, exactly like the plan's own worked
example (2H-66033's flushing port at 0.980 in from datum -A-, not from face A); an AI-generated
contract is a future input to the same schema; it is not exempt from review just because it is
here first.
"""

from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from drawing2step.datums import DatumRegistry
from drawing2step.models import Contract, Nonnegative, Number

RequirementKind = Literal["axial_band", "hole_pattern", "passage"]
Interpretation = Literal["reviewed", "unresolved", "approved_assumption"]


class SourceRequirement(Contract):
    """One accepted requirement, shaped to match a `revb_geometry.check_structure` expectation.

    `referenced_to` names a datum in the contract's own `DatumRegistry`; every local axial
    value below (`applies_between`, `z_span`, the z of `start`/`end`) is measured from that
    datum, not from face A, unless `referenced_to` names face A itself.
    """

    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    feature: str = Field(min_length=1)
    quantity: str = Field(min_length=1)
    kind: RequirementKind
    referenced_to: str = Field(min_length=1)
    unit: Literal["mm", "in"]
    evidence: str = Field(min_length=1)
    interpretation: Interpretation = "unresolved"
    limit_minus: Nonnegative | None = None
    limit_plus: Nonnegative | None = None

    # axial_band: an outer diameter, a bore, or both, held across a local axial span.
    applies_between: tuple[Number, Number] | None = None
    diameter: Number | None = None
    bore: Number | None = None

    # hole_pattern: an axial pattern of `count` holes on `pcd`, first hole at `angle`.
    count: int | None = Field(default=None, ge=1, le=200)
    pcd: Number | None = None
    angle: Number | None = None
    z_span: tuple[Number, Number] | None = None

    # passage: a radial port/drill from `start` to `end`, both (x, y, z) in mm, z local.
    start: tuple[Number, Number, Number] | None = None
    end: tuple[Number, Number, Number] | None = None

    @model_validator(mode="after")
    def shape_matches_kind(self) -> Self:
        if self.kind == "axial_band":
            if self.applies_between is None or (self.diameter is None and self.bore is None):
                raise ValueError("axial_band requires applies_between and diameter and/or bore")
            if self.applies_between[1] <= self.applies_between[0]:
                raise ValueError("applies_between must be an ordered (start, end) span")
        elif self.kind == "hole_pattern":
            missing = [
                name
                for name, value in (
                    ("diameter", self.diameter),
                    ("count", self.count),
                    ("pcd", self.pcd),
                    ("angle", self.angle),
                    ("z_span", self.z_span),
                )
                if value is None
            ]
            if missing:
                raise ValueError(f"hole_pattern requires {', '.join(missing)}")
        elif self.kind == "passage":
            missing = [
                name
                for name, value in (
                    ("diameter", self.diameter),
                    ("start", self.start),
                    ("end", self.end),
                )
                if value is None
            ]
            if missing:
                raise ValueError(f"passage requires {', '.join(missing)}")
        return self


class SourceContract(Contract):
    schema_version: Literal["source-contract-v1"] = "source-contract-v1"
    drawing_number: str = Field(min_length=1)
    datums: DatumRegistry = DatumRegistry()
    requirements: tuple[SourceRequirement, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_requirement_ids(self) -> Self:
        ids = [r.id for r in self.requirements]
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate source requirement id")
        return self

    @model_validator(mode="after")
    def every_datum_resolves(self) -> Self:
        # A contract that cites a datum it never registered is not reviewed, it is broken:
        # this must fail to load, not degrade into a per-requirement UNKNOWN at verify time.
        for requirement in self.requirements:
            try:
                self.datums.resolve(requirement.referenced_to)
            except ValueError as error:
                raise ValueError(f"requirement {requirement.id}: {error}") from error
        return self


def load_source_contract(path: Path) -> SourceContract:
    return SourceContract.model_validate_json(path.read_text())


def default_contract_path(directory: Path) -> Path:
    return directory / "source-contract.json"


__all__ = [
    "Interpretation",
    "RequirementKind",
    "SourceContract",
    "SourceRequirement",
    "default_contract_path",
    "load_source_contract",
]
