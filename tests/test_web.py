import io
import time

import pymupdf
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from drawing2step.web_api import create_app
from drawing2step.web_pipeline import DrawingDraft, detect_unit, prepare_spec, render_input


def draft(statement=""):
    return DrawingDraft.model_validate(
        {
            "drawing_number": "TEST01",
            "part_name": "Synthetic ring",
            "units_statement": statement,
            "callouts": [
                {
                    "id": key,
                    "raw_text": value,
                    "value_printed": value,
                    "kind": kind,
                    "unit_printed": "",
                }
                for key, value, kind in [
                    ("OD", "12", "diameter"),
                    ("ID", "8", "diameter"),
                    ("L", "1.6", "linear"),
                ]
            ],
            "stations": [
                {"z": {"expr": "L-L"}, "od": {"ledger": "OD"}, "id": {"ledger": "ID"}},
                {"z": {"ledger": "L"}, "od": {"ledger": "OD"}, "id": {"ledger": "ID"}},
            ],
            "unsupported_features": [],
            "uncertainties": [],
        }
    )


@pytest.mark.parametrize(
    "statement,unit",
    [
        ("", "mm"),
        ("DIMENSIONS IN MM", "mm"),
        ("DIMENSIONS IN CENTIMETRES", "cm"),
        ("DIMENSIONS IN INCHES", "in"),
    ],
)
def test_default_mm_and_explicit_units(statement, unit):
    spec, info = prepare_spec(draft(statement), None)
    assert info["unit"] == unit
    assert spec is not None
    assert spec.ledger["OD"].unit == unit


def test_override_and_per_callout_units_preserved():
    raw = draft("INCHES").model_dump()
    raw["callouts"][0].update(raw_text="12 cm", unit_printed="cm")
    spec, info = prepare_spec(DrawingDraft.model_validate(raw), "mm")
    assert spec.ledger["OD"].unit == "cm"
    assert spec.ledger["ID"].unit == "mm"
    assert info["unit_source"] == "user override"
    with pytest.raises(ValueError, match="conflicting"):
        detect_unit("cm and inches")


def test_model_computed_numbers_never_enter_geometry():
    raw = draft().model_dump()
    raw["callouts"][0]["value_printed"] = "15"
    spec, info = prepare_spec(DrawingDraft.model_validate(raw), None)
    assert "OD" not in spec.ledger
    assert info["warnings"]


def png():
    out = io.BytesIO()
    Image.new("RGB", (200, 100), "white").save(out, format="PNG")
    return out.getvalue()


def test_upload_preview_and_download_with_recorded_reader(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "private-fixture-key")

    def reader(*args, **kwargs):
        return {
            "modelVersion": "fixture-v1",
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {"parts": [{"text": draft("cm").model_dump_json()}]},
                }
            ],
        }

    monkeypatch.setattr("drawing2step.web_pipeline.call_gemini", reader)
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/api/health").status_code == 200
        response = client.post("/api/drawings", files={"file": ("drawing.png", png(), "image/png")})
        assert response.status_code == 202
        job = response.json()["id"]
        for _ in range(100):
            result = client.get(f"/api/drawings/{job}").json()
            if result["status"] in {"ready", "review", "failed"}:
                break
            time.sleep(0.05)
        assert result["status"] == "ready", result
        assert result["unit"] == "cm"
        assert result["measurements"]["bbox"] == pytest.approx([120, 120, 16])
        assert result["verification"]["release"] == "BLOCKED"
        mesh = client.get(f"/api/drawings/{job}/files/mesh").json()
        assert mesh["positions"] and mesh["indices"]
        assert client.get(f"/api/drawings/{job}/files/step").content.startswith(b"ISO-10303")
        assert client.get(f"/api/drawings/{job}/files/stl").status_code == 200
        assert client.get(f"/api/drawings/{job}/files/report").status_code == 200
        assert client.get(f"/api/drawings/{job}/files/secret").status_code == 404
    for p in tmp_path.rglob("*.json"):
        assert "private-fixture-key" not in p.read_text()


