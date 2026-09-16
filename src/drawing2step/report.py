"""Standalone, escaped HTML report for synthetic ledger experiments."""

import html
import json
from collections.abc import Sequence
from typing import Any

from drawing2step.models import EvalCase


def render_report(cases: Sequence[EvalCase], metrics: dict[str, Any]) -> str:
    sections: list[str] = []
    for case in cases:
        rows, overlays = [], []
        for index, requirement in enumerate(case.predictions):
            key = f"case-{len(sections)}-row-{index}"
            box = requirement.box
            label = html.escape(requirement.raw_text)
            overlays.append(
                f'<a href="#{key}"><rect x="{box.x0}" y="{box.y0}" '
                f'width="{box.x1 - box.x0}" height="{box.y1 - box.y0}" '
                f'fill="#dbeafe" stroke="#2563eb"/><text x="{box.x0 + 4}" '
                f'y="{box.y0 + 15}" font-size="12">{label}</text></a>'
            )
            evidence = (
                "<br>".join(
                    html.escape(f"{r.source} ({r.source_version}): {r.raw_text}")
                    for r in requirement.evidence
                )
                or "No recorded readings"
            )
            statuses = html.escape(
                f"{requirement.text_status} / {requirement.interpretation_status} / "
                f"{requirement.association_status}"
            )
            rows.append(
                f'<tr id="{key}"><td>{html.escape(requirement.id)}</td><td>{label}</td>'
                f"<td>{statuses}</td><td>{evidence}</td></tr>"
            )
        sections.append(
            f"<section><h2>{html.escape(case.id)}</h2><p>{case.quality} · {case.split}</p>"
            "<p>Generated callout layout for scoring tests. This is not an uploaded drawing.</p>"
            '<svg viewBox="0 0 600 220" role="img" aria-label="Synthetic callout ledger overlay">'
            + "".join(overlays)
            + '</svg><div class="table"><table><thead><tr>'
            "<th>Requirement</th><th>Reading</th><th>Text / interpretation / association</th>"
            "<th>Evidence</th></tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table></div></section>"
        )
    return (
        """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>Drawing-to-STEP · Synthetic evaluation</title><style>
body{font:16px/1.55 system-ui,sans-serif;background:#f4f6f8;color:#172337;margin:0}
main{max-width:1100px;margin:auto;padding:36px 24px}h1{font-size:34px;line-height:1.2}
.notice{padding:20px;background:#fff0cc;border-left:5px solid #a65a00}
section{margin-top:24px;background:white;border:1px solid #d8dfe8;border-radius:10px;padding:24px}
svg{width:100%;max-height:260px;background:#f8fafc}table{border-collapse:collapse;width:100%}
th,td{text-align:left;padding:12px;border-bottom:1px solid #d8dfe8;vertical-align:top}
th{font-size:13px}.table{overflow:auto}tr:target{background:#fff0cc}pre{white-space:pre-wrap}
a:focus{outline:3px solid #a65a00}</style></head><body><main>
<p>DRAWING-TO-STEP / FEASIBILITY LAB</p><h1>Evidence before geometry.</h1>
<p class="notice"><strong>SYNTHETIC DATA ONLY. CAD and release gates are BLOCKED.</strong><br>
This report tests scoring and decision behavior. It does not measure OCR accuracy, validate a
customer drawing, or establish manufacturing conformance.</p>"""
        + "".join(sections)
        + (
            "<section><h2>Evaluation metrics</h2><p>UNKNOWN is not PASS. "
            "Missing metric denominators "
            "are null. Accepted-value error includes unmatched accepted predictions.</p><pre>"
            + html.escape(json.dumps(metrics, indent=2))
            + "</pre></section></main></body></html>"
        )
    )
