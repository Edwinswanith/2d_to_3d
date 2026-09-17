"""Edge cases found on real gland-ring sheets, reproduced with synthetic data only.

Reader mislabels, multiplier notation, printed thread forms, thin-flange through holes,
self-contradicting title-block units, open OD slots and uncited printed dimensions.
"""

import hashlib
import io
import json
import math
from decimal import Decimal
from pathlib import Path

import cadquery as cq
import pytest
from PIL import Image
from pydantic import ValidationError

from drawing2step.pdf_diagnostic import PdfReading
from drawing2step.revb import Requirement, resolve_context
from drawing2step.revb_build_pipeline import build_from_audit, uncited_dimensions
from drawing2step.revb_geometry import check_structure
from drawing2step.revb_model import (
    DraftSpec,
    build_model,
    cavity_depth,
    enrich_ledger,
    thread_drill_mm,
)
from drawing2step.revb_pipeline import PipelineConfig, _ledger, run_audit


def callout(identifier: str, raw_text: str, kind: str, value_printed: str) -> dict:
    return {
        "id": identifier,
        "raw_text": raw_text,
        "kind": kind,
        "value_printed": value_printed,
        "unit_printed": "",
        "tolerance_printed": "",
        "feature_proposal": "",
        "box": [10, 10, 20, 40],
    }


def reading(callouts: list[dict], units: str = "DIMENSIONS IN INCHES") -> PdfReading:
    return PdfReading.model_validate(
        {
            "drawing_number": "SYN-1",
            "revision": "0",
            "part_family": "gland ring",
            "title_block_units": units,
            "notes": [],
            "uncertainties": [],
            "callouts": callouts,
        }
    )


def test_count_label_on_a_diameter_callout_is_demoted_not_fatal():
    source = reading([callout("c1", "4X .562 HOLE THRU", "count", ".562")])
    rows = _ledger(source, [], "in")
    assert rows[0].kind == "note" and rows[0].value is None
    children = {r.id: r for r in enrich_ledger(rows, "in")}
    assert children["R1_n1"].kind == "count" and children["R1_n1"].value == 4
    assert children["R1_n2"].kind == "linear" and children["R1_n2"].value == Decimal(".562")


@pytest.mark.parametrize(
    "text,count_value,length_value",
    [
        ("4X Ø.562 HOLE THRU", 4, Decimal(".562")),
        ("2X.132 PIN EXT.", 2, Decimal(".132")),
        ("2~ ø14MM HOLES THRU' ON 4.528 PCD", 2, Decimal("14")),
        ("10X Ø4.96 ▽9.52", 10, Decimal("4.96")),
    ],
)
def test_multiplier_notation_becomes_a_count_reading(text, count_value, length_value):
    row = Requirement(id="R1", raw_text=text, kind="note", geometry_driving=True)
    children = [r for r in enrich_ledger([row], "in") if r.id != "R1"]
    assert children[0].kind == "count" and children[0].value == count_value
    assert children[1].kind in {"linear", "diameter"} and children[1].value == length_value


def test_mm_marker_inside_an_inch_drawing_keeps_its_explicit_unit():
    row = Requirement(id="R1", raw_text="4~ ø14MM HOLES THRU' ON 4.528 PCD", kind="note")
    children = {r.id: r for r in enrich_ledger([row], "in")}
    assert children["R1_n2"].unit == "mm" and children["R1_n2"].kind == "diameter"
    assert children["R1_n3"].unit == "in"


@pytest.mark.parametrize(
    "designation,expected_mm",
    [
        ("#10-24", 0.1495 * 25.4),
        ("5/16-18 UNC", 0.257 * 25.4),
        ("2~ 5/16-18 UNC TAP", 0.257 * 25.4),
        ("M8", 6.8),
        ("2X M8 TAP", 6.8),
        ("M8x1.25", 6.8),
        ("1/2 NPT", 0.71875 * 25.4),
        (".500 NPT'S", 0.71875 * 25.4),
        (".375 NPT", 0.5781 * 25.4),
        ("3/8-18 NPT", 0.5781 * 25.4),
        (".250 NPT", 0.4375 * 25.4),
    ],
)
def test_printed_thread_forms_resolve_to_one_drill(designation, expected_mm):
    assert thread_drill_mm(designation) == pytest.approx(expected_mm, abs=1e-6)


@pytest.mark.parametrize("designation", ["", "7/13-99 UNC", "M99", "2 NPT", "G1/2", None])
def test_unknown_threads_are_never_guessed(designation):
    assert thread_drill_mm(designation) is None


def requirement(identifier, value, kind="linear", unit="mm"):
    return Requirement(
        id=identifier,
        raw_text=f"Ø{value}" if kind == "diameter" else f"{value} {unit}",
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
        requirement("N", 8, "count", "count"),
        requirement("N2", 2, "count", "count"),
        requirement("A", 45, "angle", "degree"),
    ]


def spec(features: list[dict]) -> DraftSpec:
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


def tapped(thread: str) -> dict:
    return {
        "id": "taps",
        "kind": "tapped_hole",
        "citations": ["PCD", "N2", "H"],
        "thread": thread,
        "depth": {"expr": "H/2"},
        "pcd": {"ledger": "PCD"},
        "count": {"ledger": "N2"},
        "angle": {"centreline": 90, "reason": "shown on the vertical centreline"},
        "host": "face_a",
        "reference_face": "face_A",
    }


def test_unified_fraction_thread_builds_its_tap_drill(tmp_path):
    result = build_model(spec([tapped("5/16-18 UNC")]), ledger(), tmp_path)
    receipt = result["features"][0]
    assert receipt["status"] == "BUILT" and receipt["thread"] == "5/16-18 UNC"
    measured = check_structure(
        tmp_path / result["step"],
        [
            {
                "id": "taps",
                "kind": "hole_pattern",
                "count": 2,
                "diameter": 0.257 * 25.4,
                "pcd": 80,
                "angle": 90,
                "z0": 0,
                "z1": 10,
            }
        ],
    )
    assert measured[0]["status"] == "PASS"


def test_unknown_thread_names_the_feature_and_asks_for_a_cited_drill(tmp_path):
    result = build_model(spec([tapped("7/13-99 UNC")]), ledger(), tmp_path)
    receipt = result["features"][0]
    assert receipt["status"] == "FAILED"
    assert "7/13-99 UNC" in receipt["detail"] and "cite" in receipt["detail"].lower()


def port(**updates) -> dict:
    value = {
        "id": "quench",
        "kind": "port",
        "citations": ["HD", "H"],
        "thread": ".375 NPT",
        "diameter": {"ledger": "HD"},
        "depth": {"expr": "OD/2-ID/2"},
        "z": {"expr": "H/2"},
        "angle": {"centreline": 0, "reason": "shown on the horizontal centreline"},
        "entry_depth": {"ledger": "HD"},
        "entry_diameter": {"expr": "ID/2"},
        "host": "outside",
        "reference_face": "face_A",
    }
    value.update(updates)
    return value


def test_port_uses_the_printed_flat_drill_over_the_thread_table(tmp_path):
    printed = build_model(spec([port()]), ledger(), tmp_path / "printed")
    table = build_model(spec([port(entry_diameter=None)]), ledger(), tmp_path / "table")
    assert printed["features"][0]["status"] == table["features"][0]["status"] == "BUILT"
    # Printed Ø20 entry versus the 3/8 NPT table drill (Ø14.68): more material leaves.
    assert printed["features"][0]["removed_volume_mm3"] > table["features"][0]["removed_volume_mm3"]
    assert table["features"][0]["removed_volume_mm3"] > math.pi * 3**2 * 30


def test_a_meets_cavity_port_with_no_printed_depth_reaches_the_bore(tmp_path):
    # "DRILL TO MEET BORE/GROOVE AS SHOWN" prints a destination, not a length. Leaving depth
    # uncited must measure the real wall thickness (OD/2-ID/2 = 30mm here), not accept some
    # unrelated assumed figure that stops the drill short of the bore it is meant to reach.
    result = build_model(spec([port(depth=None)]), ledger(), tmp_path)
    receipt = result["features"][0]
    assert receipt["status"] == "BUILT", receipt.get("detail")
    # The independent re-verification against the exported STEP is the authoritative check;
    # it runs after (and so overwrites, by subject) the tentative in-memory one from construction.
    passages = {c["subject"]: c for c in result["checks"] if c["layer"] == "H2"}
    assert passages["quench"]["status"] == "PASS", passages["quench"]


def test_a_thru_or_blind_port_still_requires_a_cited_depth(tmp_path):
    feature = port(depth=None, termination="thru")
    with pytest.raises(ValidationError, match="requires a cited depth"):
        spec([feature])


