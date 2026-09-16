"""Single-shop local web API. Not intended for public exposure without authentication."""

import json
import re
import threading
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from drawing2step.storage import canonical_json
from drawing2step.web_pipeline import MAX_FILE, process_drawing, render_input


def create_app(root: Path = Path("work/web")) -> FastAPI:
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
            process_drawing(
                directory, name, rotation, unit, lambda changes: update(directory, changes)
            )
        except Exception:
            # Keep provider request bodies, file contents, credentials and tracebacks private.
            update(
                directory,
                {
                    "status": "failed",
                    "message": "Could not build a body from this drawing. "
                    "Check orientation, units and readable dimensions, then try again.",
                    "download_available": False,
                    "model_available": False,
                },
            )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": "local-draft-workspace"}

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
        return json.loads((job_path(job_id) / "status.json").read_text())

    @app.get("/api/drawings/{job_id}/files/{kind}")
    def artifact(job_id: str, kind: str) -> FileResponse:
        directory = job_path(job_id)
        files = {
            "drawing": ("drawing.png", "image/png"),
            "mesh": ("mesh.json", "application/json"),
            "step": ("cad/evaluation-body.step", "application/step"),
            "stl": ("model.stl", "model/stl"),
            "report": ("manifest.json", "application/json"),
        }
        if kind not in files:
            raise HTTPException(404, "File not found")
        if kind in {"step", "stl", "mesh", "report"}:
            state = json.loads((directory / "status.json").read_text())
            if state.get("status") != "ready" or not state.get("download_available"):
                raise HTTPException(409, "Design is not ready to download")
        relative, mime = files[kind]
        path = directory / relative
        if not path.is_file():
            raise HTTPException(404, "File not ready")
        return FileResponse(
            path,
            media_type=mime,
            filename=f"body-draft.{kind}" if kind in {"step", "stl", "report"} else None,
        )

    frontend = Path(__file__).resolve().parents[2] / "ui/dist"
    if frontend.is_dir():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return app


app = create_app()
