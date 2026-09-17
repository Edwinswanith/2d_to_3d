"""Revision B contracts and fail-closed decisions, independent of model providers."""

import ast
from decimal import Decimal
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from drawing2step.models import Contract, Number

Status = Literal["PASS", "FAIL", "UNKNOWN"]
FeatureType = Literal[
    "body",
    "bore_step",
    "groove",
    "chamfer",
    "hole_pattern",
    "tapped_hole",
    "port",
    "bore_slot",
    "od_slot",
    "marking",
    "other",
]


class Requirement(Contract):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    raw_text: str = Field(min_length=1)
    kind: Literal["diameter", "linear", "radius", "angle", "count", "thread", "note"]
    value: Number | None = None
    unit: Literal["mm", "cm", "in", "degree", "count"] | None = None
    tolerance_printed: str = ""
    interpretation_status: Literal["UNKNOWN", "CONFIRMED"] = "UNKNOWN"
    tolerance_minus: Number | None = Field(default=None, ge=0)
    tolerance_plus: Number | None = Field(default=None, ge=0)
    box: tuple[float, float, float, float] | None = None
    sources: tuple[str, ...] = ()
    geometry_driving: bool = True
    text_status: str = "UNREAD"
    feature: str | None = None
    reference_only: bool = False
    disposition_reason: str | None = None

    @model_validator(mode="after")
    def valid_disposition(self) -> Self:
        if self.reference_only and not self.disposition_reason:
            raise ValueError("Reference-only disposition requires evidence and reason")
        allowed = {
            "diameter": {"mm", "cm", "in"},
            "linear": {"mm", "cm", "in"},
            "radius": {"mm", "cm", "in"},
            "angle": {"degree"},
            "count": {"count"},
            "thread": set(),
            "note": set(),
        }
        if self.unit is not None and self.unit not in allowed[self.kind]:
            raise ValueError("Unit incompatible with requirement type")
        if self.kind == "count" and self.value is not None:
            if self.value < 1 or self.value != self.value.to_integral_value():
                raise ValueError("Feature count must be a positive integer")
        return self


class InventoryFeature(Contract):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    type: FeatureType
    view: str = Field(min_length=1)
    count: int | None = Field(default=None, ge=1, le=200)
    box: tuple[float, float, float, float]
    description: str = Field(min_length=1)
    same_physical_group: str | None = None

    @model_validator(mode="after")
    def valid_box(self) -> Self:
        y0, x0, y1, x1 = self.box
        if not all(0 <= n <= 1000 for n in self.box) or y0 >= y1 or x0 >= x1:
            raise ValueError("Box must be ordered normalized [top,left,bottom,right] in 0..1000")
        return self


class SectionInventory(Contract):
    view: str
    reference_face: str
    outer_sequence: list[str]
    bore_sequence: list[str]
    uncertainties: list[str] = Field(default_factory=list)


class Inventory(Contract):
    schema_version: Literal["inventory-revb-v1"] = "inventory-revb-v1"
    features: list[InventoryFeature] = Field(max_length=200)
    sections: list[SectionInventory] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_ids(self) -> Self:
        if len({f.id for f in self.features}) != len(self.features):
            raise ValueError("Inventory feature identifiers must be unique")
        return self


class Association(Contract):
    id: str
    requirement: str
    feature: str
    attribute: Literal["diameter", "depth", "axial_position", "angle", "count", "note"]
    reference_face: str | None
    status: Literal["PROPOSED", "VALIDATED", "AMBIGUOUS", "CONFIRMED"] = "PROPOSED"
    evidence: list[str] = Field(min_length=1)
    reviewer: str | None = None

    @model_validator(mode="after")
    def confirmed_by_engineer(self) -> Self:
        if self.status == "CONFIRMED" and not self.reviewer:
            raise ValueError("Confirmed associations require a named reviewer")
        return self


class Assumption(Contract):
    id: str
    item: str
    value: Number
    unit: Literal["mm", "cm", "in", "degree", "count"]
    type: Literal["ASSUMED", "IMPLIED_CENTRELINE", "MIRRORED", "SCALED_FROM_VIEW", "INTERPRETATION"]
    reason: str = Field(min_length=1)
    source: str = Field(min_length=1)
    # Sign-offs live in separate version-bound records. Models cannot fill these fields.


class Numeric(Contract):
    ledger: str | None = None
    expr: str | None = Field(default=None, max_length=500)
    datum: str | None = None
    centreline: int | None = None
    assumption: str | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def exactly_one(self) -> Self:
        fields = (self.ledger, self.expr, self.datum, self.centreline, self.assumption)
        if sum(value is not None for value in fields) != 1:
            raise ValueError("Exactly one numeric provenance is required")
        if any(value == "" for value in (self.ledger, self.expr, self.datum, self.assumption)):
            raise ValueError("Empty numeric provenance")
        if self.centreline is not None and (
            self.centreline not in (0, 90, 180, 270) or not self.reason
        ):
            raise ValueError("Centreline must be a cardinal angle with a reason")
        return self


