FROM python:3.11-slim

WORKDIR /app

# Hardware profile build arguments.
# TORCH_INDEX selects the PyTorch wheel channel (cpu / cu126 / cu128 / cu129 / cu130).
# Default is CPU-only — keeps images ~3.4 GB lighter on hosts without GPU.
# torch is a transitive dependency (datasets → transformers → torch); pre-installing
# the CPU wheel here prevents the CUDA-bundled PyPI default from being pulled.
# scripts/build.sh sets this automatically based on detected hardware.
ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu
ARG TORCH_VERSION=2.11.0+cpu

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# ── Layer 1: pre-install torch from the hardware-specific wheel index ─────────
# MUST run before docintel-common and project deps: those pull torch transitively
# (datasets → transformers → torch), and if installed first they drag in the
# CUDA-bundled PyPI torch (~5 GB of nvidia-* wheels) that the later pin can't
# remove. Pinning torch here first means everything downstream finds it satisfied.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system \
        --index-strategy unsafe-best-match \
        --index-url "${TORCH_INDEX}" \
        --extra-index-url https://pypi.org/simple \
        "torch==${TORCH_VERSION}"

# ── Layer 1b: local common lib ───────────────────────────────────────────────
COPY lib/docintel-common/ /app/lib/docintel-common/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system /app/lib/docintel-common/

# ── Layer 2: service deps (only reruns when pyproject.toml changes) ────────────
COPY services/data-loader/pyproject.toml services/data-loader/README.md ./
RUN mkdir -p src && touch src/__init__.py
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --no-sources .

# ── Layer 3: actual source ─────────────────────────────────────────────────────
COPY services/data-loader/src/ ./src/

ENV HF_HOME=/opt/hf-cache
RUN mkdir -p /opt/hf-cache && chmod 0777 /opt/hf-cache

RUN useradd -r -u 1001 -g root -m appuser
ENV HOME=/home/appuser
USER 1001

EXPOSE 8002

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8002/health || exit 1

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8002"]
