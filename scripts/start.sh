#!/bin/bash
# DocIntel Start Script
# =====================
# Starts all DocIntel services
#
# Usage:
#   ./scripts/start.sh          # Start with authentication
#   ./scripts/start.sh --build  # Rebuild images before starting (after code changes)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Load model defaults (single source of truth)
# shellcheck source=../config/defaults.env
source "$PROJECT_DIR/config/defaults.env"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# Parse arguments
DO_BUILD=false
for arg in "$@"; do
    case $arg in
        --build)
            DO_BUILD=true
            shift
            ;;
    esac
done

echo "================================================"
echo "Starting DocIntel"
echo "================================================"

# =============================================================================
# Check Ollama
# =============================================================================

if ! curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo ""
    echo "Warning: Ollama is not running!"
    echo ""
    if command -v ollama &> /dev/null; then
        echo "Ollama is installed but not running."
        echo "  - On macOS: Open the Ollama app from Applications"
        echo "  - On Linux: Run 'ollama serve' in a separate terminal"
        echo ""
        read -p "Press Enter once Ollama is running, or Ctrl+C to abort..."
        if ! curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
            echo "Ollama is still not responding. Aborting."
            exit 1
        fi
    else
        echo "Ollama is not installed. Run ./scripts/setup.sh first."
        exit 1
    fi
fi

REQUIRED_MODELS=("$DEFAULT_LLM_MODEL" "$DEFAULT_EMBED_MODEL")
MISSING_MODELS=()
for model in "${REQUIRED_MODELS[@]}"; do
    if ! ollama list 2>/dev/null | grep -q "^${model}"; then
        MISSING_MODELS+=("$model")
    fi
