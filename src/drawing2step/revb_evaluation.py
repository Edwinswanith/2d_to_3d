"""Inventory and structural-error evaluation. Synthetic data cannot pass acceptance."""

import hashlib
import json
from pathlib import Path
from typing import Any

from drawing2step.revb import Inventory
from drawing2step.revb_geometry import check_structure, inspect_step


def _iou(a: list[float], b: list[float]) -> float:
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0, min(a[3], b[3]) - max(a[1], b[1])
    )
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / union if union else 0


def score_inventory(
    truth: list[dict[str, Any]], predictions: list[dict[str, Any]]
) -> dict[str, Any]:
    # Labels are view-specific observations, not a sum of physical instances across views.
    labels = Inventory.model_validate({"features": truth}).features
    candidates = Inventory.model_validate({"features": predictions}).features
    if any(t.count is None for t in labels):
        raise ValueError("Ground truth must resolve visible feature counts")
    edges = sorted(
        (
            (_iou(list(t.box), list(p.box)), i, j)
            for i, t in enumerate(labels)
            for j, p in enumerate(candidates)
            if t.type == p.type and t.view == p.view
        ),
        reverse=True,
    )
    used_truth: set[int] = set()
    used_predictions: set[int] = set()
    found = 0
    for overlap, i, j in edges:
        if overlap < 0.25 or i in used_truth or j in used_predictions:
            continue
        used_truth.add(i)
        used_predictions.add(j)
        found += min(labels[i].count or 0, candidates[j].count or 0)
    total = sum(t.count or 0 for t in labels)
    predicted = sum(p.count or 0 for p in candidates)
    return {
        "recall": found / total if total else None,
        "expected_instances": total,
        "matched_instances": found,
        "missing_instances": total - found,
        "extra_instances": predicted - found,
        "matching_policy": "view/type and IoU >= 0.25, one-to-one",
    }


def assess_dataset(cases: list[dict[str, Any]]) -> dict[str, Any]:
    blockers = []
    groups: dict[str, set[str]] = {}
    for c in cases:
        groups.setdefault(c.get("group", ""), set()).add(c.get("split", ""))
    if any(len(splits) > 1 for splits in groups.values()):
        blockers.append("Family/revision leakage between development and held-out splits")
    real = [c for c in cases if c.get("synthetic") is False and c.get("approved_by")]
    if len([c for c in real if c.get("split") == "development"]) < 20:
        blockers.append("At least 20 approved real development cases required")
    if len([c for c in real if c.get("split") == "held_out"]) < 10:
        blockers.append("At least 10 approved real held-out cases required")
    if len({c.get("id") for c in cases}) != len(cases):
        blockers.append("Duplicate evaluation case IDs")
    return {"status": "BLOCKED" if blockers else "DATASET_DECLARED", "blockers": blockers}


