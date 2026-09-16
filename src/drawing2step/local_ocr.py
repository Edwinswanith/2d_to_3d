"""Independent local OCR evidence for diagnostic runs; never automatic approval."""

import csv
import hashlib
import io
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from drawing2step.storage import canonical_json, write_once


def parse_tokens(tsv: str, width: int, height: int) -> list[dict[str, Any]]:
    """Retain word observations including confidence and full-page coordinates."""
    if width <= 0 or height <= 0:
        raise ValueError("Image dimensions must be positive")
    tokens: list[dict[str, Any]] = []
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        text = row.get("text", "").strip()
        if row.get("level") != "5" or not text:
            continue
        left, top, w, h = (int(row[k]) for k in ("left", "top", "width", "height"))
        tokens.append(
            {
                "id": f"B-{len(tokens) + 1:04d}",
                "raw_text": text,
                "confidence": float(row["conf"]),
                "box": [
                    top / height * 1000,
                    left / width * 1000,
                    (top + h) / height * 1000,
                    (left + w) / width * 1000,
                ],
                "source": "tesseract",
                "accepted": False,
            }
        )
    return tokens


def _execute(arguments: list[str], timeout: int) -> str:
    try:
        return subprocess.run(
            arguments, capture_output=True, text=True, check=True, timeout=timeout
        ).stdout
    except (subprocess.SubprocessError, OSError):
        raise ValueError(
            "Local OCR failed or timed out; check the Tesseract installation"
        ) from None


def run_local_ocr(run: Path) -> Path:
    """Read a saved diagnostic image with fixed local OCR and preserve its provenance."""
    executable = shutil.which("tesseract")
    if not executable:
        raise ValueError("Tesseract is not installed")
    summary = json.loads((run / "summary.json").read_text())
    image = run / "page-600.png"
    metadata = summary["renders"]["600"]
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    if digest != metadata["sha256"]:
        raise ValueError("Saved render integrity mismatch")
    version = _execute([executable, "--version"], 10).splitlines()
    if not version:
        raise ValueError("Tesseract returned no version information")
    result = _execute([executable, str(image.resolve()), "stdout", "--psm", "11", "tsv"], 120)
    tokens = parse_tokens(result, metadata["width"], metadata["height"])
    output = run / "independent-local-ocr"
    write_once(output / "raw.tsv", result.encode())
    write_once(output / "tokens.json", canonical_json(tokens))
    manifest = {
        "source": "tesseract",
        "version": version[0],
        "psm": 11,
        "image_sha256": digest,
        "tokens": len(tokens),
        "independent_from_gemini": True,
        "document_ai": "NOT_RUN",
        "automatic_acceptance": False,
        "remaining_blocks": [
            "Unit decision",
            "Callout/tolerance reconciliation",
            "Validated associations",
            "Reference STEP and accuracy dataset",
            "CAD implementation and verification",
            "Engineer release review",
        ],
    }
    write_once(output / "manifest.json", canonical_json(manifest))
    return output
