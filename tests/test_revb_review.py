"""Review/release behavior using synthetic immutable artifacts and no provider calls."""

import hashlib
import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from drawing2step.revb_review import (
    ReleaseRequest,
    ReviewDecision,
    current_model,
    decisions,
    record_decision,
    release_bundle,
)
from drawing2step.storage import canonical_json
from drawing2step.web_api import create_app

JOB_ID = "a" * 32


def check(layer, subject, status="PASS"):
    return {"layer": layer, "subject": subject, "status": status, "detail": "Synthetic test check"}


def reference_checks():
    # A synthetic PASS row is not a real drawing or approved acceptance record.
    # It must not bypass the independent dataset and CAM approval requirements.
    return [
        check("U", "units"),
        check("D", "coverage"),
        check("G", "round_trip"),
        check("H1", "hole_size"),
        check("H2", "hole_location"),
        check("H3", "sections", "UNKNOWN"),
        check("R", "port_depth", "UNKNOWN"),
        check("D", "accuracy_gate"),
    ]


def model_fixture(root: Path, checks=None, version=1):
    directory = root / JOB_ID
    folder_name = f"{version:032x}"
    folder = directory / "models" / folder_name
    folder.mkdir(parents=True, exist_ok=True)
    artifacts = {
        "model.step": b"ISO-10303-21; SYNTHETIC REVIEW UNIT FIXTURE; END-ISO-10303-21;",
        "model.stl": b"solid synthetic\nendsolid synthetic\n",
        "mesh.json": canonical_json({"positions": [], "indices": [], "units": "mm"}),
        "checks.json": canonical_json(reference_checks() if checks is None else checks),
    }
    for name, content in artifacts.items():
        (folder / name).write_bytes(content)
    manifest = {
        "version": version,
        "artifacts": {
            name: hashlib.sha256(content).hexdigest() for name, content in artifacts.items()
        },
    }
    manifest_bytes = canonical_json(manifest)
    (folder / "manifest.json").write_bytes(manifest_bytes)
    state = {
        "id": JOB_ID,
        "revision_b": True,
        "status": "review",
        "model_version": version,
        "model_folder": folder_name,
        "model_available": True,
        "download_available": True,
        "release": "BLOCKED",
        "step_sha256": manifest["artifacts"]["model.step"],
    }
    (directory / "status.json").write_bytes(canonical_json(state))
    return directory, folder, hashlib.sha256(manifest_bytes).hexdigest()


def decision(subject="sections", layer="H3", decision="APPROVE", version=1):
    return ReviewDecision(
        expected_version=version,
        subject=subject,
        layer=layer,
        decision=decision,
        reason="Checked against the source drawing for this synthetic test",
    )


def release_request(digest, version=1):
    return ReleaseRequest(expected_version=version, manifest_sha256=digest)


def reviewer_credentials(monkeypatch):
    token = "test-only-local-reviewer-token"
    monkeypatch.setenv(
        "D2S_REVIEWER_TOKEN_HASHES",
        json.dumps({"Engineer A": hashlib.sha256(token.encode()).hexdigest()}),
    )
    return {"Authorization": f"Bearer {token}"}


def test_current_model_requires_current_available_version(tmp_path):
    directory, folder, digest = model_fixture(tmp_path)
    state, actual_folder, actual_digest = current_model(directory, 1)
    assert state["status"] == "review"
    assert actual_folder == folder
    assert actual_digest == digest
    with pytest.raises(ValueError, match="Stale"):
        current_model(directory, 2)


def test_stale_decision_is_not_recorded(tmp_path):
    directory, _, _ = model_fixture(tmp_path, version=2)
    with pytest.raises(ValueError, match="Stale"):
        record_decision(directory, decision(version=1), "Engineer A")
    assert decisions(directory) == []