def test_a_meets_cavity_port_ignores_an_explicit_but_insufficient_depth_citation(tmp_path):
    # A real drawing can print a "drill depth" note next to a "TO MEET GROOVE" port that is
    # still short of the true wall thickness (a nominal/rough figure, not a sagitta-corrected
    # one). termination stays meets_cavity by default even when the model also cites a depth,
    # so that citation must not override the measured one, or the drill stops short of the
    # bore it is meant to reach.
    result = build_model(spec([port(depth={"expr": "OD/2-ID/2-HD"})]), ledger(), tmp_path)
    receipt = result["features"][0]
    assert receipt["status"] == "BUILT", receipt.get("detail")
    passages = {c["subject"]: c for c in result["checks"] if c["layer"] == "H2"}
    assert passages["quench"]["status"] == "PASS", passages["quench"]


def test_cavity_depth_measures_where_a_thin_drill_first_clears_a_solid():
    box = cq.Solid.makeBox(10, 10, 10)
    start = cq.Vector(5, 5, 5)
    direction = cq.Vector(1, 0, 0)
    assert cavity_depth(box, start, direction, 0.1, 10) == pytest.approx(5.0, abs=0.05)


def test_cavity_depth_names_the_error_when_the_corridor_never_clears():
    box = cq.Solid.makeBox(10, 10, 10)
    start = cq.Vector(5, 5, 5)
    direction = cq.Vector(1, 0, 0)
    with pytest.raises(ValueError, match="No cavity found"):
        cavity_depth(box, start, direction, 0.1, 2)


def test_cavity_depth_accounts_for_a_wide_drill_meeting_a_curved_bore():
    # A drill's flat end clears at its centre before its rim does against a concave (bore) wall
    # curving away from it; the reported depth must cover the whole corridor, not just the axis.
    tube = (
        cq.Workplane("XZ")
        .polyline([(20, 0), (50, 0), (50, 20), (20, 20)])
        .close()
        .revolve(360, (0, 0), (0, 1))
        .val()
    )
    start = cq.Vector(50, 0, 10)
    direction = cq.Vector(-1, 0, 0)
    depth = cavity_depth(tube, start, direction, 3, 60)
    # Centerline alone reaches the bore at exactly 30mm; a flat radius-3 drill's rim needs the
    # extra sagitta r^2/(2R) = 3^2/(2*20) = 0.225mm to fully clear the concave bore wall.
    assert depth == pytest.approx(30.225, abs=0.05)


def stepped_spec(features: list[dict]) -> DraftSpec:
    # Flange Ø100 for z 0..10, hub Ø80 (PCD citation) for z 10..20, bore Ø40 throughout.
    return DraftSpec.model_validate(
        {
            "reference_face": "face_A",
            "coordinate_policy": "Z increases from face A",
            "profile": [
                {"z": {"datum": "face_A"}, "radius": {"expr": "OD/2"}},
                {"z": {"expr": "H/2"}, "radius": {"expr": "OD/2"}},
                {"z": {"expr": "H/2"}, "radius": {"expr": "PCD/2"}},
                {"z": {"ledger": "H"}, "radius": {"expr": "PCD/2"}},
                {"z": {"ledger": "H"}, "radius": {"expr": "ID/2"}},
                {"z": {"datum": "face_A"}, "radius": {"expr": "ID/2"}},
            ],
            "features": features,
        }
    )


def test_holes_may_start_on_a_recessed_flange_face(tmp_path):
    # Nose Ø80 for z 0..10 (face A end), flange Ø100 for z 10..20. Bolt holes on the
    # flange face at z=10 drill toward face B; their pitch circle clears the nose.
    profile = [
        {"z": {"datum": "face_A"}, "radius": {"expr": "PCD/2"}},
        {"z": {"expr": "H/2"}, "radius": {"expr": "PCD/2"}},
        {"z": {"expr": "H/2"}, "radius": {"expr": "OD/2"}},
        {"z": {"ledger": "H"}, "radius": {"expr": "OD/2"}},
        {"z": {"ledger": "H"}, "radius": {"expr": "ID/2"}},
        {"z": {"datum": "face_A"}, "radius": {"expr": "ID/2"}},
    ]
    holes = {
        "id": "bolts",
        "kind": "hole_pattern",
        "citations": ["HD", "N2", "H"],
        "diameter": {"ledger": "HD"},
        "depth": {"expr": "H/2"},
        "z": {"expr": "H/2"},
        "pcd": {"expr": "OD-HD-HD"},
        "count": {"ledger": "N2"},
        "angle": {"centreline": 0, "reason": "on the horizontal centreline"},
        "host": "face_a",
        "reference_face": "face_A",
    }
    draft = spec([holes]).model_dump(mode="json")
    draft["profile"] = profile
    result = build_model(DraftSpec.model_validate(draft), ledger(), tmp_path)
    receipt = result["features"][0]
    assert receipt["status"] == "BUILT", receipt.get("detail")
    assert receipt["removed_volume_mm3"] == pytest.approx(2 * math.pi * 3**2 * 10, rel=0.01)
    assert not [c for c in result["checks"] if c["subject"] == "bolts" and c["status"] == "FAIL"]


def test_holes_on_a_buried_plane_are_still_rejected(tmp_path):
    holes = {
        "id": "buried",
        "kind": "hole_pattern",
        "citations": ["HD", "N2", "H"],
        "diameter": {"ledger": "HD"},
        "depth": {"expr": "H/2"},
        "z": {"expr": "H/2"},
        "pcd": {"ledger": "PCD"},
        "count": {"ledger": "N2"},
        "angle": {"centreline": 0, "reason": "on the horizontal centreline"},
        "host": "face_a",
        "reference_face": "face_A",
    }
    result = build_model(spec([holes]), ledger(), tmp_path)
    receipt = result["features"][0]
    assert receipt["status"] == "FAILED" and "not an exposed face_a face" in receipt["detail"]


def test_port_on_a_hub_enters_at_the_local_outside_surface_not_the_flange(tmp_path):
    hub_port = port(
        thread=None,
        entry_depth=None,
        entry_diameter=None,
        z={"expr": "H/2+HD"},
        depth={"expr": "PCD/2-ID/2"},
    )
    result = build_model(stepped_spec([hub_port]), ledger(), tmp_path)
    receipt = result["features"][0]
    assert receipt["status"] == "BUILT", receipt.get("detail")
    assert receipt["removed_volume_mm3"] == pytest.approx(math.pi * 3**2 * 20, rel=0.02)
    passages = [c for c in result["checks"] if c["layer"] == "H2" and c["subject"] == "quench"]
    assert any(c["status"] == "PASS" and "corridor" in c["detail"] for c in passages), passages


def test_port_angle_may_offset_a_printed_angle_from_a_cardinal_centreline(tmp_path):
    offset = port(thread=None, entry_depth=None, entry_diameter=None, angle={"expr": "180+A"})
    result = build_model(spec([offset]), ledger(), tmp_path)
    assert result["features"][0]["status"] == "BUILT"
    solid = cq.importers.importStep(str(tmp_path / result["step"])).val().Solids()[0]
    theta = math.radians(225)
    assert solid.isInside((45 * math.cos(theta), 45 * math.sin(theta), 10)) is False
    assert solid.isInside((45, 0, 10)) is True


@pytest.mark.parametrize("expression", ["OD+90", "OD/2+180", "A+45", "A*2"])
def test_non_cardinal_or_length_constants_stay_forbidden(tmp_path, expression):
    number = "angle" if expression.startswith("A") else "depth"
    # A meets_cavity port (the port() fixture's default) never evaluates its cited depth, so a
    # bad depth expression needs a thru/blind termination to actually reach the grammar check.
    termination = "blind" if number == "depth" else "meets_cavity"
    bad = port(
        thread=None,
        entry_depth=None,
        entry_diameter=None,
        termination=termination,
        **{number: {"expr": expression}},
    )
    result = build_model(spec([bad]), ledger(), tmp_path)
    receipt = result["features"][0]
    assert receipt["status"] == "FAILED", receipt
    assert "permitted" in receipt["detail"] or "dimensions" in receipt["detail"]


def test_port_entry_smaller_than_follow_on_drill_is_rejected(tmp_path):
    result = build_model(spec([port(entry_diameter={"expr": "HD/2"})]), ledger(), tmp_path)
    receipt = result["features"][0]
    assert receipt["status"] == "FAILED" and "larger" in receipt["detail"]


def compound_port_ledger() -> list[Requirement]:
    return [
        requirement("OD", 100, "diameter"),
        requirement("ID", 80, "diameter"),
        requirement("H", 60),
        requirement("HD", 6, "diameter"),
        requirement("Z1", 10),
        requirement("Z2", 40),
        requirement("D1", 12),
        requirement("D2", 12),
        requirement("T1", 15, "angle", "degree"),
        requirement("T2", 20, "angle", "degree"),
    ]


def compound_body_spec(features: list[dict]) -> DraftSpec:
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


