"""Accuracy scoring of a recorded run against a transcribed drawing truth (layer I evidence).

A truth file lists what the sheet prints: body envelope, every feature with its printed
numbers, and every printed dimension. Scoring separates three failure classes that the
run's own checks cannot: a printed dimension never read (`unread`), read but never used by
the draft (`uncited`), and a feature built with the wrong numbers (`partial`). Nothing here
grants acceptance; a transcription is engineer-reviewable evidence, not a release gate.
"""

import json
import math
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from drawing2step.models import Contract
from drawing2step.revb import Requirement
from drawing2step.revb_model import (
    CAPABILITIES,
    DraftSpec,
    enrich_ledger,
    numeric_value,
    thread_drill_mm,
)
from drawing2step.revb_proposal import cited_ids

Unit = Literal["mm", "cm", "in"]
SCALE = {"mm": 1.0, "cm": 10.0, "in": 25.4}
# A tap drill and a plain hole are the same physical class for matching purposes.
MATCH_CLASSES = {"hole_pattern": {"hole_pattern", "tapped_hole"}, "tapped_hole": {"tapped_hole"}}
RELATIVE_TOLERANCE = 0.02
ABSOLUTE_TOLERANCE_MM = 0.15


class TruthFeature(Contract):
    id: str
    kind: str
    count: int = Field(default=1, ge=1)
    diameter: float | None = None
    diameter_unit: Unit | None = None
    pcd: float | None = None
    depth: float | None = None
    through: bool = False
    angle: float | None = None
    thread: str | None = None
    entry_diameter: float | None = None
    entry_depth: float | None = None
    width: float | None = None
    tilt: float | None = None


class PrintedDimension(Contract):
    kind: Literal["diameter", "linear"]
    value: float


class TruthBody(Contract):
    outer_diameter: float
    length: float


class DrawingTruth(Contract):
    drawing_number: str
    aliases: list[str] = Field(default_factory=list)  # mis-read title-block spellings
    unit: Unit
    transcribed_by: str = ""
    body: TruthBody
    features: list[TruthFeature]
    printed_dimensions: list[PrintedDimension] = Field(default_factory=list)

    def mm(self, value: float | None, unit: Unit | None = None) -> float | None:
        return None if value is None else value * SCALE[unit or self.unit]


def load_truth(path: Path) -> DrawingTruth:
    return DrawingTruth.model_validate_json(path.read_text())


def _close(actual: float | None, expected: float | None) -> bool | None:
    if actual is None or expected is None:
        return None
    return abs(actual - expected) <= max(RELATIVE_TOLERANCE * abs(expected), ABSOLUTE_TOLERANCE_MM)


def _normalised_thread(designation: str | None) -> str | None:
    if not designation:
        return None
    drill = thread_drill_mm(designation)
    return f"{drill:.4f}" if drill is not None else designation.upper().replace(" ", "")


def resolved_features(
    spec: DraftSpec, requirements: list[Requirement], report: dict[str, Any]
) -> list[dict[str, Any]]:
    """Every built feature with its numbers evaluated to mm/degrees/counts."""
    ledger = {r.id: r for r in requirements}
    assumptions = {a.id: a for a in spec.assumptions}
    built = {f["id"] for f in report["features"] if f["status"] == "BUILT"}
    kinds = {"angle": "degree", "tilt": "degree", "count": "count"}
    rows = []
    for feature in spec.features:
        if feature.id not in built:
            continue
        row: dict[str, Any] = {"id": feature.id, "kind": feature.kind, "thread": feature.thread}
        for name in (
            "diameter",
            "pcd",
            "depth",
            "angle",
            "count",
            "entry_diameter",
            "entry_depth",
            "width",
            "tilt",
            "z",
        ):
            number = getattr(feature, name)
            try:
                row[name] = (
                    numeric_value(number, ledger, assumptions, kinds.get(name, "length"))
                    if number is not None
                    else None
                )
            except ValueError:
                row[name] = None
        if feature.kind == "tapped_hole" and row["diameter"] is None:
            row["diameter"] = thread_drill_mm(feature.thread)
        if feature.kind == "port" and row.get("entry_diameter") is None and feature.thread:
            row["entry_diameter"] = thread_drill_mm(feature.thread)
        row["count"] = int(row["count"]) if row.get("count") is not None else 1
        rows.append(row)
    return rows


