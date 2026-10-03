FROM python:3.11-slim

WORKDIR /app

# Pull uv binary — needed to resolve the docintel-common path dependency
# (not on PyPI; pip has no way to install it without a local checkout).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# ── Layer 1: local common lib (not on PyPI) ──────────────────────────────────
# Only pulls docintel-common's minimal base deps — this service never imports
# the ml/db submodules, so torch/transformers/psycopg2 extras stay uninstalled.
COPY lib/docintel-common/ /app/lib/docintel-common/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system /app/lib/docintel-common/

# ── Layer 2: service deps (only reruns when pyproject.toml changes) ──────────
# --no-sources ignores [tool.uv.sources]' path override for docintel-common
# (already installed above, so the resolver finds it satisfied).
COPY services/analytics-service-py/pyproject.toml ./
RUN mkdir -p src && touch src/__init__.py
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --no-sources .

# ── Layer 3: actual source ────────────────────────────────────────────────────
COPY services/analytics-service-py/src/ ./src/

RUN useradd -r -u 1001 -g root appuser
USER 1001

EXPOSE 8001

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8001"]
