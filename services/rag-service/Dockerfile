# RAG Service Dockerfile
# Python 3.11 with Haystack and LiteLLM

FROM python:3.11-slim

WORKDIR /app

# Pull uv binary — 10-100x faster than pip
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

RUN apt-get update && apt-get install -y \
    curl \
    build-essential \
    git \
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
COPY services/rag-service/pyproject.toml services/rag-service/README.md ./
RUN mkdir -p src && touch src/__init__.py
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --no-sources .

# ── Layer 3: actual source (changes here don't bust dep layers above) ─────────
COPY services/rag-service/src/ ./src/

# ── Layer 4: pre-bake cross-encoder reranker model ───────────────────────────
# Run a full forward pass so the HF weights, ONNX Runtime op kernels, and BLAS
# parameters are compiled and cached inside the image layer.  At container
# start the model loads instantly — no runtime download, no cold compilation.
#
# HF_HOME is pinned to /root/.cache/huggingface here and carried into runtime
# (even when the process runs as appuser) so the baked cache is always found.
# Non-root user. -m creates home dir so Haystack/HF libs that write to ~/.config
# don't fail with PermissionError at startup.
RUN useradd -r -u 1001 -g root -m appuser
ENV HOME=/home/appuser
USER 1001

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
