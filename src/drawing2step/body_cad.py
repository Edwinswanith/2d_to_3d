"""Citation-driven axial body evaluation builder. No manufacturing release path."""

import ast
import hashlib
import math
import re
from decimal import Decimal, DecimalException
from pathlib import Path
from typing import Any, Literal, Self

import cadquery as cq
from pydantic import Field, model_validator

from drawing2step.models import Contract, Number
from drawing2step.storage import canonical_json, write_once


class NumericInput(Contract):
    ledger: str | None = None
    expr: str | None = None

    @model_validator(mode="after")
    def exclusive(self) -> Self:
        if bool(self.ledger) == bool(self.expr):
            raise ValueError("Number must contain exactly one ledger citation or expression")
        return self


class Dimension(Contract):
    value: Number
    kind: Literal["diameter", "linear", "radius"]
    unit: Literal["mm", "cm", "in"]


class Station(Contract):
    z: NumericInput
    od: NumericInput
    id: NumericInput


class BodySpec(Contract):
    schema_version: Literal["body-evaluation-v1"] = "body-evaluation-v1"
    synthetic: bool
    engineer: str | None = None
    unit_decision_reason: str | None = None
    ledger: dict[str, Dimension]
    stations: list[Station] = Field(min_length=2, max_length=100)
    unsupported_features: list[str] = []


def evaluate_number(value: dict[str, Any], ledger: dict[str, Any]) -> Decimal:
    """Evaluate bounded dimensionally consistent arithmetic; no calls, attributes or code."""
    inp = NumericInput.model_validate(value)
    aliases = {f"citation_{i}": key for i, key in enumerate(ledger)}

    def cited(key: str) -> tuple[Decimal, int]:
        if key not in ledger:
            raise ValueError(f"Unknown ledger citation {key}")
        dimension = Dimension.model_validate(ledger[key])
        if abs(dimension.value) > 1_000_000:
            raise ValueError("Ledger dimension exceeds geometry bound")
        scale = {"mm": Decimal(1), "cm": Decimal(10), "in": Decimal("25.4")}[dimension.unit]
        result = dimension.value * scale
        return result, 1

    def visit(node: ast.AST) -> tuple[Decimal, int]:
        if isinstance(node, ast.Name):
            return cited(aliases.get(node.id, node.id))
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            literal = ast.get_source_segment(expression, node) or str(node.value)
            number = Decimal(literal.replace("_", ""))
            if not number.is_finite() or abs(number) > 1_000_000:
                raise ValueError("Scalar constant exceeds arithmetic bound")
            if number and abs(number) < Decimal("1e-12"):
                raise ValueError("Scalar constant is too small")
            return number, 0
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            number, unit = visit(node.operand)
            return (-number if isinstance(node.op, ast.USub) else number), unit
        if isinstance(node, ast.BinOp):
            a, au = visit(node.left)
            b, bu = visit(node.right)
            if isinstance(node.op, (ast.Add, ast.Sub)) and au == bu:
                return (a + b if isinstance(node.op, ast.Add) else a - b), au
            if isinstance(node.op, ast.Mult) and au + bu <= 1:
                return a * b, au + bu
            if isinstance(node.op, ast.Div) and bu == 0 and b != 0:
                return a / b, au
        raise ValueError("Unsupported or dimensionally inconsistent expression")

    expression = inp.expr or ""
    if inp.ledger:
        result, _ = cited(inp.ledger)
        if abs(result) > 1_000_000:
            raise ValueError("Geometry input exceeds bound")
        return result
    if len(expression) > 500:
        raise ValueError("Expression too long")
    # Ledger IDs such as R-0101 are atomic citations, not subtraction expressions.
    for alias, key in sorted(aliases.items(), key=lambda item: len(item[1]), reverse=True):
        expression = re.sub(r"(?<![\w-])" + re.escape(key) + r"(?![\w-])", alias, expression)
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        raise ValueError("Invalid derivation expression") from None
    if len(list(ast.walk(tree))) > 100:
        raise ValueError("Expression too complex")
    try:
        result, unit = visit(tree.body)
    except DecimalException:
        raise ValueError("Invalid or overflowing dimension arithmetic") from None
    if unit != 1 or not result.is_finite() or abs(result) > 1_000_000:
        raise ValueError("Geometry input must be a bounded cited length")
    return result


def _measure(shape: Any) -> dict[str, Any]:
    box = shape.BoundingBox()
    cylinders = []
    for face in shape.Faces():
        if face.geomType() == "CYLINDER":
            from OCP.BRepAdaptor import BRepAdaptor_Surface  # type: ignore[import-untyped]

            surface = BRepAdaptor_Surface(face.wrapped).Cylinder()
            cylinders.append(
                {"diameter": surface.Radius() * 2, "axis": list(surface.Axis().Direction().Coord())}
            )
    return {
        "valid": shape.isValid(),
        "solids": len(shape.Solids()),
        "volume": shape.Volume(),
        "bbox": [box.xlen, box.ylen, box.zlen],
        "cylinders": cylinders,
    }


