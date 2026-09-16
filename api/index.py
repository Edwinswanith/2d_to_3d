"""Vercel Python runtime entrypoint."""

from drawing2step.web_api import create_app

app = create_app()
