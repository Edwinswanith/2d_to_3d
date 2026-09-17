"""Role-safe profile compilation: named spans compiled to a boundary polygon, never a raw one.

Proves profile_compiler.py against hand-authored spans only, per the plan's own "prove the
representation first, without any AI call" — extraction is not wired to this module yet.
"""

from decimal import Decimal

import pytest

from drawing2step.datums import Datum, DatumRegistry
from drawing2step.profile_compiler import ProfileSpan, compile_profile
from drawing2step.revb import Requirement
from drawing2step.revb_model import DraftSpec, build_model

# A trivially valid placeholder: build_model reads spec.profile only when points_override is
# None, so any schema-valid profile is inert here.
_PLACEHOLDER_PROFILE = [
    {"z": {"datum": "face_a"}, "radius": {"expr": "OD/2"}},
    {"z": {"ledger": "H"}, "radius": {"expr": "OD/2"}},
    {"z": {"ledger": "H"}, "radius": {"expr": "ID/2"}},
    {"z": {"datum": "face_a"}, "radius": {"expr": "ID/2"}},
]


def _requirement(identifier: str, value: float, unit: str = "mm") -> Requirement:
    return Requirement(
        id=identifier,
        raw_text=str(value),
        kind="linear",
        value=Decimal(str(value)),
        unit=unit,
        sources=(f"synthetic:{identifier}",),
        text_status="AGREED",
    )


def _ledger() -> dict[str, Requirement]:
    rows = {
        "OD": 100,
        "ID": 40,
        "HUB_OD": 80,
        "H": 20,
        "HUB_Z": 10,
        "HUB_Z_LATE": 12,
        "HUB_Z_PLUS1": 11,
    }
    return {k: _requirement(k, v) for k, v in rows.items()}


def _stepped_spans() -> list[ProfileSpan]:
    # Flange OD100 for z 0..10, hub OD80 for z 10..20, bore ID40 throughout — same shape as
    # tests/test_revb_drawing_edges.py's stepped_spec(), authored as named spans instead.
    return [
        ProfileSpan.model_validate(
            {
                "id": "flange",
                "surface": "external",
                "role": "straight",
                "z_start": {"datum": "face_a"},
                "z_end": {"datum": "face_a", "offset": {"ledger": "HUB_Z"}},
                "diameter_start": {"ledger": "OD"},
            }
        ),
        ProfileSpan.model_validate(
            {
                "id": "flange_to_hub",
                "surface": "external",
                "role": "shoulder",
                "z_start": {"datum": "face_a", "offset": {"ledger": "HUB_Z"}},
                "z_end": {"datum": "face_a", "offset": {"ledger": "HUB_Z"}},
                "diameter_start": {"ledger": "OD"},
                "diameter_end": {"ledger": "HUB_OD"},
            }
        ),
        ProfileSpan.model_validate(
            {
                "id": "hub",
                "surface": "external",
                "role": "straight",
                "z_start": {"datum": "face_a", "offset": {"ledger": "HUB_Z"}},
                "z_end": {"datum": "face_a", "offset": {"ledger": "H"}},
                "diameter_start": {"ledger": "HUB_OD"},
            }
        ),
        ProfileSpan.model_validate(
            {
                "id": "bore",
                "surface": "bore",
                "role": "straight",
                "z_start": {"datum": "face_a"},
                "z_end": {"datum": "face_a", "offset": {"ledger": "H"}},
                "diameter_start": {"ledger": "ID"},
            }
        ),
    ]


def test_compile_profile_walks_a_stepped_body_into_one_closed_boundary():
    ledger = _ledger()
    points, body_checks, overall_length_mm = compile_profile(_stepped_spans(), ledger, {})
    assert points == [
        (50.0, 0.0),
        (50.0, 10.0),
        (40.0, 10.0),
        (40.0, 20.0),
        (20.0, 20.0),
        (20.0, 0.0),
    ]
    # An identified endpoint difference, never "the largest printed linear dimension".
    assert overall_length_mm == 20.0
    ids = {c["id"] for c in body_checks}
    assert ids == {"flange", "hub", "bore"}  # the shoulder carries no span-band check itself


def test_compile_profile_and_build_model_agree_the_body_is_correct_before_any_feature(tmp_path):
    ledger = _ledger()
    points, body_checks, _ = compile_profile(_stepped_spans(), ledger, {})
    spec = DraftSpec.model_validate(
        {
            "reference_face": "face_A",
            "coordinate_policy": "Z increases from face A",
            "profile": _PLACEHOLDER_PROFILE,
            "features": [],
        }
    )
    result = build_model(
        spec, list(ledger.values()), tmp_path, body_checks=body_checks, points_override=points
    )
    span_checks = {c["subject"]: c for c in result["checks"] if c["subject"] in ids_of(body_checks)}
    assert all(c["status"] == "PASS" for c in span_checks.values()), span_checks


def ids_of(body_checks: list[dict]) -> set[str]:
    return {c["id"] for c in body_checks}