class ProfileError(ValueError):
    """Deterministic profile inconsistency requiring a new association decision."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def evaluate_profile(spec: BodySpec) -> list[tuple[float, float, float]]:
    ledger = {k: v.model_dump(mode="json") for k, v in spec.ledger.items()}
    rows = [
        (
            float(evaluate_number(s.z.model_dump(), ledger)),
            float(evaluate_number(s.od.model_dump(), ledger)),
            float(evaluate_number(s.id.model_dump(), ledger)),
        )
        for s in spec.stations
    ]
    for i, (z, od, bore) in enumerate(rows):
        if z < 0 or od <= bore or bore < 0:
            raise ProfileError(
                "PROFILE_NESTING",
                f"Profile station {i + 1} has negative position or non-nested diameters. "
                "Confirm its cited dimensions.",
            )
    for i, (before, after) in enumerate(zip(rows[:-1], rows[1:], strict=True)):
        if after[0] < before[0]:
            raise ProfileError(
                "PROFILE_AXIAL_ORDER",
                f"Profile station {i + 2} goes backward from {before[0]:.6g} mm "
                f"to {after[0]:.6g} mm. Confirm whether the cited axial dimensions "
                "are offsets or absolute positions.",
            )
        if after == before:
            raise ProfileError(
                "PROFILE_DUPLICATE",
                f"Profile stations {i + 1} and {i + 2} are identical. "
                "Confirm the proposed profile sequence.",
            )
    if rows[-1][0] <= rows[0][0]:
        raise ProfileError(
            "PROFILE_ZERO_LENGTH",
            "The proposed profile has zero axial length. Confirm its thickness dimension.",
        )
    return rows


def build_verified(spec: BodySpec, output: Path, reference: Path | None = None) -> dict[str, Any]:
    """Export/reimport a body; report independent dimensions with explicit UNKNOWNs."""
    if not spec.synthetic and (not spec.engineer or not spec.unit_decision_reason):
        raise ValueError("Real drawing geometry requires an engineer and explicit unit decision")
    ledger = {k: v.model_dump(mode="json") for k, v in spec.ledger.items()}
    rows = evaluate_profile(spec)
    points = [(od / 2, z) for z, od, bore in rows] + [
        (bore / 2, z) for z, od, bore in reversed(rows)
    ]
    points = [point for i, point in enumerate(points) if i == 0 or point != points[i - 1]]
    model = cq.Workplane("XZ").polyline(points).close().revolve(360, (0, 0), (0, 1))
    before = _measure(model.val())
    if not before["valid"] or before["solids"] != 1:
        raise ValueError("Builder did not produce one valid solid")
    expected_volume = sum(
        math.pi
        * (b[0] - a[0])
        / 12
        * (a[1] ** 2 + a[1] * b[1] + b[1] ** 2 - a[2] ** 2 - a[2] * b[2] - b[2] ** 2)
        for a, b in zip(rows[:-1], rows[1:], strict=True)
    )
    if not math.isclose(before["volume"], expected_volume, rel_tol=1e-7, abs_tol=1e-5):
        raise ValueError("V1: solid volume disagrees with evaluated profile")
    output.mkdir(parents=True, exist_ok=True)
    step = output / "evaluation-body.step"
    if step.exists():
        raise ValueError("Use a new output directory; STEP evidence must not be overwritten")
    cq.exporters.export(model, str(step))
    fresh = cq.importers.importStep(str(step)).val()
    if not isinstance(fresh, cq.Shape):
        raise ValueError("Reimport returned no CAD shape")
    after = _measure(fresh)
    volume_ok = math.isclose(before["volume"], after["volume"], rel_tol=1e-7, abs_tol=1e-5)
    box_ok = all(
        math.isclose(a, b, rel_tol=1e-7, abs_tol=1e-5)
        for a, b in zip(before["bbox"], after["bbox"], strict=True)
    )
    drawing_checks = []
    for key, dimension in ledger.items():
        target = float(evaluate_number({"ledger": key}, ledger))
        if dimension["kind"] == "diameter":
            matched = [c for c in after["cylinders"] if abs(c["diameter"] - target) <= 1e-5]
            status = "VALUE_MATCH_ONLY" if matched else "FAIL"
        else:
            status = "UNKNOWN"
        drawing_checks.append(
            {"requirement": key, "status": status, "association_and_location": "UNKNOWN"}
        )
    reference_result: Any = "UNKNOWN"
    if reference:
        reference_shape = cq.importers.importStep(str(reference)).val()
        if not isinstance(reference_shape, cq.Shape):
            raise ValueError("Reference contains no CAD shape")
        ref_measure = _measure(reference_shape)
        # Boolean symmetric difference distinguishes equal-volume, differently placed solids.
        deviation = fresh.cut(reference_shape).Volume() + reference_shape.cut(fresh).Volume()
        reference_result = {
            "status": "PASS"
            if ref_measure["valid"] and ref_measure["solids"] == 1 and deviation <= 1e-5
            else "FAIL",
            "symmetric_difference_volume": deviation,
        }
    report = {
        "schema_version": "body-verification-v1",
        "synthetic": spec.synthetic,
        "V1": "UNKNOWN",
        "build_validity": "PASS",
        "analytical_profile_volume": "PASS",
        "V1_limitation": "Individual station and feature-count conformance not yet measured",
        "V2": "PASS"
        if volume_ok and box_ok and after["valid"] and after["solids"] == 1
        else "FAIL",
        "V3": drawing_checks,
        "fresh_measurements": after,
        "reference_step": reference_result,
        "unsupported_features": spec.unsupported_features,
        "release": "BLOCKED",
        "artifact_kind": "PARTIAL_BODY_EVALUATION_ONLY",
        "step_units": "mm",
        "step_schema": "CadQuery exporter default; CAM approval pending",
        "step_sha256": hashlib.sha256(step.read_bytes()).hexdigest(),
        "cadquery_version": cq.__version__,
    }
    write_once(output / "spec.json", canonical_json(spec.model_dump(mode="json")))
    write_once(output / "verification.json", canonical_json(report))
    return report
