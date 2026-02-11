#!/bin/bash
# DocIntel Start Script
# =====================
# Starts the application (assumes setup.sh has been run once)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

echo "================================================"
echo "Starting DocIntel"
echo "================================================"

# =============================================================================
# Check Ollama
# =============================================================================

echo ""
echo "Checking Ollama..."

if ! curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo "⚠️  Ollama is not running!"
    echo ""
    
    if command -v ollama &> /dev/null; then
        echo "Ollama is installed but not running."
        echo "  - On macOS: Open the Ollama app from Applications"
        echo "  - On Linux: Run 'ollama serve' in a separate terminal"
        echo ""
        read -p "Press Enter once Ollama is running, or Ctrl+C to abort..."
        
        if ! curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
            echo "❌ Ollama is still not responding. Aborting."
            exit 1
        fi
    else
        echo "❌ Ollama is not installed. Run ./scripts/setup.sh first."
        exit 1
    fi
fi
echo "✅ Ollama is running"

# Check required models
REQUIRED_MODELS=("qwen3:4b" "nomic-embed-text")
MISSING_MODELS=()

for model in "${REQUIRED_MODELS[@]}"; do
    if ! ollama list 2>/dev/null | grep -q "^${model}"; then
        MISSING_MODELS+=("$model")
    fi
done

if [ ${#MISSING_MODELS[@]} -gt 0 ]; then
    echo "⚠️  Missing required models: ${MISSING_MODELS[*]}"
    echo "Run ./scripts/setup.sh to pull them."
    exit 1
fi
echo "✅ Required models available"

# =============================================================================
# Check Docker
# =============================================================================

echo ""
echo "Checking Docker..."

if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker is not running. Please start Docker Desktop."
    exit 1
fi
echo "✅ Docker is running"

# =============================================================================
# Start Infrastructure Services
# =============================================================================

echo ""
echo "Starting infrastructure services..."

docker compose up -d qdrant postgres redis minio clickhouse langfuse-worker langfuse-web

# Wait for critical services
echo "Waiting for services to be ready..."

# Qdrant
printf "  Qdrant: "
for i in {1..30}; do
    if curl -s http://localhost:6333/healthz > /dev/null 2>&1; then
        echo "✅"
        break
    fi
    if [ $i -eq 30 ]; then
        echo "❌ timeout"
        exit 1
    fi
    sleep 1
done

# PostgreSQL
printf "  PostgreSQL: "
for i in {1..30}; do
    if docker compose exec -T postgres pg_isready -U docintel > /dev/null 2>&1; then
        echo "✅"
        break
    fi
    if [ $i -eq 30 ]; then
        echo "❌ timeout"
        exit 1
    fi
    sleep 1
done

# =============================================================================
# Build and Start Application Services
# =============================================================================

echo ""
echo "Building and starting application services..."
docker compose --profile app up -d --build

# Wait for app services
echo "Waiting for application services..."

# RAG Service
printf "  RAG Service: "
for i in {1..60}; do
    if curl -s http://localhost:8000/health > /dev/null 2>&1; then
        echo "✅"
        break
    fi
    if [ $i -eq 60 ]; then
        echo "❌ timeout (check logs: docker compose logs rag-service)"
        exit 1
    fi
    sleep 2
done

# Web UI
printf "  Web UI: "
for i in {1..30}; do
    if curl -s http://localhost:3001 > /dev/null 2>&1; then
        echo "✅"
        break
    fi
    if [ $i -eq 30 ]; then
        echo "❌ timeout (check logs: docker compose logs web-ui)"
        exit 1
    fi
    sleep 2
done

# =============================================================================
# Open Browser
# =============================================================================

echo ""
echo "================================================"
echo "DocIntel is running!"
echo "================================================"
echo ""
echo "Services:"
echo "  Web UI:      http://localhost:3001"
echo "  API Gateway: http://localhost:8080"
echo "  RAG Service: http://localhost:8000/docs"
echo "  Langfuse:    http://localhost:3000"
echo ""

# Open browser
if [[ "$OSTYPE" == "darwin"* ]]; then
    open http://localhost:3001
elif command -v xdg-open &> /dev/null; then
    xdg-open http://localhost:3001
elif command -v wslview &> /dev/null; then
    wslview http://localhost:3001
fi

echo "To stop: docker compose --profile app down"
echo "To view logs: docker compose --profile app logs -f"
