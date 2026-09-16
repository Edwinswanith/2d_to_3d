"""Read-only inventory and evidence-backed prerequisite checklist.

This checks declared records and file integrity, not legal sufficiency or cloud settings.
No original drawing is rendered, parsed, copied, or uploaded.
"""

import hashlib
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from drawing2step.models import Contract, Nonnegative


class Pair(Contract):
    id: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    group: str = Field(min_length=1)
    drawing: str = Field(min_length=1)
    step: str = Field(min_length=1)
    split: Literal["development", "held_out"]
    quality: Literal["clean", "scan"]
    drawing_number: str | None = None
    revision: str | None = None
    synthetic: bool = False
    drawing_sha256: str | None = None
    step_sha256: str | None = None


class Permission(Contract):
    owner: str = Field(min_length=1)
    document: str = Field(min_length=1)
    confirmed_by: str = Field(min_length=1)
    cloud_processing_allowed: bool = False
    drawing_sha256: tuple[str, ...] = ()


class Baseline(Contract):
    pair_id: str = Field(min_length=1)
    engineer: str = Field(min_length=1)
    minutes: Nonnegative = Field(gt=0)


class Policies(Contract):
    geometry_value: Literal["nominal", "mid_tolerance"] = "nominal"
    step_units: Literal["mm", "in"] = "mm"
    step_schema: Literal["AP242", "AP214", "AP203"] = "AP242"
    threads: Literal["tap_drill_with_notes", "modeled", "cosmetic"] = "tap_drill_with_notes"
    approved_by: str | None = None
    target_cam: str | None = None
    cam_import_evidence: str | None = None


class Environment(Contract):
    region: str | None = None
    retention_days: int | None = Field(default=None, ge=0)
    model_versions_verified: bool = False
    ocr_processor_version: str | None = None
    configuration_evidence: str | None = None


class Prerequisites(Contract):
    schema_version: Literal["prerequisites-v1"] = "prerequisites-v1"
    pairs: tuple[Pair, ...] = ()
    permissions: tuple[Permission, ...] = ()
    baseline: tuple[Baseline, ...] = ()
    gland_ring_workload_share: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    ground_truth_reviewer: str | None = None
    release_reviewers: tuple[str, ...] = ()
    policies: Policies = Policies()
    environment: Environment = Environment()


def template() -> dict[str, Any]:
    return Prerequisites().model_dump(mode="json")


def load_manifest(path: Path) -> dict[str, Any]:
    return Prerequisites.model_validate_json(path.read_bytes()).model_dump(mode="json")


def _file(base: Path, filename: str) -> Path:
    path = (base / filename).resolve()
    if not path.is_file():
        raise ValueError(f"Evidence file missing: {filename}")
    if path.stat().st_size == 0:
        raise ValueError(f"Evidence file empty: {filename}")
    return path


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inventory(raw: dict[str, Any], base: Path) -> dict[str, Any]:
    """Hash explicit pairs and reject duplicate sources and cross-split group leakage."""
    manifest = Prerequisites.model_validate(raw)
    groups: dict[str, str] = {}
    seen_ids: set[str] = set()
    seen_drawings: set[str] = set()
    pairs: list[dict[str, Any]] = []
    for pair in manifest.pairs:
        if pair.id in seen_ids:
            raise ValueError(f"Duplicate pair ID: {pair.id}")
        seen_ids.add(pair.id)
        if pair.group in groups and groups[pair.group] != pair.split:
            raise ValueError(f"Development/held-out group leakage: {pair.group}")
        groups[pair.group] = pair.split
        drawing = _file(base, pair.drawing)
        step = _file(base, pair.step)
        if drawing.suffix.lower() not in {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}:
            raise ValueError(f"Unsupported drawing extension: {drawing.suffix}")
        if step.suffix.lower() not in {".step", ".stp"}:
            raise ValueError("Paired model must be a STEP file")
        drawing_hash, step_hash = _digest(drawing), _digest(step)
        if drawing_hash in seen_drawings:
            raise ValueError("Duplicate drawing content; dataset counts require distinct originals")
        seen_drawings.add(drawing_hash)
        for recorded, actual in (
            (pair.drawing_sha256, drawing_hash),
            (pair.step_sha256, step_hash),
        ):
            if recorded is not None and recorded != actual:
                raise ValueError(f"Recorded hash mismatch for pair {pair.id}")
        updated = pair.model_dump(mode="json")
        updated.update(drawing_sha256=drawing_hash, step_sha256=step_hash)
        pairs.append(updated)
    result = manifest.model_dump(mode="json")
    result["pairs"] = pairs
    return result


