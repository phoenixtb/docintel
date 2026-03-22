FROM python:3.11-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# ── Layer 1: service deps (only reruns when pyproject.toml changes) ────────────
COPY services/data-loader/pyproject.toml services/data-loader/README.md ./
RUN mkdir -p src && touch src/__init__.py
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system .

# ── Layer 2: actual source ─────────────────────────────────────────────────────
COPY services/data-loader/src/ ./src/

RUN useradd -r -u 1001 -g root -m appuser
USER 1001

EXPOSE 8002

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8002/health || exit 1

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8002"]
