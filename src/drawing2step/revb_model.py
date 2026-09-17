"""Typed draft construction. Evidence uncertainty never suppresses a valid draft artifact.

All public numbers have provenance. Internal geometric constants (pi, cutter overshoot,
thread tables) are code policy, not model-authored expressions. Release is separate.
"""

import ast
import hashlib
import math
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal, Self

import cadquery as cq
from pydantic import Field, model_validator

from drawing2step.models import Contract
from drawing2step.revb import Association, Assumption, Numeric, Requirement, evaluate_numeric
from drawing2step.revb_geometry import check_structure, inspect_step
from drawing2step.revb_sections import compare_section, section_svg
from drawing2step.storage import canonical_json, write_once

BUILDER_VERSION = "revb-feature-builder-v2"
CAPABILITIES = {
    "hole_pattern": "axial cylindrical holes, blind or through",
    "tapped_hole": "tap-drill geometry with thread annotation",
    "port": "radial/oblique cylindrical drill; optional thread entry drill",
    "bore_slot": "axial cylindrical bore notch",
    "od_slot": "open axial slot from a pitch circle out through the outside diameter",
    "chamfer": "specified circular edge, width and angle",
    "counterbore": "axial counterbore on a hole pattern, or a flat spotface at a radial port entry",
    "marking": "report only",
}
# Nominal tap/entry drill diameters. These are draft shop policies requiring release review.
_UNIFIED_TAP_DRILL_IN = {
    "#4-40": 0.089,
    "#6-32": 0.1065,
    "#8-32": 0.136,
    "#10-24": 0.1495,
    "#10-32": 0.159,
    "1/4-20": 0.201,
    "1/4-28": 0.213,
    "5/16-18": 0.257,
    "5/16-24": 0.272,
    "3/8-16": 0.3125,
    "3/8-24": 0.332,
    "7/16-14": 0.368,
    "7/16-20": 0.3906,
    "1/2-13": 0.4219,
    "1/2-20": 0.4531,
    "9/16-12": 0.4844,
    "5/8-11": 0.5312,
    "5/8-18": 0.5625,
    "3/4-10": 0.6562,
    "3/4-16": 0.6875,
}
_NPT_TAP_DRILL_IN = {
    "1/16": 0.246,
    "1/8": 0.3438,
    "1/4": 0.4375,
    "3/8": 0.5781,
    "1/2": 0.71875,
    "3/4": 0.9219,
    "1": 1.1562,
}
_METRIC_TAP_DRILL_MM = {
    "M3": 2.5,
    "M4": 3.3,
    "M5": 4.2,
    "M6": 5.0,
    "M8": 6.8,
    "M10": 8.5,
    "M12": 10.2,
    "M14": 12.0,
    "M16": 14.0,
    "M20": 17.5,
    "M24": 21.0,
}
_DECIMAL_NOMINALS = {
    "0.0625": "1/16",
    "0.125": "1/8",
    "0.25": "1/4",
    "0.3125": "5/16",
    "0.375": "3/8",
    "0.4375": "7/16",
    "0.5": "1/2",
    "0.5625": "9/16",
    "0.625": "5/8",
    "0.75": "3/4",
    "1": "1",
}


def _nominal_size(token: str) -> str:
    token = token.strip()
    if token.startswith("#") or "/" in token:
        return token
    try:
        return _DECIMAL_NOMINALS.get(format(Decimal(token).normalize(), "f"), token)
    except (InvalidOperation, ValueError):
        return token


def thread_drill_mm(designation: str | None) -> float | None:
    """Nominal drill for a printed thread designation; None when the table has no entry.

    Accepts unified (#10-24, 5/16-18 UNC), metric (M8, M8x1.25) and pipe threads written as
    fractions or decimals (.375 NPT, 3/8-18 NPT). Never guesses an unknown size.
    """
    if not designation:
        return None
    text = re.sub(r"\s+", " ", designation.upper()).strip()
    text = re.sub(r"^\d+\s*[X×~]\s*", "", text)
    text = re.sub(r"\b(?:TAP|TAPPED|THREAD|THD|TPI|HOLES?|DEEP|PORT)\b", "", text)
    text = text.replace("'S", "").replace("’S", "").strip().rstrip(".,;:")
    pipe = re.fullmatch(r"([#\d./]+)(?:-[\d.]+)?\s*-?\s*NPTF?", text)
    if pipe:
        size = _nominal_size(pipe[1])
        return _NPT_TAP_DRILL_IN[size] * 25.4 if size in _NPT_TAP_DRILL_IN else None
    metric = re.fullmatch(r"M\s*(\d+)(?:\.0+)?(?:\s*[X×]\s*[\d.]+)?", text)
    if metric:
        return _METRIC_TAP_DRILL_MM.get(f"M{metric[1]}")
    unified = re.fullmatch(r"(#\d+|[\d./]+)\s*-\s*(\d+)(?:\s*UN[CF]?(?:-[123][AB])?)?", text)
    if unified:
        key = f"{_nominal_size(unified[1])}-{unified[2]}"
        return _UNIFIED_TAP_DRILL_IN[key] * 25.4 if key in _UNIFIED_TAP_DRILL_IN else None
    return None


