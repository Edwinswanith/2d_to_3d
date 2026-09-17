"""Immutable feature-spec proposals, construction attempts and correction versions."""

import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from drawing2step.pdf_diagnostic import call_gemini, load_api_key
from drawing2step.revb import Requirement
from drawing2step.revb_model import (
    BUILDER_VERSION,
    DraftSpec,
    build_model,
    enrich_ledger,
    numeric_value,
)
from drawing2step.revb_pipeline import PipelineConfig, Provider
from drawing2step.revb_proposal import (
    SPEC_PROMPT,
    Requester,
    axial_length_check,
    construction_failures,
    decode_proposal,
    geometry_feedback,
    rejected_features,
    reliable_overall_length_mm,
    request_proposal,
    spec_schema,
    uncited_dimensions,
)
from drawing2step.storage import canonical_json, write_once

__all__ = ["build_from_audit", "decode_proposal", "spec_schema", "uncited_dimensions"]


def _save(directory: Path, name: str, data: Any) -> None:
    write_once(directory / name, canonical_json(data))


# A tap drill and a plain hole are one visual class in a plan view; the thread is text.
_VISUAL_CLASSES = ({"hole_pattern", "tapped_hole"},)


def _same_visual_class(observed: str, proposed: str) -> bool:
    return observed == proposed or any({observed, proposed} <= group for group in _VISUAL_CLASSES)


def _context_length_mm(audit: dict[str, Any], field: str) -> float | None:
    """A context-stage printed length in mm; a recorded unit correction overrides the label."""
    proposal = audit.get("context_proposal") or {}
    if proposal.get(f"{field}_value") is None:
        return None
    context = audit["context"]
    unit = context["unit"]
    if not context.get("unit_correction"):
        unit = proposal.get(f"{field}_unit") or unit
    return float(proposal[f"{field}_value"]) * {"mm": 1, "cm": 10, "in": 25.4}[unit]


def _envelope_mm(audit: dict[str, Any]) -> float | None:
    return _context_length_mm(audit, "envelope")


def _construct(spec: DraftSpec, requirements: list[Requirement], outdir: Path) -> dict[str, Any]:
    outdir.mkdir()
    try:
        return build_model(spec, requirements, outdir)
    except (ValueError, RuntimeError, OSError) as error:
        _save(
            outdir,
            "build-failure.json",
            {"error": type(error).__name__, "detail": str(error)[:1000], "release": "BLOCKED"},
        )
        raise


def _construct_with_correction(
    folder: Path,
    spec: DraftSpec,
    requirements: list[Requirement],
    prompt: str,
    envelope_mm: float | None,
    request: Requester | None,
    inventory_ids: set[str],
    overall_mm: float | None = None,
) -> tuple[DraftSpec, dict[str, Any]]:
    """Build the proposal; feed named construction failures back once; keep the better draft.

    Each draft is built in its own numbered folder so both remain inspectable; the winner's
    files are promoted into the model folder. An engineer-corrected spec is never re-asked.
    """
    result = _construct(spec, requirements, folder / "draft-1")
    failures = construction_failures(result, spec, requirements, overall_mm)
    drafts = [{"draft": "draft-1", "failed": [f for f, _ in failures]}]
    winner = "draft-1"
    if failures and request is not None:
        corrected = prompt + geometry_feedback(spec, failures)
        write_once(folder / "correction-prompt.txt", corrected.encode())
        revised = request_proposal(
            folder,
            corrected,
            requirements,
            envelope_mm,
            request,
            inventory_ids=inventory_ids,
            label="correction",
            max_rejections=2,
        )
        if revised is not None:
            try:
                second = _construct(revised, requirements, folder / "draft-2")
            except (ValueError, RuntimeError, OSError):
                drafts.append({"draft": "draft-2", "failed": ["construction raised"]})
            else:
                remaining = construction_failures(second, revised, requirements, overall_mm)
                # A correction that deletes a failing feature is not an improvement: every
                # feature the first draft proposed but the revision no longer builds counts
                # against it, so only a draft that fixes more than it drops is promoted.
                dropped = sorted({f.id for f in spec.features} - {f.id for f in revised.features})
                drafts.append(
                    {"draft": "draft-2", "failed": [f for f, _ in remaining], "dropped": dropped}
                )
                if len(remaining) + len(dropped) < len(failures):
                    spec, result, winner = revised, second, "draft-2"
    for path in list((folder / winner).iterdir()):
        path.rename(folder / path.name)
    (folder / winner).rmdir()
    _save(folder, "drafts.json", [{**d, "promoted": d["draft"] == winner} for d in drafts])
    return spec, result


