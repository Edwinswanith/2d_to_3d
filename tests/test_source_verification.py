"""Independent source-contract verification: datum resolution and STEP measurement.

These are the smallest fixtures that demonstrate the acceptance test named in the plan: the
normal build/check path rejects an 80 mm bore against an accepted 40 mm requirement, without a
separate manual script, and a genuinely correct candidate still passes.
"""

from decimal import Decimal
from pathlib import Path

import pytest

from drawing2step.datums import Datum, DatumRegistry
from drawing2step.revb import Requirement
from drawing2step.revb_model import DraftSpec, build_model
from drawing2step.source_contract import SourceContract, SourceRequirement
from drawing2step.source_verification import _expectation, verify_contract


def test_unknown_datum_raises_instead_of_defaulting_to_zero():
    registry = DatumRegistry()
    with pytest.raises(ValueError, match="Unknown datum"):
        registry.resolve("-A-")


def test_face_a_is_the_coordinate_origin_by_definition_not_by_default():
    registry = DatumRegistry()
    assert registry.resolve("face_a") == 0
    registry = DatumRegistry(datums=(Datum(name="A", position_mm=Decimal("3.175"), source="s"),))
    assert registry.resolve("face_a") == 0
    assert registry.resolve("A") == Decimal("3.175")


def test_face_a_cannot_be_registered_away_from_zero():
    with pytest.raises(ValueError, match="coordinate origin"):
        DatumRegistry(datums=(Datum(name="face_a", position_mm=Decimal(5), source="s"),))


def _contract(**requirement_kwargs) -> SourceContract:
    base = {
        "id": "req",
        "feature": "Test feature",
        "quantity": "Test quantity",
        "kind": "axial_band",
        "referenced_to": "face_a",
        "unit": "mm",
        "evidence": "test fixture",
        "applies_between": (Decimal(0), Decimal(20)),
        "bore": Decimal(40),
    }
    base.update(requirement_kwargs)
    return SourceContract(
        drawing_number="TEST-1",
        datums=DatumRegistry(datums=(Datum(name="A", position_mm=Decimal("3.175"), source="s"),)),
        requirements=(SourceRequirement.model_validate(base),),
    )


def test_contract_fails_to_load_when_a_requirement_cites_an_unregistered_datum():
    with pytest.raises(ValueError, match="Unknown datum"):
        _contract(referenced_to="-B-")


def test_offset_from_a_registered_datum_is_not_the_bare_local_number():
    # The plan's own worked example: projecting front face (face_a) = 0.000 in, datum A =
    # 0.125 in, a feature 0.980 in from A sits at an ABSOLUTE 1.105 in — not at 0.980 in.
    contract = _contract(
        referenced_to="A",
        unit="in",
        applies_between=(Decimal("0.980"), Decimal("1.000")),
        bore=Decimal("0.312"),
    )
    expectation = _expectation(contract.requirements[0], contract)
    assert expectation["z0"] == pytest.approx(1.105 * 25.4, abs=1e-6)
    assert expectation["z1"] == pytest.approx(1.125 * 25.4, abs=1e-6)


def _ledger(od: int, bore: int, height: int) -> list[Requirement]:
    return [
        Requirement(
            id="OD",
            raw_text=f"Ø{od}",
            kind="diameter",
            value=Decimal(od),
            unit="mm",
            sources=("t:OD",),
            text_status="AGREED",
        ),
        Requirement(
            id="ID",
            raw_text=f"Ø{bore}",
            kind="diameter",
            value=Decimal(bore),
            unit="mm",
            sources=("t:ID",),
            text_status="AGREED",
        ),
        Requirement(
            id="H",
            raw_text=str(height),
            kind="linear",
            value=Decimal(height),
            unit="mm",
            sources=("t:H",),
            text_status="AGREED",
        ),
    ]


def _cylinder_spec() -> DraftSpec:
    return DraftSpec.model_validate(
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


def test_verify_contract_rejects_a_bore_built_wrong_without_a_manual_script(tmp_path: Path):
    result = build_model(_cylinder_spec(), _ledger(od=100, bore=80, height=20), tmp_path)
    contract = _contract()  # requires bore 40 mm between z 0..20
    [check] = verify_contract(tmp_path / result["step"], contract)
    assert check["status"] == "FAIL"
    assert "40" in check["detail"] and "80" in check["detail"]
    assert check["layer"] == "SRC" and check["subject"] == "req"


def test_verify_contract_passes_a_correctly_built_bore(tmp_path: Path):
    result = build_model(_cylinder_spec(), _ledger(od=100, bore=40, height=20), tmp_path)
    contract = _contract()
    [check] = verify_contract(tmp_path / result["step"], contract)
    assert check["status"] == "PASS"


def test_requirement_outside_declared_scope_is_unknown_and_never_measured(tmp_path: Path):
    contract = _contract()
    [check] = verify_contract(Path("does/not/exist.step"), contract, scope=frozenset())
    assert check["status"] == "UNKNOWN"
    assert "declared modelling scope" in check["detail"]