def evaluate_numeric(
    value: Numeric, ledger: dict[str, Requirement], assumptions: dict[str, Assumption]
) -> Decimal:
    """Only citations, dimensionally consistent +/- and literal division by 2."""

    def scaled(number: Decimal, unit: str | None) -> tuple[Decimal, str]:
        scales = {"mm": Decimal(1), "cm": Decimal(10), "in": Decimal("25.4")}
        if unit is None:
            raise ValueError("Unresolved unit")
        return number * scales.get(unit, Decimal(1)), "length" if unit in scales else unit

    def cited(key: str) -> tuple[Decimal, str]:
        if key in assumptions and key not in ledger:
            # A registered assumption may anchor an expression; it stays a visible finding.
            return scaled(assumptions[key].value, assumptions[key].unit)
        if key not in ledger or ledger[key].value is None:
            raise ValueError(f"Unavailable citation {key}")
        r = ledger[key]
        assert r.value is not None
        return scaled(r.value, r.unit)

    def visit(node: ast.AST) -> tuple[Decimal, str]:
        if isinstance(node, ast.Name):
            return cited(node.id)
        if (
            isinstance(node, ast.Constant)
            and type(node.value) is int
            and node.value in (0, 90, 180, 270)
        ):
            # An implied cardinal centreline may offset a printed angle: "180+R45_n1".
            return Decimal(node.value), "degree"
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
            a, ak = visit(node.left)
            b, bk = visit(node.right)
            if ak != bk:
                raise ValueError("Incompatible dimensions in expression")
            return (a + b if isinstance(node.op, ast.Add) else a - b), ak
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            if isinstance(node.right, ast.Constant) and type(node.right.value) is int:
                if node.right.value == 2:
                    a, kind = visit(node.left)
                    return a / 2, kind
        raise ValueError(
            "Only citations, +, -, /2 and cardinal angle offsets (0, 90, 180, 270) are permitted"
        )

    if value.ledger is not None:
        result, _ = cited(value.ledger)
    elif value.expr is not None:
        try:
            tree = ast.parse(value.expr, mode="eval")
            if sum(1 for _ in ast.walk(tree)) > 100:
                raise ValueError("Expression exceeds complexity limit")
            result, _ = visit(tree.body)
        except (SyntaxError, RecursionError) as error:
            raise ValueError("Invalid restricted expression") from error
    elif value.datum is not None:
        result = Decimal(0)
    elif value.centreline is not None:
        result = Decimal(value.centreline)
    else:
        if value.assumption not in assumptions:
            raise ValueError("Unregistered assumption")
        assumption = assumptions[value.assumption]
        result, _ = scaled(assumption.value, assumption.unit)
    if not result.is_finite() or abs(result) > 1_000_000:
        raise ValueError("Numeric result outside modelling bounds")
    return result


def resolve_context(
    statement: str,
    identity: str,
    envelope_value: float | None,
    override: str | None = None,
    max_envelope_mm: float = 2000,
) -> dict[str, Any]:
    """Plausibility may block, never choose or silently override a unit."""
    from drawing2step.web_pipeline import detect_unit

    try:
        unit = detect_unit(statement)
    except ValueError:
        return {"status": "FAIL", "unit": None, "detail": "Conflicting unit statements"}
    if not identity or identity.lower() in {"unknown", "unread", "unavailable"}:
        return {
            "status": "FAIL",
            "unit": unit,
            "detail": "Drawing identity must be resolved from the title block",
        }
    if unit is None:
        return {
            "status": "FAIL",
            "unit": None,
            "detail": (
                "No explicit drawing units. Engineer context review required; no silent default."
            ),
        }
    scales = {"mm": 1, "cm": 10, "in": 25.4}
    sheet_plausible = (
        envelope_value is not None and 0 < envelope_value * scales[unit] <= max_envelope_mm
    )
    if override and override != unit:
        if sheet_plausible or envelope_value is None:
            return {
                "status": "FAIL",
                "unit": unit,
                "sheet_unit": unit,
                "detail": (
                    "Selected units conflict with the sheet. "
                    "Record an engineer correction before modelling."
                ),
            }
        # The sheet contradicts itself: its stated unit makes the printed envelope impossible
        # for this family. An explicit selection is then a recorded correction, not a guess.
        selected_mm = envelope_value * scales[override]
        if not 0 < selected_mm <= max_envelope_mm:
            return {
                "status": "FAIL",
                "unit": unit,
                "sheet_unit": unit,
                "detail": (
                    "Neither the sheet unit nor the selected unit gives a plausible envelope; "
                    "context review required."
                ),
            }
        return {
            "status": "PASS",
            "unit": override,
            "sheet_unit": unit,
            "unit_correction": {
                "sheet_unit": unit,
                "selected_unit": override,
                "envelope_value": envelope_value,
            },
            "detail": (
                f"Sheet states {unit} but its printed envelope {envelope_value:g} {unit} is "
                f"outside the family bound; the selected {override} is recorded as an "
                "engineer unit correction and remains a visible finding."
            ),
        }
    if envelope_value is None:
        return {
            "status": "FAIL",
            "unit": unit,
            "sheet_unit": unit,
            "detail": "Overall envelope unread; context review required",
        }
    if not sheet_plausible:
        return {
            "status": "FAIL",
            "unit": unit,
            "sheet_unit": unit,
            "detail": (
                f"Envelope {envelope_value:g} {unit} is outside provisional gland-ring bounds "
                "under the sheet's stated unit. If the title block is wrong, upload again "
                "selecting the correct unit to record a unit correction."
            ),
        }
    return {
        "status": "PASS",
        "unit": unit,
        "sheet_unit": unit,
        "detail": (
            "Explicit units and title-block identity resolved; associations still require review"
        ),
    }