def test_invalid_input_and_provider_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "private-fixture-key")
    monkeypatch.setattr("drawing2step.web_pipeline.call_gemini", lambda *a, **k: {})
    with TestClient(create_app(tmp_path)) as client:
        assert client.post("/api/drawings", files={"file": ("bad.pdf", b"bad")}).status_code == 422
        assert (
            client.post(
                "/api/drawings", files={"file": ("x.png", png())}, data={"rotation": 45}
            ).status_code
            == 422
        )
        assert client.get("/api/drawings/not-a-job").status_code == 404
        response = client.post("/api/drawings", files={"file": ("x.png", png())})
        job = response.json()["id"]
        for _ in range(50):
            result = client.get(f"/api/drawings/{job}").json()
            if result["status"] == "failed":
                break
            time.sleep(0.02)
        assert result["status"] == "failed"
        assert client.get(f"/api/drawings/{job}/files/step").status_code == 409


def test_multi_page_pdf_and_rotation_are_handled_explicitly():
    with pymupdf.open() as doc:
        doc.new_page(width=100, height=200)
        single = doc.tobytes()
        doc.new_page()
        multiple = doc.tobytes()
    with pytest.raises(ValueError, match="one unencrypted"):
        render_input(multiple, "x.pdf", 0)
    image = Image.open(io.BytesIO(render_input(single, "x.pdf", 90)))
    assert image.width > image.height


@pytest.mark.parametrize("printed,accepted", [(".500", True), ("500", False)])
def test_leading_decimal_is_a_complete_source_token(printed, accepted):
    raw = draft().model_dump()
    raw["callouts"][0].update(raw_text=".500", value_printed=printed)
    spec, _ = prepare_spec(DrawingDraft.model_validate(raw), None)
    assert ("OD" in spec.ledger) == accepted


def test_explicit_unit_markers_never_silently_fall_back():
    raw = draft().model_dump()
    raw["callouts"][0].update(raw_text='12"', unit_printed='"')
    spec, _ = prepare_spec(DrawingDraft.model_validate(raw), None)
    assert spec.ledger["OD"].unit == "in"
    for marker in ["ft", "metres", "bogus"]:
        raw["callouts"][0].update(raw_text="12 " + marker, unit_printed=marker)
        with pytest.raises(ValueError):
            prepare_spec(DrawingDraft.model_validate(raw), None)
    assert detect_unit("14MM") == "mm"
    assert detect_unit("shown in section") is None


@pytest.mark.parametrize("outer", [True, False])
def test_shoulder_has_no_duplicate_contour_points(tmp_path, outer):
    from drawing2step.body_cad import BodySpec, build_verified

    raw = draft().model_dump()
    spec, _ = prepare_spec(DrawingDraft.model_validate(raw), None)
    body = spec.model_dump(mode="json")
    body["synthetic"] = True
    body["ledger"]["OD2"] = {"value": "10", "kind": "diameter", "unit": "mm"}
    body["ledger"]["ID2"] = {"value": "6", "kind": "diameter", "unit": "mm"}
    body["stations"] = [
        {"z": {"expr": "L-L"}, "od": {"ledger": "OD"}, "id": {"ledger": "ID"}},
        {"z": {"expr": "L/2"}, "od": {"ledger": "OD"}, "id": {"ledger": "ID"}},
        {
            "z": {"expr": "L/2"},
            "od": {"ledger": "OD2" if outer else "OD"},
            "id": {"ledger": "ID" if outer else "ID2"},
        },
        {
            "z": {"ledger": "L"},
            "od": {"ledger": "OD2" if outer else "OD"},
            "id": {"ledger": "ID" if outer else "ID2"},
        },
    ]
    result = build_verified(BodySpec.model_validate(body), tmp_path / "body")
    assert result["V2"] == "PASS"


