"""Generated fixtures only: no customer file ingestion or remote inference."""

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Literal

from drawing2step import __version__
from drawing2step.evaluation import evaluate
from drawing2step.models import Box, EvalCase, Observation, Requirement
from drawing2step.precedence import reconcile
from drawing2step.report import render_report
from drawing2step.storage import ArtifactStore, canonical_json, write_once


def demo_cases() -> list[EvalCase]:
    cases = []
    scenarios: tuple[tuple[str, Literal["clean", "scan"]], ...] = (
        ("agreement", "clean"),
        ("wrong-value", "clean"),
        ("conflict", "scan"),
        ("missing", "scan"),
    )
    for scenario, quality in scenarios:
        truth, predictions = [], []
        for index, value in enumerate(("120", "80", "16")):
            box = Box(page=1, x0=20, y0=20 + index * 55, x1=260, y1=48 + index * 55)
            target = Requirement(
                id=f"T-{index}",
                value=Decimal(value),
                kind="diameter",
                unit="mm",
                raw_text=f"Diameter {value} mm",
                box=box,
                feature=f"cylinder-{index}",
                text_status="ACCEPTED",
                interpretation_status="SUPPORTED",
                association_status="VALIDATED",
            )
            truth.append(target)
            if scenario == "missing" and index == 2:
                continue
            observations = []
            for source in ("a", "b"):
                observed = value
                if scenario == "wrong-value" and index == 0:
                    observed = "121"
                if scenario == "conflict" and index == 1 and source == "b":
                    observed = "88"
                observations.append(
                    Observation(
                        id=f"{source}-{index}",
                        source=source,
                        source_version="synthetic-v1",
                        value=Decimal(observed),
                        kind="diameter",
                        unit="mm",
                        box=box,
                        raw_text=f"Diameter {observed} mm",
                    )
                )
            decision = reconcile(observations)
            selected = next(
                (r for r in observations if r.id == decision.selected_reading), observations[0]
            )
            predictions.append(
                Requirement(
                    id=f"R-{index}",
                    value=selected.value,
                    kind=selected.kind,
                    unit=selected.unit,
                    raw_text=selected.raw_text,
                    box=box,
                    feature=f"cylinder-{index}",
                    text_status="ACCEPTED" if decision.accepted else "REVIEW",
                    interpretation_status="SUPPORTED",
                    association_status="VALIDATED",
                    evidence=tuple(observations),
                )
            )
        cases.append(
            EvalCase(
                id=f"synthetic-{scenario}",
                synthetic=True,
                group=scenario,
                quality=quality,
                truth=tuple(truth),
                predictions=tuple(predictions),
            )
        )
    return cases


def load_cases(path: Path) -> list[EvalCase]:
    raw = json.loads(path.read_bytes())
    if not isinstance(raw, list):
        raise ValueError("Evaluation input must be a JSON array of synthetic cases")
    return [EvalCase.model_validate(case) for case in raw]


def save_run(cases: list[EvalCase], root: Path) -> Path:
    metrics = evaluate(cases)
    payload = canonical_json([case.model_dump(mode="json") for case in cases])
    store = ArtifactStore(root / "artifacts")
    input_hash = store.put(payload)
    # Hash the actual experiment implementation so edits cannot reuse stale run outputs.
    code_hash = hashlib.sha256()
    for source in sorted(Path(__file__).parent.glob("*.py")):
        code_hash.update(source.name.encode())
        code_hash.update(source.read_bytes())
    manifest = {
        "schema_version": "run-v1",
        "package_version": __version__,
        "input_sha256": input_hash,
        "code_sha256": code_hash.hexdigest(),
        "configuration": {"matching_iou": 0.5, "synthetic_only": True},
    }
    run_hash = hashlib.sha256(canonical_json(manifest)).hexdigest()
    output = root / "runs" / run_hash
    write_once(output / "cases.json", payload)
    metrics_payload = canonical_json(metrics)
    report_payload = render_report(cases, metrics).encode()
    write_once(output / "evaluation.json", metrics_payload)
    write_once(output / "report.html", report_payload)
    manifest["outputs"] = {
        "evaluation_sha256": store.put(metrics_payload),
        "report_sha256": store.put(report_payload),
    }
    write_once(output / "manifest.json", canonical_json(manifest))
    return output


def run_demo(root: Path) -> Path:
    return save_run(demo_cases(), root)
