"""Named datums resolved to real axial positions, never silently treated as zero.

Revision B's candidate-construction path (`revb.evaluate_numeric`) resolves a `Numeric` whose
provenance is `datum` to a flat `Decimal(0)` regardless of the name: `face_a`, `-A-`, a typo,
all land at the origin. That is only correct for the one datum the coordinate policy actually
defines as z=0. A drawing's own datum flag (2H-66033's "-A-", for instance) can sit somewhere
else entirely, and a dimension measured "from A" must add that offset rather than restate the
printed distance as if it were already an absolute coordinate.

This registry is independent of any candidate spec: it is built once, by hand or from reviewed
evidence, and used by the source-contract verifier to resolve a requirement's own boundaries.
It never invents a position for a name it was not given, and it never lets an unknown name pass
silently — the whole point is that a wrong or missing datum must be loud, not free.
"""

from decimal import Decimal
from typing import Self

from pydantic import Field, model_validator

from drawing2step.models import Contract, Number


class Datum(Contract):
    """One named reference position, in mm from face A (the coordinate origin)."""

    name: str = Field(min_length=1)
    position_mm: Number
    source: str = Field(min_length=1)


class DatumRegistry(Contract):
    """`face_a` is always resolvable at 0 mm — that is the coordinate policy's own definition,
    not a default applied to whatever name shows up. Every other name must be registered here,
    with its own evidence, before anything may be measured relative to it.
    """

    datums: tuple[Datum, ...] = ()

    @model_validator(mode="after")
    def valid(self) -> Self:
        names = [d.name for d in self.datums]
        if len(set(names)) != len(names):
            raise ValueError("Duplicate datum name")
        if any(d.name == "face_a" and d.position_mm != 0 for d in self.datums):
            raise ValueError("face_a is the coordinate origin: it must be registered at 0 mm")
        return self

    def resolve(self, name: str) -> Decimal:
        """Absolute axial position of a named datum, in mm from face A."""
        for datum in self.datums:
            if datum.name == name:
                return datum.position_mm
        if name == "face_a":
            return Decimal(0)
        raise ValueError(
            f"Unknown datum {name!r}; register it in the datum registry with evidence "
            "before a requirement may be measured relative to it"
        )

    def offset(self, referenced_to: str, value_mm: Decimal) -> Decimal:
        """Absolute axial position (mm from face A) of `value_mm` measured from `referenced_to`."""
        return self.resolve(referenced_to) + value_mm
