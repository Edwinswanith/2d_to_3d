"""repair_controller.evaluate_repair: invariants a repair round may never violate."""

from drawing2step.repair_controller import evaluate_repair, spec_hash
from drawing2step.revb_model import DraftSpec

_PROFILE = [
    {"z": {"datum": "face_a"}, "radius": {"expr": "OD/2"}},
    {"z": {"ledger": "H"}, "radius": {"expr": "OD/2"}},
    {"z": {"ledger": "H"}, "radius": {"expr": "ID/2"}},
    {"z": {"datum": "face_a"}, "radius": {"expr": "ID/2"}},
]

_PORT = {
    "id": "quench",
    "kind": "port",
    "citations": ["HD", "H"],
    "diameter": {"ledger": "HD"},
    "depth": {"expr": "OD/2-ID/2"},
    "z": {"expr": "H/2"},
    "angle": {"centreline": 0, "reason": "test"},
    "host": "outside",
    "reference_face": "face_A",
}


def _spec(features: list[dict]) -> DraftSpec:
    return DraftSpec.model_validate(
        {
            "reference_face": "face_A",
            "coordinate_policy": "Z increases from face A",
            "profile": _PROFILE,
            "features": features,
        }
    )


def _check(layer: str, subject: str, status: str) -> dict[str, str]:
    return {"layer": layer, "subject": subject, "status": status, "detail": ""}


def test_demoting_a_required_feature_to_report_only_is_rejected():
    previous = _spec([_PORT])
    candidate = _spec([{**_PORT, "report_only": True, "reason": "operator will drill by hand"}])
    outcome = evaluate_repair(
        previous, [_check("G", "quench", "FAIL")], candidate, [_check("F", "quench", "UNKNOWN")]
    )
    assert not outcome.accepted
    assert outcome.evaded == ("quench",)


def test_dropping_a_required_feature_entirely_is_rejected():
    previous = _spec([_PORT])
    candidate = _spec([])
    outcome = evaluate_repair(previous, [_check("G", "quench", "FAIL")], candidate, [])
    assert not outcome.accepted
    assert outcome.evaded == ("quench",)


def test_renaming_a_feature_with_the_same_citations_and_kind_is_not_evasion():
    previous = _spec([_PORT])
    candidate = _spec([{**_PORT, "id": "renamed"}])
    outcome = evaluate_repair(
        previous, [_check("G", "quench", "FAIL")], candidate, [_check("G", "renamed", "PASS")]
    )
    assert outcome.accepted


def test_a_correction_that_regresses_a_previously_passing_check_is_rejected():
    previous = _spec([_PORT])
    candidate = _spec([_PORT])
    outcome = evaluate_repair(
        previous,
        [_check("G", "quench", "PASS"), _check("H1", "bolts", "PASS")],
        candidate,
        [_check("G", "quench", "PASS"), _check("H1", "bolts", "FAIL")],
    )
    assert not outcome.accepted
    assert outcome.regressions == ("H1:bolts",)


def test_a_byte_identical_resubmission_is_rejected_as_no_repair():
    previous = _spec([_PORT])
    candidate = _spec([_PORT])
    outcome = evaluate_repair(
        previous, [_check("G", "quench", "FAIL")], candidate, [_check("G", "quench", "FAIL")]
    )
    assert not outcome.accepted
    assert outcome.repeated


def test_a_correction_that_neither_fixes_nor_breaks_anything_is_not_progress():
    previous = _spec([_PORT])
    candidate = _spec([{**_PORT, "z": {"expr": "H/3"}}])
    outcome = evaluate_repair(
        previous, [_check("G", "quench", "FAIL")], candidate, [_check("G", "quench", "FAIL")]
    )
    assert not outcome.accepted
    assert outcome.no_progress


def test_a_genuine_fix_is_accepted():
    previous = _spec([_PORT])
    candidate = _spec([{**_PORT, "z": {"expr": "H/3"}}])
    outcome = evaluate_repair(
        previous, [_check("G", "quench", "FAIL")], candidate, [_check("G", "quench", "PASS")]
    )
    assert outcome.accepted


def test_spec_hash_is_stable_and_sensitive_to_content():
    a, b = _spec([_PORT]), _spec([_PORT])
    c = _spec([{**_PORT, "z": {"expr": "H/3"}}])
    assert spec_hash(a) == spec_hash(b)
    assert spec_hash(a) != spec_hash(c)
