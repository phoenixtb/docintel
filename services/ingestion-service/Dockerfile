FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    curl \
    build-essential \
    git \
    libglib2.0-0 \
    libgl1 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY lib/docintel-common/ /app/lib/docintel-common/
COPY services/ingestion-service/pyproject.toml services/ingestion-service/README.md ./
COPY services/ingestion-service/src/ ./src/

RUN pip install --no-cache-dir pip --upgrade && \
    pip install --no-cache-dir /app/lib/docintel-common/ && \
    pip install --no-cache-dir .

# Docling models are cached in a Docker volume (DOCLING_ARTIFACTS_PATH).
# On first startup the models are downloaded and cached; subsequent startups reuse the cache.
ENV DOCLING_ARTIFACTS_PATH=/app/docling-cache
RUN mkdir -p /app/docling-cache

RUN useradd -r -u 1001 -g root -m appuser && \
    chown -R appuser /app/docling-cache
ENV HOME=/home/appuser
USER 1001

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -f http://localhost:8001/health || exit 1

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8001"]
