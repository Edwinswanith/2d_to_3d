"""Measure structure from freshly imported STEP. Never trust builder parameters as evidence.

Implemented scope: coaxial circular sections, axial hole patterns and straight cylindrical
passages into a coaxial cavity. Other measurement obligations explicitly remain UNKNOWN.
"""

import math
from pathlib import Path
from typing import Any

import cadquery as cq
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface  # type: ignore[import-untyped]


def _load(path: Path) -> cq.Shape:
    value = cq.importers.importStep(str(path)).val()
    if not isinstance(value, cq.Shape):
        raise ValueError("STEP contains no measurable B-rep")
    return value


def inspect_step(path: Path) -> dict[str, Any]:
    """Analytic B-rep gate for the turned gland-ring family, not arbitrary planar parts."""
    try:
        text = path.read_text(errors="replace").upper()
        if any(
            name in text
            for name in (
                "FACETED_BREP",
                "TRIANGULATED_FACE_SET",
                "TESSELLATED_SHELL",
                "POLYGONAL_FACE_SET",
            )
        ):
            return {
                "status": "FAIL",
                "detail": "Faceted/tessellated STEP is not an analytic gland-ring B-rep",
            }
        shape = _load(path)
        if not shape.isValid() or len(shape.Solids()) != 1:
            return {"status": "FAIL", "detail": "STEP must contain exactly one valid solid"}
        kinds = {face.geomType() for face in shape.Faces()}
        if kinds <= {"PLANE"}:
            return {
                "status": "FAIL",
                "detail": (
                    "All-planar faceted representation cannot represent the turned gland-ring body"
                ),
            }
        if not kinds.intersection({"CYLINDER", "CONE", "TORUS"}):
            return {
                "status": "UNKNOWN",
                "detail": "Analytic turned surfaces could not be established",
            }
        return {
            "status": "PASS",
            "detail": "One valid solid with analytic turned surfaces",
            "face_types": sorted(kinds),
        }
    except (ValueError, OSError, RuntimeError):
        return {"status": "FAIL", "detail": "STEP import or B-rep inspection failed"}


def _number(raw: Any) -> float:
    n = float(raw)
    if not math.isfinite(n) or abs(n) > 1_000_000:
        raise ValueError("Measurement input outside finite modelling bounds")
    return n


def _section_radii(shape: cq.Shape, z: float, tolerance: float) -> list[float]:
    cut = cq.Workplane("XY").add(shape).section(z).val()
    if not isinstance(cut, cq.Shape):
        return []
    radii = []
    for edge in cut.Edges():
        if edge.geomType() != "CIRCLE":
            continue
        circle = BRepAdaptor_Curve(edge.wrapped).Circle()
        location = circle.Location()
        if math.hypot(location.X(), location.Y()) <= tolerance:
            radii.append(circle.Radius())
    return sorted(set(round(r, 8) for r in radii))


def _axial_band(shape: cq.Shape, e: dict[str, Any], tol: float) -> tuple[str, str]:
    z0, z1, od = (_number(e[key]) for key in ("z0", "z1", "od"))
    bore = _number(e["idiameter"]) if "idiameter" in e else None
    if not (z1 > z0 and od > 0 and (bore is None or od > bore >= 0)):
        raise ValueError("Invalid section expectation")
    box = shape.BoundingBox()
    if z0 < box.zmin - tol or z1 > box.zmax + tol:
        return "FAIL", "Expected axial interval extends beyond the model"
    # Include every actual face boundary, so a short unexpected shoulder is not skipped.
    levels = {z0, z1}
    for face in shape.Faces():
        bounds = face.BoundingBox()
        levels.update(z for z in (bounds.zmin, bounds.zmax) if z0 < z < z1)
    ordered = sorted(levels)
    samples = [(a + b) / 2 for a, b in zip(ordered, ordered[1:], strict=False) if b - a > 1e-6]
    if not samples:
        return "UNKNOWN", "Axial interval is below measurement resolution"
    for z in samples:
        radii = _section_radii(shape, z, tol)
        if not radii:
            return "UNKNOWN", f"Circular section unresolved at z={z:g} mm"
        actual_od = max(radii) * 2
        actual_id = min(radii) * 2 if len(radii) > 1 else 0.0
        if abs(actual_od - od) > tol or (bore is not None and abs(actual_id - bore) > tol):
            return "FAIL", (
                f"At z={z:g} mm expected OD/bore {od:g}/{bore}, "
                f"measured {actual_od:g}/{actual_id:g} mm"
            )
    return "PASS", "All actual axial face intervals match the expected outer and bore diameters"


