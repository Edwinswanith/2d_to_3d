"""Accuracy scoring against a transcribed truth, on synthetic runs only."""

import json
from decimal import Decimal
from pathlib import Path

from drawing2step.revb import Requirement
from drawing2step.revb_accuracy import (
    DrawingTruth,
    dimension_coverage,
    score_run,
    score_runs,
    scoreboard,
)
from drawing2step.revb_model import DraftSpec, build_model, enrich_ledger
from drawing2step.revb_proposal import cited_ids


def requirement(identifier, value, kind="linear", unit="mm"):
    return Requirement(
        id=identifier,
        raw_text=f"Ø{value}" if kind == "diameter" else f"{value}",
        kind=kind,
        value=Decimal(str(value)),
        unit=unit,
        sources=(f"synthetic:{identifier}",),
        text_status="AGREED",
    )


def ledger():
    return [
        requirement("OD", 100, "diameter"),
        requirement("ID", 40, "diameter"),
        requirement("H", 20),
        requirement("HD", 6, "diameter"),
        requirement("PCD", 80, "diameter"),
        requirement("N", 4, "count", "count"),
        requirement("N2", 2, "count", "count"),
        requirement("GROOVE", 3),  # printed but never used by the draft
    ]


def draft(features: list[dict]) -> DraftSpec:
    return DraftSpec.model_validate(
        {
            "reference_face": "face_A",
            "coordinate_policy": "Z increases from face A",
            "profile": [
                {"z": {"datum": "face_A"}, "radius": {"expr": "OD/2"}},
                {"z": {"ledger": "H"}, "radius": {"expr": "OD/2"}},
                {"z": {"ledger": "H"}, "radius": {"expr": "ID/2"}},
                {"z": {"datum": "face_A"}, "radius": {"expr": "ID/2"}},
            ],
            "features": features,
        }
    )


def holes(count_id: str = "N", pcd: dict | None = None) -> dict:
    return {
        "id": "bolts",
        "kind": "hole_pattern",
        "citations": ["HD", "PCD", count_id, "H"],
        "diameter": {"ledger": "HD"},
        "depth": {"ledger": "H"},
        "pcd": pcd or {"ledger": "PCD"},
        "count": {"ledger": count_id},
        "angle": {"centreline": 0, "reason": "on the horizontal centreline"},
        "host": "face_a",
        "reference_face": "face_A",
    }


def port() -> dict:
    return {
        "id": "quench",
        "kind": "port",
        "citations": ["HD", "H"],
        "thread": ".375 NPT",
        "diameter": {"ledger": "HD"},
        "depth": {"expr": "OD/2-ID/2"},
        "z": {"expr": "H/2"},
        "angle": {"centreline": 0, "reason": "shown on the horizontal centreline"},
        "entry_depth": {"ledger": "HD"},
        "host": "outside",
        "reference_face": "face_A",
    }


def truth(**overrides) -> DrawingTruth:
    data = {
        "drawing_number": "SYN-1",
        "unit": "mm",
        "body": {"outer_diameter": 100, "length": 20},
        "features": [
            {
                "id": "bolt_holes",
                "kind": "hole_pattern",
                "count": 4,
                "diameter": 6,
                "pcd": 80,
                "through": True,
            },
            {"id": "quench_port", "kind": "port", "count": 1, "thread": "3/8 NPT", "diameter": 6},
            {"id": "knurl", "kind": "knurl", "count": 2, "diameter": 12},
        ],
        "printed_dimensions": [
            {"kind": "diameter", "value": 100},
            {"kind": "linear", "value": 3},
            {"kind": "linear", "value": 7.77},
        ],
    }
    data.update(overrides)
    return DrawingTruth.model_validate(data)


def recorded_run(tmp_path: Path, spec: DraftSpec, name: str = "SYN-1-v1") -> Path:
    run = tmp_path / name
    folder = run / "models" / "abc123"
    rows = enrich_ledger(ledger(), "mm")
    result = build_model(spec, rows, folder)
    status = {
        "drawing_number": "SYN-1",
        "status": "review",
        "message": "synthetic",
        "model_folder": "abc123",
        "checks": result["checks"]
        + [{"layer": "D", "subject": "x", "status": "FAIL", "detail": ""}],
    }
    (run / "status.json").write_text(json.dumps(status))
    return run