def evaluate_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text())
    cases = manifest.get("cases", [])
    readiness = assess_dataset(cases)
    results = []
    valid_cases = []
    seen_drawings: set[str] = set()
    seen_references: dict[str, str] = {}
    blockers = list(readiness["blockers"])
    if not manifest.get("frozen_candidate"):
        blockers.append("Held-out acceptance requires a frozen candidate fingerprint")
    for case in cases:
        try:
            if case.get("quality") not in {"clean", "scan"} or case.get("split") not in {
                "development",
                "held_out",
            }:
                raise ValueError("Invalid quality or split")
            if not isinstance(case.get("synthetic"), bool) or not case.get("group"):
                raise ValueError("Explicit synthetic flag and family group required")
            # Every input is bound to a hash approved in this manifest.
            loaded = {}
            for name in ("drawing", "reference_step", "truth", "audit"):
                entry = case[name]
                artifact = path.parent / entry["path"]
                data = artifact.read_bytes()
                if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                    raise ValueError(f"{name} hash mismatch")
                loaded[name] = data
            drawing_hash = case["drawing"]["sha256"]
            reference_hash = case["reference_step"]["sha256"]
            if drawing_hash in seen_drawings:
                raise ValueError("Duplicate drawing hash")
            if (
                reference_hash in seen_references
                and seen_references[reference_hash] != case["group"]
            ):
                raise ValueError("Shared reference geometry crosses family groups")
            seen_drawings.add(drawing_hash)
            seen_references[reference_hash] = case["group"]
            truth = json.loads(loaded["truth"])
            audit = json.loads(loaded["audit"])
            if audit.get("candidate_fingerprint") != manifest.get("frozen_candidate"):
                raise ValueError("Audit does not belong to the frozen candidate")
            if audit["original_sha256"] != hashlib.sha256(loaded["drawing"]).hexdigest():
                raise ValueError("Audit belongs to a different drawing")
            if not case.get("approved_by") or not case.get("approval_reason"):
                raise ValueError("Ground-truth approval and discrepancy-resolution reason required")
            expected = truth["structure"]
            reference = path.parent / case["reference_step"]["path"]
            baseline = check_structure(reference, expected)
            if (
                inspect_step(reference)["status"] != "PASS"
                or not baseline
                or any(check["status"] != "PASS" for check in baseline)
            ):
                raise ValueError("Approved reference failed its own structural expectations")
            score = score_inventory(
                truth["inventory"]["features"], (audit.get("inventory") or {}).get("features", [])
            )
            catches = []
            for fault in case.get("seeded_errors", []):
                candidate = path.parent / fault["step"]["path"]
                if hashlib.sha256(candidate.read_bytes()).hexdigest() != fault["step"]["sha256"]:
                    raise ValueError("Seeded STEP hash mismatch")
                if fault["subject"] not in {e["id"] for e in expected}:
                    raise ValueError("Fault lacks a passing baseline expectation")
                checks = check_structure(candidate, expected)
                catches.append(
                    any(c["subject"] == fault["subject"] and c["status"] == "FAIL" for c in checks)
                )
            results.append(
                {
                    "id": case["id"],
                    "quality": case["quality"],
                    "split": case["split"],
                    "eligible": case["synthetic"] is False,
                    "inventory": score,
                    "seeded_errors": len(catches),
                    "caught_errors": sum(catches),
                }
            )
            valid_cases.append(case)
        except (KeyError, ValueError, OSError, RuntimeError) as error:
            blockers.append(
                f"{case.get('id', 'unknown')}: {type(error).__name__}; "
                "reference evidence invalid or incomplete"
            )
    blockers.extend(assess_dataset(valid_cases)["blockers"])
    metrics = {}
    for quality in ("clean", "scan"):
        subset = [
            r
            for r in results
            if r["quality"] == quality and r["split"] == "held_out" and r["eligible"]
        ]
        total = sum(r["inventory"]["expected_instances"] for r in subset)
        matched = sum(r["inventory"]["matched_instances"] for r in subset)
        seeded = sum(r["seeded_errors"] for r in subset)
        caught = sum(r["caught_errors"] for r in subset)
        metrics[quality] = {
            "inventory_recall": matched / total if total else None,
            "structure_catch_rate": caught / seeded if seeded else None,
        }
    clean = metrics["clean"]
    if clean["inventory_recall"] is None or clean["inventory_recall"] < 0.98:
        blockers.append("Clean held-out inventory recall target not demonstrated")
    if clean["structure_catch_rate"] is None or clean["structure_catch_rate"] < 0.95:
        blockers.append("Clean held-out structural catch-rate target not demonstrated")
    # This harness measures stage-one feasibility only, never authorises manufacturing.
    return {
        "schema_version": "revb-evaluation-v1",
        "status": "BLOCKED" if blockers else "STAGE_ONE_METRICS_MET",
        "release": "BLOCKED",
        "blockers": list(dict.fromkeys(blockers)),
        "metrics": metrics,
        "cases": results,
        "limitations": [
            "No automatic dataset promotion from engineer corrections",
            "Ledger/value/association acceptance remains separately required",
        ],
    }
