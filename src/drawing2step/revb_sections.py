"""Independent B-rep section topology; no pixel or scaled-length comparison."""

import html
import math
from pathlib import Path
from typing import Any

import cadquery as cq


def _canonical(tokens: list[str]) -> list[str]:
    if not tokens:
        return []
    reverse = [
        t.replace("LEFT", "tmp").replace("RIGHT", "LEFT").replace("tmp", "RIGHT")
        for t in reversed(tokens)
    ]
    variants = [row[i:] + row[:i] for row in (tokens, reverse) for i in range(len(row))]
    return min(variants)


def section_topology(path: Path, plane: str, offset: float = 0) -> dict[str, Any]:
    if plane not in {"XY", "XZ", "YZ"} or not math.isfinite(offset) or abs(offset) > 2000:
        raise ValueError("Invalid section plane or offset")
    shape = cq.importers.importStep(str(path)).val()
    if not isinstance(shape, cq.Shape) or not shape.isValid() or len(shape.Solids()) != 1:
        raise ValueError("Section requires one valid reimported solid")
    section = cq.Workplane(plane).add(shape).section(offset).val()
    if not isinstance(section, cq.Shape):
        raise ValueError("No section geometry")
    transform = cq.Plane.named(plane)

    def local(point: cq.Vector) -> cq.Vector:
        value: cq.Vector = transform.toLocalCoords(point)  # type: ignore[no-untyped-call]
        return value

    loops, paths = [], []
    for wire in section.Wires():
        edges = list(wire)
        if not edges:
            continue
        if any(e.geomType() != "LINE" for e in edges):
            # Curved adjacency is retained; no fabricated corner-turn classification.
            loops.append(_canonical([e.geomType() for e in edges]))
        else:
            pairs = [(e.startPoint(), e.endPoint()) for e in edges]
            if len(pairs) > 1:
                a, b = pairs[0]
                if min((a - v).Length for v in pairs[1]) < min((b - v).Length for v in pairs[1]):
                    pairs[0] = b, a
                for i in range(1, len(pairs)):
                    a, b = pairs[i]
                    if (pairs[i - 1][1] - b).Length < (pairs[i - 1][1] - a).Length:
                        pairs[i] = b, a
            vectors = [local(b) - local(a) for a, b in pairs]
            tokens = []
            for i, v in enumerate(vectors):
                prev = vectors[i - 1]
                cross = prev.x * v.y - prev.y * v.x
                tokens.append(
                    "LINE:" + ("LEFT" if cross > 1e-8 else "RIGHT" if cross < -1e-8 else "STRAIGHT")
                )
            loops.append(_canonical(tokens))
        for edge in edges:
            points, _ = edge.sample(2 if edge.geomType() == "LINE" else 48)
            paths.append([[float(p.x), float(p.y)] for p in (local(v) for v in points)])
    return {
        "plane": plane,
        "offset_mm": offset,
        "closed_loops": sorted(loops),
        "paths": paths,
        "scope": "Ordered boundary edge types and line-turn adjacency; dimensions checked by H2",
    }


def compare_section(path: Path, reference: dict[str, Any]) -> dict[str, Any]:
    measured = section_topology(path, reference["plane"], float(reference.get("offset_mm", 0)))
    expected = reference.get("closed_loops")
    if not expected or not reference.get("source") or not reference.get("reviewer"):
        status, detail = "UNKNOWN", "Drawing section topology has no reviewed reference annotation"
    else:
        normalized = sorted(_canonical(list(loop)) for loop in expected)
        status = "PASS" if normalized == measured["closed_loops"] else "FAIL"
        detail = (
            "Section boundary types and adjacency match"
            if status == "PASS"
            else "Section boundary count/order/adjacency differ"
        )
    return {
        "layer": "H3",
        "subject": reference.get("id", reference["plane"]),
        "status": status,
        "detail": detail,
        "measurement": measured,
    }


def section_svg(measured: dict[str, Any]) -> str:
    points = [p for path in measured["paths"] for p in path]
    if not points:
        return (
            '<svg xmlns="http://www.w3.org/2000/svg"><text x="10" y="20">Empty section</text></svg>'
        )
    xmin, xmax = min(p[0] for p in points), max(p[0] for p in points)
    ymin, ymax = min(p[1] for p in points), max(p[1] for p in points)
    width, height = max(xmax - xmin, 1), max(ymax - ymin, 1)
    pad = max(width, height) * 0.04
    commands = []
    for points in measured["paths"]:
        commands.append("M " + " L ".join(f"{x:.6f},{-y:.6f}" for x, y in points))
    title = html.escape(
        f"{measured['plane']} section at {measured['offset_mm']:g} mm; reimported STEP"
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="{xmin - pad} {-ymax - pad} {width + 2 * pad} {height + 2 * pad}" role="img">'
        f'<title>{title}</title><rect x="{xmin - pad}" y="{-ymax - pad}" '
        f'width="{width + 2 * pad}" height="{height + 2 * pad}" fill="#f4f7f1"/>'
        f'<path d="{" ".join(commands)}" stroke="#214c3e" '
        f'stroke-width="{pad / 12}" fill="none"/></svg>'
    )
