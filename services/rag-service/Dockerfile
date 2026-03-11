# RAG Service Dockerfile
# Python 3.11 with Haystack and LiteLLM

FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy shared lib first (path dep resolved at install time)
COPY lib/docintel-common/ /app/lib/docintel-common/

# Copy project files
COPY services/rag-service/pyproject.toml services/rag-service/README.md ./
COPY services/rag-service/src/ ./src/

# Install Python dependencies
RUN pip install --no-cache-dir pip --upgrade && \
    pip install --no-cache-dir /app/lib/docintel-common/ && \
    pip install --no-cache-dir .

# Non-root user for security.
# -m creates the home directory (/home/appuser) so Haystack telemetry and other
# libraries that write to ~/.config/ don't fail with PermissionError at startup.
RUN useradd -r -u 1001 -g root -m appuser
ENV HOME=/home/appuser
USER 1001

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run the application
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