def test_fail_cannot_be_waived_by_an_engineer(tmp_path):
    checks = reference_checks() + [check("H2", "missing_hole", "FAIL")]
    directory, _, digest = model_fixture(tmp_path, checks)
    with pytest.raises(ValueError, match="FAIL cannot be waived"):
        record_decision(directory, decision("missing_hole", "H2"), "Engineer A")
    assert decisions(directory) == []
    result = release_bundle(directory, release_request(digest), "Engineer A")
    assert result["release"] == "BLOCKED"
    assert "FAIL: missing_hole" in result["blockers"]


def test_each_unknown_requires_its_own_version_bound_signoff(tmp_path):
    directory, _, digest = model_fixture(tmp_path)
    first = record_decision(directory, decision(), "Engineer A")
    assert first["manifest_sha256"] == digest
    assert first["expected_version"] == 1
    assert first["reviewer"] == "Engineer A"
    result = release_bundle(directory, release_request(digest), "Engineer A")
    assert result["release"] == "BLOCKED"
    assert "Unsigned: port_depth" in result["blockers"]
    assert "Unsigned: sections" not in result["blockers"]
    record_decision(directory, decision("port_depth", "R"), "Engineer A")
    result = release_bundle(directory, release_request(digest), "Engineer A")
    assert result["release"] == "BLOCKED"
    assert not any(item.startswith("Unsigned:") for item in result["blockers"])
    assert {item["subject"] for item in decisions(directory)} == {"sections", "port_depth"}
    assert not (directory / "releases" / f"{digest}.json").exists()


def test_new_model_version_invalidates_earlier_unknown_signoffs(tmp_path):
    directory, _, _ = model_fixture(tmp_path)
    record_decision(directory, decision(), "Engineer A")
    record_decision(directory, decision("port_depth", "R"), "Engineer A")
    directory, _, new_digest = model_fixture(tmp_path, version=2)
    result = release_bundle(directory, release_request(new_digest, version=2), "Engineer A")
    assert result["release"] == "BLOCKED"
    assert {"Unsigned: sections", "Unsigned: port_depth"} <= set(result["blockers"])


def test_latest_rejection_overrides_prior_approval(tmp_path):
    directory, _, digest = model_fixture(tmp_path)
    record_decision(directory, decision(), "Engineer A")
    record_decision(directory, decision("port_depth", "R"), "Engineer A")
    record_decision(directory, decision(decision="REJECT"), "Engineer B")
    result = release_bundle(directory, release_request(digest), "Engineer A")
    assert result["release"] == "BLOCKED"
    assert "Rejected: sections" in result["blockers"]


def test_dataset_accuracy_gate_cannot_be_waived(tmp_path):
    checks = reference_checks()
    next(item for item in checks if item["subject"] == "accuracy_gate")["status"] = "UNKNOWN"
    directory, _, digest = model_fixture(tmp_path, checks)
    with pytest.raises(ValueError, match="evaluation report"):
        record_decision(directory, decision("accuracy_gate", "D"), "Engineer A")
    record_decision(directory, decision(), "Engineer A")
    record_decision(directory, decision("port_depth", "R"), "Engineer A")
    result = release_bundle(directory, release_request(digest), "Engineer A")
    assert result["release"] == "BLOCKED"
    assert any("paired-data acceptance" in item for item in result["blockers"])


def test_omitting_accuracy_gate_cannot_bypass_dataset_acceptance(tmp_path):
    checks = [item for item in reference_checks() if item["subject"] != "accuracy_gate"]
    directory, _, digest = model_fixture(tmp_path, checks)
    record_decision(directory, decision(), "Engineer A")
    record_decision(directory, decision("port_depth", "R"), "Engineer A")
    result = release_bundle(directory, release_request(digest), "Engineer A")
    assert result["release"] == "BLOCKED"
    assert any(
        "acceptance" in item.lower() or "accuracy" in item.lower() for item in result["blockers"]
    )


