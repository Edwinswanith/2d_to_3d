"""Local prerequisite and synthetic-lab command line."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from drawing2step.lab import load_cases, run_demo, save_run
from drawing2step.models import EvalCase
from drawing2step.preflight import Prerequisites, assess, inventory, load_manifest, template
from drawing2step.storage import canonical_json, write_once


def _emit(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Drawing-to-STEP local prerequisite and synthetic lab"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Create an empty prerequisite manifest; never overwrite")
    init.add_argument("path", type=Path)
    ready = sub.add_parser(
        "readiness", help="Check prerequisite records locally; exit 2 if blocked"
    )
    ready.add_argument("manifest", type=Path)
    inv = sub.add_parser("inventory", help="Hash explicit drawing/STEP pairs without uploading")
    inv.add_argument("manifest", type=Path)
    demo = sub.add_parser("demo", help="Run generated synthetic evaluation fixtures")
    demo.add_argument("--output", type=Path, default=Path("work/lab"))
    evaluation = sub.add_parser("eval", help="Score recorded synthetic cases; never unlock CAD")
    evaluation.add_argument("cases", type=Path)
    evaluation.add_argument("--output", type=Path, default=Path("work/lab"))
    schema = sub.add_parser("schema", help="Print versioned JSON schemas")
    schema.add_argument("name", choices=["prerequisites", "eval"])
    diagnostic = sub.add_parser("inspect-pdf", help="Single-page reading diagnostic; no acceptance")
    diagnostic.add_argument("pdf", type=Path)
    diagnostic.add_argument("--output", type=Path, default=Path("work/pdf-check"))
    diagnostic.add_argument("--live", action="store_true", help="Send this rendered page to Gemini")
    diagnostic.add_argument("--rotate", type=int, choices=[0, 90], default=0)
    diagnostic.add_argument("--model", default="gemini-3.5-flash")
    diagnostic.add_argument("--env-file", type=Path, default=Path(".env"))
    ocr = sub.add_parser("local-ocr", help="Add independent Tesseract evidence to a saved PDF run")
    ocr.add_argument("run", type=Path)
    reconciliation = sub.add_parser("reconcile", help="Correlate saved Gemini and OCR evidence")
    reconciliation.add_argument("run", type=Path)
    review = sub.add_parser(
        "validate-review", help="Validate explicit engineer association decisions"
    )
    review.add_argument("reconciliation", type=Path)
    review.add_argument("decisions", type=Path)
    body = sub.add_parser("build-body", help="Build a citation-driven evaluation body; no release")
    body.add_argument("spec", type=Path)
    body.add_argument("--output", type=Path, required=True)
    body.add_argument("--reference", type=Path)
    units = sub.add_parser("resolve-units", help="Record a confirmed drawing length-unit decision")
    units.add_argument("run", type=Path)
    units.add_argument("--unit", choices=["mm", "in"], required=True)
    units.add_argument("--confirmed-by", required=True)
    units.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "resolve-units":
            from drawing2step.unit_resolution import resolve_units

            print(str(resolve_units(args.run, args.unit, args.confirmed_by, args.reason).resolve()))
        elif args.command == "reconcile":
            from drawing2step.resolution import reconcile_run

            print(str(reconcile_run(args.run).resolve()))
        elif args.command == "validate-review":
            from drawing2step.association_review import validate_review

            print(str(validate_review(args.reconciliation, args.decisions).resolve()))
        elif args.command == "build-body":
            from drawing2step.body_cad import BodySpec, build_verified

            _emit(
                build_verified(
                    BodySpec.model_validate_json(args.spec.read_bytes()),
                    args.output,
                    args.reference,
                )
            )
        elif args.command == "local-ocr":
            from drawing2step.local_ocr import run_local_ocr

            print(str(run_local_ocr(args.run).resolve()))
        elif args.command == "init":
            if args.path.exists():
                raise ValueError("Manifest already exists; refusing to overwrite")
            write_once(args.path, canonical_json(template()))
            print(str(args.path.resolve()))
        elif args.command in {"readiness", "inventory"}:
            raw = load_manifest(args.manifest)
            base = args.manifest.resolve().parent
            if args.command == "inventory":
                _emit(inventory(raw, base))
            else:
                result = assess(raw, base)
                _emit(result)
                return 2 if result["blockers"] else 0
        elif args.command == "demo":
            print(str(run_demo(args.output).resolve()))
        elif args.command == "eval":
            print(str(save_run(load_cases(args.cases), args.output).resolve()))
        elif args.command == "schema":
            model = Prerequisites if args.name == "prerequisites" else EvalCase
            _emit(model.model_json_schema())
        elif args.command == "inspect-pdf":
            from drawing2step.pdf_diagnostic import inspect_pdf

            output = inspect_pdf(
                args.pdf,
                args.output,
                live=args.live,
                rotation=args.rotate,
                model=args.model,
                env_file=args.env_file,
            )
            print(str(output.resolve()))
            summary = json.loads((output / "summary.json").read_text())
            return 2 if summary["reader_a"] == "FAILED" else 0
        return 0
    except (ValueError, OSError, ValidationError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
