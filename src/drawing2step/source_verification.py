"""Independent verification of a delivered STEP against the accepted source contract.

Expected geometry comes only from `SourceContract` and its own `DatumRegistry` — never from the
candidate spec that generated the model, and never from whatever the builder assumed a datum's
position to be. `revb_geometry.check_structure` already re-imports the STEP itself, so nothing
here re-trusts an in-memory build; this module's own job is to turn an accepted requirement into
that function's expectation shape, and to size the tolerance from the drawing's own permitted
limits rather than a blanket percentage of the nominal.
"""

from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from drawing2step.revb_geometry import check_structure
from drawing2step.source_contract import SourceContract, SourceRequirement

Scope = frozenset[str] | Literal["all"]

_SCALE = {"mm": Decimal(1), "in": Decimal("25.4")}


def _mm(value: Decimal, unit: str) -> float:
    return float(value * _SCALE[unit])


def _tolerance_mm(requirement: SourceRequirement) -> float:
    """Half the drawing's own permitted band, never a percentage of the nominal.

    Numerical measurement precision (how exactly OCCT can locate a face) is not manufacturing
    tolerance (how far the drawing lets the part vary); only the latter may widen this number,
    and only when the drawing states it explicitly.
    """
    limits = [limit for limit in (requirement.limit_minus, requirement.limit_plus) if limit]
    if not limits:
        return 0.05
    return _mm(max(limits), requirement.unit)


def _expectation(requirement: SourceRequirement, contract: SourceContract) -> dict[str, Any]:
    unit = requirement.unit
    # The datum registry stores positions in mm; a requirement's own axial values are in its
    # declared unit. Convert each to mm independently before adding — never add a datum's mm
    # offset to an unconverted native-unit value and scale the sum, which silently rescales
    # the offset too.
    origin_mm = float(contract.datums.resolve(requirement.referenced_to))
    if requirement.kind == "axial_band":
        assert requirement.applies_between is not None
        z0, z1 = requirement.applies_between
        expectation: dict[str, Any] = {
            "id": requirement.id,
            "kind": "axial_band",
            "z0": origin_mm + _mm(z0, unit),
            "z1": origin_mm + _mm(z1, unit),
        }
        if requirement.diameter is not None:
            expectation["od"] = _mm(requirement.diameter, unit)
        if requirement.bore is not None:
            expectation["idiameter"] = _mm(requirement.bore, unit)
        return expectation
    if requirement.kind == "hole_pattern":
        assert requirement.z_span is not None
        z0, z1 = requirement.z_span
        return {
            "id": requirement.id,
            "kind": "hole_pattern",
            "count": requirement.count,
            "diameter": _mm(requirement.diameter, unit),  # type: ignore[arg-type]
            "pcd": _mm(requirement.pcd, unit),  # type: ignore[arg-type]
            "angle": float(requirement.angle),  # type: ignore[arg-type]
            "z0": origin_mm + _mm(z0, unit),
            "z1": origin_mm + _mm(z1, unit),
        }
    assert requirement.start is not None and requirement.end is not None
    sx, sy, sz = requirement.start
    ex, ey, ez = requirement.end
    return {
        "id": requirement.id,
        "kind": "passage",
        "diameter": _mm(requirement.diameter, unit),  # type: ignore[arg-type]
        "start": [_mm(sx, unit), _mm(sy, unit), origin_mm + _mm(sz, unit)],
        "end": [_mm(ex, unit), _mm(ey, unit), origin_mm + _mm(ez, unit)],
    }


def verify_contract(
    step_path: Path, contract: SourceContract, scope: Scope = "all"
) -> list[dict[str, Any]]:
    """One result per in-scope requirement: PASS/FAIL/UNKNOWN, expected and measured geometry,
    and the requirement's own evidence — never the candidate's.
    """
    in_scope = [r for r in contract.requirements if scope == "all" or r.id in scope]
    in_scope_ids = {r.id for r in in_scope}
    out_of_scope = [r for r in contract.requirements if r.id not in in_scope_ids]
    results = []
    for requirement in in_scope:
        # SourceContract's own validators already guarantee every referenced_to resolves and
        # every kind-required field is present, so building the expectation cannot fail here.
        expectation = _expectation(requirement, contract)
        expectation["tolerance"] = _tolerance_mm(requirement)
        [check] = check_structure(step_path, [expectation])
        results.append(
            {
                "layer": "SRC",
                "subject": requirement.id,
                "status": check["status"],
                "detail": (f"{requirement.feature} — {requirement.quantity}: {check['detail']}"),
                "requirement": requirement.model_dump(mode="json"),
                "expected": {k: v for k, v in expectation.items() if k != "id"},
                "evidence": requirement.evidence,
            }
        )
    results.extend(
        {
            "layer": "SRC",
            "subject": requirement.id,
            "status": "UNKNOWN",
            "detail": "Requirement is outside the declared modelling scope for this build",
            "requirement": requirement.model_dump(mode="json"),
            "evidence": requirement.evidence,
        }
        for requirement in out_of_scope
    )
    return results
