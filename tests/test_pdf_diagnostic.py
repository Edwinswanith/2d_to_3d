import io
import json
import urllib.error
from unittest.mock import MagicMock

import pymupdf
import pytest

from drawing2step.cli import main
from drawing2step.pdf_diagnostic import (
    PdfReading,
    call_gemini,
    context_issues,
    inspect_pdf,
    load_api_key,
    map_box_to_original,
    parse_response,
)


def response():
    return {
        "drawing_number": "GEBK472722A",
        "revision": "1",
        "part_family": "gland ring",
        "title_block_units": "DIMENSIONS IN INCHES",
        "notes": [],
        "uncertainties": [],
        "callouts": [
            {
                "id": "C1",
                "raw_text": "Ø218.0",
                "kind": "diameter",
                "value_printed": "218.0",
                "unit_printed": "",
                "tolerance_printed": "",
                "feature_proposal": "outside diameter",
                "box": [100, 100, 200, 200],
            }
        ],
    }


def test_key_from_dotenv_is_not_interpolated(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text('GEMINI_API_KEY="test-${OTHER}"\n')
    assert load_api_key(env) == "test-${OTHER}"
    monkeypatch.setenv("GEMINI_API_KEY", "environment-key")
    assert load_api_key(env) == "environment-key"
    monkeypatch.delenv("GEMINI_API_KEY")
    env.write_text("")
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        load_api_key(env)


def test_unit_conflict_is_flagged_never_converted():
    reading = PdfReading.model_validate(response())
    issues = context_issues(reading)
    assert any(item["code"] == "UNIT_PLAUSIBILITY_CONFLICT" for item in issues)
    assert reading.callouts[0].value_printed == "218.0"


def test_extreme_exponent_does_not_crash_plausibility():
    raw = response()
    raw["callouts"][0]["value_printed"] = "1E999999999"
    assert context_issues(PdfReading.model_validate(raw))[0]["code"] == "UNIT_PLAUSIBILITY_CONFLICT"


def test_unresolved_context_and_repeated_ids():
    raw = response()
    raw.update(drawing_number="", title_block_units="")
    codes = {item["code"] for item in context_issues(PdfReading.model_validate(raw))}
    assert {"IDENTITY_UNRESOLVED", "UNITS_UNRESOLVED"} <= codes
    raw["callouts"].append(raw["callouts"][0])
    with pytest.raises(ValueError, match="Duplicate"):
        PdfReading.model_validate(raw)


def test_box_rotation_round_trip():
    # Upright page 200x100; its top-right quadrant maps to original top-left quadrant.
    mapped = map_box_to_original([0, 500, 500, 1000], 100, 200, 90)
    assert mapped == pytest.approx([0, 0, 50, 100])
    assert map_box_to_original([0, 0, 1000, 1000], 100, 200, 0) == [0, 0, 100, 200]


def test_offline_flow_never_contacts_gemini(tmp_path, monkeypatch):
    pdf = tmp_path / "input.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page(width=120, height=160)
        page.insert_text((10, 25), "Test drawing")
        doc.save(pdf)
    monkeypatch.setattr(
        "drawing2step.pdf_diagnostic.call_gemini", lambda *a: pytest.fail("network")
    )
    output = inspect_pdf(pdf, tmp_path / "out", live=False, rotation=90)
    summary = json.loads((output / "summary.json").read_text())
    assert summary["cad_gate"] == summary["release_gate"] == "BLOCKED"
    assert summary["reader_a"] == "NOT_RUN"
    assert summary["native_spans"] > 0
    assert (output / "original.pdf").read_bytes() == pdf.read_bytes()
    assert (output / "report.html").is_file()


def test_live_flow_retains_raw_response_and_provenance(tmp_path, monkeypatch):
    pdf = tmp_path / "input.pdf"
    with pymupdf.open() as doc:
        doc.new_page(width=120, height=160)
        doc.save(pdf)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    body = {
        "candidates": [
            {"content": {"parts": [{"text": json.dumps(response())}]}, "finishReason": "STOP"}
        ],
        "modelVersion": "test-model",
    }
    monkeypatch.setattr("drawing2step.pdf_diagnostic.call_gemini", lambda *a: body)
    output = inspect_pdf(pdf, tmp_path / "out", live=True)
    summary = json.loads((output / "summary.json").read_text())
    assert summary["reader_a"] == "SCHEMA_VALIDATED"
    assert summary["reader_b"] == "UNAVAILABLE"
    assert summary["accepted_requirements"] == 0
    assert "test-key" not in "".join(p.read_text() for p in output.glob("*.json"))
    assert json.loads((output / "gemini-response.json").read_text()) == body


def test_invalid_pdf_and_multi_page_rejected_before_cloud(tmp_path):
    invalid = tmp_path / "bad.pdf"
    invalid.write_text("not a PDF")
    with pytest.raises(ValueError, match="PDF"):
        inspect_pdf(invalid, tmp_path / "out")
    with pymupdf.open() as doc:
        doc.new_page()
        doc.new_page()
        doc.save(tmp_path / "multiple.pdf")
    with pytest.raises(ValueError, match="single-page"):
        inspect_pdf(tmp_path / "multiple.pdf", tmp_path / "out")


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {},
        {"candidates": [None]},
        {"candidates": [{"finishReason": "MAX_TOKENS"}]},
        {"candidates": [{"finishReason": "STOP", "content": None}]},
        {"candidates": [{"finishReason": "STOP", "content": {"parts": [None]}}]},
    ],
)
def test_malformed_provider_envelopes_are_rejected(body):
    with pytest.raises(ValueError):
        parse_response(body)


