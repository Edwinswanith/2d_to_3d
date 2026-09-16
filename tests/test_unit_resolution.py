import hashlib
import json

import pytest

from drawing2step.unit_resolution import resolve_units


def test_confirmed_default_preserves_explicit_thread_and_evidence(tmp_path):
    original = b"%PDF-fixture"
    (tmp_path / "original.pdf").write_bytes(original)
    (tmp_path / "summary.json").write_text(
        json.dumps(
            {"original_sha256": hashlib.sha256(original).hexdigest(), "title_block_units": "INCHES"}
        )
    )
    entries = [
        {"id": "A", "kind": "diameter", "unit_printed": "", "raw_text": "218.0"},
        {"id": "B", "kind": "linear", "unit_printed": "in", "raw_text": "1 in"},
        {"id": "C", "kind": "thread", "unit_printed": "", "raw_text": ".500 NPT"},
    ]
    payload = json.dumps(entries).encode()
    (tmp_path / "ledger.json").write_bytes(payload)
    output = resolve_units(tmp_path, "mm", "User", "User confirmed millimetres")
    result = json.loads((output / "ledger.json").read_text())
    assert result[0]["resolved_unit"] == "mm"
    assert result[1]["resolved_unit"] == "in"
    assert result[2]["resolved_unit"] is None
    assert result[2]["raw_text"] == ".500 NPT"
    assert all(not item["accepted"] for item in result)
    assert (tmp_path / "ledger.json").read_bytes() == payload
    assert resolve_units(tmp_path, "mm", "User", "User confirmed millimetres") == output
    (tmp_path / "original.pdf").write_bytes(b"changed")
    with pytest.raises(ValueError, match="integrity"):
        resolve_units(tmp_path, "mm", "User", "Confirmed")


def test_missing_confirmer_does_not_create_decision(tmp_path):
    with pytest.raises(ValueError):
        resolve_units(tmp_path, "mm", "", "Reason")