class ProfilePoint(Contract):
    z: Numeric
    radius: Numeric


class Feature(Contract):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    kind: Literal[
        "hole_pattern",
        "tapped_hole",
        "port",
        "bore_slot",
        "od_slot",
        "chamfer",
        "counterbore",
        "marking",
    ]
    citations: list[str] = Field(min_length=1)
    inventory_ids: list[str] = Field(default_factory=list)
    diameter: Numeric | None = None
    depth: Numeric | None = None
    z: Numeric | None = None
    angle: Numeric | None = None
    tilt: Numeric | None = None
    pcd: Numeric | None = None
    count: Numeric | None = None
    width: Numeric | None = None
    length: Numeric | None = None
    radius: Numeric | None = None
    entry_depth: Numeric | None = None
    entry_diameter: Numeric | None = None
    host: Literal["face_a", "face_b", "outside", "bore"]
    reference_face: str = Field(min_length=1)
    thread: str | None = None
    report_only: bool = False
    reason: str = ""

    @model_validator(mode="after")
    def required_geometry(self) -> Self:
        if self.report_only or self.kind == "marking":
            if not self.reason:
                raise ValueError("Report-only features require an explicit disposition reason")
            return self
        fields = {
            "hole_pattern": ("diameter", "depth", "pcd", "count", "angle"),
            "tapped_hole": ("depth", "pcd", "count", "angle"),
            "port": ("diameter", "depth", "z", "angle"),
            "bore_slot": ("diameter", "radius", "z", "depth", "angle"),
            "od_slot": ("diameter", "depth", "pcd", "count", "angle"),
            "chamfer": ("z", "radius", "width", "angle"),
            "counterbore": ("diameter", "depth", "angle"),
        }
        for name in fields[self.kind]:
            if getattr(self, name) is None:
                raise ValueError(f"{self.id}: {self.kind} requires {name}")
        if self.kind == "port" and self.count is not None:
            raise ValueError("Represent every radial port separately, each with its own position")
        if self.kind == "tapped_hole" and not self.thread:
            raise ValueError("Tapped hole requires a supported thread designation")
        if self.kind == "port" and self.thread and self.entry_depth is None:
            raise ValueError("Threaded port requires cited or assumed entry_depth")
        if self.kind == "counterbore":
            if self.host == "outside" and self.z is None:
                raise ValueError(f"{self.id}: a spotface at a port entry requires z")
            if self.host in {"face_a", "face_b"} and (self.pcd is None or self.count is None):
                raise ValueError(f"{self.id}: an axial counterbore requires pcd and count")
        return self


class DraftSpec(Contract):
    schema_version: Literal["feature-spec-revb-v1"] = "feature-spec-revb-v1"
    provenance: Literal["draft"] = "draft"
    reference_face: str = Field(min_length=1)
    coordinate_policy: str = Field(min_length=1)
    profile: list[ProfilePoint] = Field(min_length=4, max_length=100)
    features: list[Feature] = Field(default_factory=list, max_length=100)
    assumptions: list[Assumption] = Field(default_factory=list, max_length=200)
    associations: list[Association] = Field(default_factory=list, max_length=300)
    unresolved: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_ids(self) -> Self:
        for collection in (self.features, self.assumptions, self.associations):
            if len({item.id for item in collection}) != len(collection):
                raise ValueError("Duplicate identifiers in specification")
        if any(a.status == "CONFIRMED" or a.reviewer for a in self.associations):
            raise ValueError("A model proposal cannot grant engineer approval")
        return self


def numeric_value(
    number: Numeric, ledger: dict[str, Requirement], assumptions: dict[str, Assumption], kind: str
) -> float:
    """Check the destination dimension as well as expression dimensional consistency."""
    dimensions: list[str | None] = []
    if number.ledger:
        if number.ledger not in ledger:
            raise ValueError(f"Missing citation {number.ledger}")
        dimensions.append(ledger[number.ledger].unit)
    elif number.expr:
        for node in ast.walk(ast.parse(number.expr, mode="eval")):
            if isinstance(node, ast.Name):
                if node.id in ledger:
                    dimensions.append(ledger[node.id].unit)
                elif node.id in assumptions:
                    dimensions.append(assumptions[node.id].unit)
                else:
                    raise ValueError(f"Missing citation {node.id}")
    elif number.assumption:
        if number.assumption not in assumptions:
            raise ValueError(f"Unregistered assumption {number.assumption}")
        dimensions.append(assumptions[number.assumption].unit)
    elif number.centreline is not None:
        dimensions.append("degree")
    elif number.datum:
        if kind != "length":
            raise ValueError("Datum zero is only a coordinate")
    for unit in dimensions:
        actual = "length" if unit in {"mm", "cm", "in"} else unit
        if actual != kind:
            raise ValueError(f"Expected {kind}, citation has {actual}")
    return float(evaluate_numeric(number, ledger, assumptions))


