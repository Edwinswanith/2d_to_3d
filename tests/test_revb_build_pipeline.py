import hashlib
import json

import pytest

from drawing2step.revb import Requirement
from drawing2step.revb_build_pipeline import build_from_audit, decode_proposal, spec_schema
from drawing2step.revb_model import DraftSpec, enrich_ledger


def test_child_readings_preserve_explicit_units_signed_exponents_and_radius():
    rows = [
        Requirement(id="R1", raw_text="1 inch", value=1, unit="in", kind="linear"),
        Requirement(id="R2", raw_text="-2", value=-2, unit="mm", kind="linear"),
        Requirement(id="R3", raw_text="1e-3", unit="mm", kind="linear"),
        Requirement(id="R4", raw_text="R.125", unit="in", kind="radius"),
    ]
    result = {r.id: r for r in enrich_ledger(rows, "mm")}
    assert result["R1_n1"].unit == "in"
    assert result["R2_n1"].value == -2
    assert float(result["R3_n1"].value) == 0.001
    assert float(result["R4_n1"].value) == 0.125
    assert result["R4_n1"].kind == "radius"
    assert result["R4_n1"].text_status == "A_ONLY"


def test_provider_schema_encodes_exclusive_provenance():
    schema = spec_schema()
    numeric = schema["$defs"]["Numeric"]
    assert numeric["additionalProperties"] is False
    assert numeric["required"] == ["mode", "value", "reason"]
    assert set(numeric["properties"]["mode"]["enum"]) == {
        "ledger",
        "expr",
        "datum",
        "centreline",
        "assumption",
    }


def source(tmp_path):
    rows = [
        Requirement(id=i, raw_text=str(v), value=v, unit="mm", kind="linear").model_dump(
            mode="json"
        )
        for i, v in [("OD", 100), ("ID", 40), ("H", 20)]
    ]
    audit = {
        "context": {"status": "PASS", "unit": "mm", "detail": "Explicit mm"},
        "requirements": rows,
        "inventory": {"features": []},
        "drawing_number": "synthetic",
        "original_sha256": "a" * 64,
    }
    raw = json.dumps(audit).encode()
    (tmp_path / "audit.json").write_bytes(raw)
    (tmp_path / "audit-manifest.json").write_text(
        json.dumps({"artifacts": {"audit.json": hashlib.sha256(raw).hexdigest()}})
    )
    spec = DraftSpec.model_validate(
        {
            "reference_face": "face_a",
            "coordinate_policy": "Z into body",
            "profile": [
                {"radius": {"expr": "OD/2"}, "z": {"datum": "face_a"}},
                {"radius": {"expr": "OD/2"}, "z": {"ledger": "H"}},
                {"radius": {"expr": "ID/2"}, "z": {"ledger": "H"}},
                {"radius": {"expr": "ID/2"}, "z": {"datum": "face_a"}},
            ],
        }
    )
    return spec


def test_corrected_version_build_is_offline_and_persists_audit_bindings(tmp_path):
    spec = source(tmp_path)
    updates = []
    result = build_from_audit(
        tmp_path,
        updates.append,
        corrected_spec=spec,
        version=2,
        correction={"reviewer": "engineer", "reason": "profile corrected", "previous_version": 1},
    )
    assert result["model_available"] and result["download_available"]
    assert result["release"] == "BLOCKED"
    assert result["model_version"] == 2
    folder = tmp_path / "models" / result["model_folder"]
    manifest = json.loads((folder / "manifest.json").read_text())
    assert (
        manifest["audit_sha256"]
        == hashlib.sha256((tmp_path / "audit.json").read_bytes()).hexdigest()
    )
    assert "spec.json" in manifest["artifacts"]
    assert "correction.json" in manifest["artifacts"]
    assert any(
        c["subject"] == "accuracy_gate" and c["status"] == "UNKNOWN" for c in result["checks"]
    )


def test_model_construction_preserves_unresolved_source_evidence(tmp_path):
    spec = source(tmp_path)
    audit = json.loads((tmp_path / "audit.json").read_text())
    audit["findings"] = [
        {
            "code": "OCR_UNAVAILABLE",
            "subject": "drawing",
            "status": "UNKNOWN",
            "detail": "Independent OCR has not run",
        }
    ]
    raw = json.dumps(audit).encode()
    (tmp_path / "audit.json").write_bytes(raw)
    (tmp_path / "audit-manifest.json").write_text(
        json.dumps({"artifacts": {"audit.json": hashlib.sha256(raw).hexdigest()}})
    )
    result = build_from_audit(tmp_path, lambda _: None, corrected_spec=spec)
    assert any(
        c["layer"] == "EVIDENCE" and "OCR_UNAVAILABLE" in c["detail"] and c["status"] == "UNKNOWN"
        for c in result["checks"]
    )


