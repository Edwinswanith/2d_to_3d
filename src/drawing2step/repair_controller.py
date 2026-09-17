"""Requirement-driven repair: replaces the "did it drop more than it fixed" promotion heuristic.

The review named two concrete failure modes of the old logic in ``_construct_with_correction``:
a required feature could vanish, be marked ``report_only``, or reappear under a NEW feature id
(evading the "dropped" check, which only ever compared id strings); and a correction round could
fix one named failure while quietly regressing something that used to pass, since nothing
compared the FULL check list before and after — only the named construction failures. This
module makes both impossible in code:

- Required geometry cannot disappear, become ``report_only``, or evade checking through a
  renamed feature id. Identity is tracked by what a feature CITES (its ledger requirement ids),
  not its id string, so a rename with the same citations is still recognized as the same
  obligation.
- A ``(layer, subject)`` check that PASSED before cannot become ``FAIL``/``UNKNOWN`` after.
- A repeated candidate (byte-identical revised spec) is flagged rather than silently promoted or
  silently rejected — resubmitting the same spec unchanged means the correction made no attempt.

Source-contract limits cannot loosen by construction elsewhere, not by anything checked here: a
``SourceContract`` is loaded fresh from its immutable file on every ``verify_contract`` call, and
no repair round is ever given write access to it — a candidate correction only ever changes
``DraftSpec``, never ``source-contract.json``. Enforcing that here as well would be redundant.
"""

import hashlib
from dataclasses import dataclass
from typing import Any

from drawing2step.revb_model import DraftSpec, Feature


@dataclass(frozen=True)
class RepairOutcome:
    accepted: bool
    reason: str
    evaded: tuple[str, ...] = ()
    regressions: tuple[str, ...] = ()
    repeated: bool = False
    no_progress: bool = False


def spec_hash(spec: DraftSpec) -> str:
    """A content hash for repeated-candidate detection (never a promotion decision on its own)."""
    return hashlib.sha256(spec.model_dump_json(exclude_none=True).encode()).hexdigest()


def _citation_key(feature: Feature) -> frozenset[str]:
    """A feature's identity for repair tracking: what it cites, never its id string.

    An id rename with the same citations and kind must not evade the "is this obligation still
    here" check; the (kind, citations) pair is what a drawing requirement actually maps to.
    """
    return frozenset({feature.kind, *feature.citations})


def _required_features(spec: DraftSpec) -> dict[frozenset[str], Feature]:
    return {_citation_key(f): f for f in spec.features if not f.report_only and f.kind != "marking"}


def _status_by_subject(checks: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    return {(c["layer"], c["subject"]): c["status"] for c in checks}


def evaluate_repair(
    previous_spec: DraftSpec,
    previous_checks: list[dict[str, Any]],
    candidate_spec: DraftSpec,
    candidate_checks: list[dict[str, Any]],
) -> RepairOutcome:
    """Decide whether a corrected candidate may replace the previous draft.

    Both invariants are checked before either draft's OWN failure count is even considered:
    a candidate that evades a required feature or regresses a passing check is rejected
    regardless of how many of the originally-named failures it happens to fix.
    """
    previous_required = _required_features(previous_spec)
    candidate_required = _required_features(candidate_spec)
    evaded = tuple(
        sorted(
            previous_required[key].id for key in previous_required if key not in candidate_required
        )
    )
    if evaded:
        return RepairOutcome(
            accepted=False,
            reason=(
                f"Required feature(s) {', '.join(evaded)} disappeared, were marked report_only, "
                "or were replaced by a feature with different citations (a renamed id with the "
                "same citations is still recognized and does not count as evasion; anything else "
                "does): a correction may only change placement, never drop an obligation."
            ),
            evaded=evaded,
        )
    previous_status = _status_by_subject(previous_checks)
    candidate_status = _status_by_subject(candidate_checks)
    regressions = tuple(
        sorted(
            f"{layer}:{subject}"
            for (layer, subject), status in previous_status.items()
            if status == "PASS"
            and candidate_status.get((layer, subject), "PASS") in {"FAIL", "UNKNOWN"}
        )
    )
    if regressions:
        return RepairOutcome(
            accepted=False,
            reason=(
                f"Check(s) {', '.join(regressions)} passed before this correction and do not "
                "pass after it: a repair round may never trade one passing requirement for "
                "another."
            ),
            regressions=regressions,
        )
    repeated = spec_hash(previous_spec) == spec_hash(candidate_spec)
    if repeated:
        return RepairOutcome(
            accepted=False,
            reason="The corrected proposal is byte-identical to the previous one; no repair made.",
            repeated=True,
        )
    newly_passing = any(
        key not in previous_status and status == "PASS" for key, status in candidate_status.items()
    )
    progressed = (
        any(
            status in {"FAIL", "UNKNOWN"} and candidate_status.get(key) == "PASS"
            for key, status in previous_status.items()
        )
        or newly_passing
    )
    if not progressed:
        return RepairOutcome(
            accepted=False,
            reason=(
                "The correction evades nothing and regresses nothing, but also turns no "
                "previously-failing check into a pass: it is not an improvement."
            ),
            no_progress=True,
        )
    return RepairOutcome(accepted=True, reason="No feature evaded; no passing check regressed.")
