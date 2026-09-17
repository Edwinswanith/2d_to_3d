"""PR4's own regression battery: deliberately construct the wrong part, and confirm the
independent source-contract verifier — not the candidate's own self-checks — names the failure.

Each candidate below is INTERNALLY consistent (its own build succeeds; nothing it checks about
itself objects) and is measured only through ``verify_contract`` against a small, hand-authored
``SourceContract`` for the same synthetic drawing, exactly like the plan's acceptance tests. A
correct candidate is included too, so the point is that the verifier discriminates, not that it
blocks everything.
"""

from decimal import Decimal
from pathlib import Path

from drawing2step.revb import Requirement
from drawing2step.revb_model import DraftSpec, build_model
from drawing2step.source_contract import SourceContract, SourceRequirement
from drawing2step.source_verification import verify_contract


def _requirement(identifier: str, value: float, kind: str = "diameter") -> Requirement:
    return Requirement(
        id=identifier,
        raw_text=f"Ø{value}" if kind == "diameter" else str(value),
        kind=kind,
        value=Decimal(str(value)),
        unit="count" if kind == "count" else "mm",
        sources=(f"synthetic:{identifier}",),
        text_status="AGREED",
    )


def _contract(*requirements: dict) -> SourceContract:
    base = {
        "referenced_to": "face_a",
        "unit": "mm",
        "evidence": "reviewed by hand against the sheet",
        "interpretation": "reviewed",
    }
    return SourceContract(
        drawing_number="synthetic",
        requirements=tuple(SourceRequirement.model_validate({**base, **r}) for r in requirements),
    )


def _run(spec: DraftSpec, rows: list[Requirement], tmp_path: Path) -> dict:
    return build_model(spec, rows, tmp_path)


def test_diameter_used_as_radius_is_a_dimensional_failure(tmp_path):
    # Ø40 is the printed bore; using 40 as a RADIUS instead of a diameter builds an 80 mm bore.
    rows = [
        _requirement("OD", 100),
        _requirement("ID", 80),  # the mistake: should have been 40
        _requirement("H", 20, "linear"),
    ]
    spec = DraftSpec.model_validate(
        {
            "reference_face": "face_A",
            "coordinate_policy": "Z increases from face A",
            "profile": [
                {"z": {"datum": "face_a"}, "radius": {"expr": "OD/2"}},
                {"z": {"ledger": "H"}, "radius": {"expr": "OD/2"}},
                {"z": {"ledger": "H"}, "radius": {"expr": "ID/2"}},
                {"z": {"datum": "face_a"}, "radius": {"expr": "ID/2"}},
            ],
            "features": [],
        }
    )
    result = _run(spec, rows, tmp_path)
    contract = _contract(
        {
            "id": "bore",
            "feature": "Main bore",
            "quantity": "Internal diameter",
            "kind": "axial_band",
            "applies_between": (0, 20),
            "bore": 40,
        }
    )
    [check] = verify_contract(tmp_path / result["step"], contract)
    assert check["status"] == "FAIL"
    assert "40" in check["detail"] and "80" in check["detail"]


def test_internal_groove_modeled_on_the_external_surface_is_a_surface_role_failure(tmp_path):
    # Accepted: an internal groove — the BORE widens to 60 mm between z=10..20. The candidate
    # instead cuts that widening into the OUTSIDE surface, leaving the bore untouched.
    rows = [
        _requirement("OD", 100),
        _requirement("OD_GROOVE", 60),  # wrongly placed on the external surface
        _requirement("ID", 40),
        _requirement("H", 30, "linear"),
        _requirement("Z0", 10, "linear"),
        _requirement("Z1", 20, "linear"),
    ]
    spec = DraftSpec.model_validate(
        {
            "reference_face": "face_A",
            "coordinate_policy": "Z increases from face A",
            "profile": [
                {"z": {"datum": "face_a"}, "radius": {"expr": "OD/2"}},
                {"z": {"ledger": "Z0"}, "radius": {"expr": "OD/2"}},
                {"z": {"ledger": "Z0"}, "radius": {"expr": "OD_GROOVE/2"}},
                {"z": {"ledger": "Z1"}, "radius": {"expr": "OD_GROOVE/2"}},
                {"z": {"ledger": "Z1"}, "radius": {"expr": "OD/2"}},
                {"z": {"ledger": "H"}, "radius": {"expr": "OD/2"}},
                {"z": {"ledger": "H"}, "radius": {"expr": "ID/2"}},
                {"z": {"datum": "face_a"}, "radius": {"expr": "ID/2"}},
            ],
            "features": [],
        }
    )
    result = _run(spec, rows, tmp_path)
    contract = _contract(
        {
            "id": "internal_groove",
            "feature": "Internal annular groove",
            "quantity": "Bore diameter at the groove",
            "kind": "axial_band",
            "applies_between": (10, 20),
            "bore": 60,
        }
    )
    [check] = verify_contract(tmp_path / result["step"], contract)
    assert check["status"] == "FAIL"
    assert "60" in check["detail"]


