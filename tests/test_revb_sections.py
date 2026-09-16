import cadquery as cq

from drawing2step.revb_sections import compare_section, section_svg, section_topology


def solid(tmp_path, name, stepped=False):
    model = cq.Workplane("XY").circle(50).circle(20).extrude(20)
    if stepped:
        model = model.faces(">Z").workplane().circle(35).circle(20).extrude(10)
    path = tmp_path / name
    cq.exporters.export(model, str(path))
    return path


def test_xy_section_records_two_circular_boundaries_and_no_fake_pass(tmp_path):
    path = solid(tmp_path, "ring.step")
    result = compare_section(path, {"plane": "XY", "offset_mm": 10})
    assert result["status"] == "UNKNOWN"
    assert result["measurement"]["closed_loops"] == [["CIRCLE"], ["CIRCLE"]]
    assert "reimported STEP" in section_svg(result["measurement"])


def test_xz_section_rejects_extra_shoulder_structure(tmp_path):
    expected = section_topology(solid(tmp_path, "reference.step"), "XZ")
    reference = {**expected, "source": "engineer section annotation", "reviewer": "engineer"}
    assert compare_section(tmp_path / "reference.step", reference)["status"] == "PASS"
    wrong = solid(tmp_path, "wrong.step", stepped=True)
    assert compare_section(wrong, reference)["status"] == "FAIL"


def test_section_reference_requires_source_and_reviewer(tmp_path):
    path = solid(tmp_path, "ring.step")
    measured = section_topology(path, "XZ")
    assert compare_section(path, measured)["status"] == "UNKNOWN"
