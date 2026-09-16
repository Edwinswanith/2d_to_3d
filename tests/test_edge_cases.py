import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from pydantic import ValidationError

from drawing2step.cli import main
from drawing2step.evaluation import evaluate
from drawing2step.lab import demo_cases, load_cases, run_demo
from drawing2step.models import Box, EvalCase, Requirement
from drawing2step.precedence import reconcile
from drawing2step.preflight import assess, inventory
from drawing2step.report import render_report
from drawing2step.storage import ArtifactStore


def test_unit_conversion_and_exact_tolerances():
    base = demo_cases()[0].truth[0]
    metric = base.model_copy(update={"value": Decimal("25.4")})
    imperial = base.model_copy(update={"value": Decimal(1), "unit": "in"})
    assert metric.signature() == imperial.signature()
    assert (
        metric.signature()
        != imperial.model_copy(update={"tolerance_plus": Decimal("0.01")}).signature()
    )


def test_invalid_units_counts_and_unknown_schema_fields():
    raw = demo_cases()[0].truth[0].model_dump()
    for update in (
        {"unit": "degree"},
        {"kind": "count", "unit": "count", "value": "1.5"},
        {"kind": "count", "unit": "count", "value": "0"},
        {"unknown": 3},
    ):
        with pytest.raises(ValidationError):
            Requirement.model_validate(raw | update)


def test_wrong_note_is_an_accepted_reading_error():
    case = demo_cases()[0]
    note = Requirement(
        id="note",
        kind="note",
        raw_text="REMOVE BURRS",
        box=case.truth[0].box,
        geometry_driving=False,
        text_status="ACCEPTED",
        interpretation_status="SUPPORTED",
    )
    wrong = note.model_copy(update={"raw_text": "DO NOT REMOVE BURRS"})
    case = case.model_copy(update={"truth": (note,), "predictions": (wrong,)})
    score = evaluate([case])["cohorts"]["clean"]
    assert score["accepted_value_error"] == 1
    assert not score["reading_thresholds_met"]


def test_incomplete_numeric_truth_is_rejected_and_prediction_is_wrong():
    case = demo_cases()[0]
    raw = case.model_dump()
    raw["truth"][0]["value"] = None
    with pytest.raises(ValidationError, match="Numeric ground truth"):
        EvalCase.model_validate(raw)
    incomplete = case.predictions[0].model_copy(update={"value": None, "unit": None})
    case = case.model_copy(update={"predictions": (incomplete,)})
    assert evaluate([case])["cohorts"]["clean"]["accepted_value_error"] == 1


def test_synthetic_declaration_required():
    raw = demo_cases()[0].model_dump()
    del raw["synthetic"]
    with pytest.raises(ValidationError):
        EvalCase.model_validate(raw)


def test_different_pages_do_not_match():
    box = Box(page=1, x0=1, y0=1, x1=10, y1=10)
    assert box.overlap(box.model_copy(update={"page": 2})) == 0


def test_precedence_empty_internal_conflict_and_native_disagreement():
    assert reconcile([]).status == "UNREAD"
    a, b = demo_cases()[0].predictions[0].evidence
    assert reconcile([a, a.model_copy(update={"value": Decimal(1)})]).status == "CONFLICT"
    native = a.model_copy(update={"source": "native", "value": Decimal(1)})
    assert not reconcile([a, b, native]).accepted
    assert not reconcile([a.model_copy(update={"source": "escalation"})]).accepted


def test_native_agreement_precedence():
    a, b = demo_cases()[0].predictions[0].evidence
    native = a.model_copy(update={"source": "native"})
    disagreeing_b = b.model_copy(update={"value": Decimal(1)})
    assert reconcile([native, a, disagreeing_b]).status == "NATIVE_CONFIRMED"


def test_duplicate_cases_leakage_and_real_data_rejected():
    case = demo_cases()[0]
    with pytest.raises(ValueError, match="Duplicate"):
        evaluate([case, case])
    with pytest.raises(ValueError, match="leakage"):
        evaluate([case, case.model_copy(update={"id": "other", "split": "held_out"})])
    with pytest.raises(ValueError, match="synthetic"):
        evaluate([case.model_copy(update={"synthetic": False})])
    with pytest.raises(ValidationError, match="Duplicate"):
        EvalCase.model_validate(case.model_dump() | {"truth": [case.truth[0], case.truth[0]]})


def test_report_escapes_untrusted_text():
    case = demo_cases()[0]
    requirement = case.predictions[0].model_copy(update={"raw_text": '<script>alert("x")</script>'})
    case = case.model_copy(update={"id": "<img src=x>", "predictions": (requirement,)})
    report = render_report([case], evaluate([case]))
    assert "<script>" not in report
    assert "<img src=x>" not in report
    assert "&lt;script&gt;" in report
    assert "Content-Security-Policy" in report


