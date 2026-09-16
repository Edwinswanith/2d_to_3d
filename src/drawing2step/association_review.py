"""Validate explicit engineering decisions; proposals never become sign-offs automatically."""

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from drawing2step.models import Contract, Number
from drawing2step.storage import canonical_json, write_once


class AssociationDecision(Contract):
    id: str
    confirmed_value: Number
    confirmed_unit: Literal["mm", "in", "degree", "count"]
    confirmed_tolerance: str = Field(min_length=1)
    feature_id: str = Field(min_length=1)
    feature_type: Literal["diameter", "linear", "angle", "count"]
    reason: str = Field(min_length=1)


class EngineerDecisions(Contract):
    schema_version: Literal["engineer-decisions-v1"] = "engineer-decisions-v1"
    source_fingerprint: str
    reconciliation_sha256: str
    reviewer: str = Field(min_length=1)
    unit_decision: Literal["mm", "in"]
    unit_reason: str = Field(min_length=1)
    whole_sheet_complete: bool
    decisions: list[AssociationDecision]


def validate_review(reconciliation: Path, decision_path: Path) -> Path:
    ledger_payload = (reconciliation / "reconciled.json").read_bytes()
    ledger = json.loads(ledger_payload)
    payload = decision_path.read_bytes()
    review = EngineerDecisions.model_validate_json(payload)
    if review.source_fingerprint != ledger["source_fingerprint"]:
        raise ValueError("Stale review: source evidence fingerprint changed")
    if review.reconciliation_sha256 != hashlib.sha256(ledger_payload).hexdigest():
        raise ValueError("Stale review: reconciled ledger content changed")
    by_id = {c["id"]: c for c in ledger["requirements"]}
    seen = set()
    associations: list[dict[str, Any]] = []
    for decision in review.decisions:
        if decision.id in seen or decision.id not in by_id:
            raise ValueError("Duplicate or unknown requirement decision")
        seen.add(decision.id)
        requirement = by_id[decision.id]
        if requirement["kind"].lower() != decision.feature_type:
            raise ValueError("Requirement and feature dimensional types are incompatible")
        allowed_units = {
            "diameter": {"mm", "in"},
            "linear": {"mm", "in"},
            "angle": {"degree"},
            "count": {"count"},
        }
        if decision.confirmed_unit not in allowed_units[decision.feature_type]:
            raise ValueError("Feature type and unit are incompatible")
        if decision.confirmed_value <= 0:
            raise ValueError("Confirmed dimensions and counts must be positive")
        if decision.feature_type == "count" and decision.confirmed_value % 1:
            raise ValueError("Count must be an integer")
        associations.append(
            {
                **decision.model_dump(mode="json"),
                "association_status": "ENGINEER_CONFIRMED_TYPE_COMPATIBLE",
                "source_box": requirement["box"],
                "geometric_placement_verified": False,
            }
        )
    result = {
        "source_fingerprint": review.source_fingerprint,
        "reviewer": review.reviewer,
        "unit_decision": review.unit_decision,
        "unit_reason": review.unit_reason,
        "associations": associations,
        "unreviewed_requirements": sorted(set(by_id) - seen),
        "whole_sheet_complete": review.whole_sheet_complete,
        "geometry_verification": "UNKNOWN",
        "release": "BLOCKED",
    }
    output = reconciliation / "reviews" / hashlib.sha256(payload).hexdigest()[:16]
    write_once(output / "decisions.json", canonical_json(review.model_dump(mode="json")))
    write_once(output / "validated-associations.json", canonical_json(result))
    return output
