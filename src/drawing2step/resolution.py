"""Conservative spatial evidence reconciliation and version-bound review templates."""

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from drawing2step.storage import canonical_json, write_once


def _overlap(a: list[float], b: list[float]) -> float:
    y0, x0, y1, x1 = a
    v0, u0, v1, u1 = b
    intersection = max(0, min(y1, v1) - max(y0, v0)) * max(0, min(x1, u1) - max(x0, u0))
    area = (v1 - v0) * (u1 - u0)
    return intersection / area if area > 0 else 0


def reconcile_run(run: Path) -> Path:
    """Retain all OCR evidence; character agreement is not parsed-value approval."""
    reading_payload = (run / "reading.json").read_bytes()
    ocr_payload = (run / "independent-local-ocr/tokens.json").read_bytes()
    reading = json.loads(reading_payload)
    tokens = json.loads(ocr_payload)
    fingerprint = hashlib.sha256(
        reading_payload + ocr_payload + Path(__file__).read_bytes()
    ).hexdigest()
    requirements = []
    for c in reading["callouts"]:
        nearby = [t for t in tokens if _overlap(c["box"], t["box"]) >= 0.5]
        # Only plain standalone numeric strings qualify for character corroboration.
        numeric = bool(re.fullmatch(r"\d+(?:\.\d+)?", c["raw_text"].strip()))
        matching = [t for t in nearby if t["raw_text"] == c["raw_text"] and t["confidence"] >= 70]
        requirements.append(
            {
                **c,
                "ocr_evidence_ids": [t["id"] for t in nearby],
                "text_status": "CHARACTERS_CORROBORATED"
                if numeric and len(matching) == 1
                else "REVIEW",
                "interpretation_status": "REVIEW",
                "association_status": "REVIEW",
                "geometry_accepted": False,
            }
        )
    result: dict[str, Any] = {
        "schema_version": "reconciliation-diagnostic-v1",
        "source_fingerprint": fingerprint,
        "requirements": requirements,
        "ocr_observations": tokens,
        "note": "All OCR observations retained, including unmatched text. Character agreement "
        "does not verify tolerances, units, or feature associations.",
    }
    template = {
        "schema_version": "engineer-decisions-v1",
        "source_fingerprint": fingerprint,
        "reconciliation_sha256": hashlib.sha256(canonical_json(result)).hexdigest(),
        "reviewer": "",
        "unit_decision": None,
        "unit_reason": "",
        "whole_sheet_complete": False,
        "decisions": [
            {
                "id": c["id"],
                "confirmed_value": None,
                "confirmed_unit": None,
                "confirmed_tolerance": None,
                "feature_id": None,
                "feature_type": None,
                "reason": "",
            }
            for c in requirements
        ],
    }
    output = run / "reconciliation" / fingerprint[:16]
    write_once(output / "reconciled.json", canonical_json(result))
    write_once(output / "review-template.json", canonical_json(template))
    return output