def test_captured_partial_missing_and_unsupported_features_are_told_apart(tmp_path):
    run = recorded_run(tmp_path, draft([holes(), port()]))
    result = score_run(run, truth())
    by_id = {f["id"]: f for f in result["features"]}
    assert by_id["bolt_holes"]["status"] == "captured"
    assert by_id["bolt_holes"]["matched"] == ["bolts"]
    assert by_id["bolt_holes"]["placement"] == "verified"
    assert by_id["quench_port"]["status"] == "captured"  # 3/8 NPT equals .375 NPT
    assert by_id["knurl"]["status"] == "unsupported"
    assert result["body"]["outer_diameter_ok"] and result["body"]["length_ok"]
    summary = result["summary"]
    assert summary["captured"] == 2 and summary["unsupported"] == 1
    assert summary["feature_recall"] == round(2 / 3, 3)
    assert summary["failed_checks"] == 1


def test_wrong_numbers_are_partial_with_named_deviations_and_wrong_placement(tmp_path):
    # Pattern of 2 on a Ø68 circle where the sheet prints 4 on Ø80.
    run = recorded_run(tmp_path, draft([holes("N2", {"expr": "PCD-HD-HD"})]))
    result = score_run(run, truth())
    bolts = {f["id"]: f for f in result["features"]}["bolt_holes"]
    assert bolts["status"] == "partial" and bolts["matched"] == ["bolts"]
    assert any("pcd built 68.000 mm, printed 80.000 mm" in d for d in bolts["deviations"])
    assert any("built 2 of 4 printed instances" in d for d in bolts["deviations"])
    assert bolts["placement"] == "wrong"
    assert {f["id"]: f for f in result["features"]}["quench_port"]["status"] == "missing"


def test_printed_dimensions_split_into_cited_uncited_and_unread():
    rows = enrich_ledger(ledger(), "mm")
    coverage = dimension_coverage(truth(), rows, cited_ids(draft([holes()])))
    assert [(d["value"], d["status"]) for d in coverage] == [
        (100, "cited"),
        (3, "uncited"),
        (7.77, "unread"),
    ]


def test_runs_without_a_model_still_score_reading_and_count_everything_missing(tmp_path):
    run = tmp_path / "SYN-1-v0"
    run.mkdir()
    audit = {
        "context": {"unit": "mm"},
        "requirements": [r.model_dump(mode="json") for r in ledger()],
    }
    (run / "audit.json").write_text(json.dumps(audit))
    (run / "status.json").write_text(json.dumps({"drawing_number": "SYN-1", "status": "review"}))
    result = score_run(run, truth())
    assert not result["model_available"] and result["body"] is None
    assert {f["status"] for f in result["features"]} == {"missing"}
    assert result["summary"]["unread"] == 1 and result["summary"]["uncited"] == 2


def test_scoreboard_scores_only_runs_with_a_truth_file(tmp_path):
    truth_dir = tmp_path / "truth"
    truth_dir.mkdir()
    (truth_dir / "SYN-1.json").write_text(truth().model_dump_json())
    run = recorded_run(tmp_path, draft([holes(), port()]))
    other = tmp_path / "OTHER-v1"
    other.mkdir()
    (other / "status.json").write_text(json.dumps({"drawing_number": "OTHER"}))
    rows = score_runs([run, other, tmp_path / "absent"], truth_dir)
    assert [r["run"] for r in rows] == ["SYN-1-v1"]
    table = scoreboard(rows)
    assert "SYN-1-v1" in table and "2/0/0/1 of 3" in table and "1/0/1/1 of 3" in table


def test_limit_pair_nominals_used_through_an_assumption_count_as_used():
    rows = enrich_ledger(ledger() + [requirement("PAIR", 30.02), requirement("LO", 29.98)], "mm")
    with_nominal = truth(
        printed_dimensions=[{"kind": "linear", "value": 30.02}, {"kind": "linear", "value": 3}]
    )
    coverage = dimension_coverage(with_nominal, rows, set(), [(30.0, 0.3)])
    assert [d["status"] for d in coverage] == ["assumed", "uncited"]