def enrich_ledger(requirements: list[Requirement], drawing_unit: str) -> list[Requirement]:
    """Atomize printed compound callouts without changing original readings.

    These remain single-source proposals, never confirmed readings. Every child names its
    parent and uses exactly a printed token. Angles in degrees/minutes are converted by code.
    """
    result = list(requirements)
    for r in requirements:
        if r.kind == "note" and not r.geometry_driving:
            continue
        text = r.raw_text
        for index, match in enumerate(
            re.finditer(r"(?<![\d.])[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?(?![\d.])", text)
        ):
            token = match.group()
            # A number glued to letters is part of an identifier or a thread code
            # ('GEBK472722A', 'M8x1.25'), never a printed length; R.062 and 14MM/10X stay.
            head = text[: match.start()]
            # Letters glued before (except R for a radius and X as a multiplier), or the
            # 'x' of a thread pitch ('M8x1.25'), mark an identifier rather than a length.
            glued_before = re.search(r"[A-QS-WYZa-qs-wyz]$", head) or re.search(
                r"[A-Za-z]\d+(?:\.\d+)?[xX×]$", head
            )
            glued_after = re.match(r"[A-Za-z]", text[match.end() :]) and not re.match(
                r"(?:MM|CM|IN|INCH(?:ES)?|DEG|X|NOS?|HOLES?|PL(?:CS|ACES)?|TYP)\b",
                text[match.end() :],
                re.I,
            )
            if glued_before or glued_after:
                continue
            before, after = text[max(0, match.start() - 12) : match.start()], text[match.end() :]
            unit = r.unit if r.unit in {"mm", "cm", "in"} else drawing_unit
            kind = "linear"
            if re.match(r"\s*(?:°|DEG)", after, re.I):
                unit, kind = "degree", "angle"
                # A DMS callout gets its own exact code-converted reading below.
            elif re.fullmatch(r"\d+", token) and re.match(
                r"\s*(?:NOS?\b|HOLES?\b|PL(?:CS|ACES)?\b|~|[X×](?=\s|[Øø∅.#\d]|$))", after, re.I
            ):
                # "4X Ø.562", "2X.132", "2~ Ø14MM" and "10 HOLES" are multipliers; a decimal
                # such as "Ø.562 HOLE" before the word HOLE is the hole size, never a count.
                unit, kind = "count", "count"
            elif before.endswith(("Ø", "ø", "∅")):
                kind = "diameter"
            elif before.endswith("R"):
                kind = "radius"
            if re.match(r"\s*MM\b", after, re.I):
                unit = "mm"
            elif re.match(r"\s*CM\b", after, re.I):
                unit = "cm"
            elif re.match(r'\s*(?:"|″|INCH(?:ES)?\b|IN\b)', after, re.I):
                unit = "in"
            if unit == "count" and float(token) < 1:
                continue
            result.append(
                Requirement.model_validate(
                    dict(
                        id=f"{r.id}_n{index + 1}",
                        raw_text=token,
                        kind=kind,
                        value=token,
                        unit=unit,
                        box=r.box,
                        sources=(*r.sources, f"callout:{r.id}"),
                        text_status="A_ONLY",
                    )
                )
            )
        dms = re.search(r"(\d+)°\s*(\d+)[′']", text)
        if dms:
            value = Decimal(dms[1]) + Decimal(dms[2]) / 60
            result.append(
                Requirement(
                    id=f"{r.id}_angle",
                    raw_text=dms[0],
                    kind="angle",
                    value=value,
                    unit="degree",
                    box=r.box,
                    sources=(*r.sources, f"callout:{r.id}"),
                    text_status="A_ONLY",
                )
            )
    return result


def _check(layer: str, subject: str, status: str, detail: str) -> dict[str, str]:
    return {"layer": layer, "subject": subject, "status": status, "detail": detail}


def outer_radius_at(points: list[tuple[float, float]], z: float) -> float | None:
    """Largest profile radius at axial position z; None where the revolved body is absent."""
    radii: list[float] = []
    for (r1, z1), (r2, z2) in zip(points, points[1:] + points[:1], strict=True):
        if not min(z1, z2) - 1e-9 <= z <= max(z1, z2) + 1e-9:
            continue
        if abs(z2 - z1) < 1e-9:
            radii.extend((r1, r2))
        else:
            radii.append(r1 + (r2 - r1) * (z - z1) / (z2 - z1))
    return max(radii) if radii else None


