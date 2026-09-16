"""Drawing-structure regressions measured from exported and reimported STEP.

These are synthetic gland-ring solids, not customer geometry or acceptance data.
"""

import math
from pathlib import Path

import cadquery as cq
import pytest

from drawing2step.revb_geometry import check_structure, inspect_step


def export_step(tmp_path: Path, body: cq.Workplane, name: str = "body") -> Path:
    assert body.val().isValid(), "The regression input itself must be a valid B-rep"
    assert len(body.solids().vals()) == 1
    path = tmp_path / f"{name}.step"
    cq.exporters.export(body, str(path))
    return path


def annulus(od: float = 100, bore: float = 40, height: float = 20) -> cq.Workplane:
    return cq.Workplane("XY").circle(od / 2).circle(bore / 2).extrude(height)


def assert_result(path: Path, expected: dict, status: str) -> dict:
    results = check_structure(path, [expected])
    assert len(results) == 1
    result = results[0]
    assert result["layer"] == "H2"
    assert result["subject"] == expected["id"]
    assert result["status"] == status
    assert isinstance(result["detail"], str) and result["detail"].strip()
    return result


def test_real_diameters_in_wrong_axial_positions_fail(tmp_path):
    # The flange has the drawing's full OD, but only for the first 1.06 inches.
    # The remaining length wrongly uses the internal-groove diameter as an OD.
    flange = annulus(221.996, 95.25, 26.924)
    hub = annulus(152.4, 95.25, 60.706 - 26.924).translate((0, 0, 26.924))
    path = export_step(tmp_path, flange.union(hub))
    expected = {
        "id": "body",
        "kind": "axial_band",
        "z0": 0,
        "z1": 60.706,
        "od": 221.996,
        "idiameter": 95.25,
        "tolerance": 0.01,
    }
    assert_result(path, expected, "FAIL")


def test_full_length_outer_and_bore_diameters_pass(tmp_path):
    path = export_step(tmp_path, annulus(221.996, 95.25, 60.706))
    expected = {
        "id": "body",
        "kind": "axial_band",
        "z0": 0,
        "z1": 60.706,
        "od": 221.996,
        "idiameter": 95.25,
        "tolerance": 0.01,
    }
    assert_result(path, expected, "PASS")


def patterned_ring(count: int = 8, angular_offset: float = 0, pcd: float = 80):
    # Keep 45 degree spacing even when one of the expected eight holes is absent.
    points = [
        (
            pcd / 2 * math.cos(math.radians(angular_offset + i * 45)),
            pcd / 2 * math.sin(math.radians(angular_offset + i * 45)),
        )
        for i in range(count)
    ]
    return annulus().faces(">Z").workplane().pushPoints(points).hole(6)


@pytest.mark.parametrize(
    "count,offset,pcd,status",
    [
        (8, 0, 80, "PASS"),
        (7, 0, 80, "FAIL"),
        (8, 10, 80, "FAIL"),
        (8, 0, 75, "FAIL"),
    ],
    ids=["eight-correct", "missing-eighth", "wrong-angles", "wrong-pitch-circle"],
)
def test_hole_pattern_checks_count_and_placement(tmp_path, count, offset, pcd, status):
    path = export_step(tmp_path, patterned_ring(count, offset, pcd))
    expected = {
        "id": "mounting-holes",
        "kind": "hole_pattern",
        "count": 8,
        "diameter": 6,
        "pcd": 80,
        "angle": 0,
        "z0": 0,
        "z1": 20,
        "tolerance": 0.01,
    }
    assert_result(path, expected, status)


@pytest.mark.parametrize("drill_end,status", [(19, "PASS"), (22, "FAIL")])
def test_radial_passage_must_reach_intended_bore(tmp_path, drill_end, status):
    drill = cq.Solid.makeCylinder(2, 51 - drill_end, cq.Vector(51, 0, 10), cq.Vector(-1, 0, 0))
    body = annulus().cut(drill)
    path = export_step(tmp_path, body)
    expected = {
        "id": "quench-port",
        "kind": "passage",
        "start": [50, 0, 10],
        "end": [19, 0, 10],
        "diameter": 4,
        "tolerance": 0.01,
    }
    assert_result(path, expected, status)


