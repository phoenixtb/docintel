FROM python:3.11-slim

WORKDIR /app

# Hardware profile build arguments.
# TORCH_INDEX selects the PyTorch wheel channel (cpu / cu126 / cu128 / cu129 / cu130).
# Default is CPU-only — keeps images ~3.4 GB lighter on hosts without GPU.
# scripts/build.sh sets this automatically based on detected hardware.
ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu
ARG TORCH_VERSION=2.11.0+cpu

# Pull uv binary — 10-100x faster than pip
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

RUN apt-get update && apt-get install -y \
    curl \
    build-essential \
    git \
    libglib2.0-0 \
    libgl1 \
    libgomp1 \
    tesseract-ocr \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

# ── Layer 1: pre-install the torch family from the hardware-specific index ────
# MUST run before docintel-common and project deps: those depend on torch, and
# if installed first they pull the CUDA-bundled PyPI torch (~5 GB of nvidia-*
# wheels). Pinning torch here first means everything downstream finds it already
# satisfied. torchvision is pulled transitively by docling — pin it to the SAME
# wheel channel so their compiled ABIs match (otherwise: "operator
# torchvision::nms does not exist"). torchvision is left unpinned; uv resolves
# the build matching torch from the same index. --extra-index-url pypi lets all
# other packages resolve from PyPI normally.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system \
        --index-strategy unsafe-best-match \
        --index-url "${TORCH_INDEX}" \
        --extra-index-url https://pypi.org/simple \
        "torch==${TORCH_VERSION}" torchvision

# ── Layer 1b: local common lib (not on PyPI) ─────────────────────────────────
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
# Set HF_HOME to a neutral path that is not tied to any user's $HOME.
# This ensures the bake step, the Docker volume mount, and the runtime lookup
# all resolve to the same directory, regardless of which user the process runs as.
ENV HF_HOME=/opt/hf-cache
RUN mkdir -p /opt/hf-cache && chmod 0777 /opt/hf-cache

# Pre-download bert-base-uncased tokenizer vocabulary files (~700KB) at build time.
# TokenAwareSplitter uses this tokenizer — the exact one nomic-embed-text uses —
# to count BERT tokens before chunking.
# With HF_HOME set, files land in /opt/hf-cache and are found at runtime.
RUN python3 -c "from transformers import AutoTokenizer; AutoTokenizer.from_pretrained('bert-base-uncased')"
# Make the baked cache world-writable so that appuser (uid 1001) and any other
# service sharing this volume can write additional models into sub-directories.
# Without this, HF hub creates 'hub/' owned by root (0755), blocking writes at runtime.
RUN chmod -R 0777 /opt/hf-cache

# Bake Docling layout (~280MB) + tableformer (~70MB) models into the image.
# RapidOCR ONNX models are bundled with the rapidocr-onnxruntime package itself
# so no separate download step is needed for OCR models.
ENV DOCLING_ARTIFACTS_PATH=/app/docling-cache
RUN mkdir -p /app/docling-cache && \
    docling-tools models download layout tableformer \
        -o /app/docling-cache --quiet

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