def _hole_pattern(shape: cq.Shape, e: dict[str, Any], tol: float) -> tuple[str, str]:
    count = int(e["count"])
    if count != _number(e["count"]) or not 1 <= count <= 200:
        raise ValueError("Invalid hole count")
    diameter, pcd, angle, z0, z1 = (_number(e[k]) for k in ("diameter", "pcd", "angle", "z0", "z1"))
    if not (diameter > 0 and pcd > diameter and z1 > z0):
        raise ValueError("Invalid hole-pattern dimensions")
    groups: list[dict[str, Any]] = []
    for face in shape.Faces():
        if face.geomType() != "CYLINDER":
            continue
        cylinder = BRepAdaptor_Surface(face.wrapped).Cylinder()
        direction = cylinder.Axis().Direction()
        origin = cylinder.Axis().Location()
        x, y = origin.X(), origin.Y()
        if abs(abs(direction.Z()) - 1) > 1e-7 or math.hypot(x, y) <= tol:
            continue
        if abs(cylinder.Radius() * 2 - diameter) > tol:
            continue
        bounds = face.BoundingBox()
        if bounds.zmax < z0 + tol or bounds.zmin > z1 - tol:
            continue
        # A cylindrical boss is not a hole. Test the axis within the specified span.
        middle = (max(z0, bounds.zmin) + min(z1, bounds.zmax)) / 2
        if shape.Solids()[0].isInside((x, y, middle), tol):
            continue
        group = next((g for g in groups if math.hypot(g["x"] - x, g["y"] - y) <= tol), None)
        if group is None:
            group = {"x": x, "y": y, "spans": []}
            groups.append(group)
        group["spans"].append((bounds.zmin, bounds.zmax))
    if len(groups) != count:
        return "FAIL", f"Expected {count} distinct holes; measured {len(groups)}"
    unmatched = list(groups)
    for i in range(count):
        theta = math.radians(angle + i * 360 / count)
        x, y = pcd / 2 * math.cos(theta), pcd / 2 * math.sin(theta)
        match = next((g for g in unmatched if math.hypot(g["x"] - x, g["y"] - y) <= tol), None)
        if match is None:
            return "FAIL", f"Hole {i + 1} is missing from its expected pitch-circle position"
        unmatched.remove(match)
        covered = z0
        for start, end in sorted(match["spans"]):
            if start > covered + tol:
                break
            covered = max(covered, end)
        if covered < z1 - tol:
            return "FAIL", f"Hole {i + 1} does not span its expected depth"
        # A hidden cap can split an apparently matching cylindrical wall.
        probe = cq.Solid.makeCylinder(diameter / 2 - tol, z1 - z0, cq.Vector(x, y, z0))
        if shape.intersect(probe).Volume() > max(1e-6, probe.Volume() * 1e-7):
            return "FAIL", f"Hole {i + 1} contains material in its required passage"
    return "PASS", "Distinct hole count, diameter, pitch circle, angles and through span match"


def _passage(shape: cq.Shape, e: dict[str, Any], tol: float) -> tuple[str, str]:
    start = cq.Vector(*[_number(n) for n in e["start"]])
    end = cq.Vector(*[_number(n) for n in e["end"]])
    diameter = _number(e["diameter"])
    direction = end - start
    if direction.Length <= tol or diameter <= 2 * tol:
        raise ValueError("Invalid passage expectation")
    direction = direction.normalized()
    entry_radii = _section_radii(shape, start.z, tol)
    if not entry_radii or abs(math.hypot(start.x, start.y) - max(entry_radii)) > tol:
        return "FAIL", "Radial port entry is not on the measured outer host face"
    walls = []
    for face in shape.Faces():
        if face.geomType() != "CYLINDER":
            continue
        cylinder = BRepAdaptor_Surface(face.wrapped).Cylinder()
        axis = cylinder.Axis()
        d = cq.Vector(axis.Direction().X(), axis.Direction().Y(), axis.Direction().Z())
        origin = cq.Vector(axis.Location().X(), axis.Location().Y(), axis.Location().Z())
        if (
            abs(cylinder.Radius() * 2 - diameter) <= tol
            and d.cross(direction).Length <= 1e-7
            and (start - origin).cross(direction).Length <= tol
        ):
            walls.append(face)
    if not walls:
        return "FAIL", "No matching drilled cylindrical wall; empty space is not a port"
    probe = cq.Solid.makeCylinder(diameter / 2 - tol, (end - start).Length, start, direction)
    if shape.intersect(probe).Volume() > max(1e-6, probe.Volume() * 1e-7):
        return (
            "FAIL",
            "Required drill corridor still contains material; drill stops short or is misplaced",
        )
    past = end + direction.multiply(tol * 2)
    radii = _section_radii(shape, past.z, tol)
    if len(radii) < 2:
        return "UNKNOWN", "Intended receiving cavity cannot be identified independently"
    if math.hypot(past.x, past.y) >= min(radii) - tol:
        return "FAIL", "Drill endpoint does not enter the independently measured coaxial cavity"
    return "PASS", "Cylindrical corridor is clear and its endpoint enters the coaxial cavity"


def check_structure(path: Path, expectations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    shape = _load(path)
    results = []
    for expected in expectations:
        status, detail = "UNKNOWN", "Measurement type is not implemented"
        try:
            tolerance = _number(expected.get("tolerance", 0.01))
            if not 0 < tolerance <= 1:
                raise ValueError("Declare a positive numerical tolerance no larger than 1 mm")
            if not shape.isValid() or len(shape.Solids()) != 1:
                status, detail = "FAIL", "Reimported STEP is not one valid solid"
            else:
                handlers = {
                    "axial_band": _axial_band,
                    "hole_pattern": _hole_pattern,
                    "passage": _passage,
                }
                handler = handlers.get(expected.get("kind", ""))
                if handler:
                    status, detail = handler(shape, expected, tolerance)
        except (ValueError, KeyError, TypeError, RuntimeError) as error:
            status, detail = "UNKNOWN", f"Unmeasurable expectation: {type(error).__name__}"
        results.append(
            {
                "layer": "H2",
                "subject": expected.get("id", "unidentified"),
                "status": status,
                "detail": detail,
            }
        )
    return results