def test_runs_that_stopped_before_reading_score_without_crashing(tmp_path):
    run = tmp_path / "SYN-1-v0"
    run.mkdir()
    (run / "status.json").write_text(json.dumps({"drawing_number": "SYN-1", "status": "error"}))
    result = score_run(run, truth())
    assert result["summary"]["unread"] == 3 and result["summary"]["missing"] == 3


def chamfer(identifier: str, z: dict, radius: dict) -> dict:
    return {
        "id": identifier,
        "kind": "chamfer",
        "citations": ["HD", "H"],
        "z": z,
        "radius": radius,
        "width": {"expr": "HD/2"},
        "angle": {"assumption": "A45"},
        "host": "outside",
        "reference_face": "face_A",
    }


def test_per_edge_features_accumulate_instances_against_a_printed_count(tmp_path):
    two = draft(
        [
            chamfer("c1", {"datum": "face_A"}, {"expr": "OD/2"}),
            chamfer("c2", {"ledger": "H"}, {"expr": "OD/2"}),
        ]
    )
    two = DraftSpec.model_validate(
        {
            **two.model_dump(mode="json"),
            "assumptions": [
                {
                    "id": "A45",
                    "item": "chamfer angle",
                    "value": 45,
                    "unit": "degree",
                    "type": "ASSUMED",
                    "reason": "shown",
                    "source": "sheet",
                }
            ],
        }
    )
    run = recorded_run(tmp_path, two)
    printed = truth(
        features=[{"id": "chamfers", "kind": "chamfer", "count": 2, "width": 3, "angle": 45}]
    )
    result = score_run(run, printed)
    chamfers = result["features"][0]
    assert chamfers["status"] == "captured", chamfers
    assert chamfers["matched"] == ["c1", "c2"]
    one_printed = truth(
        features=[{"id": "chamfers", "kind": "chamfer", "count": 4, "width": 3, "angle": 45}]
    )
    partial = score_run(run, one_printed)["features"][0]
    assert partial["status"] == "partial"
    assert "built 2 of 4 printed instances" in partial["deviations"]


def test_a_through_hole_in_a_thin_flange_verifies_and_a_short_one_does_not(tmp_path):
    # Flange Ø100 for z 0..10 only; hub Ø80 beyond. Holes on Ø90 through the flange are THRU
    # even though they span a quarter of the body; the same holes 5 mm deep are not.
    def stepped(depth_expr: str) -> DraftSpec:
        holes_ = holes()
        holes_["pcd"] = {"expr": "PCD+HD+HD-HD/2"}  # Ø89
        holes_["depth"] = {"expr": depth_expr}
        return DraftSpec.model_validate(
            {
                **draft([holes_]).model_dump(mode="json"),
                "profile": [
                    {"z": {"datum": "face_A"}, "radius": {"expr": "OD/2"}},
                    {"z": {"expr": "H/2"}, "radius": {"expr": "OD/2"}},
                    {"z": {"expr": "H/2"}, "radius": {"expr": "PCD/2"}},
                    {"z": {"ledger": "H"}, "radius": {"expr": "PCD/2"}},
                    {"z": {"ledger": "H"}, "radius": {"expr": "ID/2"}},
                    {"z": {"datum": "face_A"}, "radius": {"expr": "ID/2"}},
                ],
            }
        )

    printed = truth(
        features=[
            {
                "id": "bolt_holes",
                "kind": "hole_pattern",
                "count": 4,
                "diameter": 6,
                "pcd": 89,
                "through": True,
            }
        ]
    )
    through = score_run(recorded_run(tmp_path, stepped("H/2"), "SYN-1-v1"), printed)
    assert through["features"][0]["placement"] == "verified"
    short = score_run(recorded_run(tmp_path, stepped("H/2-HD+HD/2"), "SYN-1-v2"), printed)
    assert short["features"][0]["placement"] == "wrong"
