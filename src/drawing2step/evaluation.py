"""Conservative fixture scoring with one-to-one, position-based correspondence."""

from collections import Counter
from collections.abc import Sequence
from typing import Any

from drawing2step.models import EvalCase, Requirement


def _matches(truth: Sequence[Requirement], predictions: Sequence[Requirement]) -> dict[int, int]:
    candidates = {
        i: [
            j
            for j, predicted in enumerate(predictions)
            if target.kind == predicted.kind and target.box.overlap(predicted.box) >= 0.5
        ]
        for i, target in enumerate(truth)
    }
    uses = Counter(j for indices in candidates.values() for j in indices)
    # Ambiguous matches never get credit through value-based cherry-picking.
    return {
        i: indices[0]
        for i, indices in candidates.items()
        if len(indices) == 1 and uses[indices[0]] == 1
    }


def _score(cases: Sequence[EvalCase]) -> dict[str, Any]:
    truth_count = prediction_count = found = accepted = wrong = associated = unknown = 0
    for case in cases:
        matches = _matches(case.truth, case.predictions)
        reverse = {prediction: truth for truth, prediction in matches.items()}
        truth_count += len(case.truth)
        prediction_count += len(case.predictions)
        found += len(matches)
        for index, prediction in enumerate(case.predictions):
            target = case.truth[reverse[index]] if index in reverse else None
            if prediction.text_status == "ACCEPTED":
                accepted += 1
                wrong += int(target is None or prediction.signature() != target.signature())
            if (
                target is not None
                and target.feature is not None
                and prediction.association_status == "VALIDATED"
                and target.feature == prediction.feature
            ):
                associated += 1
            unknown += int(
                prediction.text_status != "ACCEPTED"
                or prediction.interpretation_status == "UNKNOWN"
                or (prediction.geometry_driving and prediction.association_status == "UNKNOWN")
            )
    recall = found / truth_count if truth_count else None
    error = wrong / accepted if accepted else None
    return {
        "cases": len(cases),
        "ground_truth_requirements": truth_count,
        "predicted_requirements": prediction_count,
        "matched_requirements": found,
        "accepted_values": accepted,
        "wrong_accepted_values": wrong,
        "ledger_recall": recall,
        "accepted_value_error": error,
        "association_accuracy": associated / truth_count if truth_count else None,
        "unknown_rate": unknown / prediction_count if prediction_count else None,
        "latency_seconds": sum(float(c.latency_seconds) for c in cases),
        "cost_usd": sum(float(c.cost_usd) for c in cases),
        "reading_thresholds_met": recall is not None
        and recall >= 0.98
        and error is not None
        and error <= 0.005,
    }


def evaluate(cases: Sequence[EvalCase]) -> dict[str, Any]:
    if len({case.id for case in cases}) != len(cases):
        raise ValueError("Duplicate evaluation case IDs")
    groups: dict[str, str] = {}
    for case in cases:
        if case.group in groups and groups[case.group] != case.split:
            raise ValueError("Development/held-out group leakage")
        groups[case.group] = case.split
    if any(not case.synthetic for case in cases):
        raise ValueError("This feasibility harness accepts synthetic fixtures only")
    return {
        "schema_version": "metrics-v1",
        "synthetic": True,
        "cad_gate": "BLOCKED",
        "release_gate": "BLOCKED",
        "reason": "Synthetic fixtures cannot establish production reading accuracy",
        "geometry_conformance": "UNKNOWN",
        "cohorts": {
            quality: _score([c for c in cases if c.quality == quality])
            for quality in ("clean", "scan")
        },
        "splits": {
            split: _score([c for c in cases if c.split == split])
            for split in ("development", "held_out")
        },
    }
