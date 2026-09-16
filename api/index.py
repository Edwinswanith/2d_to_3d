"""Vercel Python runtime entrypoint."""

from pathlib import Path

from drawing2step.web_api import create_app

app = create_app(root=Path("/tmp/drawing2step-web"))
