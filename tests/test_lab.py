from decimal import Decimal

import pytest
from pydantic import ValidationError

from drawing2step.evaluation import evaluate
from drawing2step.lab import demo_cases, run_demo
from drawing2step.models import Box, Observation
from drawing2step.precedence import reconcile


def observation(source="a", value="20", **kwargs):
    return Observation(
        id=f"{source}-{value}",
        source=source,
        source_version="fixture-v1",
        raw_text=f"diameter {value}",
        value=Decimal(value),
        kind="diameter",
        unit="mm",
        box=Box(page=1, x0=10, y0=10, x1=30, y1=20),
        **kwargs,
    )


@pytest.mark.parametrize(
    "sources,status,accepted",
    [
        (["a", "b"], "AGREED", True),
        (["native", "a"], "NATIVE_CONFIRMED", True),
        (["native", "b"], "NATIVE_CONFIRMED", True),
        (["a"], "A_ONLY", False),
        (["b"], "B_ONLY", False),
        (["native"], "SINGLE_SOURCE", False),
        (["a", "a"], "A_ONLY", False),
    ],
)
def test_independent_agreement(sources, status, accepted):
    result = reconcile([observation(s) for s in sources])
    assert result.status == status
    assert result.accepted is accepted


def test_escalation_never_resolves_geometry_conflict():
    result = reconcile([observation("a"), observation("b", "21"), observation("escalation")])
    assert result.status == "CONFLICT"
    assert not result.accepted


def test_native_digit_agreement_does_not_resolve_tolerance_conflict():
    result = reconcile([observation("native"), observation("a", tolerance_plus=Decimal("0.1"))])
    assert not result.accepted


def test_non_geometry_escalation_can_resolve():
    result = reconcile(
        [
            observation("a", geometry_driving=False),
            observation("b", "21", geometry_driving=False),
            observation("escalation", geometry_driving=False),
        ]
    )
    assert result.accepted
    assert result.status == "RESOLVED_BY_ESCALATION"


def test_unresolved_units_do_not_pass():
    a = observation().model_copy(update={"unit": None})
    b = observation("b").model_copy(update={"unit": None})
    assert not reconcile([a, b]).accepted


def test_box_and_number_validation():
    with pytest.raises(ValidationError):
        Box(page=1, x0=10, y0=2, x1=1, y1=3)
    with pytest.raises(ValidationError):
        observation(value="NaN")


def test_synthetic_results_cannot_unlock_cad():
    report = evaluate(demo_cases())
    assert report["cad_gate"] == "BLOCKED"
    assert report["synthetic"] is True
    assert report["cohorts"]["clean"]["accepted_value_error"] > 0


def test_duplicate_predictions_do_not_inflate_recall():
    case = demo_cases()[0]
    duplicate = case.predictions[0].model_copy(update={"id": "extra"})
    case = case.model_copy(update={"predictions": (*case.predictions, duplicate)})
    scores = evaluate([case])["cohorts"]["clean"]
    assert scores["ledger_recall"] <= 1
    assert scores["accepted_value_error"] > 0


def test_no_acceptances_are_not_zero_error():
    case = demo_cases()[0]
    case = case.model_copy(update={"predictions": ()})
    scores = evaluate([case])["cohorts"]["clean"]
    assert scores["accepted_value_error"] is None
    assert scores["ledger_recall"] == 0


def test_demo_is_replayable_and_report_is_escaped(tmp_path):
    output = run_demo(tmp_path)
    repeated = run_demo(tmp_path)
    assert output == repeated
    assert (output / "report.html").is_file()
    assert "SYNTHETIC" in (output / "report.html").read_text()
    assert (output / "evaluation.json").is_file()
