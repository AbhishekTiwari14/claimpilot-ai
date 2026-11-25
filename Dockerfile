FROM node:24-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json frontend/
RUN npm ci --prefix frontend --include=dev
COPY frontend/ frontend/
COPY data/knowledge/ data/knowledge/
RUN npm run --prefix frontend build

FROM ghcr.io/astral-sh/uv:0.13.7 AS uv

FROM python:3.11-slim-bookworm AS runtime
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    CLAIMPILOT_SPA_DIR=/app/frontend/dist \
    CLAIMPILOT_INTAKE_DB=/runtime/intake.sqlite3 \
    CLAIMPILOT_KB_CHROMA=/runtime/chroma \
    CLAIMPILOT_KB_MODEL_CACHE=/runtime/embeddings

RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 claimpilot \
    && useradd --uid 10001 --gid claimpilot --create-home claimpilot \
    && install -d -o claimpilot -g claimpilot /runtime
COPY pyproject.toml uv.lock ./
COPY --from=uv /uv /usr/local/bin/uv
RUN UV_NO_CACHE=1 UV_PYTHON_DOWNLOADS=never uv sync --locked --no-dev --group agents --no-install-projectt
COPY claimpilot/ claimpilot/
COPY data/contractors/ data/contractors/
COPY data/knowledge/ data/knowledge/
COPY --from=frontend /build/frontend/dist/ frontend/dist/

USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=30s \
    CMD ["python", "-c", "from urllib.request import urlopen; urlopen('http://127.0.0.1:8000/health', timeout=2).close()"]
CMD ["uvicorn", "claimpilot.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