def test_correct_max_diameter_but_shortened_body_is_a_full_span_profile_failure(tmp_path):
    # Accepted: Ø100 for the full 30 mm length. The candidate gets the diameter right but the
    # body 12 mm short — a plausible "the axial chain dropped a span" bug, not a diameter error.
    rows = [
        _requirement("OD", 100),
        _requirement("ID", 40),
        _requirement("H_SHORT", 18, "linear"),
    ]
    spec = DraftSpec.model_validate(
        {
            "reference_face": "face_A",
            "coordinate_policy": "Z increases from face A",
            "profile": [
                {"z": {"datum": "face_a"}, "radius": {"expr": "OD/2"}},
                {"z": {"ledger": "H_SHORT"}, "radius": {"expr": "OD/2"}},
                {"z": {"ledger": "H_SHORT"}, "radius": {"expr": "ID/2"}},
                {"z": {"datum": "face_a"}, "radius": {"expr": "ID/2"}},
            ],
            "features": [],
        }
    )
    result = _run(spec, rows, tmp_path)
    contract = _contract(
        {
            "id": "full_length",
            "feature": "Main OD",
            "quantity": "Outer diameter",
            "kind": "axial_band",
            "applies_between": (0, 30),
            "diameter": 100,
        }
    )
    [check] = verify_contract(tmp_path / result["step"], contract)
    assert check["status"] == "FAIL"
    assert "beyond the model" in check["detail"]


def test_external_boss_replaced_by_internal_counterbore_is_a_feature_association_failure(
    tmp_path,
):
    # Accepted: an external locating boss — OD steps UP to 120 mm between z=5..10. The
    # candidate instead enlarges the bore there (an internal counterbore) and leaves the OD flat.
    rows = [
        _requirement("OD", 100),
        _requirement("ID", 40),
        _requirement("ID_COUNTERBORE", 70),  # wrongly placed internally instead
        _requirement("H", 20, "linear"),
        _requirement("Z0", 5, "linear"),
        _requirement("Z1", 10, "linear"),
    ]
    spec = DraftSpec.model_validate(
        {
            "reference_face": "face_A",
            "coordinate_policy": "Z increases from face A",
            "profile": [
                {"z": {"datum": "face_a"}, "radius": {"expr": "OD/2"}},
                {"z": {"ledger": "H"}, "radius": {"expr": "OD/2"}},
                {"z": {"ledger": "H"}, "radius": {"expr": "ID/2"}},
                {"z": {"ledger": "Z1"}, "radius": {"expr": "ID/2"}},
                {"z": {"ledger": "Z1"}, "radius": {"expr": "ID_COUNTERBORE/2"}},
                {"z": {"ledger": "Z0"}, "radius": {"expr": "ID_COUNTERBORE/2"}},
                {"z": {"ledger": "Z0"}, "radius": {"expr": "ID/2"}},
                {"z": {"datum": "face_a"}, "radius": {"expr": "ID/2"}},
            ],
            "features": [],
        }
    )
    result = _run(spec, rows, tmp_path)
    contract = _contract(
        {
            "id": "boss",
            "feature": "External locating boss",
            "quantity": "Outer diameter at the boss",
            "kind": "axial_band",
            "applies_between": (5, 10),
            "diameter": 120,
        }
    )
    [check] = verify_contract(tmp_path / result["step"], contract)
    assert check["status"] == "FAIL"
    assert "120" in check["detail"]