def test_a_reviewed_compound_port_spec_cuts_two_independently_angled_segments(tmp_path):
    # 1H-139899's real port: a shallow "BOTTOM DRILL" stage and a steeper NPT thread
    # stage, cited and angled independently, not one shared axis with a diameter change
    # partway (that remains a plain "port").
    feature = {
        "id": "quench",
        "kind": "compound_port",
        "citations": ["HD", "H"],
        "host": "outside",
        "reference_face": "face_A",
        "segments": [
            {
                "diameter": {"ledger": "HD"},
                "depth": {"ledger": "D1"},
                "z": {"ledger": "Z1"},
                "angle": {"centreline": 90, "reason": "bottom drill shown on vertical centreline"},
                "tilt": {"ledger": "T1"},
                "termination": "meets_cavity",
            },
            {
                "thread": ".375 NPT",
                "depth": {"ledger": "D2"},
                "z": {"ledger": "Z2"},
                "angle": {"centreline": 90, "reason": "NPT thread shown on vertical centreline"},
                "tilt": {"ledger": "T2"},
                "termination": "meets_cavity",
            },
        ],
    }
    result = build_model(compound_body_spec([feature]), compound_port_ledger(), tmp_path)
    receipt = result["features"][0]
    assert receipt["status"] == "BUILT", receipt.get("detail")
    residual = [c for c in result["checks"] if c["layer"] == "H1" and c["subject"] == "quench"]
    assert residual and residual[0]["status"] == "PASS", residual
    passages = {
        c["subject"]: c for c in result["checks"] if c["layer"] == "H2" and "_seg" in c["subject"]
    }
    assert passages["quench_seg0"]["status"] == "PASS", passages["quench_seg0"]
    assert passages["quench_seg1"]["status"] == "PASS", passages["quench_seg1"]


def test_open_od_slots_cut_through_the_outside_diameter(tmp_path):
    feature = {
        "id": "od_slots",
        "kind": "od_slot",
        "citations": ["HD", "PCD", "N2", "H"],
        "diameter": {"ledger": "HD"},
        "depth": {"ledger": "H"},
        "pcd": {"ledger": "PCD"},
        "count": {"ledger": "N2"},
        "angle": {"centreline": 0, "reason": "180 degrees apart from the centreline"},
        "host": "face_a",
        "reference_face": "face_A",
    }
    result = build_model(spec([feature]), ledger(), tmp_path)
    receipt = result["features"][0]
    assert receipt["status"] == "BUILT"
    expected = 2 * (math.pi * 3**2 / 2 + 6 * 10) * 20
    assert receipt["removed_volume_mm3"] == pytest.approx(expected, rel=0.01)
    solid = cq.importers.importStep(str(tmp_path / result["step"])).val()
    assert solid.Solids()[0].isInside((49, 0, 10)) is False
    assert solid.Solids()[0].isInside((49, 5, 10)) is True


def test_open_od_slots_with_outside_host_cut_through_the_flange_only(tmp_path):
    # Flange Ø100 for z 0..10, hub Ø80 above; slots on PCD 90 only meet the flange.
    feature = {
        "id": "od_slots",
        "kind": "od_slot",
        "citations": ["HD", "PCD", "N2", "H"],
        "diameter": {"ledger": "HD"},
        "depth": {"ledger": "H"},
        "pcd": {"expr": "PCD+HD+HD"},
        "count": {"ledger": "N2"},
        "angle": {"centreline": 90, "reason": "180 degrees apart on the vertical centreline"},
        "host": "outside",
        "reference_face": "face_A",
    }
    result = build_model(stepped_spec([feature]), ledger(), tmp_path)
    receipt = result["features"][0]
    assert receipt["status"] == "BUILT", receipt.get("detail")
    # Only the 10 mm flange is cut: half-round end plus tongue to the OD (radius 46 -> 50).
    expected = 2 * (math.pi * 3**2 / 2 + 6 * 4) * 10
    assert receipt["removed_volume_mm3"] == pytest.approx(expected, rel=0.02)


def export_step(tmp_path: Path, shape: cq.Shape, name: str = "body") -> Path:
    path = tmp_path / f"{name}.step"
    cq.exporters.export(shape, str(path))
    return path


def flanged_ring(with_holes: bool = True) -> cq.Shape:
    flange = cq.Workplane("XY").circle(50).circle(20).extrude(10)
    hub = cq.Workplane("XY").circle(35).circle(20).extrude(20).translate((0, 0, 10))
    body = flange.union(hub).val()
    if not with_holes:
        return body
    return drilled(body, 10)


def drilled(body: cq.Shape, depth: float) -> cq.Shape:
    for i in range(4):
        theta = math.radians(i * 90)
        centre = cq.Vector(42 * math.cos(theta), 42 * math.sin(theta), 0)
        body = body.cut(cq.Solid.makeCylinder(3, depth, centre))
    return body


def through_pattern(z1: float) -> dict:
    return {
        "id": "bolts",
        "kind": "hole_pattern",
        "count": 4,
        "diameter": 6,
        "pcd": 84,
        "angle": 0,
        "z0": 0,
        "z1": z1,
        "tolerance": 0.01,
    }


@pytest.mark.parametrize("z1", [10, 30])
def test_through_holes_in_a_thin_flange_pass_with_or_without_full_length_span(tmp_path, z1):
    path = export_step(tmp_path, flanged_ring())
    result = check_structure(path, [through_pattern(z1)])[0]
    assert result["status"] == "PASS", result["detail"]


def test_blind_hole_in_the_flange_still_fails(tmp_path):
    body = drilled(flanged_ring(with_holes=False), 6)
    result = check_structure(export_step(tmp_path, body), [through_pattern(10)])[0]
    assert result["status"] == "FAIL" and "span" in result["detail"]


def test_selected_unit_corrects_a_self_contradicting_title_block():
    context = resolve_context("DIMENSIONS IN INCHES", "G_BK472722A", 218.0, override="mm")
    assert context["status"] == "PASS"
    assert context["unit"] == "mm" and context["sheet_unit"] == "in"
    assert context["unit_correction"]["sheet_unit"] == "in"


def test_selected_unit_cannot_override_a_plausible_sheet():
    context = resolve_context("DIMENSIONS IN INCHES", "2H-183624", 5.88, override="mm")
    assert context["status"] == "FAIL" and "conflict" in context["detail"].lower()


def test_selected_unit_needs_a_readable_envelope_to_justify_a_correction():
    context = resolve_context("DIMENSIONS IN INCHES", "PART", None, override="mm")
    assert context["status"] == "FAIL"


def test_implausible_sheet_without_a_selection_explains_how_to_correct():
    context = resolve_context("DIMENSIONS IN INCHES", "G_BK472722A", 218.0)
    assert context["status"] == "FAIL" and "selecting the correct unit" in context["detail"]


def png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (160, 120), "white").save(out, format="PNG")
    return out.getvalue()


def test_audit_records_a_unit_correction_and_still_reads_the_sheet(tmp_path):
    roles = []
    responses = {
        "context": {
            "drawing_number": "G_BK472722A",
            "revision": "1",
            "units_statement": "DIMENSIONS IN INCHES",
            "projection": "third angle",
            "default_tolerances": "",
            "envelope_value": 218.0,
            "envelope_unit": None,
            "uncertainties": [],
        },
        "inventory": {"features": [], "sections": [], "uncertainties": []},
        "text": reading(
            [callout("c1", "Ø218.0", "diameter", "218.0")], "DIMENSIONS IN INCHES"
        ).model_dump(mode="json"),
    }
    responses["text"]["drawing_number"] = "G_BK472722A"

    def provider(image, role, prompt, schema):
        roles.append(role)
        return {
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {"parts": [{"text": json.dumps(responses[role])}]},
                }
            ]
        }

    result = run_audit(
        png(),
        "sheet.png",
        tmp_path,
        provider=provider,
        config=PipelineConfig(ocr="disabled", detailed_inventory=False),
        override="mm",
    )
    assert roles == ["context", "inventory", "text"]
    assert result["context"]["status"] == "PASS" and result["context"]["unit"] == "mm"
    assert any(f["code"] == "UNIT_CORRECTION" for f in result["findings"])
    assert result["requirements"][0]["unit"] == "mm"


def audit_directory(tmp_path: Path, rows: list[Requirement]) -> None:
    audit = {
        "context": {"status": "PASS", "unit": "mm", "detail": "Explicit mm"},
        "requirements": [r.model_dump(mode="json") for r in rows],
        "inventory": {"features": []},
        "drawing_number": "synthetic",
        "original_sha256": "a" * 64,
    }
    raw = json.dumps(audit).encode()
    (tmp_path / "audit.json").write_bytes(raw)
    (tmp_path / "audit-manifest.json").write_text(
        json.dumps({"artifacts": {"audit.json": hashlib.sha256(raw).hexdigest()}})
    )
    (tmp_path / "drawing.png").write_bytes(b"synthetic fixture only")


