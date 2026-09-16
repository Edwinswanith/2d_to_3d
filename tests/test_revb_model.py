"""Synthetic feature-builder contracts; successful previews never imply release approval."""

import json
from decimal import Decimal

import pytest

from drawing2step.revb import Requirement
from drawing2step.revb_geometry import check_structure, inspect_step
from drawing2step.revb_model import DraftSpec, build_model


def requirement(identifier, value, kind="linear", unit="mm"):
    return Requirement(
        id=identifier,
        raw_text=f"{value} {unit}",
        kind=kind,
        value=Decimal(str(value)),
        unit=unit,
        sources=(f"synthetic:{identifier}",),
        text_status="AGREED",
        interpretation_status="CONFIRMED",
    )


def ledger():
    return {
        "OD": requirement("OD", 100, "diameter"),
        "ID": requirement("ID", 40, "diameter"),
        "H": requirement("H", 20),
        "HD": requirement("HD", 6, "diameter"),
        "PCD": requirement("PCD", 80, "diameter"),
        "N": requirement("N", 8, "count", "count"),
        "A": requirement("A", 45, "angle", "degree"),
    }


def profile():
    # Counterclockwise closed radial cross-section about the declared Z axis.
    return [
        {"z": {"datum": "face_A"}, "radius": {"expr": "OD/2"}},
        {"z": {"ledger": "H"}, "radius": {"expr": "OD/2"}},
        {"z": {"ledger": "H"}, "radius": {"expr": "ID/2"}},
        {"z": {"datum": "face_A"}, "radius": {"expr": "ID/2"}},
        {"z": {"datum": "face_A"}, "radius": {"expr": "OD/2"}},
    ]


def spec(**updates):
    value = {
        "reference_face": "face_A",
        "coordinate_policy": "Z increases from face A; origin is the bore-axis intersection",
        "profile": profile(),
        "features": [],
        "assumptions": [],
        "associations": [],
        "unresolved": [],
    }
    value.update(updates)
    return DraftSpec.model_validate(value)


def assert_named_failure(candidate, requirements, directory, name):
    try:
        result = build_model(candidate, list(requirements.values()), directory)
    except ValueError as error:
        assert name.lower() in str(error).lower()
    else:
        failures = [check for check in result["checks"] if check["status"] == "FAIL"]
        assert failures, "A failed build operation needs an explicit failure result"
        assert name.lower() in json.dumps(failures).lower()
        assert result["release"] == "BLOCKED"


def test_revolved_preview_is_analytic_brep_with_independent_dimensions(tmp_path):
    result = build_model(spec(), list(ledger().values()), tmp_path)
    path = tmp_path / result["step"]
    assert inspect_step(path)["status"] == "PASS"
    measured = check_structure(
        path,
        [{"id": "body", "kind": "axial_band", "z0": 0, "z1": 20, "od": 100, "idiameter": 40}],
    )
    assert measured[0]["status"] == "PASS"
    assert any(check["status"] == "UNKNOWN" for check in result["checks"])
    assert result["release"] == "BLOCKED"


@pytest.mark.parametrize("wrong_quantity", ["A", "N"])
def test_profile_radius_rejects_angle_or_count_citations(tmp_path, wrong_quantity):
    points = profile()
    points[0]["radius"] = {"ledger": wrong_quantity}
    points[-1]["radius"] = {"ledger": wrong_quantity}
    assert_named_failure(spec(profile=points), ledger(), tmp_path, "length")


def test_profile_rejects_missing_ledger_citation(tmp_path):
    points = profile()
    points[1]["z"] = {"ledger": "MISSING"}
    assert_named_failure(spec(profile=points), ledger(), tmp_path, "MISSING")


def test_profile_rejects_arbitrary_expression_constant(tmp_path):
    points = profile()
    points[0]["radius"] = {"expr": "OD*3"}
    assert_named_failure(spec(profile=points), ledger(), tmp_path, "permitted")


def hole_feature(**updates):
    value = {
        "id": "mounting_holes",
        "kind": "hole_pattern",
        "citations": ["HD", "PCD", "N", "H"],
        "inventory_ids": ["visible_holes"],
        "diameter": {"ledger": "HD"},
        "depth": {"ledger": "H"},
        "z": {"datum": "face_A"},
        "angle": {"centreline": 0, "reason": "Pattern starts at the shown centreline"},
        "pcd": {"ledger": "PCD"},
        "count": {"ledger": "N"},
        "host": "face_a",
        "reference_face": "face_A",
        "report_only": False,
    }
    value.update(updates)
    return value