def entry_face_z(
    plain: cq.Shape, points: list[tuple[float, float]], host: str, pitch: float, radius: float
) -> float:
    """Axial position of the first exposed annular face a drill from ``host`` meets.

    Candidates are the profile's flat edges whose radial span holds the pitch radius (a
    footprint may overhang a chamfer); exposure is tested on the revolved body itself.
    Nothing is invented: the face comes from cited profile vertices, and the choice is
    reported for review.
    """
    side = -1 if host == "face_a" else 1  # void lies on this side of the face
    solid = plain.Solids()[0]
    exposed: list[float] = []
    for (r1, z1), (r2, z2) in zip(points, points[1:] + points[:1], strict=True):
        if abs(z1 - z2) > 1e-9 or not min(r1, r2) - 1e-6 <= pitch <= max(r1, r2) + 1e-6:
            continue
        inside = solid.isInside(cq.Vector(pitch, 0, z1 - side * 0.01))
        outside = solid.isInside(cq.Vector(pitch, 0, z1 + side * 0.01))
        if inside and not outside:
            exposed.append(z1)
    if not exposed:
        raise ValueError(
            f"No exposed {host} face holds the pitch radius {pitch:.3f} mm; "
            "cite z for the face the section shows the holes entering, or choose the other host"
        )
    return max(exposed) if host == "face_b" else min(exposed)