def test_unused_printed_dimensions_become_visible_checks(tmp_path):
    rows = ledger() + [
        Requirement(id="STEP", raw_text="2.755 2.751", kind="linear", unit="mm"),
        Requirement(id="NOTE", raw_text="BREAK EDGES", kind="note"),
    ]
    audit_directory(tmp_path, rows)
    draft = spec([])
    assert [r.id for r in uncited_dimensions(draft, enrich_ledger(rows, "mm"))] == [
        "HD",
        "PCD",
        "STEP",
    ]
    result = build_from_audit(tmp_path, lambda _: None, corrected_spec=draft)
    by_subject = {c["subject"]: c for c in result["checks"] if c["layer"] == "D"}
    assert by_subject["STEP"]["status"] == "UNKNOWN"
    assert "not used" in by_subject["STEP"]["detail"]
    assert {u["id"] for u in result["uncited_dimensions"]} == {"HD", "PCD", "STEP"}


def wire(value):
    if isinstance(value, list):
        return [wire(v) for v in value]
    if not isinstance(value, dict):
        return value
    if "ledger" in value and "expr" in value:
        mode = next(
            k
            for k in ["ledger", "expr", "datum", "centreline", "assumption"]
            if value.get(k) is not None
        )
        return {"mode": mode, "value": str(value[mode]), "reason": value.get("reason") or ""}
    return {k: wire(v) for k, v in value.items()}


def proposal_json(draft: DraftSpec) -> str:
    proposal = draft.model_dump(mode="json")
    for key in ["schema_version", "provenance"]:
        proposal.pop(key)
    return json.dumps(wire(proposal))


def test_registered_assumption_may_anchor_an_expression(tmp_path):
    draft = spec([]).model_dump(mode="json")
    draft["assumptions"] = [
        {
            "id": "A1",
            "item": "groove start",
            "value": "12",
            "unit": "mm",
            "type": "ASSUMED",
            "reason": "scaled from view",
            "source": "section",
        }
    ]
    draft["profile"][1]["z"] = {"expr": "A1+H/2"}
    draft["profile"][2]["z"] = {"expr": "A1+H/2"}
    result = build_model(DraftSpec.model_validate(draft), ledger(), tmp_path)
    assert result["measurements"]["bbox"][2] == pytest.approx(22, abs=1e-3)
    assert any(c["layer"] == "R" and c["subject"] == "A1" for c in result["checks"])


def test_reference_and_envelope_problems_trigger_a_targeted_retry_not_a_lost_build(tmp_path):
    audit_directory(tmp_path, ledger())
    audit = json.loads((tmp_path / "audit.json").read_text())
    audit["context_proposal"] = {"envelope_value": 100, "envelope_unit": "mm"}
    raw = json.dumps(audit).encode()
    (tmp_path / "audit.json").write_bytes(raw)
    (tmp_path / "audit-manifest.json").write_text(
        json.dumps({"artifacts": {"audit.json": hashlib.sha256(raw).hexdigest()}})
    )
    bad = spec([]).model_dump(mode="json")
    bad["profile"][0]["z"] = {"expr": "A7-H"}
    bad["profile"][1]["radius"] = {"ledger": "OD"}
    bad["profile"][2]["z"] = {"expr": "H-OD"}
    bad["profile"].append(dict(bad["profile"][3]))
    prompts = []

    def provider(image, role, prompt, schema):
        prompts.append(prompt)
        proposal = bad if len(prompts) == 1 else spec([]).model_dump(mode="json")
        for key in ["schema_version", "provenance"]:
            proposal.pop(key, None)
        return {
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {"parts": [{"text": json.dumps(wire(proposal))}]},
                }
            ]
        }

    result = build_from_audit(tmp_path, lambda _: None, provider=provider)
    assert result["model_available"]
    assert "profile[0].z: Missing citation A7" in prompts[1]
    assert "exceeds half the printed envelope" in prompts[1]
    assert "profile[2]: evaluates to radius 20.000 mm, z -80.000 mm" in prompts[1]
    assert "profile[4]: duplicates vertex profile[3]" in prompts[1]


def test_a_dropped_inventoried_feature_triggers_correction_and_can_be_recovered(tmp_path):
    # Draft-1 builds cleanly (no construction_failures) but never attempts a feature the
    # independent inventory found. Coverage gaps must trigger the correction round on their
    # own, not only geometry failures — this is the class of gap that showed up as ten dropped
    # features on a real drawing, none of which construction_failures alone could ever surface.
    #
    # A minimal, fully-self-contained ledger (no N2/A survivors) so the FIRST response cites
    # every printed dimension: this isolates the NEW coverage-gap retry in
    # _construct_with_correction from request_proposal's own PRE-EXISTING (and differently
    # triggered) uncited-dimension retry, which would otherwise fire first and confound it.
    rows = [
        requirement("OD", 100, "diameter"),
        requirement("ID", 40, "diameter"),
        requirement("H", 20),
        requirement("HD", 6, "diameter"),
        requirement("PCD", 80, "diameter"),
        requirement("N", 8, "count", "count"),
    ]
    audit = {
        "context": {"status": "PASS", "unit": "mm", "detail": "Explicit mm"},
        "requirements": [r.model_dump(mode="json") for r in rows],
        "inventory": {
            "features": [
                {
                    "id": "bolts_plan",
                    "type": "hole_pattern",
                    "description": "8 bolt holes on the flange face",
                    "same_physical_group": "bolts_plan",
                }
            ]
        },
        "drawing_number": "synthetic",
        "original_sha256": "a" * 64,
    }
    raw = json.dumps(audit).encode()
    (tmp_path / "audit.json").write_bytes(raw)
    (tmp_path / "audit-manifest.json").write_text(
        json.dumps({"artifacts": {"audit.json": hashlib.sha256(raw).hexdigest()}})
    )
    (tmp_path / "drawing.png").write_bytes(b"synthetic fixture only")
    bolts = {
        "id": "bolts",
        "kind": "hole_pattern",
        "citations": ["HD", "PCD", "N", "H"],
        "inventory_ids": ["bolts_plan"],
        "diameter": {"ledger": "HD"},
        "depth": {"expr": "H/2"},
        "pcd": {"ledger": "PCD"},
        "count": {"ledger": "N"},
        "angle": {"centreline": 0, "reason": "shown on the horizontal centreline"},
        "host": "face_a",
        "reference_face": "face_A",
    }
    # A decoy pattern of a different diameter on its own, non-overlapping pitch circle: it must
    # cite the same ledger IDs (to leave nothing uncited) without sharing any hole with "bolts"
    # — same position would be an "ineffective cut" (no new material removed) or invalidate the
    # other's own hole-pattern check (a shared diameter make its count-and-placement check see
    # both patterns' holes as one, since the check groups candidates by diameter alone).
    decoy = {
        **bolts,
        "id": "decoy",
        "inventory_ids": [],
        "diameter": {"expr": "HD/2"},
        "pcd": {"expr": "PCD-HD-HD-HD-HD"},
    }
    prompts = []

    def provider(image, role, prompt, schema):
        prompts.append(prompt)
        features = [decoy, bolts] if len(prompts) > 1 else [decoy]
        proposal = spec(features).model_dump(mode="json")
        for key in ["schema_version", "provenance"]:
            proposal.pop(key, None)
        return {
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {"parts": [{"text": json.dumps(wire(proposal))}]},
                }
            ]
        }

    result = build_from_audit(tmp_path, lambda _: None, provider=provider)
    assert len(prompts) == 2, "a coverage gap alone must still trigger the correction round"
    assert "COVERAGE CHECK" in prompts[1]
    assert "8 bolt holes on the flange face" in prompts[1]
    folder = tmp_path / "models" / result["model_folder"]
    drafts = json.loads((folder / "drafts.json").read_text())
    assert drafts[0]["uncovered"] == ["bolts_plan"]
    assert drafts[1]["promoted"] is True and drafts[1]["uncovered"] == []
    built = {f["id"]: f for f in result["model_features"]}
    assert built["bolts"]["status"] == "BUILT"
    coverage = [c for c in result["checks"] if c["layer"] == "D" and c["subject"] == "bolts_plan"]
    assert not coverage, "a covered group must not still be reported as a dropped feature"