def test_atomic_duplicate_writes_and_symlink_refusal(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    with ThreadPoolExecutor(max_workers=8) as pool:
        digests = list(pool.map(store.put, [b"same content"] * 20))
    assert len(set(digests)) == 1
    assert store.get(digests[0]) == b"same content"
    target = tmp_path / "target"
    target.write_bytes(b"same content")
    artifact = store.root / digests[0]
    artifact.unlink()
    artifact.symlink_to(target)
    with pytest.raises(ValueError, match="integrity"):
        store.get(digests[0])
    with pytest.raises(ValueError, match="integrity"):
        store.put(b"same content")


def test_schema_and_eval_cli(tmp_path, capsys):
    assert main(["schema", "prerequisites"]) == 0
    assert json.loads(capsys.readouterr().out)["title"] == "Prerequisites"
    assert main(["schema", "eval"]) == 0
    assert json.loads(capsys.readouterr().out)["title"] == "EvalCase"
    assert main(["demo", "--output", str(tmp_path)]) == 0
    run = capsys.readouterr().out.strip()
    assert main(["eval", run + "/cases.json", "--output", str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip() == run
    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    with pytest.raises(ValueError, match="array"):
        load_cases(bad)
    bad.write_text("invalid json")
    assert main(["eval", str(bad)]) == 2


def test_corrupted_run_is_not_overwritten(tmp_path):
    output = run_demo(tmp_path)
    (output / "report.html").write_text("tampered")
    with pytest.raises(ValueError, match="integrity"):
        run_demo(tmp_path)


def manifest_with_pairs(tmp_path, count=30):
    pairs = []
    for index in range(count):
        (tmp_path / f"{index}.pdf").write_bytes(f"%PDF-1.7 fixture {index}".encode())
        (tmp_path / f"{index}.step").write_text(f"ISO-10303-21; fixture {index}")
        pairs.append(
            {
                "id": str(index),
                "owner": "shop",
                "group": str(index),
                "drawing": f"{index}.pdf",
                "step": f"{index}.step",
                "split": "development" if index < 20 else "held_out",
                "quality": "clean",
            }
        )
    return {"pairs": pairs}


def test_recorded_prerequisites_never_unlock_cad_or_release(tmp_path):
    # These dummy files test record validation, not real dataset qualification.
    raw = inventory(manifest_with_pairs(tmp_path), tmp_path)
    (tmp_path / "permission.txt").write_text("Synthetic permission fixture, not legal permission")
    raw.update(
        permissions=[
            {
                "owner": "shop",
                "document": "permission.txt",
                "confirmed_by": "Reviewer",
                "cloud_processing_allowed": True,
                "drawing_sha256": [p["drawing_sha256"] for p in raw["pairs"]],
            }
        ],
        baseline=[{"pair_id": str(i), "engineer": "Engineer", "minutes": 20} for i in range(5)],
        gland_ring_workload_share=0.7,
        ground_truth_reviewer="Engineer",
        release_reviewers=["Engineer"],
    )
    result = assess(raw, tmp_path)
    assert result["customer_pipeline"] == "PREREQUISITES_RECORDED"
    assert result["cad"] == result["release"] == "BLOCKED"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(raw))
    assert main(["readiness", str(manifest)]) == 0
    assert main(["inventory", str(manifest)]) == 0
    raw["permissions"][0]["drawing_sha256"].pop()
    assert assess(raw, tmp_path)["customer_pipeline"] == "BLOCKED"
    raw["permissions"][0]["document"] = "missing.txt"
    assert "missing" in " ".join(assess(raw, tmp_path)["blockers"])


@pytest.mark.parametrize(
    "mutation,expected",
    [
        ("wrong_hash", "hash mismatch"),
        ("duplicate_id", "Duplicate pair"),
        ("duplicate_content", "Duplicate drawing"),
        ("wrong_extension", "extension"),
        ("wrong_step_extension", "STEP file"),
        ("empty", "empty"),
    ],
)
def test_invalid_inventory(tmp_path, mutation, expected):
    raw = manifest_with_pairs(tmp_path, 2)
    first, second = raw["pairs"]
    if mutation == "wrong_hash":
        first["drawing_sha256"] = "0" * 64
    elif mutation == "duplicate_id":
        second["id"] = first["id"]
    elif mutation == "duplicate_content":
        second["drawing"] = first["drawing"]
    elif mutation == "wrong_extension":
        first["drawing"] = first["step"]
    elif mutation == "wrong_step_extension":
        first["step"] = first["drawing"]
    else:
        (tmp_path / first["drawing"]).write_bytes(b"")
    with pytest.raises(ValueError, match=expected):
        inventory(raw, tmp_path)
    assert assess(raw, tmp_path)["customer_pipeline"] == "BLOCKED"
