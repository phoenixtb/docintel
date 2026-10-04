# RAG Service Dockerfile
# Python 3.11 with Haystack and LiteLLM

FROM python:3.11-slim

WORKDIR /app

# Hardware profile build arguments.
# TORCH_INDEX selects the PyTorch wheel channel (cpu / cu126 / cu128 / cu129 / cu130).
# Default is CPU-only — keeps images ~3.4 GB lighter on hosts without GPU.
# scripts/build.sh sets this automatically based on detected hardware.
ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu
ARG TORCH_VERSION=2.11.0+cpu

# Pull uv binary — 10-100x faster than pip
COPY --from=ghcr.io/astral-sh/uv:0.12.22 /uv /usr/local/bin/uv

RUN apt-get update && apt-get install -y \
    curl \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

# ── Layer 1: pre-install torch from the hardware-specific wheel index ─────────
# MUST run before docintel-common and project deps: those depend on torch, and
# if installed first they pull the CUDA-bundled PyPI torch (~5 GB of nvidia-*
# wheels) that the later pin can't remove. Pinning torch here first means
# everything downstream finds it already satisfied. --extra-index-url pypi lets
# all other packages resolve from PyPI normally.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system \
        --index-strategy unsafe-best-match \
        --index-url "${TORCH_INDEX}" \
        --extra-index-url https://pypi.org/simple \
        "torch==${TORCH_VERSION}"

# ── Layer 2: locked service deps (reruns only when pyproject.toml / uv.lock change) ─
# Versions come from uv.lock, never from a fresh resolve — see
# scripts/docker/install-locked-deps.sh. The lib's pyproject.toml is enough for
# uv to validate the path dependency recorded in the lock.
COPY scripts/docker/install-locked-deps.sh /usr/local/bin/install-locked-deps
COPY services/rag-service/pyproject.toml services/rag-service/uv.lock /build/services/rag-service/
COPY lib/docintel-common/pyproject.toml /build/lib/docintel-common/
RUN --mount=type=cache,target=/root/.cache/uv \
    install-locked-deps /build/services/rag-service

# ── Layer 2b: local common lib (its own deps are already pinned above) ─────────
COPY lib/docintel-common/ /app/lib/docintel-common/
RUN uv pip install --system --no-deps /app/lib/docintel-common/ \
    && uv pip check --system

# ── Layer 3: actual source (changes here don't bust dep layers above) ─────────
COPY services/rag-service/src/ ./src/

# ── Layer 4: non-root user ────────────────────────────────────────────────────
# Set HF_HOME to a neutral path so the Docker volume mount resolves correctly
# for appuser regardless of $HOME. Must be set before USER switch.
ENV HF_HOME=/opt/hf-cache
RUN mkdir -p /opt/hf-cache && chmod 0777 /opt/hf-cache

# -m creates home dir so Haystack/HF libs that write to ~/.config
# don't fail with PermissionError at startup.
# Reranker runs in the Infinity sidecar (no model weights in this image).
RUN useradd -r -u 1001 -g root -m appuser
ENV HOME=/home/appuser
USER 1001

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