def test_hole_drilled_from_the_wrong_face_is_an_entry_face_failure(tmp_path):
    # Accepted: a hole entering from face A (z 0..8). The candidate drills the same nominal
    # depth from face B instead — same diameter, same depth, wrong end of the part.
    rows = [
        _requirement("OD", 100),
        _requirement("ID", 40),
        _requirement("H", 20, "linear"),
        _requirement("HD", 6),
        _requirement("PCD", 40),
        _requirement("N", 1, "count"),
        _requirement("DEPTH", 8, "linear"),
    ]
    spec = DraftSpec.model_validate(
        {
            "reference_face": "face_A",
            "coordinate_policy": "Z increases from face A",
            "profile": [
                {"z": {"datum": "face_a"}, "radius": {"expr": "OD/2"}},
                {"z": {"ledger": "H"}, "radius": {"expr": "OD/2"}},
                {"z": {"ledger": "H"}, "radius": {"expr": "ID/2"}},
                {"z": {"datum": "face_a"}, "radius": {"expr": "ID/2"}},
            ],
            "features": [
                {
                    "id": "hole",
                    "kind": "hole_pattern",
                    "citations": ["HD", "PCD", "N", "H", "DEPTH"],
                    "diameter": {"ledger": "HD"},
                    "depth": {"ledger": "DEPTH"},
                    "z": {"ledger": "H"},
                    "pcd": {"ledger": "PCD"},
                    "count": {"ledger": "N"},
                    "angle": {"centreline": 0, "reason": "test"},
                    "host": "face_b",
                    "reference_face": "face_A",
                }
            ],
        }
    )
    result = _run(spec, rows, tmp_path)
    receipt = result["features"][0]
    assert receipt["status"] == "BUILT", receipt.get("detail")  # a genuinely built, wrong-face hole
    contract = _contract(
        {
            "id": "hole",
            "feature": "Locating hole",
            "quantity": "Axial through-hole from face A",
            "kind": "hole_pattern",
            "diameter": 6,
            "count": 1,
            "pcd": 40,
            "angle": 0,
            "z_span": (0, 8),
        }
    )
    [check] = verify_contract(tmp_path / result["step"], contract)
    assert check["status"] == "FAIL"


def test_a_correctly_built_part_passes_every_requirement(tmp_path):
    # The verifier must discriminate, not block everything: a genuinely correct candidate
    # against all four requirement shapes above should pass all of them.
    rows = [
        _requirement("OD", 100),
        _requirement("ID", 40),
        _requirement("H", 20, "linear"),
        _requirement("HD", 6),
        _requirement("PCD", 40),
        _requirement("N", 1, "count"),
        _requirement("DEPTH", 8, "linear"),
    ]
    spec = DraftSpec.model_validate(
        {
            "reference_face": "face_A",
            "coordinate_policy": "Z increases from face A",
            "profile": [
                {"z": {"datum": "face_a"}, "radius": {"expr": "OD/2"}},
                {"z": {"ledger": "H"}, "radius": {"expr": "OD/2"}},
                {"z": {"ledger": "H"}, "radius": {"expr": "ID/2"}},
                {"z": {"datum": "face_a"}, "radius": {"expr": "ID/2"}},
            ],
            "features": [
                {
                    "id": "hole",
                    "kind": "hole_pattern",
                    "citations": ["HD", "PCD", "N", "H", "DEPTH"],
                    "diameter": {"ledger": "HD"},
                    "depth": {"ledger": "DEPTH"},
                    "z": {"datum": "face_a"},
                    "pcd": {"ledger": "PCD"},
                    "count": {"ledger": "N"},
                    "angle": {"centreline": 0, "reason": "test"},
                    "host": "face_a",
                    "reference_face": "face_A",
                }
            ],
        }
    )
    result = _run(spec, rows, tmp_path)
    contract = _contract(
        {
            "id": "bore",
            "feature": "Main bore",
            "quantity": "Internal diameter",
            "kind": "axial_band",
            "applies_between": (0, 20),
            "bore": 40,
        },
        {
            "id": "od",
            "feature": "Main OD",
            "quantity": "Outer diameter",
            "kind": "axial_band",
            "applies_between": (0, 20),
            "diameter": 100,
        },
        {
            "id": "hole",
            "feature": "Locating hole",
            "quantity": "Axial through-hole from face A",
            "kind": "hole_pattern",
            "diameter": 6,
            "count": 1,
            "pcd": 40,
            "angle": 0,
            "z_span": (0, 8),
        },
    )
    checks = verify_contract(tmp_path / result["step"], contract)
    assert all(c["status"] == "PASS" for c in checks), checks
