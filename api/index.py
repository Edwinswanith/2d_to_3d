"""Vercel Python runtime entrypoint."""

from pathlib import Path

from drawing2step.web_api import create_app
from drawing2step.web_storage import default_web_storage

root = Path("/tmp/drawing2step-web")
app = create_app(
    root=root,
    storage=default_web_storage(root),
    frontend=Path(__file__).resolve().parent / "frontend",
)
