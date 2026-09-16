"""Isolated S8 precedence experiment on already-grouped synthetic observations.

This does not implement spatial reconciliation, OCR, unit inference, or associations.
"""

from collections.abc import Sequence

from drawing2step.models import Decision, DecisionStatus, Observation


def reconcile(readings: Sequence[Observation]) -> Decision:
    """Decide a single callout; repeated reads by one source are not independent votes."""
    if not readings:
        return Decision(status="UNREAD", accepted=False, reason="No observations")
    by_source: dict[str, list[Observation]] = {}
    for reading in readings:
        by_source.setdefault(reading.source, []).append(reading)
    # Internal disagreement in any source must be reviewed, not cherry-picked.
    if any(len({r.signature() for r in group}) > 1 for group in by_source.values()):
        return Decision(status="CONFLICT", accepted=False, reason="A source disagrees with itself")
    sources = {key: group[0] for key, group in by_source.items()}
    agreement_rules: tuple[tuple[str, str, DecisionStatus], ...] = (
        ("native", "a", "NATIVE_CONFIRMED"),
        ("native", "b", "NATIVE_CONFIRMED"),
        ("a", "b", "AGREED"),
    )
    for left, right, status in agreement_rules:
        if left not in sources or right not in sources:
            continue
        selected = sources[left]
        if selected.signature() != sources[right].signature():
            continue
        # Native disagreement cannot be outvoted by the model and OCR.
        if left == "a" and "native" in sources:
            continue
        if selected.value is None or selected.unit is None:
            continue
        return Decision(
            status=status,
            accepted=True,
            selected_reading=selected.id,
            reason="Independent sources agree on value, units, and tolerance",
        )
    primary = [r for source, r in sources.items() if source != "escalation"]
    conflict = len({r.signature() for r in primary}) > 1
    if conflict and not any(r.geometry_driving for r in readings):
        escalation = sources.get("escalation")
        if escalation and escalation.value is not None and escalation.unit is not None:
            if any(escalation.signature() == r.signature() for r in primary):
                return Decision(
                    status="RESOLVED_BY_ESCALATION",
                    accepted=True,
                    selected_reading=escalation.id,
                    reason="Non-geometry conflict; escalation agrees",
                )
    if conflict:
        return Decision(status="CONFLICT", accepted=False, reason="Engineer resolution required")
    if len(primary) > 1:
        return Decision(status="UNREAD", accepted=False, reason="Value or units unresolved")
    single_source_status: dict[str, DecisionStatus] = {"a": "A_ONLY", "b": "B_ONLY"}
    status = single_source_status.get(primary[0].source if primary else "", "SINGLE_SOURCE")
    return Decision(status=status, accepted=False, reason="No independent corroboration")
