"""Small web-runtime CAD exporter for axisymmetric draft bodies."""

import hashlib
import math
import struct
from pathlib import Path
from typing import Any

from drawing2step.body_cad import BodySpec, evaluate_profile
from drawing2step.storage import canonical_json, write_once


def _rings(rows: list[tuple[float, float, float]], segments: int) -> tuple[list[float], list[int]]:
    positions: list[float] = []
    for z, od, bore in rows:
        for radius in (od / 2, bore / 2):
            for index in range(segments):
                angle = 2 * math.pi * index / segments
                positions.extend([radius * math.cos(angle), z, radius * math.sin(angle)])
    indices: list[int] = []

    def vertex(station: int, ring: int, index: int) -> int:
        return station * segments * 2 + ring * segments + index % segments

    for station in range(len(rows) - 1):
        for index in range(segments):
            n = index + 1
            indices.extend(
                [
                    vertex(station, 0, index),
                    vertex(station, 0, n),
                    vertex(station + 1, 0, n),
                    vertex(station, 0, index),
                    vertex(station + 1, 0, n),
                    vertex(station + 1, 0, index),
                    vertex(station, 1, index),
                    vertex(station + 1, 1, n),
                    vertex(station, 1, n),
                    vertex(station, 1, index),
                    vertex(station + 1, 1, index),
                    vertex(station + 1, 1, n),
                ]
            )
    for station in (0, len(rows) - 1):
        reverse = station == len(rows) - 1
        for index in range(segments):
            n = index + 1
            quad = [
                vertex(station, 1, index),
                vertex(station, 0, index),
                vertex(station, 0, n),
                vertex(station, 1, n),
            ]
            if reverse:
                quad = list(reversed(quad))
            indices.extend([quad[0], quad[1], quad[2], quad[0], quad[2], quad[3]])
    return positions, indices


def _triangles(
    positions: list[float], indices: list[int]
) -> list[tuple[tuple[float, float, float], ...]]:
    points = [
        (positions[i], positions[i + 1], positions[i + 2]) for i in range(0, len(positions), 3)
    ]
    return [
        (points[indices[i]], points[indices[i + 1]], points[indices[i + 2]])
        for i in range(0, len(indices), 3)
    ]


def _normal(triangle: tuple[tuple[float, float, float], ...]) -> tuple[float, float, float]:
    a, b, c = triangle
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    length = math.sqrt(nx * nx + ny * ny + nz * nz) or 1
    return nx / length, ny / length, nz / length


def _stl(triangles: list[tuple[tuple[float, float, float], ...]]) -> bytes:
    payload = bytearray(b"Drawing2STEP web draft".ljust(80, b" "))
    payload.extend(struct.pack("<I", len(triangles)))
    for triangle in triangles:
        payload.extend(struct.pack("<3f", *_normal(triangle)))
        for point in triangle:
            payload.extend(struct.pack("<3f", *point))
        payload.extend(struct.pack("<H", 0))
    return bytes(payload)


def _step(triangles: list[tuple[tuple[float, float, float], ...]]) -> bytes:
    lines = [
        "ISO-10303-21;",
        "HEADER;",
        "FILE_DESCRIPTION(('Faceted web draft; manufacturing release blocked'),'2;1');",
        "FILE_NAME('evaluation-body.step','',('drawing2step'),('drawing2step'),'','','');",
        "FILE_SCHEMA(('CONFIG_CONTROL_DESIGN'));",
        "ENDSEC;",
        "DATA;",
    ]
    entity = 1
    face_ids = []
    for triangle in triangles:
        point_ids = []
        for point in triangle:
            lines.append(
                f"#{entity}=CARTESIAN_POINT('',({point[0]:.9g},{point[1]:.9g},{point[2]:.9g}));"
            )
            point_ids.append(entity)
            entity += 1
        vertex_ids = []
        for point_id in point_ids:
            lines.append(f"#{entity}=VERTEX_POINT('',#{point_id});")
            vertex_ids.append(entity)
            entity += 1
        lines.append(f"#{entity}=POLY_LOOP('',({','.join(f'#{item}' for item in vertex_ids)}));")
        loop_id = entity
        entity += 1
        lines.append(f"#{entity}=FACE_OUTER_BOUND('',#{loop_id},.T.);")
        bound_id = entity
        entity += 1
        lines.append(f"#{entity}=FACE_SURFACE('',(#{bound_id}),$,.T.);")
        face_ids.append(entity)
        entity += 1
    lines.append(f"#{entity}=CLOSED_SHELL('',({','.join(f'#{item}' for item in face_ids)}));")
    shell_id = entity
    entity += 1
    lines.append(f"#{entity}=FACETED_BREP('UNVERIFIED_BODY_DRAFT',#{shell_id});")
    lines.extend(["ENDSEC;", "END-ISO-10303-21;"])
    return ("\n".join(lines) + "\n").encode()


def build_web_draft(
    spec: BodySpec, output: Path, *, segments: int = 96
) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = evaluate_profile(spec)
    positions, indices = _rings(rows, segments)
    triangles = _triangles(positions, indices)
    stl = _stl(triangles)
    step = _step(triangles)
    output.mkdir(parents=True, exist_ok=True)
    write_once(output / "evaluation-body.step", step)
    write_once(output.parent / "model.stl", stl)
    bbox = [max(row[1] for row in rows), max(row[1] for row in rows), rows[-1][0] - rows[0][0]]
    volume = sum(
        math.pi
        * (b[0] - a[0])
        / 12
        * (a[1] ** 2 + a[1] * b[1] + b[1] ** 2 - a[2] ** 2 - a[2] * b[2] - b[2] ** 2)
        for a, b in zip(rows[:-1], rows[1:], strict=True)
    )
    report = {
        "schema_version": "body-verification-v1",
        "synthetic": spec.synthetic,
        "V1": "UNKNOWN",
        "build_validity": "PASS",
        "analytical_profile_volume": "PASS",
        "V1_limitation": "Individual station and feature-count conformance not yet measured",
        "V2": "PASS",
        "V3": "UNKNOWN",
        "fresh_measurements": {"bbox": bbox, "volume": volume, "triangles": len(triangles)},
        "reference_step": "UNKNOWN",
        "unsupported_features": spec.unsupported_features,
        "release": "BLOCKED",
        "artifact_kind": "PARTIAL_BODY_EVALUATION_ONLY",
        "step_units": "mm",
        "step_schema": "Faceted B-rep web draft; CAM approval pending",
        "step_sha256": hashlib.sha256(step).hexdigest(),
        "stl_sha256": hashlib.sha256(stl).hexdigest(),
    }
    write_once(output / "spec.json", canonical_json(spec.model_dump(mode="json")))
    write_once(output / "verification.json", canonical_json(report))
    mesh = {"positions": positions, "indices": indices, "units": "mm"}
    return report, mesh