def test_analytic_annulus_passes_step_integrity(tmp_path):
    result = inspect_step(export_step(tmp_path, annulus()))
    assert result["status"] == "PASS"
    assert isinstance(result["detail"], str) and result["detail"].strip()


def test_faceted_ring_cannot_substitute_for_analytic_gland_ring(tmp_path):
    # A polygonal ring is valid, but every cylindrical surface has been replaced
    # by planar facets. Wrapping this approximation in STEP must not pass.
    outer = cq.Workplane("XY").polygon(32, 100).extrude(20)
    inner = cq.Workplane("XY").polygon(32, 40).extrude(20)
    result = inspect_step(export_step(tmp_path, outer.cut(inner)))
    assert result["status"] == "FAIL"
    assert isinstance(result["detail"], str) and result["detail"].strip()


def test_unsupported_measurement_is_unknown(tmp_path):
    path = export_step(tmp_path, annulus())
    assert_result(path, {"id": "marking", "kind": "stamped_text"}, "UNKNOWN")


def hole_expectation() -> dict:
    return {
        "id": "mounting-holes",
        "kind": "hole_pattern",
        "count": 8,
        "diameter": 6,
        "pcd": 80,
        "angle": 0,
        "z0": 0,
        "z1": 20,
        "tolerance": 0.01,
    }


def test_blind_holes_cannot_satisfy_a_through_pattern(tmp_path):
    points = [
        (40 * math.cos(math.radians(i * 45)), 40 * math.sin(math.radians(i * 45))) for i in range(8)
    ]
    body = annulus().faces(">Z").workplane().pushPoints(points).hole(6, depth=18)
    path = export_step(tmp_path, body)
    assert_result(path, hole_expectation(), "FAIL")


def test_hidden_material_cap_cannot_satisfy_a_through_pattern(tmp_path):
    # One thin plug interrupts a hole midway; top and bottom openings still exist.
    cap = cq.Workplane("XY").center(40, 0).circle(3).extrude(0.2).translate((0, 0, 10))
    body = patterned_ring().union(cap)
    path = export_step(tmp_path, body)
    assert_result(path, hole_expectation(), "FAIL")


def test_extra_same_size_hole_fails_exact_feature_count(tmp_path):
    extra_hole = cq.Solid.makeCylinder(3, 20, cq.Vector(30, 12, 0))
    body = patterned_ring().cut(extra_hole)
    path = export_step(tmp_path, body)
    assert_result(path, hole_expectation(), "FAIL")


def test_empty_space_inside_bore_is_not_a_radial_port(tmp_path):
    # There was no cut. A corridor wholly in the existing bore must not prove a port.
    path = export_step(tmp_path, annulus())
    expected = {
        "id": "missing-port",
        "kind": "passage",
        "start": [19, 0, 10],
        "end": [10, 0, 10],
        "diameter": 4,
        "tolerance": 0.01,
    }
    assert_result(path, expected, "FAIL")


def test_corridor_above_the_part_cannot_prove_a_passage(tmp_path):
    path = export_step(tmp_path, annulus())
    expected = {
        "id": "outside-port",
        "kind": "passage",
        "start": [50, 0, 30],
        "end": [10, 0, 30],
        "diameter": 4,
        "tolerance": 0.01,
    }
    results = check_structure(path, [expected])
    assert results[0]["subject"] == "outside-port"
    assert results[0]["status"] in {"FAIL", "UNKNOWN"}


def test_body_displaced_from_the_declared_axis_cannot_pass(tmp_path):
    path = export_step(tmp_path, annulus().translate((10, 0, 0)))
    expected = {
        "id": "body",
        "kind": "axial_band",
        "z0": 0,
        "z1": 20,
        "od": 100,
        "idiameter": 40,
        "tolerance": 0.01,
    }
    results = check_structure(path, [expected])
    assert results[0]["subject"] == "body"
    assert results[0]["status"] in {"FAIL", "UNKNOWN"}


def test_step_with_two_disconnected_solids_fails_integrity(tmp_path):
    compound = cq.Compound.makeCompound([annulus().val(), annulus().translate((0, 0, 30)).val()])
    assert compound.isValid()
    assert len(compound.Solids()) == 2
    path = tmp_path / "split-body.step"
    cq.exporters.export(compound, str(path))
    result = inspect_step(path)
    assert result["status"] == "FAIL"
    assert "solid" in result["detail"].lower()
