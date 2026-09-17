"""Exact-file final verification: the delivered STEP must be the one the report checked.

A build folder's checks are produced by measuring ONE specific STEP file at build time.
Nothing before this module stops a later bug, a partial re-run, or a stale cache from serving
those checks alongside a DIFFERENT STEP file — an older attempt's bytes, say, next to a newer
attempt's report. ``freeze_release`` binds the two together with content hashes;
``verify_release`` recomputes both hashes from what is actually about to be delivered and
refuses any pairing that doesn't match, rather than trusting the record at face value.

Four statuses, kept separate rather than folded into one pass/fail (the review's own request):
``valid_solid`` (the STEP round-tripped to a valid B-rep; no requirement is measured yet),
``nominal_drawing_conformance`` (every measured check passed; nothing is UNKNOWN),
``unresolved_requirements`` (a valid solid, but at least one check could not be resolved), and
``blocked`` (a structural failure, or any check FAILed outright). ``manufacturing_approval`` is
never computed here — release status is a measurement, approval is a human decision recorded
separately, and this module must not let one stand in for the other.
"""

import hashlib
from pathlib import Path
from typing import Any, Literal

from drawing2step.models import Contract
from drawing2step.storage import canonical_json

ReleaseStatus = Literal[
    "valid_solid", "nominal_drawing_conformance", "unresolved_requirements", "blocked"
]


class ReleaseRecord(Contract):
    step_sha256: str
    checks_sha256: str
    source_contract_sha256: str | None = None
    status: ReleaseStatus
    manufacturing_approval: bool = False


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _checks_sha256(checks: list[dict[str, Any]]) -> str:
    return hashlib.sha256(canonical_json(checks)).hexdigest()


def release_status(checks: list[dict[str, Any]]) -> ReleaseStatus:
    valid_solid = not any(
        c["layer"] == "G" and c["subject"] == "round_trip" and c["status"] != "PASS" for c in checks
    )
    if not valid_solid:
        return "blocked"
    statuses = {c["status"] for c in checks}
    if "FAIL" in statuses:
        return "blocked"
    if "UNKNOWN" in statuses:
        return "unresolved_requirements"
    return "nominal_drawing_conformance"


def freeze_release(
    step_path: Path,
    checks: list[dict[str, Any]],
    *,
    source_contract_path: Path | None = None,
) -> ReleaseRecord:
    """Bind a build's checks to the EXACT bytes of the STEP file they measured."""
    return ReleaseRecord(
        step_sha256=_sha256(step_path),
        checks_sha256=_checks_sha256(checks),
        source_contract_sha256=(
            _sha256(source_contract_path) if source_contract_path is not None else None
        ),
        status=release_status(checks),
    )


def verify_release(
    step_path: Path,
    checks: list[dict[str, Any]],
    record: ReleaseRecord,
    *,
    source_contract_path: Path | None = None,
) -> None:
    """Raise unless ``step_path``/``checks`` are EXACTLY what ``record`` was frozen from.

    Every hash is recomputed from the delivery's own bytes, never read back from ``record`` —
    an older STEP cannot be delivered just because some report, even a genuine one, claims a
    hash that happens to match it; the file actually being served is what gets hashed.
    """
    if _sha256(step_path) != record.step_sha256:
        raise ValueError(
            "Delivery blocked: the STEP file does not match its verification report's hash"
        )
    if _checks_sha256(checks) != record.checks_sha256:
        raise ValueError("Delivery blocked: the checks do not match the frozen verification report")
    contract_hash = _sha256(source_contract_path) if source_contract_path is not None else None
    if contract_hash != record.source_contract_sha256:
        raise ValueError(
            "Delivery blocked: the source contract does not match the frozen verification report"
        )