def test_eight_holes_are_measurable_in_the_exported_step(tmp_path):
    result = build_model(spec(features=[hole_feature()]), list(ledger().values()), tmp_path)
    path = tmp_path / result["step"]
    results = check_structure(
        path,
        [
            {
                "id": "mounting_holes",
                "kind": "hole_pattern",
                "count": 8,
                "diameter": 6,
                "pcd": 80,
                "angle": 0,
                "z0": 0,
                "z1": 20,
                "tolerance": 0.01,
            }
        ],
    )
    assert results[0]["status"] == "PASS"
    feature_checks = [check for check in result["checks"] if check["layer"] == "H2"]
    assert feature_checks
    assert all(check["subject"] == "mounting_holes" for check in feature_checks)


def test_cut_outside_body_names_the_ineffective_feature(tmp_path):
    requirements = ledger()
    requirements["PCD"] = requirement("PCD", 400, "diameter")
    assert_named_failure(spec(features=[hole_feature()]), requirements, tmp_path, "mounting_holes")


def test_cut_splitting_body_names_the_feature(tmp_path):
    requirements = ledger()
    # Opposing large holes each cut across the full wall from bore to OD. Together
    # they separate the ring into two distinct arcs, which is not a valid part.
    requirements["N"] = requirement("N", 2, "count", "count")
    requirements["HD"] = requirement("HD", 40, "diameter")
    assert_named_failure(spec(features=[hole_feature()]), requirements, tmp_path, "mounting_holes")


def test_feature_cannot_claim_nonexistent_requirement_citations(tmp_path):
    feature = hole_feature(citations=["MISSING"])
    assert_named_failure(spec(features=[feature]), ledger(), tmp_path, "mounting_holes")


def test_report_only_marking_stays_unverified_and_does_not_cut_geometry(tmp_path):
    requirements = ledger()
    requirements["MARK"] = Requirement(
        id="MARK", raw_text="PUNCH QUENCH IN", kind="note", sources=("synthetic:MARK",)
    )
    marking = {
        "id": "quench_marking",
        "kind": "marking",
        "citations": ["MARK"],
        "inventory_ids": ["label"],
        "host": "outside",
        "reference_face": "face_A",
        "report_only": True,
        "reason": "Report-only marking under the explicit draft policy",
    }
    result = build_model(spec(features=[marking]), list(requirements.values()), tmp_path)
    unknown = [check for check in result["checks"] if check["status"] == "UNKNOWN"]
    assert "quench_marking" in json.dumps(unknown)
    path = tmp_path / result["step"]
    results = check_structure(
        path,
        [{"id": "body", "kind": "axial_band", "z0": 0, "z1": 20, "od": 100, "idiameter": 40}],
    )
    assert results[0]["status"] == "PASS"


def test_fractional_derived_count_fails_the_named_feature(tmp_path):
    requirements = ledger()
    requirements["N"] = requirement("N", 3, "count", "count")
    feature = hole_feature(count={"expr": "N/2"})
    assert_named_failure(spec(features=[feature]), requirements, tmp_path, "mounting_holes")


def test_angle_cannot_supply_hole_diameter(tmp_path):
    feature = hole_feature(diameter={"ledger": "A"})
    assert_named_failure(spec(features=[feature]), ledger(), tmp_path, "mounting_holes")


def test_explicit_axial_position_cannot_be_silently_discarded(tmp_path):
    # A face-A hole cannot also have its declared entry halfway through the part.
    # The proposal needs an explicit failure rather than a cut at an invented entry.
    feature = hole_feature(z={"expr": "H/2"})
    assert_named_failure(spec(features=[feature]), ledger(), tmp_path, "mounting_holes")


def test_failed_cut_preserves_valid_draft_with_blocked_release(tmp_path):
    requirements = ledger()
    requirements["PCD"] = requirement("PCD", 400, "diameter")
    result = build_model(spec(features=[hole_feature()]), list(requirements.values()), tmp_path)
    assert result["release"] == "BLOCKED"
    assert result["partial"] is True
    assert result["features"][0]["id"] == "mounting_holes"
    assert result["features"][0]["status"] == "FAILED"
    assert inspect_step(tmp_path / result["step"])["status"] == "PASS"
    assert result["measurements"]["bbox"] == pytest.approx([100, 100, 20], abs=0.01)