def recorded(text: str) -> dict:
    return {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": text}]}}]}


def incomplete_hole_proposal() -> dict:
    proposal = spec([]).model_dump(mode="json")
    for key in ["schema_version", "provenance"]:
        proposal.pop(key)
    proposal["features"] = [
        {
            "id": "bolts",
            "kind": "hole_pattern",
            "citations": ["HD", "PCD", "N"],
            "host": "face_a",
            "reference_face": "face_A",
            "diameter": {"ledger": "HD"},
            "depth": {"ledger": "H"},
            "pcd": {"ledger": "PCD"},
            "count": {"ledger": "N"},
        }
    ]
    return wire(proposal)


def test_persistently_incomplete_feature_is_salvaged_as_a_named_unresolved_entry(tmp_path):
    rows = ledger() + [
        Requirement(id="COUNT", raw_text="8 HOLES", kind="count", unit="count", value=8)
    ]
    audit_directory(tmp_path, rows)
    calls = []

    def provider(image, role, prompt, schema):
        calls.append(prompt)
        return recorded(json.dumps(incomplete_hole_proposal()))

    result = build_from_audit(tmp_path, lambda _: None, provider=provider)
    assert len(calls) == 3
    assert result["model_available"]
    folder = tmp_path / "models" / result["model_folder"]
    salvaged = json.loads((folder / "salvaged.json").read_text())["unresolved"]
    assert salvaged and salvaged[0].startswith("bolts: proposal rejected before construction")
    assert "requires angle" in salvaged[0]
    assert any(
        c["layer"] == "AS" and "bolts" in c["detail"] and c["status"] == "UNKNOWN"
        for c in result["checks"]
    )
    rejected = [c for c in result["checks"] if c["layer"] == "D" and c["subject"] == "bolts"]
    assert rejected and rejected[0]["status"] == "FAIL"
    assert "rejected before construction" in rejected[0]["detail"]
    assert result["completion"] == "PARTIAL_DRAFT_REQUIRES_REVIEW"


def test_transient_provider_failures_do_not_consume_the_schema_retry_budget(tmp_path):
    audit_directory(tmp_path, ledger())
    good = spec([]).model_dump(mode="json")
    for key in ["schema_version", "provenance"]:
        good.pop(key)
    script = iter(["provider", "schema", "provider", "good"])
    calls = []

    def provider(image, role, prompt, schema):
        step = next(script, "good")  # a coverage retry may follow the accepted proposal
        calls.append(step)
        if step == "provider":
            raise ValueError("Gemini connection failed or timed out")
        if step == "schema":
            return recorded(json.dumps(incomplete_hole_proposal()))
        return recorded(json.dumps(wire(good)))

    result = build_from_audit(tmp_path, lambda _: None, provider=provider)
    assert calls[:4] == ["provider", "schema", "provider", "good"]
    assert result["model_available"]
    assert not (tmp_path / "models" / result["model_folder"] / "salvaged.json").exists()


def test_rejected_request_is_not_retried_as_a_schema_error(tmp_path):
    audit_directory(tmp_path, ledger())
    calls = []

    def provider(image, role, prompt, schema):
        calls.append(prompt)
        raise ValueError("Gemini HTTP 400; no response body logged")

    with pytest.raises(ValueError, match="unavailable"):
        build_from_audit(tmp_path, lambda _: None, provider=provider)
    assert len(calls) == 1
    folder = next((tmp_path / "models").iterdir())
    assert json.loads((folder / "error-1.json").read_text())["provider"] is True
    assert not list(folder.glob("retry-prompt-*.txt"))


def test_one_bounded_coverage_retry_names_the_skipped_dimensions(tmp_path):
    rows = ledger() + [Requirement(id="STEP", raw_text="2.755", kind="linear", unit="mm")]
    audit_directory(tmp_path, rows)
    prompts = []

    def provider(image, role, prompt, schema):
        prompts.append(prompt)
        return {
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {"parts": [{"text": proposal_json(spec([]))}]},
                }
            ]
        }

    result = build_from_audit(tmp_path, lambda _: None, provider=provider)
    assert len(prompts) == 2
    assert "COVERAGE CHECK" in prompts[1] and '"STEP"' in prompts[1]
    assert "COVERAGE CHECK" not in prompts[0]
    assert result["model_available"]
    assert any(c["subject"] == "STEP" and c["status"] == "UNKNOWN" for c in result["checks"])


def test_bottomed_tap_drill_without_follow_on_is_a_valid_port(tmp_path):
    # ".500 NPT, Ø18.24 BOTTOM DRILL 40.0 DEEP": entry depth equals the whole drill path.
    bottomed = port(entry_depth={"expr": "OD/2-ID/2"}, entry_diameter={"expr": "HD+HD"})
    result = build_model(spec([bottomed]), ledger(), tmp_path)
    receipt = result["features"][0]
    assert receipt["status"] == "BUILT", receipt.get("detail")
    assert receipt["removed_volume_mm3"] == pytest.approx(math.pi * 6**2 * 30, rel=0.02)
    deeper = port(entry_depth={"expr": "OD/2-ID/2+HD"}, entry_diameter={"expr": "HD+HD"})
    failed = build_model(spec([deeper]), ledger(), tmp_path / "deeper")["features"][0]
    assert failed["status"] == "FAILED" and "inside full drill path" in failed["detail"]


def buried_holes(z_expr: str) -> dict:
    return {
        "id": "bolts",
        "kind": "hole_pattern",
        "citations": ["HD", "N2", "H", "PCD"],
        "diameter": {"ledger": "HD"},
        "depth": {"expr": "H/2"},
        "z": {"expr": z_expr},
        "pcd": {"expr": "PCD-HD-HD"},
        "count": {"ledger": "N2"},
        "angle": {"centreline": 0, "reason": "on the horizontal centreline"},
        "host": "face_a",
        "reference_face": "face_A",
    }


def test_named_construction_failures_are_fed_back_once_and_the_better_draft_wins(tmp_path):
    audit_directory(tmp_path, ledger())
    prompts = []
    # Attempt 1 starts the holes on a plane buried in material; the correction moves them to
    # face A (z=0), which is exposed at that pitch radius.
    answers = iter(
        [proposal_json(spec([buried_holes("H/2")])), proposal_json(spec([buried_holes("H/2-H/2")]))]
    )

    def provider(image, role, prompt, schema):
        prompts.append(prompt)
        return recorded(next(answers, proposal_json(spec([buried_holes("H/2-H/2")]))))

    result = build_from_audit(tmp_path, lambda _: None, provider=provider)
    correction = [p for p in prompts if "GEOMETRY CHECK" in p]
    assert len(correction) == 1
    assert "bolts: Explicit z 10.000 mm is not an exposed face_a face" in correction[0]
    assert "PREVIOUS PROPOSAL" in correction[0] and '"id":"bolts"' in correction[0]
    assert result["model_features"][0]["status"] == "BUILT"
    folder = tmp_path / "models" / result["model_folder"]
    drafts = json.loads((folder / "drafts.json").read_text())
    assert [d["promoted"] for d in drafts] == [False, True]
    assert drafts[0]["failed"] == ["bolts"] and drafts[1]["failed"] == []
    assert (folder / "model.step").exists() and (folder / "draft-1" / "model.step").exists()
    assert not (folder / "draft-2").exists()
    assert (folder / "correction-response-1.json").exists()
    assert not any(c["status"] == "FAIL" and c["layer"] == "G" for c in result["checks"])


def test_a_correction_that_does_not_improve_is_recorded_but_not_promoted(tmp_path):
    audit_directory(tmp_path, ledger())
    calls = []

    def provider(image, role, prompt, schema):
        calls.append(prompt)
        return recorded(proposal_json(spec([buried_holes("H/2")])))

    result = build_from_audit(tmp_path, lambda _: None, provider=provider)
    assert sum("GEOMETRY CHECK" in p for p in calls) == 1
    folder = tmp_path / "models" / result["model_folder"]
    drafts = json.loads((folder / "drafts.json").read_text())
    assert [d["promoted"] for d in drafts] == [True, False]
    assert (folder / "draft-2" / "model.step").exists() and (folder / "model.step").exists()
    assert result["model_features"][0]["status"] == "FAILED"
    assert any(c["subject"] == "bolts" and c["status"] == "FAIL" for c in result["checks"])


def test_engineer_corrected_specs_are_built_once_without_asking_the_model(tmp_path):
    audit_directory(tmp_path, ledger())
    result = build_from_audit(tmp_path, lambda _: None, corrected_spec=spec([buried_holes("H/2")]))
    folder = tmp_path / "models" / result["model_folder"]
    assert result["model_features"][0]["status"] == "FAILED"
    assert json.loads((folder / "drafts.json").read_text()) == [
        {"draft": "draft-1", "failed": ["bolts"], "uncovered": [], "promoted": True}
    ]
    assert not (folder / "correction-prompt.txt").exists()