def _compare(truth: DrawingTruth, expected: TruthFeature, candidate: dict[str, Any]) -> list[str]:
    """Named deviations between a printed feature and a built candidate."""
    deviations = []
    pairs = {
        "diameter": truth.mm(expected.diameter, expected.diameter_unit),
        "pcd": truth.mm(expected.pcd),
        "depth": truth.mm(expected.depth),
        "entry_diameter": truth.mm(expected.entry_diameter),
        "entry_depth": truth.mm(expected.entry_depth),
        "width": truth.mm(expected.width),
    }
    for name, printed in pairs.items():
        verdict = _close(candidate.get(name), printed)
        if verdict is False:
            deviations.append(f"{name} built {candidate[name]:.3f} mm, printed {printed:.3f} mm")
    for name in ("angle", "tilt"):
        printed = getattr(expected, name)
        actual = candidate.get(name)
        if printed is not None and actual is not None and abs(actual - printed) % 360 > 0.5:
            deviations.append(f"{name} built {actual:.1f}°, printed {printed:.1f}°")
    if expected.thread and _normalised_thread(expected.thread) != _normalised_thread(
        candidate.get("thread")
    ):
        deviations.append(f"thread built {candidate.get('thread')!r}, printed {expected.thread!r}")
    return deviations


def _candidate_rank(truth: DrawingTruth, expected: TruthFeature, candidate: dict[str, Any]) -> int:
    """Higher is a better match: shared identity attributes before secondary ones."""
    rank = 0
    if expected.thread and _normalised_thread(expected.thread) == _normalised_thread(
        candidate.get("thread")
    ):
        rank += 4
    if _close(candidate.get("diameter"), truth.mm(expected.diameter, expected.diameter_unit)):
        rank += 3
    if _close(candidate.get("pcd"), truth.mm(expected.pcd)):
        rank += 2
    if candidate.get("count") == expected.count:
        rank += 1
    # Edge features carry no identity beyond their kind: width or angle agreement ranks them.
    if _close(candidate.get("width"), truth.mm(expected.width)):
        rank += 1
    if (
        expected.angle is not None
        and candidate.get("angle") is not None
        and abs(candidate["angle"] - expected.angle) % 360 <= 0.5
    ):
        rank += 1
    return rank