def test_raster_page_serializes_and_provider_failure_leaves_report(tmp_path, monkeypatch):
    pdf = tmp_path / "raster.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page(width=120, height=160)
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 20), False)
        pix.clear_with(255)
        page.insert_image(page.rect, stream=pix.tobytes("png"))
        doc.save(pdf)
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret")
    monkeypatch.setattr(
        "drawing2step.pdf_diagnostic.call_gemini", lambda *a: {"candidates": [None]}
    )
    output = inspect_pdf(pdf, tmp_path / "out", live=True)
    summary = json.loads((output / "summary.json").read_text())
    assert summary["reader_a"] == "FAILED"
    assert summary["native_spans"] == 0
    assert summary["embedded_images"] == 1
    assert (output / "report.html").is_file()
    assert not (output / "ledger.json").exists()


def test_fixed_endpoint_and_header_not_query_string(monkeypatch):
    opener = MagicMock()
    opener.open.return_value.__enter__.return_value = io.BytesIO(b'{"candidates": []}')
    monkeypatch.setattr("urllib.request.build_opener", lambda *a: opener)
    assert call_gemini(b"png", "private-key", "gemini-3.5-flash") == {"candidates": []}
    request = opener.open.call_args.args[0]
    assert (
        request.full_url
        == "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent"
    )
    assert "private-key" not in request.full_url
    assert b"private-key" not in request.data
    assert request.get_header("X-goog-api-key") == "private-key"
    with pytest.raises(ValueError, match="identifier"):
        call_gemini(b"", "private-key", "../../elsewhere")


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.HTTPError("https://example.test", 401, "error", None, None),
        urllib.error.URLError("private-key"),
        TimeoutError("private-key"),
    ],
)
def test_network_errors_do_not_echo_credentials(monkeypatch, error):
    opener = MagicMock()
    opener.open.side_effect = error
    monkeypatch.setattr("urllib.request.build_opener", lambda *a: opener)
    with pytest.raises(ValueError) as caught:
        call_gemini(b"", "private-key", "gemini-3.5-flash")
    assert "private-key" not in str(caught.value)


def test_pdf_cli_failure_and_success_exit_codes(tmp_path, monkeypatch, capsys):
    pdf = tmp_path / "input.pdf"
    with pymupdf.open() as doc:
        doc.new_page(width=120, height=160)
        doc.save(pdf)
    args = ["inspect-pdf", str(pdf), "--output", str(tmp_path / "out")]
    assert main(args) == 0
    assert capsys.readouterr().out.strip()
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret")
    monkeypatch.setattr("drawing2step.pdf_diagnostic.call_gemini", lambda *a: {})
    assert main([*args, "--live"]) == 2
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-corrupt data")
    assert main(["inspect-pdf", str(broken)]) == 2