def test_tampered_evidence_cannot_build(tmp_path):
    spec = source(tmp_path)
    with (tmp_path / "audit.json").open("a") as f:
        f.write(" ")
    with pytest.raises(ValueError, match="integrity"):
        build_from_audit(tmp_path, lambda _: None, corrected_spec=spec)


def test_group_count_catches_three_ports_but_only_two_built(tmp_path):
    spec = source(tmp_path)
    audit = json.loads((tmp_path / "audit.json").read_text())
    audit["requirements"].append(
        Requirement(
            id="PORTS", raw_text="3 NOS PORTS", kind="count", unit="count", value=3
        ).model_dump(mode="json")
    )
    raw = json.dumps(audit).encode()
    (tmp_path / "audit.json").write_bytes(raw)
    (tmp_path / "audit-manifest.json").write_text(
        json.dumps({"artifacts": {"audit.json": hashlib.sha256(raw).hexdigest()}})
    )
    data = spec.model_dump(mode="json")
    data["features"] = [
        {
            "id": f"port_{a}",
            "kind": "port",
            "citations": ["PORTS"],
            "host": "outside",
            "reference_face": "face_a",
            "diameter": {"expr": "H/2"},
            "depth": {"expr": "OD/2-ID/2+H/2"},
            "z": {"expr": "H/2"},
            "angle": {"centreline": a, "reason": "synthetic centreline"},
        }
        for a in [0, 90]
    ]
    result = build_from_audit(
        tmp_path, lambda _: None, corrected_spec=DraftSpec.model_validate(data)
    )
    assert result["model_available"]
    assert result["completion"] == "PARTIAL_DRAFT_REQUIRES_REVIEW"
    assert any(
        c["layer"] == "D" and c["subject"] == "PORTS" and c["status"] == "FAIL"
        for c in result["checks"]
    )


def test_context_envelope_catches_invented_oversized_profile(tmp_path):
    spec = source(tmp_path)
    audit = json.loads((tmp_path / "audit.json").read_text())
    audit["context_proposal"] = {"envelope_value": 60, "envelope_unit": "mm"}
    raw = json.dumps(audit).encode()
    (tmp_path / "audit.json").write_bytes(raw)
    (tmp_path / "audit-manifest.json").write_text(
        json.dumps({"artifacts": {"audit.json": hashlib.sha256(raw).hexdigest()}})
    )
    result = build_from_audit(tmp_path, lambda _: None, corrected_spec=spec)
    assert any(
        c["subject"] == "context_envelope" and c["status"] == "FAIL" for c in result["checks"]
    )


def test_section_visibility_is_not_treated_as_physical_pattern_count(tmp_path):
    spec = source(tmp_path)
    audit = json.loads((tmp_path / "audit.json").read_text())
    audit["requirements"].append(
        Requirement(id="COUNT", raw_text="8 HOLES", kind="count", unit="count", value=8).model_dump(
            mode="json"
        )
    )
    audit["inventory"]["features"] = [
        {"id": "section_holes", "type": "hole_pattern", "view": "section", "count": 2}
    ]
    raw = json.dumps(audit).encode()
    (tmp_path / "audit.json").write_bytes(raw)
    (tmp_path / "audit-manifest.json").write_text(
        json.dumps({"artifacts": {"audit.json": hashlib.sha256(raw).hexdigest()}})
    )
    data = spec.model_dump(mode="json")
    data["features"] = [
        {
            "id": "holes",
            "kind": "hole_pattern",
            "citations": ["COUNT"],
            "inventory_ids": ["section_holes"],
            "host": "face_a",
            "reference_face": "face_a",
            "count": {"ledger": "COUNT"},
            "diameter": {"expr": "H/2"},
            "depth": {"ledger": "H"},
            "pcd": {"expr": "OD-H"},
            "angle": {"centreline": 0, "reason": "declared centreline"},
        }
    ]
    result = build_from_audit(
        tmp_path, lambda _: None, corrected_spec=DraftSpec.model_validate(data)
    )
    by_subject = {c["subject"]: c for c in result["checks"]}
    assert by_subject["COUNT"]["status"] == "PASS"
    assert by_subject["holes:section_holes"]["status"] == "UNKNOWN"
    assert result["completion"] == "DRAFT_REQUIRES_REVIEW"


def test_flat_provider_schema_stays_within_provider_limits_and_names_every_kind():
    schema = spec_schema()
    assert "features" in schema["required"]
    feature = schema["$defs"]["Feature"]
    assert set(feature["properties"]["kind"]["enum"]) == {
        "hole_pattern",
        "tapped_hole",
        "port",
        "bore_slot",
        "od_slot",
        "chamfer",
        "counterbore",
        "marking",
    }
    assert {"depth", "angle", "entry_diameter", "entry_depth"} <= set(feature["properties"])
    # The grouped schema (about 8 KB) was rejected by the provider; keep the flat one small.
    assert len(json.dumps(schema)) < 5000


