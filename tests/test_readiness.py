import json

import pytest

from drawing2step.cli import main
from drawing2step.preflight import assess, inventory, load_manifest
from drawing2step.storage import ArtifactStore


def test_empty_readiness_blocks_every_gate(tmp_path):
    report = assess({}, tmp_path)
    assert report["customer_pipeline"] == "BLOCKED"
    assert report["cad"] == "BLOCKED"
    assert report["release"] == "BLOCKED"
    assert len(report["blockers"]) >= 5


def test_inventory_hashes_pairs_without_filename_identity(tmp_path):
    (tmp_path / "misleading.pdf").write_bytes(b"%PDF-1.7 test")
    (tmp_path / "part.step").write_text("ISO-10303-21;\nEND-ISO-10303-21;")
    raw = {
        "pairs": [
            {
                "id": "sample",
                "owner": "shop",
                "group": "design-1",
                "drawing": "misleading.pdf",
                "step": "part.step",
                "split": "development",
                "quality": "clean",
            }
        ]
    }
    result = inventory(raw, tmp_path)
    pair = result["pairs"][0]
    assert len(pair["drawing_sha256"]) == 64
    assert pair.get("drawing_number") is None


def test_inventory_rejects_leakage(tmp_path):
    (tmp_path / "d.pdf").write_bytes(b"%PDF-1.7 test")
    (tmp_path / "d.step").write_text("ISO-10303-21;\nEND-ISO-10303-21;")
    pair = {
        "owner": "shop",
        "group": "same",
        "drawing": "d.pdf",
        "step": "d.step",
        "quality": "clean",
    }
    with pytest.raises(ValueError, match="leakage|Duplicate"):
        inventory(
            {
                "pairs": [
                    dict(pair, id="a", split="development"),
                    dict(pair, id="b", split="held_out"),
                ]
            },
            tmp_path,
        )


def test_missing_file_is_actionable(tmp_path):
    with pytest.raises(ValueError, match="missing"):
        inventory(
            {
                "pairs": [
                    {
                        "id": "a",
                        "owner": "shop",
                        "group": "a",
                        "drawing": "missing.pdf",
                        "step": "missing.step",
                        "split": "development",
                        "quality": "scan",
                    }
                ]
            },
            tmp_path,
        )


def test_immutable_artifact_integrity_and_traversal(tmp_path):
    store = ArtifactStore(tmp_path)
    digest = store.put(b"original")
    assert store.put(b"original") == digest
    assert store.get(digest) == b"original"
    with pytest.raises(ValueError):
        store.get("../../private")
    (tmp_path / digest).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="integrity"):
        store.get(digest)
    with pytest.raises(ValueError, match="integrity"):
        store.put(b"original")


def test_template_cli_and_blocked_exit(tmp_path, capsys):
    target = tmp_path / "prerequisites.json"
    assert main(["init", str(target)]) == 0
    assert main(["init", str(target)]) == 2
    assert main(["readiness", str(target)]) == 2
    assert "BLOCKED" in capsys.readouterr().out
    assert load_manifest(target)["pairs"] == []
    assert json.loads(target.read_text())["schema_version"] == "prerequisites-v1"