def test_external_origin_cannot_trigger_cloud_upload(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        response = client.post(
            "/api/drawings",
            files={"file": ("x.png", png())},
            headers={"Origin": "https://evil.example"},
        )
        assert response.status_code == 403
        assert not list(tmp_path.glob("*/original"))


def test_wire_schema_expands_refs_but_runtime_bounds_stay_enforced():
    import json

    from drawing2step.web_pipeline import reader_schema

    assert "$ref" not in json.dumps(reader_schema())
    assert "$defs" not in json.dumps(reader_schema())
    raw = draft().model_dump()
    raw["callouts"][0]["id"] = "bad-id!"
    with pytest.raises(ValueError):
        DrawingDraft.model_validate(raw)


def test_model_zero_origin_uses_cited_datum_and_length_alias():
    raw = draft().model_dump()
    raw["stations"][0]["z"] = {"expr": "0"}
    raw["callouts"][2]["kind"] = "length"
    spec, info = prepare_spec(DrawingDraft.model_validate(raw), None)
    assert spec.stations[0].z.expr == "OD-OD"
    assert spec.ledger["L"].kind == "linear"
    assert info["requirements"][2]["resolved_unit"] == "mm"


@pytest.mark.parametrize("has_profile", [True, False])
def test_progress_stages_describe_only_work_that_runs(tmp_path, monkeypatch, has_profile):
    from drawing2step.web_pipeline import process_drawing

    monkeypatch.setenv("GEMINI_API_KEY", "private-fixture-key")
    raw = draft().model_dump()
    if not has_profile:
        raw["stations"] = []
    proposal = DrawingDraft.model_validate(raw)
    monkeypatch.setattr(
        "drawing2step.web_pipeline.call_gemini",
        lambda *a, **k: {
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {"parts": [{"text": proposal.model_dump_json()}]},
                }
            ]
        },
    )
    (tmp_path / "original").write_bytes(png())
    stages = []
    process_drawing(
        tmp_path, "synthetic.png", 0, None, lambda state: stages.append(state["status"])
    )
    if has_profile:
        assert stages == [
            "rendering",
            "reading",
            "checking",
            "checking",
            "building",
            "previewing",
            "ready",
        ]
    else:
        assert stages == ["rendering", "reading", "checking", "checking", "review"]
        assert not (tmp_path / "cad").exists()


def test_backward_inch_profile_goes_to_review_before_build(tmp_path, monkeypatch):
    from drawing2step.body_cad import ProfileError, evaluate_profile
    from drawing2step.web_pipeline import process_drawing

    raw = draft("DIMENSIONS IN INCHES").model_dump()
    raw["callouts"].extend(
        [
            {
                "id": "A",
                "raw_text": ".125",
                "value_printed": ".125",
                "kind": "linear",
                "unit_printed": "",
            },
            {
                "id": "B",
                "raw_text": ".12",
                "value_printed": ".12",
                "kind": "linear",
                "unit_printed": "",
            },
        ]
    )
    raw["stations"] = [
        raw["stations"][0],
        {"z": {"ledger": "A"}, "od": {"ledger": "OD"}, "id": {"ledger": "ID"}},
        {"z": {"ledger": "B"}, "od": {"ledger": "OD"}, "id": {"ledger": "ID"}},
        raw["stations"][-1],
    ]
    proposal = DrawingDraft.model_validate(raw)
    spec, _ = prepare_spec(proposal, None)
    with pytest.raises(ProfileError) as failure:
        evaluate_profile(spec)
    assert failure.value.code == "PROFILE_AXIAL_ORDER"
    monkeypatch.setenv("GEMINI_API_KEY", "private-fixture-key")
    monkeypatch.setattr(
        "drawing2step.web_pipeline.call_gemini",
        lambda *a, **k: {
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {"parts": [{"text": proposal.model_dump_json()}]},
                }
            ]
        },
    )

    def forbidden_build(*args, **kwargs):
        pytest.fail("A contradictory profile must not reach CAD construction")

    monkeypatch.setattr("drawing2step.web_pipeline.build_verified", forbidden_build)
    (tmp_path / "original").write_bytes(png())
    updates = []
    process_drawing(tmp_path, "synthetic.png", 0, None, updates.append)
    assert updates[-1]["status"] == "review"
    info = next(state for state in updates if "review_code" in state)
    assert info["review_code"] == "PROFILE_AXIAL_ORDER"
    assert info["profile_validation"] == "FAIL"
    assert "3.175 mm" in updates[-1]["message"] and "3.048 mm" in updates[-1]["message"]
    assert updates[-1]["download_available"] is False
    assert "building" not in [state["status"] for state in updates]
    assert len(info["requirements"]) == len(proposal.callouts)
    assert not (tmp_path / "cad").exists()