def audit_completeness(
    inventory: Inventory | None, requirements: list[Requirement], built_counts: dict[str, int]
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

    def add(code: str, subject: str, status: Status, detail: str) -> None:
        findings.append({"code": code, "subject": subject, "status": status, "detail": detail})

    if inventory is None:
        add(
            "INVENTORY_UNAVAILABLE",
            "drawing",
            "UNKNOWN",
            "Independent visual inventory unavailable",
        )
        return findings
    if not inventory.features:
        add(
            "EMPTY_INVENTORY",
            "drawing",
            "UNKNOWN",
            "No features inventoried; source completeness unproven",
        )
    ids = {f.id for f in inventory.features}
    for feature in inventory.features:
        linked = [r for r in requirements if r.feature == feature.id and not r.reference_only]
        if not linked:
            add("UNASSOCIATED_FEATURE", feature.id, "UNKNOWN", feature.description)
        if feature.count is None:
            add("UNREAD_COUNT", feature.id, "UNKNOWN", "Visual feature count unresolved")
        if feature.id in built_counts and built_counts[feature.id] != feature.count:
            add(
                "FEATURE_COUNT",
                feature.id,
                "FAIL",
                f"Expected {feature.count}, built {built_counts[feature.id]}",
            )
        for requirement in linked:
            if requirement.kind == "count" and requirement.value != feature.count:
                add(
                    "CROSS_VIEW_COUNT",
                    feature.id,
                    "FAIL",
                    "Callout and visual inventory counts disagree",
                )
    # Same physical group may appear in several views; never sum those appearances.
    groups: dict[str, set[int]] = {}
    for feature in inventory.features:
        if feature.same_physical_group and feature.count is not None:
            groups.setdefault(feature.same_physical_group, set()).add(feature.count)
    for group, counts in groups.items():
        if len(counts) > 1:
            add(
                "CROSS_VIEW_COUNT",
                group,
                "UNKNOWN",
                "Visible counts differ; occlusion or view association needs review",
            )
    for requirement in requirements:
        if not requirement.reference_only and requirement.feature not in ids:
            add("UNASSOCIATED_REQUIREMENT", requirement.id, "UNKNOWN", requirement.raw_text)
    for index, uncertainty in enumerate(inventory.uncertainties):
        add("INVENTORY_UNCERTAINTY", f"inventory-{index}", "UNKNOWN", uncertainty)
    return findings


def release_gate(
    checks: list[dict[str, Any]],
    assumptions: list[Assumption],
    *,
    completeness_approved: bool,
    accuracy_approved: bool,
) -> dict[str, Any]:
    """Candidate eligibility only. This does not issue or invent release authorisation."""
    reasons = []
    if not completeness_approved:
        reasons.append("Source completeness review required")
    if not accuracy_approved:
        reasons.append("Approved paired-data accuracy gate required")
    if not {"U", "D", "G", "H1", "H2", "H3"} <= {c.get("layer") for c in checks}:
        reasons.append("Required check layers absent")
    if any(c.get("status") != "PASS" for c in checks):
        reasons.append("Failed or unresolved checks remain")
    if assumptions:
        reasons.append("Individual version-bound assumption sign-offs required")
    return {
        "candidate": "CHECKED" if not reasons else "REVIEW",
        "release": "BLOCKED",
        "reasons": reasons or ["Named engineer release and CAM approval required"],
    }


def validate_associations(
    associations: list[Association], requirements: list[Requirement], inventory: Inventory
) -> list[dict[str, str]]:
    ledger = {r.id: r for r in requirements}
    features = {f.id: f for f in inventory.features}
    findings = []
    allowed = {
        "diameter": {"diameter", "radius"},
        "depth": {"linear"},
        "axial_position": {"linear"},
        "angle": {"angle"},
        "count": {"count"},
        "note": {"note", "thread"},
    }
    for a in associations:
        r = ledger.get(a.requirement)
        status = "UNKNOWN"
        detail = "Source view and host-face confirmation required"
        if r is None or a.feature not in features or r.kind not in allowed[a.attribute]:
            status, detail = "FAIL", "Unavailable reference or incompatible dimension type"
        elif a.attribute != "note" and not a.reference_face:
            detail = "Reference face or datum unresolved"
        findings.append({"subject": a.id, "status": status, "detail": detail})
    return findings
