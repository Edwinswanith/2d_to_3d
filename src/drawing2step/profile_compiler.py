"""Role-safe profile compilation: named axial spans, not an anonymous radius/z polygon.

Proposing a low-level ``(radius, z)`` polygon directly conflates every profile vertex into one
undifferentiated list: an external OD span, a bore ID span and the shoulder between them look
identical once compiled, so nothing downstream can ask "is THIS the groove" independent of
whatever built the polygon in the first place. Here, a profile is proposed as named, role-tagged
axial spans instead (external/bore straight runs, shoulders, grooves, lead-ins); this module
deterministically converts each span's diameter to a radius, walks every span into the ONE closed
boundary ``build_model`` already consumes (outer surface forward, then bore surface backward —
``DraftSpec.profile``'s existing convention), and returns a per-span expectation list that
``check_structure``'s existing ``_axial_band`` handler can verify independently of whatever
proposed the spans, plus the compiled overall length read off the external chain's own
identified front/back endpoints rather than guessed from "the largest printed dimension".

Axial positions are resolved through a real ``DatumRegistry`` (``AxialPosition``, below), not
through ``Numeric``'s ``datum`` provenance — that one still resolves any name to zero by design
everywhere else in the candidate path (see ``datums.py`` and the plan's F4 note), and nothing
here changes that shared evaluator. A span's z is instead "this many mm from a named, registered
datum", reproducing the plan's own worked example directly (face_a = 0.000 in, datum A =
0.125 in, a span 0.980 in from A resolves to an absolute 1.105 in, not 0.980 in).

Not yet wired to AI extraction: ``SPEC_PROMPT``/``DraftSpec`` still take a raw ``ProfilePoint``
polygon directly from the provider. This module is proven first against hand-authored spans,
per the plan's own "prove the representation first, without any AI call."
"""

from typing import Any, Literal, Self

from pydantic import Field, model_validator

from drawing2step.datums import DatumRegistry
from drawing2step.models import Contract
from drawing2step.revb import Assumption, Numeric, Requirement
from drawing2step.revb_model import numeric_value

SpanSurface = Literal["external", "bore"]
SpanRole = Literal["straight", "shoulder", "groove", "lead_in"]


class AxialPosition(Contract):
    """mm = the named datum's registered position, plus an optional local offset.

    ``offset`` is a normal citation/expression, resolved exactly like any other ``Numeric``;
    only the reference point itself comes from ``DatumRegistry.resolve`` rather than from
    ``Numeric``'s own (zero-defaulting) ``datum`` provenance.
    """

    datum: str = "face_a"
    offset: Numeric | None = None


class ProfileSpan(Contract):
    """One identified axial span of one surface (external or bore).

    ``straight`` and ``groove`` hold one constant diameter over a positive axial length (a
    groove is a straight span too — the label is retained identity, not a different shape).
    ``shoulder`` is an instantaneous step: zero axial length, a diameter change. ``lead_in`` is
    the opposite: a positive axial length whose diameter changes across it (a taper/chamfer).
    """

    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    surface: SpanSurface
    role: SpanRole
    z_start: AxialPosition
    z_end: AxialPosition
    diameter_start: Numeric
    diameter_end: Numeric | None = None

    @model_validator(mode="after")
    def has_a_diameter_end(self) -> Self:
        if self.role != "straight" and self.diameter_end is None:
            raise ValueError(f"{self.id}: {self.role} requires an explicit diameter_end")
        return self


class _Resolved:
    __slots__ = ("span", "z0", "z1", "d0", "d1")

    def __init__(self, span: ProfileSpan, z0: float, z1: float, d0: float, d1: float) -> None:
        self.span, self.z0, self.z1, self.d0, self.d1 = span, z0, z1, d0, d1


def _axial_mm(
    position: AxialPosition,
    ledger: dict[str, Requirement],
    assumptions: dict[str, Assumption],
    datums: DatumRegistry,
) -> float:
    base = float(datums.resolve(position.datum))
    if position.offset is None:
        return base
    return base + numeric_value(position.offset, ledger, assumptions, "length")