def test_compile_profile_rejects_a_gap_between_spans():
    ledger = _ledger()
    spans = _stepped_spans()
    # Detach the hub span from the shoulder: it now claims to start at HUB_OD but the shoulder
    # left off at HUB_OD too — introduce an actual gap by starting the hub 2mm late.
    broken = [s for s in spans if s.id != "hub"]
    broken.append(
        ProfileSpan.model_validate(
            {
                "id": "hub",
                "surface": "external",
                "role": "straight",
                "z_start": {"datum": "face_a", "offset": {"ledger": "HUB_Z_LATE"}},
                "z_end": {"datum": "face_a", "offset": {"ledger": "H"}},
                "diameter_start": {"ledger": "HUB_OD"},
            }
        )
    )
    with pytest.raises(ValueError, match="does not continue"):
        compile_profile(broken, ledger, {})


def test_shoulder_must_be_an_instantaneous_step():
    ledger = _ledger()
    spans = _stepped_spans()
    bad = [s for s in spans if s.id != "flange_to_hub"]
    bad.append(
        ProfileSpan.model_validate(
            {
                "id": "flange_to_hub",
                "surface": "external",
                "role": "shoulder",
                "z_start": {"datum": "face_a", "offset": {"ledger": "HUB_Z"}},
                "z_end": {"datum": "face_a", "offset": {"ledger": "HUB_Z_PLUS1"}},
                "diameter_start": {"ledger": "OD"},
                "diameter_end": {"ledger": "HUB_OD"},
            }
        )
    )
    with pytest.raises(ValueError, match="instantaneous step"):
        compile_profile(bad, ledger, {})


def test_a_straight_span_cannot_silently_change_diameter():
    ledger = _ledger()
    spans = [s for s in _stepped_spans() if s.id != "flange"]
    spans.append(
        ProfileSpan.model_validate(
            {
                "id": "flange",
                "surface": "external",
                "role": "straight",
                "z_start": {"datum": "face_a"},
                "z_end": {"datum": "face_a", "offset": {"ledger": "HUB_Z"}},
                "diameter_start": {"ledger": "OD"},
                "diameter_end": {"ledger": "HUB_OD"},
            }
        )
    )
    with pytest.raises(ValueError, match="lead_in"):
        compile_profile(spans, ledger, {})


def test_a_span_offset_from_a_registered_datum_is_not_the_bare_local_number():
    # The plan's own worked example: face_a = 0.000 in, datum A = 0.125 in, a span 0.980 in
    # from A resolves to an ABSOLUTE 1.105 in — never 0.980 in copied straight into z.
    ledger = {
        "LOCAL": _requirement("LOCAL", 0.980, unit="in"),
        "END": _requirement("END", 1.5, unit="in"),
        "OD": _requirement("OD", 2.0, unit="in"),
        "ID": _requirement("ID", 1.0, unit="in"),
    }
    datums = DatumRegistry(datums=(Datum(name="A", position_mm=Decimal("3.175"), source="s"),))
    spans = [
        ProfileSpan.model_validate(
            {
                "id": "shank",
                "surface": "external",
                "role": "straight",
                "z_start": {"datum": "A", "offset": {"ledger": "LOCAL"}},
                "z_end": {"datum": "face_a", "offset": {"ledger": "END"}},
                "diameter_start": {"ledger": "OD"},
            }
        ),
        ProfileSpan.model_validate(
            {
                "id": "bore",
                "surface": "bore",
                "role": "straight",
                "z_start": {"datum": "A", "offset": {"ledger": "LOCAL"}},
                "z_end": {"datum": "face_a", "offset": {"ledger": "END"}},
                "diameter_start": {"ledger": "ID"},
            }
        ),
    ]
    points, _, _ = compile_profile(spans, ledger, {}, datums=datums)
    assert points[0][1] == pytest.approx(1.105 * 25.4, abs=1e-6)


def test_build_model_verifies_the_body_before_any_feature_is_cut(tmp_path):
    # A plain OD100/ID40/H20 tube, but body_checks independently (and wrongly) claims OD120 —
    # simulating a mismatch an independent source contract would catch. Construction must stop
    # here; a feature must never get the chance to adapt to (and mask) a wrong host body.
    ledger = _ledger()
    points = [(50.0, 0.0), (50.0, 20.0), (20.0, 20.0), (20.0, 0.0)]
    wrong_body_checks = [{"kind": "axial_band", "id": "od_span", "z0": 0, "z1": 20, "od": 120}]
    spec = DraftSpec.model_validate(
        {
            "reference_face": "face_A",
            "coordinate_policy": "Z increases from face A",
            "profile": _PLACEHOLDER_PROFILE,
            "features": [],
        }
    )
    with pytest.raises(ValueError, match="host body fails independent span verification"):
        build_model(
            spec,
            list(ledger.values()),
            tmp_path,
            body_checks=wrong_body_checks,
            points_override=points,
        )
