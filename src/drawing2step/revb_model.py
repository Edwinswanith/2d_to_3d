"""Typed draft construction. Evidence uncertainty never suppresses a valid draft artifact.

All public numbers have provenance. Internal geometric constants (pi, cutter overshoot,
thread tables) are code policy, not model-authored expressions. Release is separate.
"""

import ast
import hashlib
import math
import re
from pathlib import Path
from typing import Any, Literal, Self

import cadquery as cq
from pydantic import Field, model_validator

from drawing2step.models import Contract
from drawing2step.revb import Association, Assumption, Numeric, Requirement, evaluate_numeric
from drawing2step.revb_geometry import check_structure, inspect_step
from drawing2step.revb_sections import compare_section, section_svg
from drawing2step.storage import canonical_json, write_once

BUILDER_VERSION = "revb-feature-builder-v1"
CAPABILITIES = {
    "hole_pattern": "axial cylindrical holes, blind or through",
    "tapped_hole": "tap-drill geometry with thread annotation",
    "port": "radial/oblique cylindrical drill; optional thread entry drill",
    "bore_slot": "axial cylindrical bore notch",
    "chamfer": "specified circular edge, width and angle",
    "marking": "report only",
}
# Nominal cutting diameters. These are draft shop policies requiring release review.
THREAD_DRILLS_MM = {"#10-24": 3.7973, "M8": 6.8, "1/2 NPT": 18.25625}


class ProfilePoint(Contract):
    z: Numeric
    radius: Numeric


class Feature(Contract):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    kind: Literal["hole_pattern", "tapped_hole", "port", "bore_slot", "chamfer", "marking"]
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
            "chamfer": ("z", "radius", "width", "angle"),
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
                if node.id not in ledger:
                    raise ValueError(f"Missing citation {node.id}")
                dimensions.append(ledger[node.id].unit)
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
            before, after = text[max(0, match.start() - 12) : match.start()], text[match.end() :]
            unit = r.unit if r.unit in {"mm", "cm", "in"} else drawing_unit
            kind = "linear"
            if re.match(r"\s*(?:°|DEG)", after, re.I):
                unit, kind = "degree", "angle"
                # A DMS callout gets its own exact code-converted reading below.
            elif re.match(r"\s*(?:NOS?\b|HOLES?\b|~)", after, re.I):
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
            from decimal import Decimal

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

            diameter = value("diameter") if f.diameter else THREAD_DRILLS_MM.get(f.thread or "", 0)
            cut: cq.Shape | None = None
            if f.kind in {"hole_pattern", "tapped_hole"}:
                count = value("count", "count")
                if count != int(count) or not 1 <= count <= 200:
                    raise ValueError("Invalid feature count")
                depth, pcd, angle = value("depth"), value("pcd"), value("angle", "degree")
                if diameter <= 0 or depth <= 0 or depth > height + 0.001 or pcd <= diameter:
                    raise ValueError("Invalid hole dimensions or depth exceeds body")
                if f.host not in {"face_a", "face_b"}:
                    raise ValueError("Axial holes require face_a or face_b")
                z0, z1 = (
                    (box.zmin, box.zmin + depth)
                    if f.host == "face_a"
                    else (box.zmax - depth, box.zmax)
                )
                if f.z and abs(value("z") - (box.zmin if f.host == "face_a" else box.zmax)) > 0.001:
                    raise ValueError("Explicit z conflicts with the host reference face")
                for i in range(int(count)):
                    theta = math.radians(angle + i * 360 / count)
                    x, y = pcd / 2 * math.cos(theta), pcd / 2 * math.sin(theta)
                    c = cq.Solid.makeCylinder(
                        diameter / 2, depth + 0.002, cq.Vector(x, y, z0 - 0.001), cq.Vector(0, 0, 1)
                    )
                    if plain.intersect(c).Volume() < 1e-6:
                        raise ValueError(f"Hole {i + 1} misses its host")
                    cut = c if cut is None else cut.fuse(c)
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
                start = cq.Vector(outer * math.cos(theta), outer * math.sin(theta), z)
                if not plain.Solids()[0].isInside(start + direction * 0.01):
                    raise ValueError("Port host absent at this axial position")
                cut = cq.Solid.makeCylinder(
                    diameter / 2, depth + 0.002, start - direction * 0.001, direction
                )
                if f.thread:
                    entry = THREAD_DRILLS_MM.get(f.thread)
                    if entry is None or not f.entry_depth:
                        raise ValueError(
                            "Thread entry needs a supported drill table and cited entry depth"
                        )
                    entry_depth = value("entry_depth")
                    if not 0 < entry_depth < depth:
                        raise ValueError("Thread entry depth must be inside full drill path")
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
            elif f.kind == "chamfer":
                z, radius, width, angle = (
                    value("z"),
                    value("radius"),
                    value("width"),
                    value("angle", "degree"),
                )
                if not 0 < width < height / 2 or not 0 < angle < 90:
                    raise ValueError("Invalid chamfer")
                # Choose a circular edge by geometry, never an unstable OCCT face index.
                edges = [
                    e
                    for e in shape.Edges()
                    if e.geomType() == "CIRCLE"
                    and abs(e.Center().z - z) < 0.01
                    and abs(e.radius() - radius) < 0.01
                ]
                if len(edges) != 1:
                    raise ValueError("Chamfer edge is missing or ambiguous")
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
            proposed = shape.cut(cut)
            removed = shape.Volume() - proposed.Volume()
            if removed <= 1e-6:
                raise ValueError("Ineffective cut: feature removes no material")
            if not proposed.isValid() or len(proposed.Solids()) != 1:
                raise ValueError("Feature splits the part or creates an invalid solid")
            shape = proposed
            cutters[f.id] = cut
            receipt.update(status="BUILT", removed_volume_mm3=removed)
            checks.append(_check("G", f.id, "PASS", "Cut leaves one valid solid"))
            if f.kind in {"bore_slot", "chamfer"}:
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