def test_compound_port_is_buildable_but_not_yet_proposable_to_the_provider():
    # build_model/Feature support compound_port today; the prompt doesn't document it yet
    # and its segments $ref would blow the provider's schema budget. Exposing a kind with
    # no instructions would just invite malformed proposals, so it stays hidden here until
    # the prompt is written.
    schema = spec_schema()
    assert "compound_port" not in schema["$defs"]["Feature"]["properties"]["kind"]["enum"]
    assert "segments" not in schema["$defs"]["Feature"]["properties"]
    assert "PortSegment" not in schema["$defs"]


@pytest.mark.parametrize(
    "kind,missing",
    [("hole_pattern", "depth"), ("tapped_hole", "thread"), ("port", "angle")],
)
def test_flat_features_still_require_kind_specific_geometry(kind, missing):
    proposal = {
        "reference_face": "face_a",
        "coordinate_policy": "Z into body",
        "profile": [],
        "features": [
            {
                "id": "f1",
                "kind": kind,
                "citations": ["R1"],
                "host": "face_a" if kind != "port" else "outside",
                "reference_face": "face_a",
                "diameter": {"mode": "ledger", "value": "R1", "reason": ""},
                "depth": {"mode": "ledger", "value": "R1", "reason": ""},
                "pcd": {"mode": "ledger", "value": "R1", "reason": ""},
                "count": {"mode": "ledger", "value": "R1", "reason": ""},
                "z": {"mode": "ledger", "value": "R1", "reason": ""},
                "angle": {"mode": "centreline", "value": "0", "reason": "shown"},
            }
        ],
        "assumptions": [],
        "associations": [],
        "unresolved": [],
    }
    proposal["features"][0].pop(missing, None)
    with pytest.raises(ValueError, match=missing):
        decode_proposal(json.dumps(proposal))


def test_non_cardinal_centrelines_become_named_implied_centreline_assumptions():
    proposal = {
        "features": [
            {"angle": {"mode": "centreline", "value": a, "reason": "shown at this angle"}}
            for a in ["135", "45"]
        ]
    }
    with pytest.raises(ValueError) as caught:
        decode_proposal(json.dumps(proposal))
    # The angles are no longer the rejection; only the missing top-level fields are.
    assert "centreline value" not in str(caught.value)
    proposal["features"] = [{"angle": {"mode": "centreline", "value": "west", "reason": ""}}]
    with pytest.raises(ValueError, match=r"\$\.features\[0\]\.angle: centreline value 'west'"):
        decode_proposal(json.dumps(proposal))


@pytest.mark.parametrize("field,value", [("mode", []), ("mode", {}), ("reason", []), ("reason", 0)])
def test_malformed_numeric_wire_types_are_retryable(field, value):
    numeric = {"mode": "ledger", "value": "R1", "reason": "source"}
    numeric[field] = value
    with pytest.raises(ValueError, match="invalid numeric wire encoding"):
        decode_proposal(json.dumps({"profile": [{"z": numeric}]}))


def test_grouped_recorded_proposal_builds_and_records_provider_response(tmp_path):
    draft = source(tmp_path)
    (tmp_path / "drawing.png").write_bytes(b"synthetic fixture only")
    proposal = draft.model_dump(mode="json")
    for key in ["schema_version", "provenance"]:
        proposal.pop(key)

    def wire(value):
        if isinstance(value, list):
            return [wire(v) for v in value]
        if not isinstance(value, dict):
            return value
        if "ledger" in value:
            mode = next(
                k
                for k in ["ledger", "expr", "datum", "centreline", "assumption"]
                if value.get(k) is not None
            )
            return {"mode": mode, "value": str(value[mode]), "reason": value.get("reason") or ""}
        return {k: wire(v) for k, v in value.items()}

    proposal = wire(proposal)
    calls = []

    def provider(image, role, prompt, schema):
        calls.append(role)
        assert "LEDGER" in prompt and "INDEPENDENT INVENTORY" in prompt
        return {
            "candidates": [
                {"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(proposal)}]}}
            ]
        }

    result = build_from_audit(tmp_path, lambda _: None, provider=provider)
    assert calls == ["spec"]
    folder = tmp_path / "models" / result["model_folder"]
    assert (folder / "response-1.json").exists()
    assert json.loads((folder / "inputs.json").read_text())["spec_source"] == "model_proposal"
    assert result["release"] == "BLOCKED" and result["model_available"]