def _resolve(
    span: ProfileSpan,
    ledger: dict[str, Requirement],
    assumptions: dict[str, Assumption],
    datums: DatumRegistry,
) -> _Resolved:
    z0 = _axial_mm(span.z_start, ledger, assumptions, datums)
    z1 = _axial_mm(span.z_end, ledger, assumptions, datums)
    d0 = numeric_value(span.diameter_start, ledger, assumptions, "length")
    d1 = (
        numeric_value(span.diameter_end, ledger, assumptions, "length")
        if span.diameter_end is not None
        else d0
    )
    if d0 <= 0 or d1 <= 0:
        raise ValueError(f"{span.id}: diameter must be positive")
    if span.role == "shoulder":
        if abs(z1 - z0) > 1e-6:
            raise ValueError(f"{span.id}: a shoulder is an instantaneous step; z_start==z_end")
        if abs(d1 - d0) < 1e-6:
            raise ValueError(f"{span.id}: a shoulder must change diameter")
    elif span.role == "lead_in":
        if z1 <= z0:
            raise ValueError(f"{span.id}: a lead-in spans a positive axial length")
        if abs(d1 - d0) < 1e-6:
            raise ValueError(f"{span.id}: a lead-in must change diameter (else it is 'straight')")
    else:
        if z1 <= z0:
            raise ValueError(f"{span.id}: a {span.role} span spans a positive axial length")
        if abs(d1 - d0) > 1e-6:
            raise ValueError(
                f"{span.id}: a {span.role} span holds one diameter; use lead_in for a taper"
            )
    return _Resolved(span, z0, z1, d0, d1)


def _chain(resolved: list[_Resolved], surface: SpanSurface) -> list[_Resolved]:
    chain = sorted((r for r in resolved if r.span.surface == surface), key=lambda r: r.z0)
    if not chain:
        raise ValueError(f"No {surface} spans supplied")
    for previous, current in zip(chain, chain[1:], strict=False):
        if abs(current.z0 - previous.z1) > 1e-6 or abs(current.d0 - previous.d1) > 1e-6:
            raise ValueError(
                f"{current.span.id}: does not continue from {previous.span.id}; expected to "
                f"start at z={previous.z1:g} mm, diameter {previous.d1:g} mm"
            )
    return chain


def _points(chain: list[_Resolved]) -> list[tuple[float, float]]:
    points = [(chain[0].d0 / 2, chain[0].z0)]
    points.extend((r.d1 / 2, r.z1) for r in chain)
    return points


def compile_profile(
    spans: list[ProfileSpan],
    ledger: dict[str, Requirement],
    assumptions: dict[str, Assumption],
    datums: DatumRegistry | None = None,
) -> tuple[list[tuple[float, float]], list[dict[str, Any]], float]:
    """Compile named spans into a boundary polygon, per-span checks, and the overall length.

    Returns ``(points, body_checks, overall_length_mm)``: ``points`` is a resolved (radius, z)
    polygon ready for ``build_model``'s ``points_override`` parameter (an already-compiled
    number has no citation to re-verify through ``Numeric``'s restricted-expression evaluator,
    so it bypasses ``DraftSpec.profile`` rather than being re-encoded into it); ``body_checks``
    is fed to ``build_model``'s own ``body_checks`` parameter so a wrong host body is caught
    before any feature is cut; ``overall_length_mm`` is the external chain's own front-to-back
    extent — an identified endpoint difference, never "the largest printed linear dimension".
    ``datums`` defaults to a registry with only ``face_a`` (0 mm, by definition); any other
    name a span cites must be registered there first or resolution raises.
    """
    if not spans:
        raise ValueError("No spans supplied")
    if len({s.id for s in spans}) != len(spans):
        raise ValueError("Duplicate span identifiers")
    registry = datums if datums is not None else DatumRegistry()
    resolved = [_resolve(s, ledger, assumptions, registry) for s in spans]
    external = _chain(resolved, "external")
    bore = _chain(resolved, "bore")
    if abs(external[0].z0 - bore[0].z0) > 1e-6:
        raise ValueError("External and bore spans must start at the same axial face")
    if abs(external[-1].z1 - bore[-1].z1) > 1e-6:
        raise ValueError("External and bore spans must end at the same axial face")
    points = _points(external) + list(reversed(_points(bore)))
    body_checks = [
        {
            "kind": "axial_band",
            "id": r.span.id,
            "z0": min(r.z0, r.z1),
            "z1": max(r.z0, r.z1),
            **({"od": r.d0} if r.span.surface == "external" else {"idiameter": r.d0}),
        }
        for r in external + bore
        if r.span.role in {"straight", "groove"}
    ]
    overall_length_mm = external[-1].z1 - external[0].z0
    return points, body_checks, overall_length_mm
