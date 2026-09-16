FROM node:22-bookworm-slim AS frontend
WORKDIR /app/ui
COPY ui/package*.json ./
RUN npm ci
COPY ui/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.6.16 /uv /usr/local/bin/uv
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --locked --no-dev --extra cad --extra cloud
COPY --from=frontend /app/ui/dist ./ui/dist
RUN useradd --create-home app && mkdir -p /app/work && chown -R app:app /app/work
USER app
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
# Local single-worker evidence workspace. Shared/public use still requires review auth.
CMD ["uvicorn", "drawing2step.web_api:app", "--host", "0.0.0.0", "--port", "8000"]
