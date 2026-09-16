import io
import json
import time

from fastapi.testclient import TestClient
from PIL import Image

from drawing2step.revb_pipeline import PipelineConfig
from drawing2step.web_api import create_app


def test_default_upload_uses_revision_b_and_does_not_release(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "private-test-key")
    monkeypatch.setattr(
        "drawing2step.revb_pipeline.PipelineConfig.from_environment",
        lambda: PipelineConfig(ocr="disabled", detailed_inventory=False),
    )
    calls = []

    def reader(image, key, model, *, prompt, schema):
        properties = schema["properties"]
        if "envelope_value" in properties:
            role = "context"
            value = {
                "drawing_number": "TEST",
                "revision": "A",
                "units_statement": "DIMENSIONS IN MM",
                "projection": "unknown",
                "default_tolerances": "",
                "envelope_value": 80,
                "envelope_unit": "mm",
                "uncertainties": [],
            }
        elif "features" in properties:
            role = "inventory"
            value = {
                "schema_version": "inventory-revb-v1",
                "features": [
                    {
                        "id": "holes",
                        "type": "hole_pattern",
                        "view": "plan",
                        "count": 8,
                        "box": [10, 10, 900, 900],
                        "description": "Eight visible holes",
                    }
                ],
            }
        else:
            role = "text"
            value = {
                "drawing_number": "TEST",
                "revision": "A",
                "part_family": "gland ring",
                "title_block_units": "DIMENSIONS IN MM",
                "notes": ["<script>unsafe</script>"],
                "uncertainties": [],
                "callouts": [],
            }
        calls.append(role)
        return {
            "candidates": [
                {"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(value)}]}}
            ]
        }

    monkeypatch.setattr("drawing2step.revb_pipeline.call_gemini", reader)
    buffer = io.BytesIO()
    Image.new("RGB", (160, 120), "white").save(buffer, format="PNG")
    with TestClient(create_app(tmp_path)) as client:
        response = client.post("/api/drawings", files={"file": ("ring.png", buffer.getvalue())})
        assert response.status_code == 202
        job = response.json()["id"]
        for _ in range(100):
            state = client.get(f"/api/drawings/{job}").json()
            if state["status"] in {"review", "failed"}:
                break
            time.sleep(0.02)
        assert state["status"] == "review", state
        assert calls == ["context", "inventory", "text"]
        assert state["revision_b"] and state["release"] == "BLOCKED"
        assert not state["download_available"]
        assert client.get(f"/api/drawings/{job}/files/step").status_code == 409
        report = client.get(f"/api/drawings/{job}/files/audit")
        assert report.status_code == 200
        assert "<script>unsafe" not in report.text
        assert "&lt;script&gt;unsafe" in report.text
        assert client.get(f"/api/drawings/{job}/files/drawing.png").status_code == 200
        assert (
            client.get(f"/api/drawings/{job}/files/ledger").json()[0]["raw_text"]
            == "<script>unsafe</script>"
        )
        assert client.get(f"/api/drawings/{job}/files/report").json()["release"] == "BLOCKED"
    assert all("private-test-key" not in p.read_text() for p in tmp_path.rglob("*.json"))