@pytest.mark.parametrize("artifact", ["model.step", "checks.json"])
def test_tampered_model_or_checks_block_review_and_release(tmp_path, artifact):
    directory, folder, digest = model_fixture(tmp_path)
    path = folder / artifact
    path.write_bytes(path.read_bytes() + b"TAMPERED")
    with pytest.raises(ValueError, match="integrity"):
        current_model(directory, 1)
    with pytest.raises(ValueError, match="integrity"):
        record_decision(directory, decision(), "Engineer A")
    with pytest.raises(ValueError, match="integrity"):
        release_bundle(directory, release_request(digest), "Engineer A")
    assert decisions(directory) == []


def test_release_rejects_a_different_manifest_hash(tmp_path):
    directory, _, _ = model_fixture(tmp_path)
    with pytest.raises(ValueError, match="exact reviewed bundle"):
        release_bundle(directory, release_request("0" * 64), "Engineer A")


def test_deleted_model_artifact_is_reported_as_integrity_failure(tmp_path):
    directory, folder, _ = model_fixture(tmp_path)
    (folder / "model.step").unlink()
    with pytest.raises(ValueError, match="integrity"):
        current_model(directory, 1)


def test_review_artifacts_remain_downloadable_as_unverified_drafts(tmp_path):
    _, folder, _ = model_fixture(tmp_path)
    with TestClient(create_app(tmp_path)) as client:
        response = client.get(f"/api/drawings/{JOB_ID}/files/step")
        assert response.status_code == 200
        assert response.content == (folder / "model.step").read_bytes()
        assert "UNVERIFIED-feature-draft-v1.step" in response.headers["content-disposition"]
        mesh = client.get(f"/api/drawings/{JOB_ID}/files/mesh")
        assert mesh.status_code == 200
        assert mesh.json()["units"] == "mm"


@pytest.mark.parametrize("route,payload_kind", [("reviews", "decision"), ("releases", "release")])
def test_review_and_release_api_require_credentials(tmp_path, monkeypatch, route, payload_kind):
    _, _, digest = model_fixture(tmp_path)
    monkeypatch.delenv("D2S_REVIEWER_TOKEN_HASHES", raising=False)
    payload = (
        decision().model_dump()
        if payload_kind == "decision"
        else release_request(digest).model_dump()
    )
    with TestClient(create_app(tmp_path)) as client:
        response = client.post(f"/api/drawings/{JOB_ID}/{route}", json=payload)
    assert response.status_code == 403


def test_authenticated_stale_review_returns_conflict(tmp_path, monkeypatch):
    directory, _, _ = model_fixture(tmp_path, version=2)
    headers = reviewer_credentials(monkeypatch)
    with TestClient(create_app(tmp_path)) as client:
        response = client.post(
            f"/api/drawings/{JOB_ID}/reviews",
            json=decision(version=1).model_dump(),
            headers=headers,
        )
    assert response.status_code == 409
    assert decisions(directory) == []


def test_authenticated_review_uses_configured_identity_and_never_records_token(
    tmp_path, monkeypatch
):
    directory, _, digest = model_fixture(tmp_path)
    headers = reviewer_credentials(monkeypatch)
    with TestClient(create_app(tmp_path)) as client:
        response = client.post(
            f"/api/drawings/{JOB_ID}/reviews", json=decision().model_dump(), headers=headers
        )
    assert response.status_code == 201
    assert response.json()["reviewer"] == "Engineer A"
    assert response.json()["manifest_sha256"] == digest
    assert "test-only-local-reviewer-token" not in json.dumps(decisions(directory))


def test_unrecognized_reviewer_token_is_rejected(tmp_path, monkeypatch):
    directory, _, _ = model_fixture(tmp_path)
    reviewer_credentials(monkeypatch)
    with TestClient(create_app(tmp_path)) as client:
        response = client.post(
            f"/api/drawings/{JOB_ID}/reviews",
            json=decision().model_dump(),
            headers={"Authorization": "Bearer wrong-token"},
        )
    assert response.status_code == 403
    assert decisions(directory) == []


