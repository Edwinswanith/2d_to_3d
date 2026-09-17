from fastapi.testclient import TestClient

from drawing2step.legacy_web_api import create_app
from drawing2step.web_storage import LocalWebStorage


def test_vercel_bundled_frontend_preserves_api_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    frontend = tmp_path / "api" / "frontend"
    (frontend / "assets").mkdir(parents=True)
    (frontend / "index.html").write_text('<html><script src="/assets/app.js"></script></html>')
    (frontend / "assets" / "app.js").write_text("console.log('workspace')")
    monkeypatch.chdir(tmp_path)
    with TestClient(
        create_app(tmp_path / "jobs", storage=LocalWebStorage(tmp_path / "jobs"), frontend=frontend)
    ) as client:
        for path in ("/", "/index.html", "/?drawing=" + "a" * 32):
            response = client.get(path)
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/html")
        assert client.get("/assets/app.js").text == "console.log('workspace')"
        response = client.get("/api/drawings/" + "a" * 32)
        assert response.status_code == 404
        assert response.json()["detail"] != "Not Found"


def test_frontend_resolved_from_project_root(tmp_path, monkeypatch):
    frontend = tmp_path / "ui" / "dist"
    frontend.mkdir(parents=True)
    (frontend / "index.html").write_text("<html>Workspace</html>")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VERCEL", raising=False)
    with TestClient(create_app(tmp_path / "jobs")) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "Workspace" in response.text


def test_legacy_runtime_labels_persisted_drafts_and_has_no_revb_build_route(tmp_path):
    from drawing2step.web_storage import put_json

    store = LocalWebStorage(tmp_path)
    job = "a" * 32
    put_json(
        store,
        job,
        "status.json",
        {"id": job, "status": "ready", "model_available": True, "download_available": True},
    )
    store.put_bytes(job, "cad/evaluation-body.step", b"legacy-fixture-only", "application/step")
    with TestClient(create_app(tmp_path, storage=store)) as client:
        assert client.get("/api/health").json()["mode"] == "legacy-faceted-body-workspace"
        state = client.get(f"/api/drawings/{job}").json()
        assert state["completion"] == "LEGACY_UNVERIFIED"
        assert state["revision_b"] is False
        assert client.get(f"/api/drawings/{job}/files/step").content == b"legacy-fixture-only"
        assert client.post(
            f"/api/drawings/{job}/build", json={"expected_version": 0}
        ).status_code in {404, 405}
        assert "/api/drawings/{job_id}/build" not in client.get("/openapi.json").json()["paths"]


# The Vercel entrypoint (api/index.py) is retired from this app: see test_web.py's
# test_vercel_entrypoint_runs_revision_b_not_the_legacy_workspace for what it does instead.
