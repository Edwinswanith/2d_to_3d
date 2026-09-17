"""Retired: no deployment entrypoint imports this module.

`api/index.py` served this app on Vercel; it now serves `web_api.create_app` (Revision B)
instead, so a feature-complete job can never silently land on this exporter, which
classifies holes/ports/slots/threads as unsupported and cannot satisfy Revision B checks.
Kept for historical reference and its own tests only. Do not wire it back into a deployment
entrypoint without also giving it Revision B's feature construction and checks.
"""

import os
import re
import shutil
import tempfile
import threading
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from drawing2step.storage import canonical_json
from drawing2step.web_pipeline import MAX_FILE, process_drawing, render_input
from drawing2step.web_storage import (
    LocalWebStorage,
    WebStorage,
    default_web_storage,
    get_json,
    put_json,
)


def _csv_env(name: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, "").split(",") if item.strip()]


def create_app(
    root: Path = Path("work/web"),
    storage: WebStorage | None = None,
    frontend: Path | None = None,
) -> FastAPI:
    default_root = Path("work/web")
    if root == default_root and os.getenv("VERCEL"):
        root = Path("/tmp/drawing2step-web")
    store = storage or (
        default_web_storage(root)
        if root == default_root or os.getenv("VERCEL")
        else LocalWebStorage(root)
    )
    lock = threading.Lock()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cad-draft")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        for job_id, state in store.list_statuses():
            if state.get("status") not in {"ready", "review", "failed"}:
                state.update(status="failed", message="Server restarted; please upload again")
                put_json(store, job_id, "status.json", state)
        yield
        executor.shutdown(wait=True)

    app = FastAPI(title="Drawing-to-STEP local workspace", lifespan=lifespan)
    allowed_hosts = [
        "localhost",
        "127.0.0.1",
        "testserver",
        "*.vercel.app",
        *_csv_env("DRAWING2STEP_ALLOWED_HOSTS"),
    ]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

    @app.middleware("http")
    async def check_origin(request: Request, call_next: Any) -> Any:
        if request.method == "POST":
            allowed = {
                f"http://{host}:{port}"
                for host in ("localhost", "127.0.0.1")
                for port in (8000, 5173)
            }
            host = request.headers.get("host")
            scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
            if host:
                allowed.add(f"{scheme}://{host}")
            allowed.update(_csv_env("DRAWING2STEP_ALLOWED_ORIGINS"))
            origin = request.headers.get("origin")
            if (origin and origin not in allowed) or request.headers.get(
                "sec-fetch-site"
            ) == "cross-site":
                return JSONResponse(
                    {"detail": "Upload must originate from the local workspace"}, status_code=403
                )
        return await call_next(request)

    def valid_job(job_id: str) -> str:
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise HTTPException(404, "Drawing not found")
        if not store.exists(job_id, "status.json"):
            raise HTTPException(404, "Drawing not found")
        return job_id

    def update(job_id: str, directory: Path, changes: dict[str, Any]) -> None:
        with lock:
            state = (
                get_json(store, job_id, "status.json")
                if store.exists(job_id, "status.json")
                else {}
            )
            state.update(changes)
            (directory / "status.json").write_bytes(canonical_json(state))
            store.publish_tree(job_id, directory)
            put_json(store, job_id, "status.json", state)

    def worker(job_id: str, directory: Path, name: str, rotation: int, unit: Any) -> None:
        try:
            process_drawing(
                directory, name, rotation, unit, lambda changes: update(job_id, directory, changes)
            )
            store.publish_tree(job_id, directory)
        except Exception:
            # Keep provider request bodies, file contents, credentials and tracebacks private.
            update(
                job_id,
                directory,
                {
                    "status": "failed",
                    "message": "Could not build a body from this drawing. "
                    "Check orientation, units and readable dimensions, then try again.",
                    "download_available": False,
                    "model_available": False,
                },
            )
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": "legacy-faceted-body-workspace"}

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
                state
                for _, state in store.list_statuses()
                if state.get("status") not in {"ready", "review", "failed"}
            ]
            if len(pending) >= 3:
                raise HTTPException(
                    429, "Three drawings are already queued. Try again after completion."
                )
            job_id = uuid4().hex
            directory = Path(tempfile.mkdtemp(prefix=f"drawing2step-{job_id}-"))
            (directory / "original").write_bytes(chunks)
            state = {
                "id": job_id,
                "filename": name,
                "status": "queued",
                "message": "Your drawing is queued",
                "model_available": False,
                "download_available": False,
                "partial": True,
            }
            (directory / "status.json").write_bytes(canonical_json(state))
            store.put_bytes(job_id, "original", bytes(chunks), "application/octet-stream")
            put_json(store, job_id, "status.json", state)
        executor.submit(
            worker, job_id, directory, name, rotation, None if units == "auto" else units
        )
        return {"id": job_id}

    @app.get("/api/drawings/{job_id}")
    def status(job_id: str) -> Any:
        job_id = valid_job(job_id)
        state = get_json(store, job_id, "status.json")
        return {**state, "revision_b": False, "completion": "LEGACY_UNVERIFIED"}

    @app.get("/api/drawings/{job_id}/files/{kind}")
    def artifact(job_id: str, kind: str) -> Response:
        job_id = valid_job(job_id)
        files = {
            "drawing": ("drawing.png", "image/png"),
            "mesh": ("mesh.json", "application/json"),
            "step": ("cad/evaluation-body.step", "application/step"),
            "stl": ("model.stl", "model/stl"),
            "report": ("manifest.json", "application/json"),
        }
        if kind not in files:
            raise HTTPException(404, "File not found")
        if kind in {"step", "stl", "mesh"}:
            state = get_json(store, job_id, "status.json")
            if state.get("status") != "ready" or not state.get("download_available"):
                raise HTTPException(409, "Design is not ready to download")
        if kind == "report":
            state = get_json(store, job_id, "status.json")
            if state.get("status") not in {"ready", "review"}:
                raise HTTPException(409, "Report is not ready to download")
        relative, mime = files[kind]
        if not store.exists(job_id, relative):
            if kind == "report":
                state = get_json(store, job_id, "status.json")
                if state.get("status") == "review":
                    return Response(
                        canonical_json(
                            {
                                "artifact_kind": "MANUAL_PROFILE_REVIEW",
                                "release": "BLOCKED",
                                "status": state,
                            }
                        ),
                        media_type=mime,
                        headers={"Content-Disposition": 'attachment; filename="body-draft.report"'},
                    )
            raise HTTPException(404, "File not ready")
        headers = {}
        if kind in {"step", "stl", "report"}:
            headers["Content-Disposition"] = f'attachment; filename="body-draft.{kind}"'
        return Response(
            store.get_bytes(job_id, relative),
            media_type=mime,
            headers=headers,
        )

    if frontend is None:
        candidates = [Path.cwd() / "ui/dist", Path(__file__).resolve().parents[2] / "ui/dist"]
        frontend = next((path for path in candidates if (path / "index.html").is_file()), None)
    if frontend is not None:
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return app


app = create_app()
