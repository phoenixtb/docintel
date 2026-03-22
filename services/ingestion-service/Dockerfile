FROM python:3.11-slim

WORKDIR /app

# Pull uv binary — 10-100x faster than pip
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

RUN apt-get update && apt-get install -y \
    curl \
    build-essential \
    git \
    libglib2.0-0 \
    libgl1 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# ── Layer 1: local common lib (not on PyPI) ──────────────────────────────────
COPY lib/docintel-common/ /app/lib/docintel-common/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system /app/lib/docintel-common/

# ── Layer 2: service deps (only reruns when pyproject.toml changes) ───────────
# Cache mount = persistent host-side package cache across builds (like ~/.gradle).
# uv downloads each package once; subsequent builds — even after pyproject.toml
# changes — pull from the local cache, not from PyPI.
# Stub src/__init__.py lets uv resolve deps from pyproject.toml without real source.
# --no-sources tells uv to ignore [tool.uv.sources] path overrides (docintel-common
# is already installed above so the resolver finds it satisfied).
COPY services/ingestion-service/pyproject.toml services/ingestion-service/README.md ./
RUN mkdir -p src && touch src/__init__.py
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --no-sources .

# ── Layer 3: baked assets (cached; only reruns when deps layer above changes) ─
# Pre-download bert-base-uncased tokenizer vocabulary files (~700KB) at build time.
# TokenAwareSplitter uses this tokenizer — the exact one nomic-embed-text uses —
# to count BERT tokens before chunking.
RUN python3 -c "from transformers import AutoTokenizer; AutoTokenizer.from_pretrained('bert-base-uncased')"

# Docling models are cached in a Docker volume (DOCLING_ARTIFACTS_PATH).
ENV DOCLING_ARTIFACTS_PATH=/app/docling-cache
RUN mkdir -p /app/docling-cache

# ── Layer 4: actual source (changes here don't bust dep layers above) ─────────
COPY services/ingestion-service/src/ ./src/

RUN useradd -r -u 1001 -g root -m appuser && \
    chown -R appuser /app/docling-cache
ENV HOME=/home/appuser
USER 1001

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -f http://localhost:8001/health || exit 1

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8001"]