def assess(raw: dict[str, Any], base: Path) -> dict[str, Any]:
    manifest = Prerequisites.model_validate(raw)
    blockers: list[str] = []
    verified: list[dict[str, Any]] = []
    try:
        verified = inventory(raw, base)["pairs"]
    except ValueError as error:
        blockers.append(str(error))
    real = [p for p in verified if not p["synthetic"]]
    if len(real) < 30:
        blockers.append(
            f"Collect at least 30 distinct historical drawing/STEP pairs ({len(real)}/30)"
        )
    for split, minimum in (("development", 20), ("held_out", 10)):
        if sum(p["split"] == split for p in real) < minimum:
            blockers.append(f"Assign at least {minimum} historical pairs to {split}")
    permitted: set[tuple[str, str]] = set()
    permission_evidence: list[dict[str, str]] = []
    for permission in manifest.permissions:
        if not permission.cloud_processing_allowed:
            continue
        try:
            document = _file(base, permission.document)
        except ValueError as error:
            blockers.append(str(error))
            continue
        permission_evidence.append({"owner": permission.owner, "sha256": _digest(document)})
        permitted.update((permission.owner, digest) for digest in permission.drawing_sha256)
    missing = [p["id"] for p in real if (p["owner"], p["drawing_sha256"]) not in permitted]
    if not real or missing:
        blockers.append(
            "Record owner permission covering every original drawing hash"
            + (f": {', '.join(missing)}" if missing else "")
        )
    known_ids = {p["id"] for p in real}
    baseline_ids = {b.pair_id for b in manifest.baseline if b.pair_id in known_ids}
    if len(baseline_ids) < 5:
        blockers.append("Record timed manual baselines for at least five distinct historical pairs")
    if any(b.pair_id not in known_ids for b in manifest.baseline):
        blockers.append("Baseline references an unknown or synthetic pair")
    if manifest.gland_ring_workload_share is None or manifest.gland_ring_workload_share < 0.5:
        blockers.append("Confirm gland rings represent at least half of incoming workload")
    if not manifest.ground_truth_reviewer:
        blockers.append("Name the engineer who will adjudicate ground truth")
    if not manifest.release_reviewers or any(not r for r in manifest.release_reviewers):
        blockers.append("Name authorized release reviewers")
    return {
        "schema_version": "readiness-v1",
        "customer_pipeline": "BLOCKED" if blockers else "PREREQUISITES_RECORDED",
        "cad": "BLOCKED",
        "release": "BLOCKED",
        "blockers": blockers,
        "paired_drawings": len(real),
        "permission_evidence": permission_evidence,
        "policies": manifest.policies.model_dump(mode="json"),
        "environment": manifest.environment.model_dump(mode="json"),
        "next_gates": [
            "Human confirmation of permission scope and dataset suitability",
            "Verify regional models, OCR, retention and cloud configuration before cloud setup",
            "Ground-truth harness and held-out S0-S9 accuracy acceptance before CAD development",
            "Engineer-approved modeling policies and CAM import before manufacturing release",
        ],
        "limitations": "File presence and hashes are checked locally. Legal validity, "
        "STEP geometry, cloud availability and engineer identity are not independently verified. "
        "No cloud calls occur.",
    }