def match_features(truth: DrawingTruth, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Assign built features to printed ones; ports match one built port per printed instance."""
    remaining = list(candidates)
    results = []
    for expected in truth.features:
        if expected.kind not in CAPABILITIES or expected.kind == "marking":
            results.append(
                {
                    "id": expected.id,
                    "kind": expected.kind,
                    "status": "unsupported",
                    "matched": [],
                    "deviations": [f"builder has no {expected.kind} capability"],
                }
            )
            continue
        classes = MATCH_CLASSES.get(expected.kind, {expected.kind})
        matched: list[dict[str, Any]] = []
        deviations: list[str] = []
        instances = 0
        # Built features accumulate instances until the printed count is reached: a pattern
        # carries its count; ports, chamfers and spotfaces are one instance per feature.
        while instances < expected.count:
            pool = [c for c in remaining if c["kind"] in classes]
            if not pool:
                break
            best = max(pool, key=lambda c: _candidate_rank(truth, expected, c))
            if _candidate_rank(truth, expected, best) == 0:
                break
            remaining.remove(best)
            matched.append(best)
            instances += best.get("count") or 1
            deviations.extend(f"{best['id']}: {d}" for d in _compare(truth, expected, best))
        if not matched:
            status = "missing"
        elif instances != expected.count:
            status = "partial"
            deviations.append(f"built {instances} of {expected.count} printed instances")
        else:
            status = "partial" if deviations else "captured"
        results.append(
            {
                "id": expected.id,
                "kind": expected.kind,
                "status": status,
                "matched": [m["id"] for m in matched],
                "deviations": deviations,
            }
        )
    return results


def verify_through(
    model_step: Path,
    plain_step: Path,
    truth: DrawingTruth,
    expected: TruthFeature,
    candidate: dict[str, Any],
) -> str:
    """A THRU hole leaves void wherever the plain body has material along its axis.

    Sampled on the reimported STEP at each printed hole position (truth pitch circle, the
    built pattern's phase): a thin flange gives a short hole that is still through; a hole
    stopping inside material is `wrong`. Blind patterns stay `unverified`.
    """
    if not expected.through or candidate.get("angle") is None or candidate.get("diameter") is None:
        return "unverified"
    import cadquery as cq  # local: keep the scorer importable without CAD for reading-only use

    def solid(path: Path) -> cq.Solid:
        shape = cq.importers.importStep(str(path)).val()
        assert isinstance(shape, cq.Shape)
        return shape.Solids()[0]

    model, plain = solid(model_step), solid(plain_step)
    box = plain.BoundingBox()
    pitch = (truth.mm(expected.pcd) or candidate.get("pcd") or 0.0) / 2
    offset = 0.35 * candidate["diameter"]
    steps = max(20, int((box.zmax - box.zmin) / 0.5))
    for i in range(expected.count):
        theta = math.radians(candidate["angle"] + i * 360 / expected.count)
        centre = (pitch * math.cos(theta), pitch * math.sin(theta))
        for k in range(steps + 1):
            z = box.zmin + (box.zmax - box.zmin) * k / steps
            for dx, dy in ((0, 0), (offset, 0), (-offset, 0), (0, offset), (0, -offset)):
                point = cq.Vector(centre[0] + dx, centre[1] + dy, z)
                if plain.isInside(point) and model.isInside(point):
                    return "wrong"
    return "verified"


def dimension_coverage(
    truth: DrawingTruth,
    requirements: list[Requirement],
    cited: set[str],
    assumed_mm: list[tuple[float, float]] | None = None,
) -> list[dict[str, Any]]:
    """Each printed dimension is cited, used through a registered assumption (a nominal
    chosen from a limit pair), read but uncited, or never read at all.

    ``assumed_mm`` holds (value, half-window) pairs for length assumptions: a printed limit
    counts as used when an assumption lies within that limit's own tolerance window.
    """
    rows = []
    for printed in truth.printed_dimensions:
        target = truth.mm(printed.value)
        assert target is not None
        readings = [
            r
            for r in requirements
            if r.value is not None
            and r.unit in SCALE
            and r.kind in {"linear", "diameter", "radius"}
            and abs(float(r.value) * SCALE[r.unit] - target) <= max(0.005 * abs(target), 0.01)
        ]
        if not readings:
            status = "unread"
        elif any(r.id in cited for r in readings):
            status = "cited"
        elif any(
            abs(value - target) <= max(window, 0.005 * abs(target), 0.01)
            for value, window in assumed_mm or []
        ):
            status = "assumed"
        else:
            status = "uncited"
        rows.append(
            {
                "kind": printed.kind,
                "value": printed.value,
                "status": status,
                "readings": [r.id for r in readings],
            }
        )
    return rows


def assumed_lengths_mm(spec: DraftSpec) -> list[tuple[float, float]]:
    """Length assumptions as (mm, window): a nominal from a limit pair sits within ~1% of it."""
    return [
        (float(a.value) * SCALE[a.unit], 0.01 * abs(float(a.value) * SCALE[a.unit]))
        for a in spec.assumptions
        if a.unit in SCALE
    ]


def _run_ledger(run_dir: Path, model_dir: Path | None) -> list[Requirement]:
    if model_dir is not None and (model_dir / "ledger.json").exists():
        return [
            Requirement.model_validate(r)
            for r in json.loads((model_dir / "ledger.json").read_text())
        ]
    if not (run_dir / "audit.json").exists():
        return []  # the run stopped before reading anything
    audit = json.loads((run_dir / "audit.json").read_text())
    rows = [Requirement.model_validate(r) for r in audit["requirements"]]
    return enrich_ledger(rows, audit["context"]["unit"])


def score_run(run_dir: Path, truth: DrawingTruth) -> dict[str, Any]:
    """Score one recorded run; writes nothing."""
    status = json.loads((run_dir / "status.json").read_text())
    folder = status.get("model_folder")
    model_dir = run_dir / "models" / folder if folder else None
    requirements = _run_ledger(run_dir, model_dir)
    result: dict[str, Any] = {
        "run": run_dir.name,
        "drawing_number": truth.drawing_number,
        "model_available": bool(model_dir and (model_dir / "model.step").exists()),
        "status": status.get("status"),
        "message": status.get("message"),
    }
    if not result["model_available"]:
        result["features"] = [
            {"id": f.id, "kind": f.kind, "status": "missing", "matched": [], "deviations": []}
            for f in truth.features
        ]
        result["body"] = None
        result["dimensions"] = dimension_coverage(truth, requirements, set())
        result["checks"] = {"FAIL": 0, "UNKNOWN": 0}
        result["summary"] = _summary(result)
        return result
    assert model_dir is not None
    spec = DraftSpec.model_validate_json((model_dir / "spec.json").read_text())
    report = json.loads((model_dir / "model-report.json").read_text())
    bbox = report["measurements"]["bbox"]
    od_ok = _close(max(bbox[0], bbox[1]), truth.mm(truth.body.outer_diameter))
    length_ok = _close(bbox[2], truth.mm(truth.body.length))
    result["body"] = {
        "outer_diameter_mm": max(bbox[0], bbox[1]),
        "length_mm": bbox[2],
        "outer_diameter_ok": bool(od_ok),
        "length_ok": bool(length_ok),
    }
    candidates = resolved_features(spec, requirements, report)
    by_id = {c["id"]: c for c in candidates}
    features = match_features(truth, candidates)
    for expected, row in zip(truth.features, features, strict=True):
        row["placement"] = "unverified"
        if row["status"] in {"captured", "partial"} and row["matched"]:
            row["placement"] = verify_through(
                model_dir / "model.step",
                model_dir / "plain-body.step",
                truth,
                expected,
                by_id[row["matched"][0]],
            )
    result["features"] = features
    result["dimensions"] = dimension_coverage(
        truth, requirements, cited_ids(spec), assumed_lengths_mm(spec)
    )
    checks = status.get("checks") or json.loads((model_dir / "checks.json").read_text())
    result["checks"] = {
        "FAIL": sum(c["status"] == "FAIL" for c in checks),
        "UNKNOWN": sum(c["status"] == "UNKNOWN" for c in checks),
    }
    result["summary"] = _summary(result)
    return result


def _summary(result: dict[str, Any]) -> dict[str, Any]:
    features = result["features"]
    counts = {
        s: sum(f["status"] == s for f in features)
        for s in ("captured", "partial", "missing", "unsupported")
    }
    dims = result["dimensions"]
    dim_counts = {
        s: sum(d["status"] == s for d in dims) for s in ("cited", "assumed", "uncited", "unread")
    }
    total = len(features)
    body = result.get("body") or {}
    return {
        "features_total": total,
        **counts,
        "feature_recall": round(counts["captured"] / total, 3) if total else None,
        "feature_recall_lenient": (
            round((counts["captured"] + counts["partial"]) / total, 3) if total else None
        ),
        "placement_verified": sum(f.get("placement") == "verified" for f in features),
        "placement_wrong": sum(f.get("placement") == "wrong" for f in features),
        "dimensions_total": len(dims),
        **dim_counts,
        "dimension_citation": (
            round((dim_counts["cited"] + dim_counts["assumed"]) / len(dims), 3) if dims else None
        ),
        "body_ok": bool(body.get("outer_diameter_ok") and body.get("length_ok")),
        "failed_checks": result["checks"]["FAIL"],
    }


def score_runs(run_dirs: list[Path], truth_dir: Path) -> list[dict[str, Any]]:
    """Score every run whose drawing has a truth file; unknown drawings are skipped.

    A run is matched by its recorded drawing number, a declared alias, or its folder name
    prefix (``<drawing>-v3``), so a mis-read title block never silently drops a sheet.
    """
    truths = [load_truth(p) for p in sorted(truth_dir.glob("*.json"))]
    rows = []
    for run_dir in run_dirs:
        if not (run_dir / "status.json").exists():
            continue
        number = json.loads((run_dir / "status.json").read_text()).get("drawing_number")
        truth = next(
            (
                t
                for t in truths
                if number in {t.drawing_number, *t.aliases}
                or run_dir.name.startswith(f"{t.drawing_number}-")
            ),
            None,
        )
        if truth is None:
            continue
        rows.append(score_run(run_dir, truth))
    return rows


def scoreboard(rows: list[dict[str, Any]]) -> str:
    """Compact per-run table for the terminal."""
    lines = [
        f"{'run':<20} {'body':<6} {'features cap/part/miss/unsup':<30} "
        f"{'dims cited/assumed/uncited/unread':<34} {'placed ok/wrong':<16} FAIL",
    ]
    for row in rows:
        s = row["summary"]
        body = "ok" if s["body_ok"] else ("none" if not row["model_available"] else "WRONG")
        features = (
            f"{s['captured']}/{s['partial']}/{s['missing']}/{s['unsupported']} "
            f"of {s['features_total']}"
        )
        dims = (
            f"{s['cited']}/{s['assumed']}/{s['uncited']}/{s['unread']} of {s['dimensions_total']}"
        )
        placed = f"{s['placement_verified']}/{s['placement_wrong']}"
        lines.append(
            f"{row['run']:<20} {body:<6} {features:<30} {dims:<34} {placed:<16} "
            f"{s['failed_checks']}"
        )
    return "\n".join(lines)
