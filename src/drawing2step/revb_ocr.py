"""Pinned Document AI REST adapter using fixed overlapping tiles.

No Gemini fallback: a missing processor or ADC remains explicitly unavailable.
"""

import base64
import importlib
import io
import re
from pathlib import Path
from typing import Any

from PIL import Image

from drawing2step.storage import canonical_json, write_once


def fixed_tiles(
    width: int, height: int, size: int = 2048, overlap: int = 192
) -> list[tuple[int, int, int, int]]:
    if width <= 0 or height <= 0 or not 0 <= overlap < size:
        raise ValueError("Invalid tile geometry")
    return [
        (x, y, min(x + size, width), min(y + size, height))
        for y in range(0, height, size - overlap)
        for x in range(0, width, size - overlap)
    ]


def map_tokens(
    document: dict[str, Any], tile: tuple[int, int, int, int], width: int, height: int, prefix: str
) -> list[dict[str, Any]]:
    text = document.get("text", "")
    left, top, right, bottom = tile
    result: list[dict[str, Any]] = []
    for page in document.get("pages", []):
        for token in page.get("tokens", []):
            layout = token.get("layout", {})
            raw = "".join(
                text[int(s.get("startIndex", 0)) : int(s["endIndex"])]
                for s in layout.get("textAnchor", {}).get("textSegments", [])
            ).strip()
            vertices = layout.get("boundingPoly", {}).get("normalizedVertices", [])
            if not raw or not vertices:
                continue
            xs = [float(v.get("x", 0)) for v in vertices]
            ys = [float(v.get("y", 0)) for v in vertices]
            if not all(0 <= v <= 1 for v in xs + ys):
                continue
            result.append(
                {
                    "id": f"{prefix}-{len(result) + 1}",
                    "source": "document_ai",
                    "raw_text": raw,
                    "box": [
                        (top + min(ys) * (bottom - top)) / height * 1000,
                        (left + min(xs) * (right - left)) / width * 1000,
                        (top + max(ys) * (bottom - top)) / height * 1000,
                        (left + max(xs) * (right - left)) / width * 1000,
                    ],
                    "confidence": layout.get("confidence"),
                    "tile": list(tile),
                }
            )
    return result


def document_ai_tokens(image_path: Path, output: Path, processor: str) -> dict[str, Any]:
    def unavailable(detail: str) -> dict[str, Any]:
        return {
            "tokens": [],
            "metadata": {
                "status": "UNAVAILABLE",
                "detail": detail,
                "processor_version": processor,
                "cost_usd": None,
            },
        }

    match = re.fullmatch(
        r"projects/[A-Za-z0-9_-]+/locations/(us|eu)/processors/[A-Za-z0-9_-]+/processorVersions/[A-Za-z0-9_.-]+",
        processor,
    )
    if not match or processor.rsplit("/", 1)[-1] in {"latest", "stable", "default", "rc"}:
        return unavailable("Configure an exact pinned Document AI processor version in us or eu")
    try:
        google_auth = importlib.import_module("google.auth")
        transport = importlib.import_module("google.auth.transport.requests")
        credentials, _ = google_auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        session = transport.AuthorizedSession(credentials)
    except Exception:
        return unavailable("Document AI ADC or optional cloud dependencies unavailable")
    tokens = []
    completed = 0
    try:
        with session, Image.open(image_path) as image:
            tiles = fixed_tiles(image.width, image.height)
            for i, tile in enumerate(tiles):
                crop = image.crop(tile)
                buffer = io.BytesIO()
                crop.save(buffer, format="PNG")
                response = session.post(
                    f"https://{match.group(1)}-documentai.googleapis.com/v1/{processor}:process",
                    json={
                        "rawDocument": {
                            "mimeType": "image/png",
                            "content": base64.b64encode(buffer.getvalue()).decode(),
                        }
                    },
                    timeout=60,
                    allow_redirects=False,
                )
                if response.status_code != 200:
                    raise ValueError("Document AI processor request unavailable")
                payload = response.json()
                write_once(output / f"tile-{i}.png", buffer.getvalue())
                write_once(output / f"tile-{i}.json", canonical_json(payload))
                tokens.extend(
                    map_tokens(
                        payload.get("document", {}), tile, image.width, image.height, f"O{i}"
                    )
                )
                completed += 1
    except Exception:
        # Preserve successful tiles; unavailable is not silently treated as complete OCR.
        return {
            "tokens": tokens,
            "metadata": {
                "status": "UNAVAILABLE",
                "completed_tiles": completed,
                "processor_version": processor,
                "detail": "One or more OCR tiles unavailable",
                "cost_usd": None,
            },
        }
    return {
        "tokens": tokens,
        "metadata": {
            "status": "AVAILABLE",
            "completed_tiles": completed,
            "processor_version": processor,
            "cost_usd": None,
        },
    }