def test_tap_drill_against_a_plain_hole_observation_is_not_a_type_conflict(tmp_path):
    audit_directory(tmp_path, ledger())
    audit = json.loads((tmp_path / "audit.json").read_text())
    audit["inventory"] = {
        "features": [
            {"id": "plan_small", "type": "hole_pattern", "view": "plan", "count": 2},
            {"id": "plan_port", "type": "port", "view": "plan", "count": 1},
        ]
    }
    raw = json.dumps(audit).encode()
    (tmp_path / "audit.json").write_bytes(raw)
    (tmp_path / "audit-manifest.json").write_text(
        json.dumps({"artifacts": {"audit.json": hashlib.sha256(raw).hexdigest()}})
    )
    feature = tapped("5/16-18 UNC")
    feature["inventory_ids"] = ["plan_small", "plan_port"]
    result = build_from_audit(tmp_path, lambda _: None, corrected_spec=spec([feature]))
    by_subject = {c["subject"]: c for c in result["checks"] if c["layer"] == "D"}
    assert by_subject[f"{feature['id']}:plan_small"]["status"] == "UNKNOWN"
    assert "engineer validation" in by_subject[f"{feature['id']}:plan_small"]["detail"]
    conflict = by_subject[f"{feature['id']}:plan_port"]
    assert conflict["status"] == "UNKNOWN"
    assert "observed a port in the plan view" in conflict["detail"]
    assert "proposes a tapped_hole" in conflict["detail"]


def test_unknown_inventory_ids_are_a_targeted_retry_not_a_built_failure(tmp_path):
    from drawing2step.revb_proposal import validate_proposal

    feature = tapped("5/16-18 UNC")
    feature["inventory_ids"] = ["plan_small", "V9_made_up"]
    problems = validate_proposal(spec([feature]), ledger(), None, {"plan_small"})
    assert problems == [
        "features[taps].inventory_ids: 'V9_made_up' is not an inventory observation id; "
        "use only ids from INDEPENDENT INVENTORY"
    ]
    assert validate_proposal(spec([feature]), ledger(), None) == []


def test_non_cardinal_centreline_becomes_a_reviewable_assumption_not_a_lost_pattern(tmp_path):
    from drawing2step.revb_proposal import decode_proposal

    proposal = spec([]).model_dump(mode="json")
    for key in ["schema_version", "provenance"]:
        proposal.pop(key)
    holes = buried_holes("H/2-H/2")
    proposal["features"] = [holes]
    encoded = wire(proposal)
    encoded["features"][0]["angle"] = {"mode": "centreline", "value": "45", "reason": "shown"}
    decoded = decode_proposal(json.dumps(encoded)).draft()
    assert decoded.features[0].angle.assumption == "A_centreline_1"
    registered = {a.id: a for a in decoded.assumptions}["A_centreline_1"]
    assert registered.type == "IMPLIED_CENTRELINE" and float(registered.value) == 45
    assert registered.unit == "degree"
    result = build_model(decoded, ledger(), tmp_path)
    assert result["features"][0]["status"] == "BUILT"
    solid = cq.importers.importStep(str(tmp_path / result["step"])).val().Solids()[0]
    theta = math.radians(45)
    assert solid.isInside((34 * math.cos(theta), 34 * math.sin(theta), 5)) is False
    assert solid.isInside((34, 0, 5)) is True
    encoded["features"][0]["angle"] = {"mode": "centreline", "value": "top", "reason": ""}
    with pytest.raises(ValueError, match="not an angle"):
        decode_proposal(json.dumps(encoded))


def test_holes_without_z_enter_the_first_exposed_face_holding_their_pitch_circle(tmp_path):
    # stepped_spec: flange Ø100 z 0..10, hub Ø80 z 10..20. Face B (z=20) holds no material
    # at pitch Ø90, so a face_b drill enters the flange back face at z=10.
    holes = {
        "id": "bolts",
        "kind": "hole_pattern",
        "citations": ["HD", "N2", "H"],
        "diameter": {"ledger": "HD"},
        "depth": {"expr": "H/2"},
        "pcd": {"expr": "OD-HD-HD"},
        "count": {"ledger": "N2"},
        "angle": {"centreline": 0, "reason": "on the horizontal centreline"},
        "host": "face_b",
        "reference_face": "face_A",
    }
    result = build_model(stepped_spec([holes]), ledger(), tmp_path)
    receipt = result["features"][0]
    assert receipt["status"] == "BUILT", receipt.get("detail")
    assert receipt["removed_volume_mm3"] == pytest.approx(2 * math.pi * 3**2 * 10, rel=0.01)
    resolved = [c for c in result["checks"] if c["subject"] == "bolts" and c["layer"] == "H2"]
    assert any("resolved from the profile at z=10.000" in c["detail"] for c in resolved)
    assert all(c["status"] == "UNKNOWN" for c in resolved if "resolved" in c["detail"])
    solid = cq.importers.importStep(str(tmp_path / result["step"])).val().Solids()[0]
    assert solid.isInside((44, 0, 5)) is False and solid.isInside((44, 0, 0.5)) is False


def test_holes_whose_footprint_fits_no_exposed_face_fail_by_name(tmp_path):
    holes = {
        "id": "bolts",
        "kind": "hole_pattern",
        "citations": ["HD", "N2", "H"],
        "diameter": {"ledger": "HD"},
        "depth": {"expr": "H/2"},
        "pcd": {"expr": "ID-HD"},
        "count": {"ledger": "N2"},
        "angle": {"centreline": 0, "reason": "on the horizontal centreline"},
        "host": "face_b",
        "reference_face": "face_A",
    }
    receipt = build_model(stepped_spec([holes]), ledger(), tmp_path)["features"][0]
    assert receipt["status"] == "FAILED"
    assert "No exposed face_b face holds the pitch radius" in receipt["detail"]


def test_self_intersecting_profiles_are_named_before_any_cad_attempt():
    from drawing2step.revb_proposal import profile_self_intersections, validate_proposal

    # A bore groove radius (PCD/2 = 40) pushed out beyond the hub wall at r = 30.
    crossing = [
        (30.0, 0.0),
        (30.0, 20.0),
        (10.0, 20.0),
        (10.0, 12.0),
        (40.0, 12.0),
        (40.0, 8.0),
        (10.0, 8.0),
        (10.0, 0.0),
    ]
    problems = profile_self_intersections(crossing)
    assert (
        problems
        and "profile[0]-profile[1] crosses or overlaps edge profile[3]-profile[4]" in problems[0]
    )
    simple = [(30.0, 0.0), (30.0, 20.0), (10.0, 20.0), (10.0, 0.0)]
    assert profile_self_intersections(simple) == []
    # A zero-thickness wall: the bore walk retraces the outer wall.
    overlap = [
        (30.0, 0.0),
        (30.0, 20.0),
        (10.0, 20.0),
        (10.0, 10.0),
        (30.0, 10.0),
        (30.0, 2.0),
        (10.0, 2.0),
        (10.0, 0.0),
    ]
    assert any("profile[0]-profile[1]" in p for p in profile_self_intersections(overlap))
    draft = spec([]).model_dump(mode="json")
    draft["profile"] = [
        {"z": {"datum": "face_A"}, "radius": {"expr": "OD/2"}},
        {"z": {"ledger": "H"}, "radius": {"expr": "OD/2"}},
        {"z": {"ledger": "H"}, "radius": {"expr": "ID/2"}},
        {"z": {"expr": "H/2"}, "radius": {"expr": "ID/2"}},
        {"z": {"expr": "H/2"}, "radius": {"expr": "OD/2+HD"}},
        {"z": {"expr": "HD"}, "radius": {"expr": "OD/2+HD"}},
        {"z": {"expr": "HD"}, "radius": {"expr": "ID/2"}},
        {"z": {"datum": "face_A"}, "radius": {"expr": "ID/2"}},
    ]
    problems = validate_proposal(DraftSpec.model_validate(draft), ledger(), None)
    assert any("crosses or overlaps" in p for p in problems), problems


def test_profile_longer_than_any_printed_dimension_fails_and_is_fed_back_once(tmp_path):
    audit_directory(tmp_path, ledger())
    too_long = spec([]).model_dump(mode="json")
    too_long["profile"][1]["z"] = too_long["profile"][2]["z"] = {"expr": "H+H"}
    prompts = []
    answers = iter([proposal_json(DraftSpec.model_validate(too_long)), proposal_json(spec([]))])

    def provider(image, role, prompt, schema):
        prompts.append(prompt)
        return recorded(next(answers, proposal_json(spec([]))))

    result = build_from_audit(tmp_path, lambda _: None, provider=provider)
    correction = [p for p in prompts if "GEOMETRY CHECK" in p]
    assert correction and all(p.count("GEOMETRY CHECK") == 1 for p in correction)
    assert "profile: Body axial length 40.000 mm exceeds the largest printed" in correction[0]
    folder = tmp_path / "models" / result["model_folder"]
    drafts = json.loads((folder / "drafts.json").read_text())
    assert drafts[0]["failed"] == ["profile"] and drafts[1]["promoted"]
    assert {c["subject"]: c["status"] for c in result["checks"]}["axial_length"] == "PASS"


def test_build_from_audit_freezes_a_release_record_matching_the_delivered_step(tmp_path):
    from drawing2step.final_verification import ReleaseRecord, verify_release

    audit_directory(tmp_path, ledger())
    result = build_from_audit(tmp_path, lambda _: None, corrected_spec=spec([]))
    folder = tmp_path / "models" / result["model_folder"]
    record = ReleaseRecord.model_validate(json.loads((folder / "release.json").read_text()))
    checks = json.loads((folder / "checks.json").read_text())
    verify_release(folder / result["step"], checks, record)  # must not raise
    assert record.status in {"nominal_drawing_conformance", "unresolved_requirements", "blocked"}
    assert not record.manufacturing_approval


