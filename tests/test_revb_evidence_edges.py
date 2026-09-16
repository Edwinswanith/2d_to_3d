"""Evidence preservation and replay integrity without network or customer data."""

import io
import json
from decimal import Decimal

import pytest
from PIL import Image

from drawing2step.pdf_diagnostic import PdfReading
from drawing2step.revb import resolve_context
from drawing2step.revb_pipeline import PipelineConfig, _ledger, run_audit


def reading(**callout_changes):
    callout = {
        "id": "C1",
        "raw_text": "Ø80 +0.10/-0.05",
        "kind": "diameter",
        "value_printed": "80",
        "unit_printed": "mm",
        "tolerance_printed": "+0.10/-0.05",
        "feature_proposal": "Outside diameter",
        "box": [100, 100, 200, 300],
    }
    callout.update(callout_changes)
    return PdfReading.model_validate(
        {
            "drawing_number": "SYNTHETIC-RING",
            "revision": "B",
            "part_family": "gland ring",
            "title_block_units": "ALL DIMENSIONS IN MM",
            "notes": [],
            "uncertainties": [],
            "callouts": [callout],
        }
    )


def test_ledger_preserves_printed_tolerance_without_claiming_interpretation():
    requirement = _ledger(reading(), [], "mm")[0]
    assert requirement.value == Decimal("80")
    assert requirement.tolerance_printed == "+0.10/-0.05"
    assert requirement.raw_text == "Ø80 +0.10/-0.05"
    assert requirement.interpretation_status == "UNKNOWN"
    assert requirement.tolerance_plus is None
    assert requirement.tolerance_minus is None


def test_general_notes_remain_requirements_with_source_citations():
    source = reading().model_copy(
        update={"notes": ["BREAK ALL SHARP EDGES", "PUNCH ‘QUENCH IN’", " "]}
    )
    requirements = _ledger(source, [], "mm")
    notes = [item for item in requirements if item.kind == "note"]
    assert [item.raw_text for item in notes] == ["BREAK ALL SHARP EDGES", "PUNCH ‘QUENCH IN’"]
    assert all(item.sources for item in notes)
    assert all(item.value is None for item in notes)
    assert len({item.id for item in requirements}) == len(requirements)


@pytest.mark.parametrize(
    "raw,printed,expected",
    [("1e3", "1", None), ("1e3", "1e3", Decimal("1000")), ("Ø18", "8", None)],
)
def test_ledger_accepts_only_complete_numeric_tokens(raw, printed, expected):
    source = reading(raw_text=raw, value_printed=printed, tolerance_printed="")
    requirement = _ledger(source, [], "mm")[0]
    assert requirement.value == expected
    assert requirement.raw_text == raw


@pytest.mark.parametrize(
    "raw,printed_unit", [("Ø2", "furlong"), ("Ø2 ft", ""), ("Ø2 inches", "mm")]
)
def test_unsupported_or_conflicting_callout_units_cannot_inherit_mm(raw, printed_unit):
    source = reading(
        raw_text=raw, value_printed="2", unit_printed=printed_unit, tolerance_printed=""
    )
    requirement = _ledger(source, [], "mm")[0]
    assert requirement.unit is None
    assert requirement.text_status == "CONFLICT"


def test_unread_envelope_blocks_even_when_identity_and_units_are_known():
    context = resolve_context("ALL DIMENSIONS IN MM", "SYNTHETIC-RING", None)
    assert context["status"] == "FAIL"
    assert "envelope" in context["detail"].lower()


def source_image():
    output = io.BytesIO()
    Image.new("RGB", (200, 100), "white").save(output, format="PNG")
    return output.getvalue()


def proposal_responses():
    return {
        "context": {
            "drawing_number": "SYNTHETIC-RING",
            "revision": "B",
            "units_statement": "ALL DIMENSIONS IN MM",
            "projection": "",
            "default_tolerances": "",
            "envelope_value": 80,
            "envelope_unit": "mm",
            "uncertainties": [],
        },
        "inventory": {
            "schema_version": "inventory-revb-v1",
            "features": [
                {
                    "id": "body",
                    "type": "body",
                    "view": "section",
                    "count": 1,
                    "box": [100, 100, 900, 900],
                    "description": "Independent visual shape observation",
                }
            ],
            "sections": [],
            "uncertainties": [],
        },
        "text": reading().model_dump(mode="json"),
    }


def envelope(value):
    return {
        "candidates": [
            {"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(value)}]}}
        ]
    }


@pytest.mark.parametrize(
    "artifact", ["audit.json", "evidence/inventory/attempt-1.json", "drawing.png"]
)
def test_replay_rejects_modified_saved_artifacts(tmp_path, artifact):
    responses = proposal_responses()

    def provider(image, role, prompt, schema):
        return envelope(responses[role])

    config = PipelineConfig(ocr="disabled", detailed_inventory=False)
    data = source_image()
    run_audit(data, "synthetic.png", tmp_path, provider=provider, config=config)
    target = tmp_path / artifact
    target.write_bytes(target.read_bytes() + b"MODIFIED")

    def forbidden(*args):
        raise AssertionError("Replay attempted a provider call")

    with pytest.raises(ValueError, match="integrity"):
        run_audit(data, "synthetic.png", tmp_path, provider=forbidden, config=config, replay=True)


def test_detailed_inventory_maps_crop_boxes_without_sharing_reader_output(tmp_path):
    responses = proposal_responses()
    responses["inventory"]["features"][0]["description"] = "INVENTORY_SENTINEL_Q17"
    responses["text"]["notes"] = ["TEXT_SENTINEL_X42"]
    calls = []
    inventory_count = 0

    def provider(image, role, prompt, schema):
        nonlocal inventory_count
        with Image.open(io.BytesIO(image)) as supplied:
            calls.append({"role": role, "prompt": prompt, "size": supplied.size})
        if role == "layout":
            return envelope(
                {"views": [{"id": "sectionA", "kind": "section", "box": [100, 200, 900, 800]}]}
            )
        if role == "inventory":
            inventory_count += 1
            if inventory_count == 2:
                return envelope(
                    {
                        "schema_version": "inventory-revb-v1",
                        "features": [
                            {
                                "id": "slot",
                                "type": "bore_slot",
                                "view": "crop",
                                "count": 1,
                                "box": [100, 250, 600, 750],
                                "description": "Slot visible in detailed section",
                            }
                        ],
                        "sections": [],
                        "uncertainties": [],
                    }
                )
        return envelope(responses[role])

    result = run_audit(
        source_image(),
        "synthetic.png",
        tmp_path,
        provider=provider,
        config=PipelineConfig(ocr="disabled", detailed_inventory=True),
    )
    inventory_calls = [call for call in calls if call["role"] == "inventory"]
    assert len(inventory_calls) == 2
    assert inventory_calls[1]["size"] == (120, 80)
    assert all("TEXT_SENTINEL_X42" not in call["prompt"] for call in inventory_calls)
    assert "INVENTORY_SENTINEL_Q17" not in next(
        call["prompt"] for call in calls if call["role"] == "text"
    )
    feature = next(item for item in result["inventory"]["features"] if item["type"] == "bore_slot")
    assert feature["box"] == pytest.approx([180, 350, 580, 650])
    transform = json.loads((tmp_path / "evidence/inventory-view-1/transform.json").read_text())
    assert transform["crop_bounds_pixels"] == [40, 10, 160, 90]
    assert (tmp_path / "evidence/inventory-view-1/crop.png").is_file()
