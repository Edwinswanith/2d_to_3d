"""Local version-bound review records and atomic release bundles.

Reviewer credentials are configured separately from draft author names. Without configured
reviewers the workspace can build drafts but cannot authorize manufacturing release.
"""

import hashlib
import hmac
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from drawing2step.storage import canonical_json, write_once


class ReviewDecision(BaseModel):
    expected_version: int = Field(ge=1)
    subject: str = Field(min_length=1, max_length=100)
    layer: str = Field(min_length=1, max_length=20)
    decision: Literal["APPROVE", "REJECT"]
    reason: str = Field(min_length=3, max_length=2000)


class ReleaseRequest(BaseModel):
    expected_version: int = Field(ge=1)
    manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def authenticate_reviewer(authorization: str | None) -> str:
    # JSON map named reviewer -> sha256(token), never store plaintext tokens in logs/artifacts.
    configured = json.loads(os.environ.get("D2S_REVIEWER_TOKEN_HASHES", "{}"))
    token = (authorization or "").removeprefix("Bearer ")
    if not token or not configured:
        raise PermissionError("A configured reviewer credential is required")
    digest = hashlib.sha256(token.encode()).hexdigest()
    for reviewer, expected in configured.items():
        if hmac.compare_digest(digest, expected):
            return str(reviewer)
    raise PermissionError("Reviewer credential rejected")


def current_model(directory: Path, expected_version: int) -> tuple[dict[str, Any], Path, str]:
    state = json.loads((directory / "status.json").read_text())
    if (
        state.get("model_version") != expected_version
        or not state.get("model_available")
        or state.get("status") not in {"ready", "review"}
    ):
        raise ValueError("Stale or unavailable model version")
    folder = directory / "models" / state["model_folder"]
    manifest_bytes = (folder / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    for relative, digest in manifest["artifacts"].items():
        path = (folder / relative).resolve()
        if (
            not path.is_relative_to(folder.resolve())
            or not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != digest
        ):
            raise ValueError("Model artifact integrity failed")
    return state, folder, hashlib.sha256(manifest_bytes).hexdigest()


def record_decision(directory: Path, request: ReviewDecision, reviewer: str) -> dict[str, Any]:
    _, folder, digest = current_model(directory, request.expected_version)
    checks = json.loads((folder / "checks.json").read_text())
    matches = [c for c in checks if c["subject"] == request.subject and c["layer"] == request.layer]
    if not matches:
        raise ValueError("Unknown review item")
    if request.decision == "APPROVE" and any(c["status"] == "FAIL" for c in matches):
        raise ValueError("FAIL cannot be waived; correct and rebuild the model")
    if request.subject == "accuracy_gate":
        raise ValueError("Dataset acceptance requires an evaluation report, not an item waiver")
    record = {
        **request.model_dump(),
        "reviewer": reviewer,
        "manifest_sha256": digest,
        "id": uuid4().hex,
        "created_at": datetime.now(UTC).isoformat(),
    }
    write_once(directory / "reviews" / f"{record['id']}.json", canonical_json(record))
    return record


def decisions(directory: Path) -> list[dict[str, Any]]:
    return sorted(
        (json.loads(p.read_text()) for p in (directory / "reviews").glob("*.json")),
        key=lambda r: r["created_at"],
    )


def release_bundle(directory: Path, request: ReleaseRequest, reviewer: str) -> dict[str, Any]:
    state, folder, digest = current_model(directory, request.expected_version)
    if digest != request.manifest_sha256:
        raise ValueError("Manifest changed; release must bind the exact reviewed bundle")
    checks = json.loads((folder / "checks.json").read_text())
    latest = {
        (d["layer"], d["subject"]): d
        for d in decisions(directory)
        if d["manifest_sha256"] == digest and d["expected_version"] == request.expected_version
    }
    blockers = []
    for check in checks:
        key = (check["layer"], check["subject"])
        if check["status"] == "FAIL":
            blockers.append(f"FAIL: {check['subject']}")
        elif check["status"] == "UNKNOWN":
            if latest.get(key, {}).get("decision") != "APPROVE":
                blockers.append(f"Unsigned: {check['subject']}")
        if latest.get(key, {}).get("decision") == "REJECT":
            blockers.append(f"Rejected: {check['subject']}")
    # Proof of dataset acceptance cannot be fabricated by setting a client flag.
    # This prototype has no approved acceptance record, so every draft retains this gate.
    # No approval evidence service exists yet. Do not infer readiness from caller-supplied
    # PASS rows or from a missing obligation. This gate is intentionally non-waivable.
    blockers.append("Engineer-approved paired-data acceptance and CAM import approval are missing")
    required = {"U", "D", "G", "H1", "H2", "H3"}
    if not required <= {c["layer"] for c in checks}:
        blockers.append("Required check layers are missing")
    if blockers:
        return {"release": "BLOCKED", "blockers": sorted(set(blockers))}
    record = {
        "release": "RELEASED",
        "id": uuid4().hex,
        "model_version": request.expected_version,
        "manifest_sha256": digest,
        "step_sha256": state["step_sha256"],
        "reviewer": reviewer,
        "created_at": datetime.now(UTC).isoformat(),
        "signoffs": list(latest.values()),
        "model_folder": folder.name,
    }
    # Caller holds its drawing lock; write_once atomically publishes the complete manifest.
    write_once(directory / "releases" / f"{digest}.json", canonical_json(record))
    return record