def test_a_failed_required_port_demoted_to_report_only_is_rejected_not_promoted(tmp_path):
    # The review's own named loophole: a required feature that fails to build must not be
    # "fixed" by declaring it report_only — that hides the obligation instead of meeting it.
    audit_directory(tmp_path, ledger())
    broken_port = port(entry_diameter={"expr": "HD/2"})  # entry smaller than follow-on: FAILS
    draft1 = spec([broken_port])
    demoted = spec([{**broken_port, "report_only": True, "reason": "operator will drill by hand"}])
    answers = iter([proposal_json(draft1), proposal_json(demoted)])

    def provider(image, role, prompt, schema):
        return recorded(next(answers, proposal_json(spec([]))))

    result = build_from_audit(tmp_path, lambda _: None, provider=provider)
    folder = tmp_path / "models" / result["model_folder"]
    drafts = json.loads((folder / "drafts.json").read_text())
    assert drafts[0]["failed"] == ["quench"]
    assert drafts[1]["promoted"] is False
    assert "report_only" in drafts[1]["promotion_reason"]
    receipts = {f["id"]: f for f in result["features"]}
    assert receipts["quench"]["status"] == "FAILED"


def test_body_length_matching_no_printed_dimension_is_a_review_item(tmp_path):
    audit_directory(tmp_path, ledger())
    matched = build_from_audit(tmp_path, lambda _: None, corrected_spec=spec([]))
    by_subject = {c["subject"]: c for c in matched["checks"]}
    assert by_subject["axial_length"]["status"] == "PASS"
    draft = spec([]).model_dump(mode="json")
    draft["profile"][1]["z"] = draft["profile"][2]["z"] = {"expr": "H/2"}
    short = build_from_audit(
        tmp_path, lambda _: None, corrected_spec=DraftSpec.model_validate(draft)
    )
    check = {c["subject"]: c for c in short["checks"]}["axial_length"]
    assert check["status"] == "UNKNOWN" and "10.000 mm matches no printed" in check["detail"]


def test_body_matching_a_smaller_printed_dimension_than_the_largest_is_a_review_item(tmp_path):
    # H = 20 mm is the largest printed length; SMALL = 6 mm is a genuine but unrelated one
    # (e.g. a groove width). A body that ends at 6 mm while 20 mm is also on the sheet is not
    # obviously correct just because 6 mm happens to be printed somewhere too.
    rows = ledger() + [requirement("SMALL", 6)]
    audit_directory(tmp_path, rows)
    draft = spec([]).model_dump(mode="json")
    draft["profile"][1]["z"] = draft["profile"][2]["z"] = {"expr": "SMALL"}
    result = build_from_audit(
        tmp_path, lambda _: None, corrected_spec=DraftSpec.model_validate(draft)
    )
    check = {c["subject"]: c for c in result["checks"]}["axial_length"]
    assert check["status"] == "UNKNOWN"
    assert "6.000 mm equals a printed linear dimension, but not the largest" in check["detail"]


def test_axial_counterbores_enlarge_each_hole_from_the_host_face(tmp_path):
    counterbore = {
        "id": "cbores",
        "kind": "counterbore",
        "citations": ["HD", "PCD", "N2", "H"],
        "diameter": {"expr": "HD+HD"},
        "depth": {"expr": "H/2-HD"},
        "pcd": {"expr": "PCD-HD-HD"},
        "count": {"ledger": "N2"},
        "angle": {"centreline": 0, "reason": "on the horizontal centreline"},
        "host": "face_a",
        "reference_face": "face_A",
    }
    holes = buried_holes("H/2-H/2")
    result = build_model(spec([holes, counterbore]), ledger(), tmp_path)
    receipts = {f["id"]: f for f in result["features"]}
    assert receipts["cbores"]["status"] == "BUILT", receipts["cbores"].get("detail")
    # Ø12 × 4 mm deep around each Ø6 hole: the ring left after the hole is removed.
    extra = 2 * math.pi * (6**2 - 3**2) * 4
    assert receipts["cbores"]["removed_volume_mm3"] == pytest.approx(extra, rel=0.02)


def test_a_radial_spotface_trims_the_curved_surface_flat_at_a_port_entry(tmp_path):
    spotface = {
        "id": "spotface",
        "kind": "counterbore",
        "citations": ["HD", "H"],
        "diameter": {"expr": "HD+HD+HD+HD"},
        "depth": {"expr": "HD/2"},
        "z": {"expr": "H/2"},
        "angle": {"centreline": 0, "reason": "shown on the horizontal centreline"},
        "host": "outside",
        "reference_face": "face_A",
    }
    result = build_model(
        spec([port(thread=None, entry_depth=None, entry_diameter=None), spotface]),
        ledger(),
        tmp_path,
    )
    receipts = {f["id"]: f for f in result["features"]}
    assert receipts["spotface"]["status"] == "BUILT", receipts["spotface"].get("detail")
    solid = cq.importers.importStep(str(tmp_path / result["step"])).val().Solids()[0]
    # Ø24 spotface 3 mm deep at the OD (r=50): flat at r=47 across its footprint.
    assert solid.isInside((48.0, 8.0, 10.0)) is False
    assert solid.isInside((46.5, 8.0, 10.0)) is True
    assert solid.isInside((48.0, 0.0, 15.0)) is False
    assert solid.isInside((45.0, 20.0, 10.0)) is True  # outside the footprint the OD remains


def test_counterbore_contract_names_its_missing_placement_fields():
    from drawing2step.revb_model import Feature

    base = {
        "id": "cb",
        "kind": "counterbore",
        "citations": ["HD"],
        "reference_face": "face_A",
        "diameter": {"ledger": "HD"},
        "depth": {"ledger": "HD"},
        "angle": {"centreline": 0, "reason": "x"},
    }
    with pytest.raises(ValueError, match="spotface at a port entry requires z"):
        Feature.model_validate({**base, "host": "outside"})
    with pytest.raises(ValueError, match="axial counterbore requires pcd and count"):
        Feature.model_validate({**base, "host": "face_a"})


def test_a_correction_that_drops_the_failing_feature_is_not_promoted(tmp_path):
    audit_directory(tmp_path, ledger())
    good_port = port(thread=None, entry_depth=None, entry_diameter=None)
    answers = iter(
        [
            proposal_json(spec([buried_holes("H/2"), good_port])),
            proposal_json(spec([good_port])),  # "fixes" the holes by deleting them
        ]
    )

    def provider(image, role, prompt, schema):
        return recorded(next(answers, proposal_json(spec([good_port]))))

    result = build_from_audit(tmp_path, lambda _: None, provider=provider)
    folder = tmp_path / "models" / result["model_folder"]
    drafts = json.loads((folder / "drafts.json").read_text())
    assert drafts[0]["promoted"] and not drafts[1]["promoted"]
    assert drafts[1]["dropped"] == ["bolts"] and drafts[1]["failed"] == []
    assert {f["id"] for f in result["model_features"]} == {"bolts", "quench"}


def chamfer_at(z: dict, radius: dict) -> dict:
    return {
        "id": "break",
        "kind": "chamfer",
        "citations": ["HD", "H", "A"],
        "z": z,
        "radius": radius,
        "width": {"expr": "HD/2"},
        "angle": {"ledger": "A"},
        "host": "outside",
        "reference_face": "face_A",
    }


def test_chamfer_snaps_to_the_nearest_edge_within_a_limit_pair_and_names_a_far_miss(tmp_path):
    # Profile uses the nominal (OD/2 = 50); the chamfer cites a limit 0.05 mm away.
    close = spec([chamfer_at({"datum": "face_A"}, {"expr": "OD/2+A1"})]).model_dump(mode="json")
    close["assumptions"] = [
        {
            "id": "A1",
            "item": "limit offset",
            "value": 0.05,
            "unit": "mm",
            "type": "ASSUMED",
            "reason": "upper limit",
            "source": "sheet",
        }
    ]
    result = build_model(DraftSpec.model_validate(close), ledger(), tmp_path / "close")
    receipt = result["features"][0]
    assert receipt["status"] == "BUILT", receipt.get("detail")
    assert receipt["removed_volume_mm3"] > 0
    far = spec([chamfer_at({"expr": "H/2"}, {"expr": "OD/2"})])
    receipt = build_model(far, ledger(), tmp_path / "far")["features"][0]
    assert receipt["status"] == "FAILED"
    assert "nearest circular edge is at z=" in receipt["detail"]


