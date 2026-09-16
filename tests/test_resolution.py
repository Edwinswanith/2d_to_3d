from decimal import Decimal

import pytest

from drawing2step.body_cad import BodySpec, build_verified, evaluate_number
from drawing2step.resolution import reconcile_run


def test_safe_derivations_reject_code_and_dimensional_errors():
    ledger = {
        "R1": {"value": "120", "kind": "diameter", "unit": "mm"},
        "R2": {"value": "80", "kind": "diameter", "unit": "mm"},
    }
    assert evaluate_number({"expr": "(R1 - R2) / 2"}, ledger) == Decimal(20)
    with pytest.raises(ValueError):
        evaluate_number({"expr": "__import__('os')"}, ledger)
    with pytest.raises(ValueError):
        evaluate_number({"expr": "R1 + 2"}, ledger)
    with pytest.raises(ValueError):
        evaluate_number({"ledger": "R404"}, ledger)
    for expression in ("R1/1e309", "R1/1e-309", "R1*float('inf')"):
        with pytest.raises(ValueError):
            evaluate_number({"expr": expression}, ledger)
    hyphenated = {"R-0101": ledger["R1"], "R-0107": ledger["R2"]}
    assert evaluate_number({"ledger": "R-0101"}, hyphenated) == 120
    assert evaluate_number({"expr": "(R-0101 - R-0107) / 2"}, hyphenated) == 20


def fixture():
    return BodySpec.model_validate(
        {
            "synthetic": True,
            "ledger": {
                "OD": {"value": "120", "kind": "diameter", "unit": "mm"},
                "ID": {"value": "80", "kind": "diameter", "unit": "mm"},
                "L": {"value": "16", "kind": "linear", "unit": "mm"},
            },
            "stations": [
                {"z": {"expr": "L-L"}, "od": {"ledger": "OD"}, "id": {"ledger": "ID"}},
                {"z": {"ledger": "L"}, "od": {"ledger": "OD"}, "id": {"ledger": "ID"}},
            ],
        }
    )


def test_ring_step_roundtrip_and_wrong_reference_detected(tmp_path):
    report = build_verified(fixture(), tmp_path)
    assert report["build_validity"] == report["V2"] == "PASS"
    assert report["V1"] == "UNKNOWN"
    assert report["release"] == "BLOCKED"
    assert report["fresh_measurements"]["bbox"] == pytest.approx([120, 120, 16])
    assert report["reference_step"] == "UNKNOWN"
    assert (tmp_path / "evaluation-body.step").is_file()
    import cadquery as cq

    wrong = cq.Workplane("XY").circle(60).circle(40).extrude(16).translate((10, 0, 0))
    cq.exporters.export(wrong, str(tmp_path / "wrong.step"))
    result = build_verified(fixture(), tmp_path / "compared", tmp_path / "wrong.step")
    assert result["reference_step"]["status"] == "FAIL"


def test_profile_nesting_and_missing_citations_block_build(tmp_path):
    raw = fixture().model_dump(mode="json")
    raw["ledger"]["ID"]["value"] = "140"
    with pytest.raises(ValueError, match="nest"):
        build_verified(BodySpec.model_validate(raw), tmp_path)
    raw = fixture().model_dump(mode="json")
    raw["stations"][1]["z"] = {"ledger": "missing"}
    with pytest.raises(ValueError, match="Unknown"):
        build_verified(BodySpec.model_validate(raw), tmp_path)


def test_real_drawing_requires_engineer_unit_decision(tmp_path):
    raw = fixture().model_dump(mode="json")
    raw["synthetic"] = False
    with pytest.raises(ValueError, match="engineer"):
        build_verified(BodySpec.model_validate(raw), tmp_path)


def test_reconciliation_keeps_ocr_and_no_false_acceptance(tmp_path):
    import json

    run = tmp_path / "run"
    (run / "independent-local-ocr").mkdir(parents=True)
    (run / "reading.json").write_text(
        json.dumps(
            {
                "callouts": [
                    {
                        "id": "c1",
                        "raw_text": "20.0",
                        "kind": "linear",
                        "value_printed": "20.0",
                        "unit_printed": "",
                        "tolerance_printed": "",
                        "feature_proposal": "width",
                        "box": [100, 100, 200, 200],
                    }
                ]
            }
        )
    )
    (run / "independent-local-ocr/tokens.json").write_text(
        json.dumps(
            [
                {"id": "B1", "raw_text": "20.0", "box": [100, 100, 200, 200], "confidence": 90},
                {"id": "B2", "raw_text": "extra", "box": [400, 400, 450, 450], "confidence": 50},
            ]
        )
    )
    output = reconcile_run(run)
    result = json.loads((output / "reconciled.json").read_text())
    assert len(result["ocr_observations"]) == 2
    assert result["requirements"][0]["text_status"] == "CHARACTERS_CORROBORATED"
    assert not result["requirements"][0]["geometry_accepted"]
    assert result["requirements"][0]["association_status"] == "REVIEW"
    from drawing2step.association_review import validate_review

    review = {
        "schema_version": "engineer-decisions-v1",
        "source_fingerprint": result["source_fingerprint"],
        "reviewer": "Test engineer",
        "unit_decision": "mm",
        "unit_reason": "Synthetic test decision",
        "whole_sheet_complete": False,
        "decisions": [
            {
                "id": "c1",
                "confirmed_value": "20",
                "confirmed_unit": "mm",
                "confirmed_tolerance": "No tolerance for synthetic test",
                "feature_id": "axial-distance",
                "feature_type": "linear",
                "reason": "Test",
            }
        ],
    }
    path = tmp_path / "review.json"
    import hashlib

    review["reconciliation_sha256"] = hashlib.sha256(
        (output / "reconciled.json").read_bytes()
    ).hexdigest()
    path.write_text(json.dumps(review))
    validated = validate_review(output, path)
    assert (
        json.loads((validated / "validated-associations.json").read_text())["release"] == "BLOCKED"
    )
    review["source_fingerprint"] = "stale"
    path.write_text(json.dumps(review))
    with pytest.raises(ValueError, match="Stale"):
        validate_review(output, path)
    review["source_fingerprint"] = result["source_fingerprint"]
    review["decisions"][0]["feature_type"] = "diameter"
    path.write_text(json.dumps(review))
    with pytest.raises(ValueError, match="incompatible"):
        validate_review(output, path)
    review["decisions"][0]["feature_type"] = "linear"
    path.write_text(json.dumps(review))
    ledger_path = output / "reconciled.json"
    changed = json.loads(ledger_path.read_text())
    changed["requirements"][0]["box"] = [0, 0, 1, 1]
    ledger_path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="Stale"):
        validate_review(output, path)
