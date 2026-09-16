import io
import json

from PIL import Image

from drawing2step.revb_pipeline import PipelineConfig, run_audit


def png():
    out = io.BytesIO()
    Image.new("RGB", (160, 120), "white").save(out, format="PNG")
    return out.getvalue()


def responses():
    return {
        "context": {
            "drawing_number": "TEST",
            "revision": "A",
            "units_statement": "ALL DIMENSIONS IN MM",
            "projection": "unknown",
            "default_tolerances": "",
            "envelope_value": 80,
            "envelope_unit": "mm",
            "uncertainties": [],
        },
        "text": {
            "drawing_number": "TEST",
            "revision": "A",
            "part_family": "gland ring",
            "title_block_units": "ALL DIMENSIONS IN MM",
            "notes": [],
            "uncertainties": [],
            "callouts": [
                {
                    "id": "D",
                    "raw_text": "Ø80",
                    "kind": "diameter",
                    "value_printed": "80",
                    "unit_printed": "",
                    "tolerance_printed": "",
                    "feature_proposal": "outside diameter",
                    "box": [10, 10, 50, 100],
                }
            ],
        },
        "inventory": {
            "features": [
                {
                    "id": "body",
                    "type": "body",
                    "view": "section",
                    "count": 1,
                    "box": [100, 100, 800, 800],
                    "description": "turned body",
                }
            ],
            "sections": [],
            "uncertainties": [],
        },
    }


def test_independent_calls_and_saved_evidence(tmp_path):
    calls = []

    def provider(image, role, prompt, schema):
        calls.append((role, prompt))
        return {
            "modelVersion": "recorded-fixture",
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {"parts": [{"text": json.dumps(responses()[role])}]},
                }
            ],
        }

    run_audit(
        png(),
        "test.png",
        tmp_path,
        provider=provider,
        config=PipelineConfig(ocr="disabled", detailed_inventory=False),
    )
    assert [r for r, p in calls] == ["context", "inventory", "text"]
    assert "Ø80" not in next(p for r, p in calls if r == "inventory")
    assert (tmp_path / "evidence/inventory/attempt-1.json").exists()
    result = json.loads((tmp_path / "audit.json").read_text())
    assert result["release"] == "BLOCKED"
    assert result["inventory"]["features"][0]["id"] == "body"
    assert result["requirements"][0]["text_status"] == "A_ONLY"
    assert result["accuracy_gate"] == "NOT_EVALUATED"


def test_replay_does_not_call_provider(tmp_path):
    def provider(image, role, prompt, schema):
        return {
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {"parts": [{"text": json.dumps(responses()[role])}]},
                }
            ]
        }

    config = PipelineConfig(ocr="disabled", detailed_inventory=False)
    run_audit(png(), "test.png", tmp_path, provider=provider, config=config)

    def forbidden(*args):
        raise AssertionError("Replay made a fresh model call")

    run_audit(png(), "test.png", tmp_path, provider=forbidden, config=config, replay=True)


def test_unit_block_stops_before_text_reading(tmp_path):
    roles = []

    def provider(image, role, prompt, schema):
        roles.append(role)
        context = responses()["context"]
        context["units_statement"] = ""
        return {
            "candidates": [
                {"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(context)}]}}
            ]
        }

    run_audit(
        png(),
        "test.png",
        tmp_path,
        provider=provider,
        config=PipelineConfig(ocr="disabled", detailed_inventory=False),
    )
    result = json.loads((tmp_path / "audit.json").read_text())
    assert result["context"]["status"] == "FAIL"
    assert roles == ["context"]
    assert result["release"] == "BLOCKED"


def test_unavailable_inventory_preserves_text_and_returns_unknown(tmp_path):
    def provider(image, role, prompt, schema):
        if role == "inventory":
            raise ValueError("provider unavailable")
        return {
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {"parts": [{"text": json.dumps(responses()[role])}]},
                }
            ]
        }

    run_audit(
        png(),
        "test.png",
        tmp_path,
        provider=provider,
        config=PipelineConfig(ocr="disabled", detailed_inventory=False),
    )
    result = json.loads((tmp_path / "audit.json").read_text())
    assert result["requirements"]
    assert any(x["code"] == "INVENTORY_UNAVAILABLE" for x in result["findings"])
    assert result["release"] == "BLOCKED"
