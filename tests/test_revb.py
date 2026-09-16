from decimal import Decimal

import pytest

from drawing2step.revb import (
    Assumption,
    Inventory,
    InventoryFeature,
    Numeric,
    Requirement,
    audit_completeness,
    evaluate_numeric,
    release_gate,
    resolve_context,
)


def requirement(**kwargs):
    return Requirement(id="D", raw_text="Ø80", kind="diameter", value="80", unit="mm", **kwargs)


def test_missing_unit_statement_blocks():
    assert resolve_context("", "PART", None)["status"] == "FAIL"


def test_impossible_inch_envelope_blocks_and_is_not_converted_to_mm():
    result = resolve_context("ALL DIMENSIONS IN INCHES", "PART", 222)
    assert result["status"] == "FAIL"
    assert result["unit"] == "in"


def test_explicit_statement_required_even_with_upload_override():
    result = resolve_context("", "PART", None, override="mm")
    assert result["status"] == "FAIL"


def test_restricted_expression():
    ledger = {"D": requirement()}
    assert evaluate_numeric(Numeric(expr="D/2"), ledger, {}) == Decimal("40")
    assert evaluate_numeric(Numeric(expr="D-D"), ledger, {}) == 0
    for expression in ("D*3", "D+3", "D/3", "abs(D)", "D**2", '__import__("os")'):
        with pytest.raises(ValueError):
            evaluate_numeric(Numeric(expr=expression), ledger, {})


def test_dimensionally_wrong_expression_rejected():
    with pytest.raises(ValueError):
        evaluate_numeric(
            Numeric(expr="D+A"),
            {
                "D": requirement(),
                "A": Requirement(id="A", raw_text="90", kind="angle", value=90, unit="degree"),
            },
            {},
        )


def test_datum_and_centreline_need_evidence():
    with pytest.raises(ValueError):
        Numeric(datum="")
    with pytest.raises(ValueError):
        Numeric(centreline=45, reason="implied")
    with pytest.raises(ValueError):
        Numeric(centreline=90)


def test_assumption_never_silently_becomes_pass():
    a = Assumption(
        id="A",
        item="Port depth",
        value=10,
        unit="mm",
        type="ASSUMED",
        reason="Undefined depth",
        source="view1",
    )
    assert evaluate_numeric(Numeric(assumption="A"), {}, {"A": a}) == 10
    result = release_gate([], [a], completeness_approved=True, accuracy_approved=True)
    assert result["release"] == "BLOCKED"


def test_geometry_fail_is_not_waivable():
    result = release_gate(
        [{"subject": "hole", "status": "FAIL", "signed_by": "Engineer"}],
        [],
        completeness_approved=True,
        accuracy_approved=True,
    )
    assert result["release"] == "BLOCKED"


def test_unknown_without_check_cannot_become_verified_candidate():
    result = release_gate([], [], completeness_approved=True, accuracy_approved=True)
    assert result["release"] == "BLOCKED"


def test_missing_port_count_is_a_finding():
    inventory = Inventory(
        features=[
            InventoryFeature(
                id="ports",
                type="port",
                view="plan",
                count=3,
                box=[0, 0, 1000, 1000],
                description="three radial ports",
            )
        ]
    )
    findings = audit_completeness(inventory, [], {"ports": 2})
    assert any(f["code"] == "FEATURE_COUNT" and f["status"] == "FAIL" for f in findings)


def test_unavailable_inventory_never_passes():
    findings = audit_completeness(None, [], {})
    assert any(f["code"] == "INVENTORY_UNAVAILABLE" for f in findings)
