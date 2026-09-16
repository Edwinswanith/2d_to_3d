"""Publish the Vite build to Vercel's root static directory."""

import shutil
from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = root / "ui" / "dist"
if not (source / "index.html").is_file():
    raise SystemExit("Build ui/ before publishing frontend assets")
shutil.copytree(source, root / "public", dirs_exist_ok=True)
shutil.copytree(source, root / "api" / "frontend", dirs_exist_ok=True)
print("Frontend published to public/ and bundled in api/frontend/")