done
if [ ${#MISSING_MODELS[@]} -gt 0 ]; then
    echo "Missing required models: ${MISSING_MODELS[*]}"
    echo "Run ./scripts/setup.sh to pull them."
    exit 1
fi

# =============================================================================
# Start Infrastructure Services
# =============================================================================

echo "Starting infrastructure services..."
docker compose up -d qdrant postgres redis minio clickhouse langfuse-worker langfuse-web

# Wait for Qdrant
echo "Waiting for Qdrant..."
until curl -s http://localhost:6333/healthz > /dev/null 2>&1; do
    sleep 1
done
echo "Qdrant is ready."

# Wait for PostgreSQL
echo "Waiting for PostgreSQL..."
until docker compose exec -T postgres pg_isready -U docintel > /dev/null 2>&1; do
    sleep 1
done
echo "PostgreSQL is ready."

# Wait for Redis
echo "Waiting for Redis..."
until docker compose exec -T redis redis-cli -a redissecret ping > /dev/null 2>&1; do
    sleep 1
done
echo "Redis is ready."

# =============================================================================
# Initialize Qdrant Collections (first run only)
# =============================================================================

COLLECTIONS_EXIST=$(curl -s http://localhost:6333/collections | grep -c '"documents"' || echo "0")

if [ "$COLLECTIONS_EXIST" = "0" ]; then
    echo ""
    echo "Initializing Qdrant collections (first run)..."
    if [ -f "$PROJECT_DIR/config/qdrant/init-collections.sh" ]; then
        chmod +x "$PROJECT_DIR/config/qdrant/init-collections.sh"
        "$PROJECT_DIR/config/qdrant/init-collections.sh"
    fi
fi

# =============================================================================
# Start All Services (including Zitadel)
# =============================================================================

echo ""
if [ "$DO_BUILD" = true ]; then
    echo "Building application services..."
    docker compose build
fi

echo "Starting all services..."
docker compose up -d

# Run Zitadel setup script after services are up
# (handles waiting for Zitadel first-instance init, creates project/users/action, writes .env)
echo ""
if ! "$SCRIPT_DIR/setup-zitadel.sh"; then
    echo ""
    echo "Error: Zitadel setup did not complete successfully."
    echo "Authentication is required. Fix the issue and re-run:"
    echo "  ./scripts/setup-zitadel.sh"
    echo "  docker compose restart web-ui"
    exit 1
fi

# Wait for services
echo "Waiting for application services..."
sleep 5

echo -n "  API Gateway: "
if curl -s http://localhost:8080/actuator/health > /dev/null 2>&1; then
    echo "ready"
else
    echo "starting..."
fi

printf "  Ingestion Service: "
for i in {1..60}; do
    if curl -s http://localhost:8001/health > /dev/null 2>&1; then
        echo "ready"
        break
    fi
    if [ $i -eq 60 ]; then
        echo "timeout (check logs: docker compose logs ingestion-service)"
    fi
    sleep 2
done

printf "  Infinity Reranker: "
for i in {1..60}; do
    if curl -s http://localhost:7997/health > /dev/null 2>&1; then
        echo "ready"
        break
    fi
    if [ $i -eq 60 ]; then
        echo "timeout (check logs: docker compose logs infinity)"
    fi
    sleep 2
done

printf "  RAG Service: "
for i in {1..60}; do
    if curl -s http://localhost:8000/health > /dev/null 2>&1; then
        echo "ready"
        break
    fi
    if [ $i -eq 60 ]; then
        echo "timeout (check logs: docker compose logs rag-service)"
    fi
    sleep 2
done

printf "  Web UI: "
for i in {1..30}; do
    if curl -s http://localhost:3001 > /dev/null 2>&1; then
        echo "ready"
        break
    fi
    if [ $i -eq 30 ]; then
        echo "timeout (check logs: docker compose logs web-ui)"
    fi
    sleep 2
done

# =============================================================================
# Done
# =============================================================================

if [[ "$OSTYPE" == "darwin"* ]]; then
    open http://localhost:3001
elif command -v xdg-open &> /dev/null; then
    xdg-open http://localhost:3001
elif command -v wslview &> /dev/null; then
    wslview http://localhost:3001
fi

echo ""
echo "================================================"
echo "  DocIntel Started"
echo "================================================"
echo ""
  echo "  Service              URL                              Credentials"
  echo "  -------------------  -------------------------------  -------------------------"
  echo "  Web UI               http://localhost:3001"
  echo "  API Gateway          http://localhost:8080"
  echo "  Ingestion Service    http://localhost:8001"
  echo "  Infinity Reranker    http://localhost:7997"
  echo "  RAG Service          http://localhost:8000"
  echo "  Langfuse             http://localhost:3000             admin@docintel.local / admin123"
  echo "  MinIO Console        http://localhost:9001             minioadmin / minioadmin"
  echo "  Qdrant               http://localhost:6333"
echo ""

echo "  Authentication: ENABLED (Zitadel)"
echo ""
echo "  Zitadel UI     http://localhost:9090         ${ZITADEL_ADMIN_USERNAME:-zitadel-admin}@docintel.localhost / ${ZITADEL_ADMIN_PASSWORD:-DocIntel@zitadel2024!}"
echo ""
echo "  Users:"
echo "    diadmin     / Diadmin@123     — Platform Admin    (platform_admin, org: platform)"
echo "    alphaadmin  / Alphaadmin@123  — Alpha Tenant Admin (tenant_admin,  org: alpha)"
echo "    alphauser   / Alphauser@123   — Alpha Tenant User  (tenant_user,   org: alpha)"
echo "    betaadmin   / Betaadmin@123   — Beta Tenant Admin  (tenant_admin,  org: beta)"
echo "    betauser    / Betauser@123    — Beta Tenant User   (tenant_user,   org: beta)"

echo ""
echo "  Commands:"
echo "    ./scripts/stop.sh              Stop (preserves containers)"
echo "    ./scripts/logs.sh debug         View logs (query path - for stuck queries)"
echo "    ./scripts/start.sh --build      Rebuild after code changes"
echo "    ./scripts/cleanup.sh --all     Remove everything"
echo ""
