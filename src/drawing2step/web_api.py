"""Single-shop local web API. Not intended for public exposure without authentication."""

import json
import re
import threading
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from drawing2step.revb_build_pipeline import build_from_audit
from drawing2step.revb_model import DraftSpec
from drawing2step.revb_pipeline import process_revb
from drawing2step.revb_review import (
    ReleaseRequest,
    ReviewDecision,
    authenticate_reviewer,
    decisions,
    record_decision,
    release_bundle,
)
from drawing2step.storage import canonical_json
from drawing2step.web_pipeline import MAX_FILE, render_input


class BuildRequest(BaseModel):
    expected_version: int = Field(default=0, ge=0)
    spec: DraftSpec | None = None
    reason: str | None = Field(default=None, min_length=3, max_length=2000)
    reviewer: str | None = Field(default=None, min_length=1, max_length=100)


def create_app(
    root: Path = Path("work/web"),
    *,
    pipeline: Callable[..., None] = process_revb,
) -> FastAPI:
    root.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cad-draft")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        for path in root.glob("*/status.json"):
            state = json.loads(path.read_text())
            if state.get("status") not in {"ready", "review", "failed"}:
                state.update(status="failed", message="Server restarted; please upload again")
                path.write_bytes(canonical_json(state))
        yield
        executor.shutdown(wait=True)

    app = FastAPI(title="Drawing-to-STEP local workspace", lifespan=lifespan)
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"]
    )

    @app.middleware("http")
    async def check_origin(request: Request, call_next: Any) -> Any:
        if request.method == "POST":
            allowed = {
                f"http://{host}:{port}"
                for host in ("localhost", "127.0.0.1")
                for port in (8000, 5173)
            }
            origin = request.headers.get("origin")
            if (origin and origin not in allowed) or request.headers.get(
                "sec-fetch-site"
            ) == "cross-site":
                return JSONResponse(
                    {"detail": "Upload must originate from the local workspace"}, status_code=403
                )
        return await call_next(request)

    def job_path(job_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise HTTPException(404, "Drawing not found")
        directory = root / job_id
        if not (directory / "status.json").is_file():
            raise HTTPException(404, "Drawing not found")
        return directory

    def update(directory: Path, changes: dict[str, Any]) -> None:
        with lock:
            path = directory / "status.json"
            state = json.loads(path.read_text()) if path.exists() else {}
            state.update(changes)
            temporary = directory / "status.pending"
            temporary.write_bytes(canonical_json(state))
            temporary.replace(path)

    def worker(directory: Path, name: str, rotation: int, unit: Any) -> None:
        try:
            pipeline(directory, name, rotation, unit, lambda changes: update(directory, changes))
        except Exception:
            # Keep provider request bodies, file contents, credentials and tracebacks private.
            update(
                directory,
                {
                    "status": "failed",
                    "message": (
                        "Drawing audit could not finish. Check the file and provider "
                        "configuration, then retry in a new run."
                    ),
                    "download_available": False,
                    "model_available": False,
                },
            )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": "revision-b-draft-and-review"}

    def model_worker(
        directory: Path, request: BuildRequest, version: int, previous: dict[str, Any]
    ) -> None:
        try:
            build_from_audit(
                directory,
                lambda changes: update(directory, changes),
                corrected_spec=request.spec,
                version=version,
                correction={
                    "reason": request.reason,
                    "reviewer": request.reviewer,
                    "previous_version": request.expected_version,
                }
                if request.spec
                else None,
            )
        except Exception as error:
            # Local deterministic failures are useful; never expose provider bodies.
            detail = str(error) if isinstance(error, ValueError) else type(error).__name__
            update(
                directory,
                {
                    **previous,
                    "status": "review",
                    "message": f"Draft build needs review: {detail[:250]}",
                    "model_available": previous.get("model_available", False),
                    "download_available": previous.get("download_available", False),
                },
            )

    @app.post("/api/drawings/{job_id}/build", status_code=202)
    def rebuild(job_id: str, request: BuildRequest) -> dict[str, Any]:
        directory = job_path(job_id)
        if request.spec and (not request.reason or not request.reviewer):
            raise HTTPException(422, "Corrections require an author and reason")
        with lock:
            state = json.loads((directory / "status.json").read_text())
            if state.get("status") not in {"ready", "review", "failed"}:
                raise HTTPException(409, "A drawing job is already running")
            if state.get("model_version", 0) != request.expected_version:
                raise HTTPException(409, "Drawing version changed; reload before editing")
            if not (directory / "audit.json").is_file():
                raise HTTPException(409, "Upload again to create Revision B evidence")
            audit = json.loads((directory / "audit.json").read_text())
            if audit["context"]["status"] != "PASS":
                raise HTTPException(409, "Resolve units and drawing identity before building")
            version = request.expected_version + 1
            previous = dict(state)
            state.update(
                status="building",
                model_version=version,
                model_available=False,
                download_available=False,
                message="Preparing feature draft",
            )
            pending = directory / "status.pending"
            pending.write_bytes(canonical_json(state))
            pending.replace(directory / "status.json")
        executor.submit(model_worker, directory, request, version, previous)
        return {"id": job_id, "model_version": version}

    @app.post("/api/drawings", status_code=202)
    async def upload(
        file: Annotated[UploadFile, File()],
        rotation: int = Form(0),
        units: Literal["auto", "mm", "cm", "in"] = Form("auto"),
    ) -> dict[str, Any]:
        if rotation not in {0, 90, 180, 270}:
            raise HTTPException(422, "Choose a supported rotation")
        chunks = bytearray()
        while chunk := await file.read(1024 * 1024):
            chunks.extend(chunk)
            if len(chunks) > MAX_FILE:
                raise HTTPException(413, "Drawing exceeds 20 MB")
        name = Path(file.filename or "drawing").name[:160]
        try:
            # Validate locally before queueing cloud work; no fake success for invalid inputs.
            await run_in_threadpool(render_input, bytes(chunks), name, rotation)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        with lock:
            pending = [
                p
                for p in root.glob("*/status.json")
                if json.loads(p.read_text()).get("status") not in {"ready", "review", "failed"}
            ]
            if len(pending) >= 3:
                raise HTTPException(
                    429, "Three drawings are already queued. Try again after completion."
                )
            job_id = uuid4().hex
            directory = root / job_id
            directory.mkdir()
            (directory / "original").write_bytes(chunks)
            (directory / "status.json").write_bytes(
                canonical_json(
                    {
                        "id": job_id,
                        "filename": name,
                        "status": "queued",
                        "message": "Your drawing is queued",
                        "model_available": False,
                        "download_available": False,
                        "partial": True,
                    }
                )
            )
        executor.submit(worker, directory, name, rotation, None if units == "auto" else units)
        return {"id": job_id}

    @app.get("/api/drawings/{job_id}")
    def status(job_id: str) -> Any:
        state = json.loads((job_path(job_id) / "status.json").read_text())
        if not state.get("revision_b") and state.get("status") == "ready":
            state["completion"] = "LEGACY_UNVERIFIED"
            state["message"] = (
                "Historical partial body. Revision B completeness checks have not run."
            )
        return state

    @app.get("/api/drawings/{job_id}/reviews")
    def get_reviews(job_id: str) -> Any:
        return decisions(job_path(job_id))

    @app.get("/api/drawings/{job_id}/versions")
    def versions(job_id: str) -> Any:
        return [json.loads(p.read_text()) for p in job_path(job_id).glob("models/*/manifest.json")]

    @app.post("/api/drawings/{job_id}/reviews", status_code=201)
    def review(job_id: str, decision: ReviewDecision, request: Request) -> Any:
        try:
            reviewer = authenticate_reviewer(request.headers.get("authorization"))
            with lock:
                return record_decision(job_path(job_id), decision, reviewer)
        except PermissionError as error:
            raise HTTPException(403, str(error)) from None
        except ValueError as error:
            raise HTTPException(409, str(error)) from None

    @app.post("/api/drawings/{job_id}/releases")
    def release(job_id: str, release_request: ReleaseRequest, request: Request) -> Any:
        try:
            reviewer = authenticate_reviewer(request.headers.get("authorization"))
            with lock:
                result = release_bundle(job_path(job_id), release_request, reviewer)
            return JSONResponse(result, status_code=409 if result["release"] == "BLOCKED" else 201)
        except PermissionError as error:
            raise HTTPException(403, str(error)) from None
        except ValueError as error:
            raise HTTPException(409, str(error)) from None

    @app.get("/api/drawings/{job_id}/files/{kind}")
    def artifact(job_id: str, kind: str) -> FileResponse:
        directory = job_path(job_id)
        state = json.loads((directory / "status.json").read_text())
        files = {
            "drawing": ("drawing.png", "image/png"),
            "mesh": ("mesh.json", "application/json"),
            "step": ("cad/evaluation-body.step", "application/step"),
            "stl": ("model.stl", "model/stl"),
            "report": (
                "audit.json" if (directory / "audit.json").exists() else "manifest.json",
                "application/json",
            ),
            "ledger": ("requirements.json", "application/json"),
            "inventory": ("evidence/inventory/result.json", "application/json"),
            "audit": ("audit.html", "text/html"),
            "drawing.png": ("drawing.png", "image/png"),
        }
        folder = state.get("model_folder")
        if folder and re.fullmatch(r"[a-f0-9]{32}", folder):
            prefix = f"models/{folder}/"
            files.update(
                {
                    "mesh": (prefix + "mesh.json", "application/json"),
                    "step": (prefix + "model.step", "application/step"),
                    "stl": (prefix + "model.stl", "model/stl"),
                    "spec": (prefix + "spec.json", "application/json"),
                    "checks": (prefix + "checks.json", "application/json"),
                    "model-report": (prefix + "model-report.json", "application/json"),
                    "model-ledger": (prefix + "ledger.json", "application/json"),
                    "model-manifest": (prefix + "manifest.json", "application/json"),
                    "associations": (prefix + "associations.json", "application/json"),
                    "sections": (prefix + "sections.json", "application/json"),
                    "section-XZ": (prefix + "section-XZ.svg", "image/svg+xml"),
                    "section-YZ": (prefix + "section-YZ.svg", "image/svg+xml"),
                }
            )
        if kind not in files:
            raise HTTPException(404, "File not found")
        if kind in {"step", "stl", "mesh"} or (
            kind == "report" and not (directory / "audit.json").exists()
        ):
            state = json.loads((directory / "status.json").read_text())
            if state.get("status") not in {"ready", "review"} or not state.get(
                "download_available"
            ):
                raise HTTPException(409, "Design is not ready to download")
        relative, mime = files[kind]
        path = directory / relative
        if not path.is_file():
            raise HTTPException(404, "File not ready")
        return FileResponse(
            path,
            media_type=mime,
            filename=f"UNVERIFIED-feature-draft-v{state.get('model_version', 0)}.{kind}"
            if kind in {"step", "stl"}
            else f"drawing-{kind}.json"
            if kind in {"report", "ledger", "inventory"}
            else None,
        )

    frontend = Path(__file__).resolve().parents[2] / "ui/dist"
    if frontend.is_dir():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return app


app = create_app()