def build_from_audit(
    directory: Path,
    update: Callable[[dict[str, Any]], None],
    *,
    provider: Provider | None = None,
    corrected_spec: DraftSpec | None = None,
    correction: dict[str, Any] | None = None,
    version: int = 1,
) -> dict[str, Any]:
    audit = json.loads((directory / "audit.json").read_text())
    manifest = json.loads((directory / "audit-manifest.json").read_text())
    for name, digest in manifest["artifacts"].items():
        path = directory / name
        if (
            not path.is_relative_to(directory)
            or hashlib.sha256(path.read_bytes()).hexdigest() != digest
        ):
            raise ValueError("Source audit artifact integrity failed")
    if audit["context"]["status"] != "PASS":
        raise ValueError("Units, identity or envelope must be resolved before modelling")
    requirements = enrich_ledger(
        [Requirement.model_validate(r) for r in audit["requirements"]], audit["context"]["unit"]
    )
    if not any(r.value is not None and r.unit in {"mm", "cm", "in"} for r in requirements):
        raise ValueError("No readable length requirements to construct a profile")
    from drawing2step.deployment import commit_sha  # local: avoid an import cycle at load time

    folder_id = uuid4().hex
    folder = directory / "models" / folder_id
    folder.mkdir(parents=True)
    config = PipelineConfig.from_environment()
    model = os.environ.get("D2S_SPEC_MODEL", config.text_model)
    inputs = {
        "version": version,
        "original_sha256": audit["original_sha256"],
        "audit_sha256": hashlib.sha256((directory / "audit.json").read_bytes()).hexdigest(),
        "builder_version": BUILDER_VERSION,
        "commit": commit_sha(),
        "model": model if corrected_spec is None else None,
        "spec_source": "model_proposal" if corrected_spec is None else "draft_correction",
        "implementation_sha256": hashlib.sha256(
            b"".join(
                (Path(__file__).parent / name).read_bytes()
                for name in (
                    "revb_build_pipeline.py",
                    "revb_proposal.py",
                    "revb_model.py",
                    "revb_sections.py",
                    "revb_geometry.py",
                    "revb.py",
                    "storage.py",
                    "pdf_diagnostic.py",
                )
            )
        ).hexdigest(),
    }
    _save(folder, "inputs.json", inputs)
    if correction:
        _save(folder, "correction.json", correction)
    update(
        {
            "status": "building",
            "message": "Associating features and building the complete draft",
            "model_version": version,
        }
    )
    spec = corrected_spec
    request: Requester | None = None
    prompt = ""
    envelope_mm = _envelope_mm(audit)
    raw_overall_mm = _context_length_mm(audit, "overall_length")
    overall_mm = reliable_overall_length_mm(raw_overall_mm, requirements)
    overall_length_unreliable = raw_overall_mm is not None and overall_mm is None
    inventory_ids = {f["id"] for f in (audit.get("inventory") or {"features": []})["features"]}
    if spec is None:
        key = load_api_key() if provider is None else ""
        context_proposal = dict(audit.get("context_proposal") or {})
        if overall_length_unreliable:
            context_proposal["overall_length_value"] = None
            context_proposal["overall_length_unit"] = None
        prompt = (
            SPEC_PROMPT
            + "\nLEDGER:\n"
            + json.dumps(
                [
                    {
                        k: r.model_dump(mode="json")[k]
                        for k in ("id", "raw_text", "value", "unit", "kind")
                    }
                    for r in requirements
                ]
            )
            + "\nINDEPENDENT INVENTORY:\n"
            + json.dumps(audit["inventory"])
            + "\nCONTEXT PROPOSAL:\n"
            + json.dumps(context_proposal)
        )
        write_once(folder / "prompt.txt", prompt.encode())
        schema = spec_schema()
        _save(folder, "schema.json", schema)
        image = (directory / "drawing.png").read_bytes()

        def request(text: str) -> dict[str, Any]:
            if provider is not None:
                return provider(image, "spec", text, schema)
            return call_gemini(
                image,
                key,
                model,
                prompt=text,
                schema=schema,
                timeout=180,
                max_output_tokens=32768,
            )

        spec = request_proposal(
            folder, prompt, requirements, envelope_mm, request, inventory_ids=inventory_ids
        )
        if spec is None:
            raise ValueError("Feature specification unavailable after bounded attempts")
    spec, result = _construct_with_correction(
        folder, spec, requirements, prompt, envelope_mm, request, inventory_ids, overall_mm
    )
    inventory = audit.get("inventory") or {"features": []}
    inventoried = {f["id"]: f for f in inventory["features"]}
    covered = {
        i
        for f in result["features"]
        if f["status"] in {"BUILT", "REPORT_ONLY"}
        for i in f["inventory_ids"]
    }
    missing = [i for i in inventoried if i not in covered]
    # Group by physical feature, not raw inventory id: the audit links repeat observations of
    # one physical port/hole across views with same_physical_group, so a group only represents
    # a genuine coverage gap when EVERY observation of it is uncovered. Restricted to the
    # kinds SPEC_PROMPT requires as their own discrete, citable Feature (never folded into the
    # profile the way a body/bore_step/groove/chamfer observation legitimately can be, and
    # never a marking, which is text rather than a cut): losing one of these is a dropped
    # physical feature, not an association nuance the profile already accounts for.
    _discrete_kinds = {"hole_pattern", "tapped_hole", "port", "bore_slot", "od_slot"}
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in inventory["features"]:
        groups.setdefault(item.get("same_physical_group") or item["id"], []).append(item)
    uncovered_features = [
        (group, items)
        for group, items in groups.items()
        if all(item["id"] not in covered for item in items)
        and any(item.get("type") in _discrete_kinds for item in items)
    ]
    unused = uncited_dimensions(spec, requirements)
    associations = [a.model_dump(mode="json") for a in spec.associations]
    for a in associations:
        a["status"] = "PROPOSED"  # Successful construction is not proof of drawing association.
    _save(folder, "associations.json", associations)
    checks = result["checks"] + [
        {
            "layer": "D",
            "subject": i,
            "status": "UNKNOWN",
            "detail": (
                "Inventory observation not linked to a built feature; "
                "review duplicate/body association or missing geometry"
            ),
        }
        for i in missing
    ]
    checks.extend(
        {
            "layer": "D",
            "subject": group,
            "status": "FAIL",
            "detail": (
                f"{next(i['type'] for i in items if i.get('type') in _discrete_kinds)} "
                f"'{items[0]['description']}' was independently inventoried "
                f"({', '.join(sorted(item['id'] for item in items))}) but no built feature cites "
                "any of its observations; this is a physical feature the drawing shows and the "
                "proposal dropped, not merely an unresolved association"
            ),
        }
        for group, items in uncovered_features
    )
    checks.extend(
        {
            "layer": "D",
            "subject": r.id,
            "status": "UNKNOWN",
            "detail": (
                f"Printed dimension {r.raw_text!r} is not used by the profile or any feature; "
                + (
                    "the proposal lists it as unresolved"
                    if any(r.id in item for item in spec.unresolved)
                    else "geometry may be missing from the draft"
                )
            ),
        }
        for r in unused
    )
    checks.extend(
        {"layer": "D", "subject": feature, "status": "FAIL", "detail": detail}
        for feature, detail in rejected_features(spec)
    )
    # A successful cut does not resolve a reading, coverage or OCR finding. Keep the
    # immutable intake obligations visible until an explicit correction resolves them.
    checks.extend(
        {
            "layer": "EVIDENCE",
            "subject": "evidence-"
            + hashlib.sha256(f"{finding['code']}:{finding['subject']}".encode()).hexdigest()[:16],
            "status": finding["status"],
            "detail": f"{finding['code']} · {finding['subject']}: {finding['detail']}",
        }
        for finding in audit.get("findings", [])
    )
    checks.append(
        {"layer": "U", "subject": "context", "status": "PASS", "detail": audit["context"]["detail"]}
    )
    if overall_length_unreliable:
        checks.append(
            {
                "layer": "U",
                "subject": "overall_length",
                "status": "UNKNOWN",
                "detail": (
                    f"Context read the overall length as {raw_overall_mm:.3f} mm, but a "
                    "printed linear dimension is longer than that; every printed length is a "
                    "sub-span of the same axial chain, so the context reading cannot be the "
                    "true envelope and was disregarded for construction and the axial-length "
                    "check. Confirm the actual overall-length dimension by hand."
                ),
            }
        )
    checks.append(axial_length_check(result["measurements"]["bbox"][2], requirements, overall_mm))
    ledger = {r.id: r for r in requirements}
    assumptions = {a.id: a for a in spec.assumptions}
    built_ids = {f["id"] for f in result["features"] if f["status"] == "BUILT"}
    for requirement in requirements:
        if requirement.kind != "count" or requirement.value is None:
            continue
        parents = [
            s.removeprefix("callout:") for s in requirement.sources if s.startswith("callout:")
        ]
        parent = parents[0] if parents else requirement.id
        linked = [
            f
            for f in spec.features
            if parent in f.citations
            and f.kind
            in {"hole_pattern", "tapped_hole", "port", "bore_slot", "od_slot", "counterbore"}
        ]
        if not linked:
            checks.append(
                {
                    "layer": "D",
                    "subject": requirement.id,
                    "status": "UNKNOWN",
                    "detail": "Printed feature count has no geometry association",
                }
            )
            continue
        actual = sum(
            numeric_value(f.count, ledger, assumptions, "count") if f.count else 1
            for f in linked
            if f.id in built_ids
        )
        checks.append(
            {
                "layer": "D",
                "subject": requirement.id,
                "status": "PASS" if actual == float(requirement.value) else "FAIL",
                "detail": (
                    f"Printed count {requirement.value}; successfully built instances {actual:g}"
                ),
            }
        )
    proposal = audit.get("context_proposal") or {}
    if proposal.get("envelope_value") is not None:
        units = proposal.get("envelope_unit") or audit["context"]["unit"]
        maximum = float(proposal["envelope_value"]) * {"mm": 1, "cm": 10, "in": 25.4}[units]
        measured_max = max(result["measurements"]["bbox"])
        checks.append(
            {
                "layer": "H2",
                "subject": "context_envelope",
                "status": "FAIL" if measured_max > maximum * 1.01 else "UNKNOWN",
                "detail": (
                    f"Measured maximum extent {measured_max:.3f} mm; "
                    f"context proposal {maximum:.3f} mm. Within-bound dimensions "
                    "still require feature-specific checks."
                ),
            }
        )
    for feature in spec.features:
        for source in feature.inventory_ids:
            observed = inventoried.get(source)
            status, detail = "UNKNOWN", "Visual association needs engineer validation"
            if observed is None:
                status, detail = "FAIL", "Referenced inventory feature does not exist"
            elif not _same_visual_class(observed["type"], feature.kind):
                # Two model readings disagree; neither is ground truth, so this is review.
                status, detail = (
                    "UNKNOWN",
                    f"Inventory observed a {observed['type']} in the {observed.get('view')} "
                    f"view; the specification proposes a {feature.kind}. Confirm which reading "
                    "matches the sheet",
                )
            elif feature.count and observed.get("count") is not None:
                count = numeric_value(feature.count, ledger, assumptions, "count")
                if count != observed["count"]:
                    status, detail = (
                        "UNKNOWN",
                        "Pattern count differs from visible features in this view; "
                        "confirm section visibility and the physical group. "
                        "Printed count requirements are checked separately.",
                    )
            checks.append(
                {
                    "layer": "D",
                    "subject": f"{feature.id}:{source}",
                    "status": status,
                    "detail": detail,
                }
            )
    checks.append(
        {
            "layer": "I",
            "subject": "accuracy_gate",
            "status": "UNKNOWN",
            "detail": (
                "Paired-data acceptance not yet approved; draft is available, "
                "manufacturing release is blocked"
            ),
        }
    )
    checks.extend(
        {
            "layer": "AS",
            "subject": a["id"],
            "status": "UNKNOWN",
            "detail": (
                f"Review association {a['requirement']} to {a['feature']} "
                f"from {a['reference_face']}"
            ),
        }
        for a in associations
    )
    _save(folder, "checks.json", checks)
    _save(
        folder,
        "manifest.json",
        {
            **inputs,
            "release": "BLOCKED",
            "artifacts": {
                str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in folder.rglob("*")
                if p.is_file()
            },
        },
    )
    failures = sum(c["status"] == "FAIL" for c in checks)
    changes = {
        **result,
        "model_folder": folder_id,
        "model_version": version,
        "revision_b": True,
        "completion": "PARTIAL_DRAFT_REQUIRES_REVIEW" if failures else "DRAFT_REQUIRES_REVIEW",
        "drawing_number": audit["drawing_number"],
        "message": (
            f"Partial 3D draft and STEP are available with {failures} failed checks. "
            "Inspect the named failures and correct the specification."
            if failures
            else "3D draft and STEP are ready. Review assumptions "
            "and feature coverage before manufacturing."
        ),
        "findings": [
            {
                "code": c.get("layer", "H2"),
                "subject": c["subject"],
                "status": c["status"],
                "detail": c["detail"],
            }
            for c in checks
            if c["status"] != "PASS"
        ],
        "checks": checks,
        "model_features": result["features"],
        "uncited_dimensions": [{"id": r.id, "raw_text": r.raw_text} for r in unused],
        "associations": associations,
        "context": audit["context"],
        "inventory": inventory,
    }
    update(changes)
    return changes
