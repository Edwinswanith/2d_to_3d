# Repository Guidelines

## Project Structure & Module Organization

This repository contains the `drawing2step` Python package and a small React/Three.js web UI.
Core Python code lives in `src/drawing2step/`, with CLI entry points in `cli.py`, FastAPI routes
in `web_api.py`, CAD/body generation in `body_cad.py`, and diagnostic/reconciliation logic in
specialized modules. Tests are in `tests/` and follow `test_*.py` naming. The frontend lives in
`ui/`, with source files in `ui/src/`. Reference docs are in `docs/`, sample inputs in `examples/`,
and generated local artifacts under ignored `work/`.

## Build, Test, and Development Commands

- `uv sync --locked`: install Python 3.12 dependencies from `uv.lock`.
- `uv run drawing2step demo --output work/lab`: run synthetic evaluation fixtures.
- `uv run uvicorn drawing2step.web_api:app --host 127.0.0.1 --port 8000`: run the local API.
- `npm ci --prefix ui`: install frontend dependencies.
- `npm run dev --prefix ui`: start Vite on port 5173 with `/api` proxied to port 8000.
- `npm run build --prefix ui`: type-check and build the frontend.
- `uv build`: build the Python package.

## Coding Style & Naming Conventions

Python targets 3.12, uses Ruff with a 100-character line length, and enables `E`, `F`, `I`, `UP`,
and `B` lint rules. Run `uv run ruff check .` and `uv run ruff format --check .` before handing
off changes. Mypy is strict; keep public data structures typed and prefer Pydantic models for
validated JSON-like data. Use snake_case for Python functions/modules and PascalCase for React
components such as `GenerationProgress.tsx`.

## Testing Guidelines

Use pytest for backend tests: `uv run pytest`. For coverage parity with the documented validation
flow, run `uv run pytest --cov=drawing2step --cov-report=term-missing --cov-fail-under=80`.
Tests should use synthetic data or temporary files only; do not require customer drawings,
network inference, or provider credentials. Name new tests `test_<behavior>.py` or
`test_<specific_case>` functions.

## Commit & Pull Request Guidelines

Recent commits use short imperative subjects, for example `Add generation progress and actionable profile review`.
Keep subjects specific and focused on the behavior changed. Pull requests should include a concise
summary, validation commands run, linked issue or context when available, and screenshots or screen
recordings for UI changes.

## Security & Configuration Tips

Store `GEMINI_API_KEY` in an ignored `.env` file or the environment; never commit credentials,
customer metadata, uploaded drawings, or generated `work/` outputs. The local prototype is intended
for localhost use, and generated CAD remains an unverified draft until explicitly reviewed.
