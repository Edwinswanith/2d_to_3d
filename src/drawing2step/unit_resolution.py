"""Version-bound user unit decisions without approving readings or feature associations."""

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from drawing2step.storage import canonical_json, write_once


def resolve_units(run: Path, unit: Literal["mm", "in"], confirmed_by: str, reason: str) -> Path:
    """Apply a confirmed drawing length default, keeping explicit callout units unchanged."""
    if unit not in {"mm", "in"} or not confirmed_by.strip() or not reason.strip():
        raise ValueError("Provide supported units, confirmer, and reason")
    summary = json.loads((run / "summary.json").read_bytes())
    original_hash = hashlib.sha256((run / "original.pdf").read_bytes()).hexdigest()
    if original_hash != summary["original_sha256"]:
        raise ValueError("Original drawing integrity mismatch")
    ledger_payload = (run / "ledger.json").read_bytes()
    ledger: list[dict[str, Any]] = json.loads(ledger_payload)
    decision = {
        "schema_version": "unit-decision-v1",
        "original_sha256": original_hash,
        "ledger_sha256": hashlib.sha256(ledger_payload).hexdigest(),
        "default_length_unit": unit,
        "confirmed_by": confirmed_by.strip(),
        "reason": reason.strip(),
        "scope": "Default linear and diameter units only; no geometry or release sign-off",
        "printed_title_block_units": summary.get("title_block_units"),
        "tolerance_notes": "Retain printed units; not approved by this default-unit decision",
        "thread_designations": "Retain printed designations without conversion",
    }
    for entry in ledger:
        kind = entry["kind"].lower()
        explicit = entry["unit_printed"].strip().lower()
        entry["unit_decision_applied"] = False
        if kind in {"diameter", "linear", "radius"} and not explicit:
            entry.update(
                resolved_unit=unit, unit_status="USER_CONFIRMED_DEFAULT", unit_decision_applied=True
            )
        elif explicit:
            # Preserve rather than reinterpret unusual callout unit strings.
            entry.update(resolved_unit=entry["unit_printed"], unit_status="EXPLICIT_AS_PRINTED")
        elif kind == "angle":
            entry.update(resolved_unit="degree", unit_status="ANGULAR_TYPE")
        else:
            entry.update(resolved_unit=None, unit_status="REVIEW")
        entry["accepted"] = False
    output = run / "unit-resolutions" / hashlib.sha256(canonical_json(decision)).hexdigest()[:16]
    status = {
        "units": "RESOLVED_BY_USER",
        "default_length_unit": unit,
        "geometry_accepted": False,
        "release": "BLOCKED",
        "remaining_review": [
            "Readings and tolerances",
            "Physical feature associations",
            "Unsupported holes, slots, pins and ports",
            "Full CAD conformance and reference STEP comparison",
        ],
    }
    write_once(output / "decision.json", canonical_json(decision))
    write_once(output / "ledger.json", canonical_json(ledger))
    write_once(output / "status.json", canonical_json(status))
    return output