def test_stale_rebuild_is_rejected_without_invalidating_current_draft(tmp_path):
    directory, _, _ = model_fixture(tmp_path, version=2)
    with TestClient(create_app(tmp_path)) as client:
        response = client.post(f"/api/drawings/{JOB_ID}/build", json={"expected_version": 1})
    assert response.status_code == 409
    state = json.loads((directory / "status.json").read_text())
    assert state["model_version"] == 2
    assert state["model_available"] is True


@pytest.mark.parametrize("error_type", [ValueError, TypeError])
def test_failed_rebuild_restores_previous_step_and_version(tmp_path, monkeypatch, error_type):
    directory, folder, digest = model_fixture(tmp_path, version=2)
    previous_step = (folder / "model.step").read_bytes()
    (directory / "audit.json").write_bytes(canonical_json({"context": {"status": "PASS"}}))
    versions = []

    def fail_build(directory, update, *, corrected_spec, version, correction):
        versions.append(version)
        update({"message": "Attempting corrected model"})
        raise error_type("Synthetic profile correction failed")

    monkeypatch.setattr("drawing2step.web_api.build_from_audit", fail_build)
    with TestClient(create_app(tmp_path)) as client:
        response = client.post(f"/api/drawings/{JOB_ID}/build", json={"expected_version": 2})
        assert response.status_code == 202
        assert response.json()["model_version"] == 3
    # App shutdown waits for its worker, avoiding timing-dependent polling here.
    assert versions == [3]
    state, current_folder, current_digest = current_model(directory, 2)
    assert state["status"] == "review"
    assert state["model_available"] is True
    assert state["download_available"] is True
    expected_message = (
        "Synthetic profile correction failed" if error_type is ValueError else "TypeError"
    )
    assert expected_message in state["message"]
    assert current_folder == folder
    assert current_digest == digest
    assert (folder / "model.step").read_bytes() == previous_step
    with TestClient(create_app(tmp_path)) as client:
        response = client.get(f"/api/drawings/{JOB_ID}/files/step")
        assert response.status_code == 200
        assert response.content == previous_step
        assert "draft-v2.step" in response.headers["content-disposition"]


def test_pending_rebuild_blocks_reviews_release_and_duplicate_builds(tmp_path, monkeypatch):
    directory, _, digest = model_fixture(tmp_path)
    (directory / "audit.json").write_bytes(canonical_json({"context": {"status": "PASS"}}))
    headers = reviewer_credentials(monkeypatch)
    entered = threading.Event()
    allow_failure = threading.Event()

    def delayed_build(directory, update, *, corrected_spec, version, correction):
        entered.set()
        if not allow_failure.wait(timeout=5):
            raise RuntimeError("Test failed to release its controlled worker")
        raise ValueError("Controlled rebuild failure")

    monkeypatch.setattr("drawing2step.web_api.build_from_audit", delayed_build)
    with TestClient(create_app(tmp_path)) as client:
        try:
            response = client.post(f"/api/drawings/{JOB_ID}/build", json={"expected_version": 1})
            assert response.status_code == 202
            assert entered.wait(timeout=5)
            state = client.get(f"/api/drawings/{JOB_ID}").json()
            assert state["status"] == "building"
            assert state["model_version"] == 2
            assert state["model_available"] is False
            for version in (1, 2):
                response = client.post(
                    f"/api/drawings/{JOB_ID}/reviews",
                    json=decision(version=version).model_dump(),
                    headers=headers,
                )
                assert response.status_code == 409
            response = client.post(
                f"/api/drawings/{JOB_ID}/releases",
                json=release_request(digest, version=2).model_dump(),
                headers=headers,
            )
            assert response.status_code == 409
            duplicate = client.post(f"/api/drawings/{JOB_ID}/build", json={"expected_version": 2})
            assert duplicate.status_code == 409
            assert decisions(directory) == []
        finally:
            allow_failure.set()
    restored, _, _ = current_model(directory, 1)
    assert restored["status"] == "review"
