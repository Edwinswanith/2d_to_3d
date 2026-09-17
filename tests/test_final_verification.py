"""final_verification: the delivered STEP must be exactly what its report was frozen from."""

import pytest

from drawing2step.final_verification import freeze_release, release_status, verify_release


def _check(layer: str, subject: str, status: str) -> dict[str, str]:
    return {"layer": layer, "subject": subject, "status": status, "detail": ""}


def test_release_status_is_blocked_when_the_step_itself_failed_to_round_trip():
    checks = [_check("G", "round_trip", "FAIL"), _check("G", "quench", "PASS")]
    assert release_status(checks) == "blocked"


def test_release_status_is_blocked_on_any_failing_check():
    checks = [_check("G", "round_trip", "PASS"), _check("H1", "quench", "FAIL")]
    assert release_status(checks) == "blocked"


def test_release_status_is_unresolved_when_a_check_is_unknown_but_none_failed():
    checks = [_check("G", "round_trip", "PASS"), _check("H2", "quench", "UNKNOWN")]
    assert release_status(checks) == "unresolved_requirements"


def test_release_status_is_nominal_conformance_when_everything_measured_passes():
    checks = [_check("G", "round_trip", "PASS"), _check("H1", "quench", "PASS")]
    assert release_status(checks) == "nominal_drawing_conformance"


def test_verify_release_accepts_the_exact_file_and_checks_it_was_frozen_from(tmp_path):
    step = tmp_path / "model.step"
    step.write_bytes(b"ISO-10303-21;\nfake step v1\nENDSEC;")
    checks = [_check("G", "round_trip", "PASS")]
    record = freeze_release(step, checks)
    verify_release(step, checks, record)  # must not raise


def test_verify_release_rejects_an_older_step_delivered_with_a_newer_report(tmp_path):
    step = tmp_path / "model.step"
    step.write_bytes(b"ISO-10303-21;\nfake step v1\nENDSEC;")
    checks_v1 = [_check("G", "round_trip", "FAIL")]
    record_v1 = freeze_release(step, checks_v1)

    # A newer attempt rebuilds the same path with different (better) content and checks.
    step.write_bytes(b"ISO-10303-21;\nfake step v2, actually fixed\nENDSEC;")
    checks_v2 = [_check("G", "round_trip", "PASS")]

    # Delivering the NEW file against the OLD (v1) report must be refused: the v1 record's
    # hash no longer matches what's on disk.
    with pytest.raises(ValueError, match="does not match its verification report's hash"):
        verify_release(step, checks_v1, record_v1)

    # The new report against the new file is fine.
    record_v2 = freeze_release(step, checks_v2)
    verify_release(step, checks_v2, record_v2)


def test_verify_release_rejects_checks_edited_after_freezing(tmp_path):
    step = tmp_path / "model.step"
    step.write_bytes(b"ISO-10303-21;\nfake step\nENDSEC;")
    checks = [_check("G", "round_trip", "PASS"), _check("H1", "quench", "FAIL")]
    record = freeze_release(step, checks)
    tampered = [_check("G", "round_trip", "PASS"), _check("H1", "quench", "PASS")]
    with pytest.raises(ValueError, match="checks do not match"):
        verify_release(step, tampered, record)


def test_verify_release_rejects_a_swapped_source_contract(tmp_path):
    step = tmp_path / "model.step"
    step.write_bytes(b"ISO-10303-21;\nfake step\nENDSEC;")
    checks = [_check("G", "round_trip", "PASS")]
    contract_a = tmp_path / "source-contract-a.json"
    contract_a.write_bytes(b'{"drawing_number": "a"}')
    record = freeze_release(step, checks, source_contract_path=contract_a)
    contract_b = tmp_path / "source-contract-b.json"
    contract_b.write_bytes(b'{"drawing_number": "b"}')
    with pytest.raises(ValueError, match="source contract does not match"):
        verify_release(step, checks, record, source_contract_path=contract_b)
    verify_release(step, checks, record, source_contract_path=contract_a)  # must not raise