def test_a_body_shorter_than_the_printed_overall_length_fails_and_is_fed_back(tmp_path):
    audit_directory(tmp_path, ledger())
    audit = json.loads((tmp_path / "audit.json").read_text())
    audit["context_proposal"] = {"overall_length_value": 20, "overall_length_unit": "mm"}
    raw = json.dumps(audit).encode()
    (tmp_path / "audit.json").write_bytes(raw)
    (tmp_path / "audit-manifest.json").write_text(
        json.dumps({"artifacts": {"audit.json": hashlib.sha256(raw).hexdigest()}})
    )
    short = spec([]).model_dump(mode="json")
    short["profile"][1]["z"] = short["profile"][2]["z"] = {"expr": "H/2"}  # 10 mm body
    answers = iter([proposal_json(DraftSpec.model_validate(short)), proposal_json(spec([]))])
    prompts = []

    def provider(image, role, prompt, schema):
        prompts.append(prompt)
        return recorded(next(answers, proposal_json(spec([]))))

    result = build_from_audit(tmp_path, lambda _: None, provider=provider)
    correction = [p for p in prompts if "GEOMETRY CHECK" in p]
    assert correction and "too short: a step is missing" in correction[0]
    axial = {c["subject"]: c for c in result["checks"]}["axial_length"]
    assert axial["status"] == "PASS" and "printed overall length" in axial["detail"]
    assert result["measurements"]["bbox"][2] == pytest.approx(20)


def test_context_overall_length_smaller_than_a_printed_dimension_is_disregarded(tmp_path):
    # A local reference distance (5 mm) misread as the overall length; H = 20 mm is the only
    # printed linear dimension, so nothing in the drawing can be shorter than it and still be
    # "the whole part end to end". Trusting the 5 mm reading would truncate the body and let
    # axial_length_check rubber-stamp that truncation against the very number that caused it.
    audit_directory(tmp_path, ledger())
    audit = json.loads((tmp_path / "audit.json").read_text())
    audit["context_proposal"] = {"overall_length_value": 5, "overall_length_unit": "mm"}
    raw = json.dumps(audit).encode()
    (tmp_path / "audit.json").write_bytes(raw)
    (tmp_path / "audit-manifest.json").write_text(
        json.dumps({"artifacts": {"audit.json": hashlib.sha256(raw).hexdigest()}})
    )
    prompts = []

    def provider(image, role, prompt, schema):
        prompts.append(prompt)
        return recorded(proposal_json(spec([])))

    result = build_from_audit(tmp_path, lambda _: None, provider=provider)
    assert '"overall_length_value": null' in prompts[0]
    assert '"overall_length_value": 5' not in prompts[0]
    assert result["measurements"]["bbox"][2] == pytest.approx(20)
    by_subject = {c["subject"]: c for c in result["checks"]}
    assert by_subject["overall_length"]["status"] == "UNKNOWN"
    assert "cannot be the true envelope" in by_subject["overall_length"]["detail"]
    assert by_subject["axial_length"]["status"] == "PASS"


def test_uncovered_inventoried_port_becomes_a_named_failure_not_a_review_item(tmp_path):
    audit_directory(tmp_path, ledger())
    audit = json.loads((tmp_path / "audit.json").read_text())
    audit["inventory"] = {
        "features": [
            {
                "id": "quench_sec",
                "type": "port",
                "view": "Section A-A",
                "description": "Quench port",
                "same_physical_group": "quench",
            },
            {
                "id": "flush_sec",
                "type": "port",
                "view": "Section B-B",
                "description": "Flush port",
                "same_physical_group": "flush",
            },
            {
                "id": "flush_plan",
                "type": "port",
                "view": "plan",
                "description": "Flush port, plan view",
                "same_physical_group": "flush",
            },
            {
                "id": "marking_note",
                "type": "marking",
                "view": "detail",
                "description": "Proprietary marking",
                "same_physical_group": None,
            },
        ]
    }
    raw = json.dumps(audit).encode()
    (tmp_path / "audit.json").write_bytes(raw)
    (tmp_path / "audit-manifest.json").write_text(
        json.dumps({"artifacts": {"audit.json": hashlib.sha256(raw).hexdigest()}})
    )
    feature = port()
    feature["inventory_ids"] = ["quench_sec"]
    result = build_from_audit(tmp_path, lambda _: None, corrected_spec=spec([feature]))
    by_subject = {c["subject"]: c for c in result["checks"]}
    # The physical flush port (two views, one same_physical_group) was never built: FAIL, not
    # a review-only UNKNOWN, since it is a dropped feature and not an association nuance.
    assert by_subject["flush"]["status"] == "FAIL"
    assert "port 'Flush port'" in by_subject["flush"]["detail"]
    assert "flush_plan, flush_sec" in by_subject["flush"]["detail"]
    # The marking has no built counterpart either, but markings are never cuts: no FAIL for it.
    assert by_subject["marking_note"]["status"] == "UNKNOWN"


def test_an_accepted_source_contract_rejects_a_wrong_bore_through_the_normal_pipeline(tmp_path):
    # The ledger itself was misread (Ø80 where the drawing prints Ø40): every existing,
    # candidate-derived check is internally consistent and passes. Only an independently
    # authored, reviewed source contract catches this, and it must do so through the ordinary
    # build_from_audit path — no separate manual verification script.
    from drawing2step.source_contract import SourceContract

    rows = ledger()
    rows = [
        r
        if r.id != "ID"
        else Requirement(**{**r.model_dump(), "raw_text": "Ø80", "value": Decimal(80)})
        for r in rows
    ]
    audit_directory(tmp_path, rows)
    contract = SourceContract(
        drawing_number="synthetic",
        requirements=(
            {
                "id": "bore",
                "feature": "Main bore",
                "quantity": "Internal diameter",
                "kind": "axial_band",
                "referenced_to": "face_a",
                "unit": "mm",
                "evidence": "reviewed by hand against the sheet",
                "applies_between": (0, 20),
                "bore": 40,
                "interpretation": "reviewed",
            },
        ),
    )
    (tmp_path / "source-contract.json").write_text(contract.model_dump_json())
    result = build_from_audit(tmp_path, lambda _: None, corrected_spec=spec([]))
    by_subject = {c["subject"]: c for c in result["checks"] if c.get("layer") == "SRC"}
    assert by_subject["bore"]["status"] == "FAIL"
    assert "40" in by_subject["bore"]["detail"] and "80" in by_subject["bore"]["detail"]
    assert result["completion"] == "PARTIAL_DRAFT_REQUIRES_REVIEW"


def test_unit_corrected_sheets_measure_context_lengths_in_the_corrected_unit():
    from drawing2step.revb_build_pipeline import _context_length_mm

    audit = {
        "context": {"unit": "mm", "unit_correction": {"sheet_unit": "in"}},
        "context_proposal": {"overall_length_value": 38.9, "overall_length_unit": "in"},
    }
    assert _context_length_mm(audit, "overall_length") == 38.9
    audit["context"] = {"unit": "in"}
    assert _context_length_mm(audit, "overall_length") == pytest.approx(38.9 * 25.4)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("GEBK472722A", []),  # a drawing number is not a 472-metre dimension
        ("2X M8x1.25 TAP", [("count", 2)]),
        ("2X.132 PIN EXT.", [("count", 2), ("linear", Decimal(".132"))]),
        ("R.062", [("radius", Decimal(".062"))]),
        ("4~ ø14MM HOLES THRU", [("count", 4), ("diameter", Decimal("14"))]),
        (
            "10X Ø4.96 ▽9.52",
            [("count", 10), ("diameter", Decimal("4.96")), ("linear", Decimal("9.52"))],
        ),
        ("SEE NOTE 3A", []),
        # A numbered thread designation is an identifier, not two lengths (#10, then -24).
        ("THEN #10-24 TAP .38 DEEP", [("linear", Decimal(".38"))]),
        # Degrees-minutes is one angle (22 + 30/60), not an angle plus a stray 30-unit length.
        ("22°30'", [("angle", Decimal("22.5"))]),
        # A unified fractional thread is one identifier, not three lengths (5, then 16, then 18).
        ("5/16-18 UNC", []),
        (
            "3X .750 3X .500 3X 5/16-18 UNC",
            [
                ("count", 3),
                ("linear", Decimal(".750")),
                ("count", 3),
                ("linear", Decimal(".500")),
                ("count", 3),
            ],
        ),
        # An NPT pipe thread is one identifier whether written as a fraction or a decimal, not
        # a bare 1-and-2 or a 0.500 in length.
        ("1/2 NPT", []),
        ("1/2-14 NPT", []),
        (".500 NPT, THEN DRILL TO MEET GROOVE", []),
        ("4X 1/2 NPT", [("count", 4)]),
    ],
)
def test_numbers_glued_to_letters_are_identifiers_not_readings(text, expected):
    row = Requirement(id="R1", raw_text=text, kind="note", geometry_driving=True)
    children = [(r.kind, r.value) for r in enrich_ledger([row], "in") if r.id != "R1"]
    assert children == expected