def build_model(spec: DraftSpec, requirements: list[Requirement], outdir: Path) -> dict[str, Any]:
    """Construct a draft, naming failed cuts and preserving the last valid solid."""
    outdir.mkdir(parents=True, exist_ok=True)
    ledger = {r.id: r for r in requirements}
    assumptions = {a.id: a for a in spec.assumptions}
    if len(ledger) != len(requirements):
        raise ValueError("Duplicate requirement identifiers")
    write_once(outdir / "spec.json", spec.model_dump_json(indent=2).encode())
    write_once(
        outdir / "ledger.json", canonical_json([r.model_dump(mode="json") for r in requirements])
    )
    points = [
        (
            numeric_value(p.radius, ledger, assumptions, "length"),
            numeric_value(p.z, ledger, assumptions, "length"),
        )
        for p in spec.profile
    ]
    if points[0] == points[-1]:
        points.pop()
    if any(r <= 0 or z < 0 for r, z in points) or len(set(points)) != len(points):
        raise ValueError("body: invalid radial profile, negative coordinate or duplicate vertex")
    if max(z for _, z in points) > 2000 or max(r for r, _ in points) > 1000:
        raise ValueError("body: family envelope exceeds modelling bounds")
    shape = cq.Workplane("XZ").polyline(points).close().revolve(360, (0, 0), (0, 1)).val()
    if not isinstance(shape, cq.Shape) or not shape.isValid() or len(shape.Solids()) != 1:
        raise ValueError("body: profile did not produce exactly one valid solid")
    plain = shape
    cq.exporters.export(plain, str(outdir / "plain-body.step"))
    checks: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    expectations: list[dict[str, Any]] = []
    cutters: dict[str, cq.Shape] = {}
    box = plain.BoundingBox()
    outer = max(r for r, _ in points)
    height = box.zmax - box.zmin

    for feature in spec.features:
        f = feature
        receipt: dict[str, Any] = {
            "id": f.id,
            "kind": f.kind,
            "citations": f.citations,
            "inventory_ids": f.inventory_ids,
            "thread": f.thread,
        }
        try:
            if any(c not in ledger for c in f.citations):
                raise ValueError("Missing requirement citation")
            if f.report_only or f.kind == "marking":
                receipt.update(
                    status="REPORT_ONLY", detail=f.reason or "Report-only marking policy"
                )
                checks.append(_check("F", f.id, "UNKNOWN", receipt["detail"]))
                receipts.append(receipt)
                continue

            def value(name: str, dimension: str = "length", feature: Feature = f) -> float:
                number = getattr(feature, name)
                if number is None:
                    raise ValueError(f"Missing {name}")
                return numeric_value(number, ledger, assumptions, dimension)

            diameter = value("diameter") if f.diameter else 0.0
            if not f.diameter and f.thread:
                drill = thread_drill_mm(f.thread)
                if drill is None:
                    raise ValueError(
                        f"Unsupported thread designation {f.thread!r}; cite the printed "
                        "tap-drill diameter or register an assumption"
                    )
                diameter = drill
            cut: cq.Shape | None = None
            parts: list[cq.Shape] = []
            if f.kind in {"hole_pattern", "tapped_hole"}:
                count = value("count", "count")
                if count != int(count) or not 1 <= count <= 200:
                    raise ValueError("Invalid feature count")
                depth, pcd, angle = value("depth"), value("pcd"), value("angle", "degree")
                if diameter <= 0 or depth <= 0 or depth > height + 0.001 or pcd <= diameter:
                    raise ValueError("Invalid hole dimensions or depth exceeds body")
                if f.host not in {"face_a", "face_b"}:
                    raise ValueError(
                        "Axial holes require host face_a or face_b (pins 'THRU TO MEET GROOVE' "
                        "enter from the face on the groove side); a radial hole is a port"
                    )
                # An explicit z names a recessed host face (a flange face behind the nose);
                # the drill still runs from that face into the body, away from face A or B.
                inward = 1 if f.host == "face_a" else -1
                if f.z:
                    entry_z = value("z")
                else:
                    # Without an explicit z the drill enters the first exposed annular face
                    # it meets from its host side whose span holds the whole hole footprint.
                    entry_z = entry_face_z(plain, points, f.host, pcd / 2, diameter / 2)
                    extreme = box.zmin if f.host == "face_a" else box.zmax
                    if abs(entry_z - extreme) > 1e-6:
                        checks.append(
                            _check(
                                "H2",
                                f.id,
                                "UNKNOWN",
                                f"Entry face resolved from the profile at z={entry_z:.3f} mm "
                                f"(no z cited): the {f.host} plane holds no material at the "
                                f"pitch circle; confirm the section shows the holes there",
                            )
                        )
                if not box.zmin - 0.001 <= entry_z <= box.zmax + 0.001:
                    raise ValueError("Explicit z lies outside the body")
                z0, z1 = (
                    (entry_z, entry_z + depth) if f.host == "face_a" else (entry_z - depth, entry_z)
                )
                if z0 < box.zmin - 0.001 or z1 > box.zmax + 0.001:
                    raise ValueError("Hole depth exceeds the body beyond its host face")
                for i in range(int(count)):
                    theta = math.radians(angle + i * 360 / count)
                    x, y = pcd / 2 * math.cos(theta), pcd / 2 * math.sin(theta)
                    outside = cq.Vector(x, y, entry_z - inward * 0.01)
                    if f.z and plain.Solids()[0].isInside(outside):
                        raise ValueError(
                            f"Explicit z {entry_z:.3f} mm is not an exposed {f.host} face at "
                            f"hole {i + 1}; material lies outside it"
                        )
                    c = cq.Solid.makeCylinder(
                        diameter / 2, depth + 0.002, cq.Vector(x, y, z0 - 0.001), cq.Vector(0, 0, 1)
                    )
                    if plain.intersect(c).Volume() < 1e-6:
                        raise ValueError(f"Hole {i + 1} misses its host")
                    parts.append(c)
                cut = cq.Compound.makeCompound(parts)
                expectations.append(
                    {
                        "kind": "hole_pattern",
                        "id": f.id,
                        "count": int(count),
                        "diameter": diameter,
                        "pcd": pcd,
                        "angle": angle,
                        "z0": z0,
                        "z1": z1,
                    }
                )
            elif f.kind == "port":
                if f.host != "outside":
                    raise ValueError("Port requires outside host")
                depth, angle, z = value("depth"), value("angle", "degree"), value("z")
                tilt = value("tilt", "degree") if f.tilt else 0
                if not 0 < diameter < outer or not 0 < depth < 2 * outer or abs(tilt) >= 80:
                    raise ValueError("Invalid port diameter, depth or tilt")
                theta, phi = math.radians(angle), math.radians(tilt)
                direction = cq.Vector(
                    -math.cos(theta) * math.cos(phi),
                    -math.sin(theta) * math.cos(phi),
                    math.sin(phi),
                )
                # A stepped body enters at its local outside surface, not the flange OD.
                entry_radius = outer_radius_at(points, z)
                if entry_radius is None:
                    raise ValueError("Port host absent at this axial position")
                start = cq.Vector(entry_radius * math.cos(theta), entry_radius * math.sin(theta), z)
                if not plain.Solids()[0].isInside(start + direction * 0.01):
                    raise ValueError("Port host absent at this axial position")
                cut = cq.Solid.makeCylinder(
                    diameter / 2, depth + 0.002, start - direction * 0.001, direction
                )
                if f.thread:
                    entry = (
                        value("entry_diameter") if f.entry_diameter else thread_drill_mm(f.thread)
                    )
                    if entry is None or not f.entry_depth:
                        raise ValueError(
                            f"Threaded port {f.thread!r} needs a cited entry_diameter (printed "
                            "flat/tap drill) or a supported thread table entry, plus entry_depth"
                        )
                    entry_depth = value("entry_depth")
                    # A bottomed tap drill with no follow-on (entry_depth == depth) is a
                    # legitimate blind port; only an entry deeper than the drill is impossible.
                    if not 0 < entry_depth <= depth:
                        raise ValueError("Thread entry depth must be inside full drill path")
                    if not diameter < entry < outer:
                        raise ValueError(
                            "Thread entry drill must be larger than the follow-on drill"
                        )
                    if entry_depth >= depth - 1e-6:
                        # Bottomed tap drill: the entry drill is the whole passage.
                        diameter = entry
                        cut = cq.Solid.makeCylinder(
                            entry / 2, depth + 0.002, start - direction * 0.001, direction
                        )
                    else:
                        cut = cut.fuse(
                            cq.Solid.makeCylinder(entry / 2, entry_depth + 0.001, start, direction)
                        )
                endpoint = start + direction * (depth + 0.01)
                expectations.append(
                    {
                        "kind": "passage",
                        "id": f.id,
                        "start": list(start.toTuple()),
                        "end": list((start + direction * depth).toTuple()),
                        "diameter": diameter,
                    }
                )
                if plain.Solids()[0].isInside(endpoint):
                    checks.append(
                        _check(
                            "H2", f.id, "FAIL", "Drill stops in material before its intended cavity"
                        )
                    )
                else:
                    # Explicit target identity is not established by empty-space alone.
                    checks.append(
                        _check(
                            "H2",
                            f.id,
                            "UNKNOWN",
                            "Drill endpoint is void; intended cavity identity requires review",
                        )
                    )
            elif f.kind == "od_slot":
                count = value("count", "count")
                if count != int(count) or not 1 <= count <= 200:
                    raise ValueError("Invalid feature count")
                depth, pcd, angle = value("depth"), value("pcd"), value("angle", "degree")
                if diameter <= 0 or depth <= 0 or depth > height + 0.001 or pcd <= diameter:
                    raise ValueError("Invalid slot dimensions or depth exceeds body")
                if pcd / 2 >= outer:
                    raise ValueError("Slot pitch circle lies outside the body")
                if f.host == "face_a":
                    z0, z1 = box.zmin, box.zmin + depth
                elif f.host == "face_b":
                    z0, z1 = box.zmax - depth, box.zmax
                elif f.host == "outside":
                    # Open to the OD means through every wall at that pitch radius.
                    z0, z1 = box.zmin, box.zmax
                    depth = height
                else:
                    raise ValueError("Open slots require face_a, face_b or outside")
                for i in range(int(count)):
                    theta = angle + i * 360 / count
                    x = pcd / 2 * math.cos(math.radians(theta))
                    y = pcd / 2 * math.sin(math.radians(theta))
                    pin = cq.Solid.makeCylinder(
                        diameter / 2, depth + 0.002, cq.Vector(x, y, z0 - 0.001), cq.Vector(0, 0, 1)
                    )
                    # Round-ended slot: the tool radius at the pitch circle, open to the OD.
                    tongue = cq.Solid.makeBox(
                        outer - pcd / 2 + 1,
                        diameter,
                        depth + 0.002,
                        cq.Vector(pcd / 2, -diameter / 2, z0 - 0.001),
                    ).rotate(cq.Vector(0, 0, 0), cq.Vector(0, 0, 1), theta)
                    # Parts stay separate solids: a fused cutter can fail to intersect at all.
                    if plain.intersect(pin).Volume() + plain.intersect(tongue).Volume() < 1e-6:
                        raise ValueError(f"Slot {i + 1} misses its host")
                    parts.extend((pin, tongue))
                cut = cq.Compound.makeCompound(parts)
                expectations.append(
                    {
                        "kind": "od_slot",
                        "id": f.id,
                        "count": int(count),
                        "diameter": diameter,
                        "pcd": pcd,
                        "angle": angle,
                        "z0": z0,
                        "z1": z1,
                    }
                )
            elif f.kind == "bore_slot":
                if f.host != "bore":
                    raise ValueError("Bore slot requires bore host; depth runs along +Z from z")
                radius, angle, z, depth = (
                    value("radius"),
                    value("angle", "degree"),
                    value("z"),
                    value("depth"),
                )
                if not 0 < diameter < outer or radius <= 0 or depth <= 0 or depth > height:
                    raise ValueError("Invalid bore notch geometry")
                if z < box.zmin or z + depth > box.zmax + 0.001:
                    raise ValueError("Bore notch extends beyond its cited axial span")
                theta = math.radians(angle)
                cut = cq.Solid.makeCylinder(
                    diameter / 2,
                    depth,
                    cq.Vector(radius * math.cos(theta), radius * math.sin(theta), z),
                )
            elif f.kind == "counterbore":
                depth, angle = value("depth"), value("angle", "degree")
                if not 0 < diameter < outer or not 0 < depth <= height:
                    raise ValueError("Invalid counterbore diameter or depth")
                if f.host in {"face_a", "face_b"}:
                    # Axial counterbore enlarging each hole of a pattern from its host face.
                    count, pcd = value("count", "count"), value("pcd")
                    if count != int(count) or not 1 <= count <= 200 or pcd <= diameter:
                        raise ValueError("Invalid counterbore count or pitch circle")
                    entry_z = (
                        value("z")
                        if f.z
                        else entry_face_z(plain, points, f.host, pcd / 2, diameter / 2)
                    )
                    z0 = entry_z if f.host == "face_a" else entry_z - depth
                    if z0 < box.zmin - 0.001 or z0 + depth > box.zmax + 0.001:
                        raise ValueError("Counterbore depth exceeds the body beyond its host face")
                    for i in range(int(count)):
                        theta = math.radians(angle + i * 360 / count)
                        x, y = pcd / 2 * math.cos(theta), pcd / 2 * math.sin(theta)
                        c = cq.Solid.makeCylinder(
                            diameter / 2,
                            depth + 0.002,
                            cq.Vector(x, y, z0 - 0.001),
                            cq.Vector(0, 0, 1),
                        )
                        if plain.intersect(c).Volume() < 1e-6:
                            raise ValueError(f"Counterbore {i + 1} misses its host")
                        parts.append(c)
                    cut = cq.Compound.makeCompound(parts)
                elif f.host == "outside":
                    # Flat spotface at a radial port entry, perpendicular to the port axis.
                    z = value("z")
                    tilt = value("tilt", "degree") if f.tilt else 0
                    theta, phi = math.radians(angle), math.radians(tilt)
                    direction = cq.Vector(
                        -math.cos(theta) * math.cos(phi),
                        -math.sin(theta) * math.cos(phi),
                        math.sin(phi),
                    )
                    entry_radius = outer_radius_at(points, z)
                    if entry_radius is None:
                        raise ValueError("Spotface host absent at this axial position")
                    start = cq.Vector(
                        entry_radius * math.cos(theta), entry_radius * math.sin(theta), z
                    )
                    # Start outside the curved surface so the whole footprint is trimmed flat.
                    cut = cq.Solid.makeCylinder(
                        diameter / 2, depth + diameter, start - direction * diameter, direction
                    )
                    if plain.intersect(cut).Volume() < 1e-6:
                        raise ValueError("Spotface misses its host")
                else:
                    raise ValueError("Counterbore requires host face_a, face_b or outside")
            elif f.kind == "chamfer":
                z, radius, width, angle = (
                    value("z"),
                    value("radius"),
                    value("width"),
                    value("angle", "degree"),
                )
                if not 0 < width < height / 2 or not 0 < angle < 90:
                    raise ValueError("Invalid chamfer")
                # Choose a circular edge by geometry, never an unstable OCCT face index. A
                # limit cited for the edge against a nominal used in the profile differs by
                # microns, so the nearest circle within a quarter millimetre is the edge.
                circles = [e for e in shape.Edges() if e.geomType() == "CIRCLE"]
                if not circles:
                    raise ValueError("Chamfer edge is missing: the body has no circular edges")
                nearest = min(
                    circles, key=lambda e: math.hypot(e.Center().z - z, e.radius() - radius)
                )
                miss = math.hypot(nearest.Center().z - z, nearest.radius() - radius)
                if miss > 0.25:
                    raise ValueError(
                        f"Chamfer edge is missing at z={z:.3f} mm, radius {radius:.3f} mm; the "
                        f"nearest circular edge is at z={nearest.Center().z:.3f} mm, radius "
                        f"{nearest.radius():.3f} mm (cite the profile vertex the chamfer breaks)"
                    )
                edges = [
                    e
                    for e in circles
                    if math.hypot(e.Center().z - z, e.radius() - radius) <= miss + 1e-6
                ]
                if len(edges) != 1:
                    raise ValueError("Chamfer edge is ambiguous: two circular edges coincide")
                proposed = (
                    cq.Workplane("XY")
                    .add(shape)
                    .newObject(edges)
                    .chamfer(width, width * math.tan(math.radians(angle)))
                    .val()
                )
                if not isinstance(proposed, cq.Shape):
                    raise ValueError("Chamfer failed")
                cut = shape.cut(proposed)
            if cut is None:
                raise ValueError("Unsupported feature")
            # Sequential cuts per solid: one boolean with a fused or compound multi-solid
            # tool can silently remove nothing on some bodies.
            proposed = shape
            for part in parts or [cut]:
                proposed = proposed.cut(part)
            removed = shape.Volume() - proposed.Volume()
            if removed <= 1e-6:
                raise ValueError("Ineffective cut: feature removes no material")
            if not proposed.isValid() or len(proposed.Solids()) != 1:
                raise ValueError("Feature splits the part or creates an invalid solid")
            shape = proposed
            cutters[f.id] = cut
            receipt.update(status="BUILT", removed_volume_mm3=removed)
            checks.append(_check("G", f.id, "PASS", "Cut leaves one valid solid"))
            if f.kind in {"bore_slot", "od_slot", "chamfer", "counterbore"}:
                checks.append(
                    _check(
                        "H2",
                        f.id,
                        "UNKNOWN",
                        (
                            "Cut exists; edge orientation, opening and end relationships "
                            "need independent review"
                        ),
                    )
                )
            if f.thread:
                checks.append(
                    _check(
                        "H1",
                        f"{f.id}_thread",
                        "UNKNOWN",
                        (
                            "Tap-drill cylinder only. Thread depth, engagement and tapered/helical "
                            "geometry are report-only under draft policy"
                        ),
                    )
                )
        except (ValueError, RuntimeError, TypeError) as error:
            receipt.update(status="FAILED", detail=f"{f.id}: {error}")
            checks.append(_check("G", f.id, "FAIL", receipt["detail"]))
        receipts.append(receipt)

    path = outdir / "model.step"
    cq.exporters.export(shape, str(path))
    integrity = inspect_step(path)
    checks.append(_check("G", "round_trip", integrity["status"], integrity["detail"]))
    if integrity["status"] != "PASS":
        raise ValueError("round_trip: exported solid failed B-rep validation")
    measured = cq.importers.importStep(str(path)).val()
    assert isinstance(measured, cq.Shape)
    checks.extend(check_structure(path, expectations))
    # Measure every successful cutter against the REIMPORTED solid, not the builder result.
    for fid, cutter in cutters.items():
        residual = measured.intersect(cutter).Volume()
        checks.append(
            _check(
                "H1",
                fid,
                "PASS" if residual < 0.001 else "FAIL",
                f"Material remaining in specified cut: {residual:.6g} mm³",
            )
        )
    # Independent drawing-to-model topology cannot be established from a candidate spec alone.
    checks.append(
        _check(
            "H3",
            "drawing_sections",
            "UNKNOWN",
            "Drawing section topology and datums require engineer comparison",
        )
    )
    sections = []
    for plane in ("XZ", "YZ"):
        comparison = compare_section(path, {"id": f"section_{plane}", "plane": plane})
        sections.append(comparison)
        write_once(outdir / f"section-{plane}.svg", section_svg(comparison["measurement"]).encode())
    write_once(outdir / "sections.json", canonical_json(sections))
    for a in spec.assumptions:
        checks.append(_check("R", a.id, "UNKNOWN", f"{a.item}: {a.reason}"))
    for i, detail in enumerate(spec.unresolved):
        checks.append(_check("AS", f"unresolved_{i + 1}", "UNKNOWN", detail))
    bounds = measured.BoundingBox()
    measured_bbox = [bounds.xlen, bounds.ylen, bounds.zlen]
    cq.exporters.export(measured, str(outdir / "model.stl"))
    positions: list[float] = []
    indices: list[int] = []
    groups = []
    for face in measured.Faces():
        vertices, triangles = face.tessellate(0.15)
        offset = len(positions) // 3
        start_index = len(indices)
        positions.extend(v for p in vertices for v in p.toTuple())
        indices.extend(offset + i for t in triangles for i in t)
        # Match analytic surface fragments to named cutters. No persistent OCCT indices.
        feature_ids = [
            fid
            for fid, cutter in cutters.items()
            if face.Area() > 1e-8 and face.intersect(cutter).Area() > face.Area() * 0.99
        ]
        groups.append(
            {"start": start_index, "count": len(indices) - start_index, "feature_ids": feature_ids}
        )
    mesh = {"positions": positions, "indices": indices, "units": "mm", "groups": groups}
    write_once(outdir / "mesh.json", canonical_json(mesh))
    bounds = measured.BoundingBox()
    result = {
        "builder_version": BUILDER_VERSION,
        "status": "review",
        "release": "BLOCKED",
        "model_available": True,
        "download_available": True,
        "partial": True,
        "step": "model.step",
        "checks": checks,
        "features": receipts,
        "assumptions": [a.model_dump(mode="json") for a in spec.assumptions],
        "measurements": {
            "bbox": measured_bbox,
            "volume": measured.Volume(),
        },
        "verification": {"step_integrity": integrity["status"]},
        "sections_available": True,
        "step_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    write_once(outdir / "model-report.json", canonical_json(result))
    return result
