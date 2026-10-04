FROM python:3.11-slim

WORKDIR /app

# Pull uv binary — needed to resolve the docintel-common path dependency
# (not on PyPI; pip has no way to install it without a local checkout).
COPY --from=ghcr.io/astral-sh/uv:0.12.22 /uv /usr/local/bin/uv

# This service depends on docintel-common without extras, so its lock (and image)
# carries no torch/transformers/psycopg2.

# ── Layer 2: locked service deps (reruns only when pyproject.toml / uv.lock change) ─
# Versions come from uv.lock, never from a fresh resolve — see
# scripts/docker/install-locked-deps.sh. The lib's pyproject.toml is enough for
# uv to validate the path dependency recorded in the lock.
COPY scripts/docker/install-locked-deps.sh /usr/local/bin/install-locked-deps
COPY services/analytics-service-py/pyproject.toml services/analytics-service-py/uv.lock /build/services/analytics-service-py/
COPY lib/docintel-common/pyproject.toml /build/lib/docintel-common/
RUN --mount=type=cache,target=/root/.cache/uv \
    install-locked-deps /build/services/analytics-service-py

# ── Layer 2b: local common lib (its own deps are already pinned above) ─────────
COPY lib/docintel-common/ /app/lib/docintel-common/
RUN uv pip install --system --no-deps /app/lib/docintel-common/ \
    && uv pip check --system

# ── Layer 3: actual source ────────────────────────────────────────────────────
COPY services/analytics-service-py/src/ ./src/

RUN useradd -r -u 1001 -g root appuser
USER 1001

EXPOSE 8001

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8001"]
