#!/bin/bash
# DocIntel Start Script
# =====================
# Starts all DocIntel services
#
# Usage:
#   ./scripts/start.sh                  # Start with authentication
#   ./scripts/start.sh --no-auth        # Start without authentication (dev mode)
#   ./scripts/start.sh --build          # Rebuild images before starting (after code changes)
#   ./scripts/start.sh --build --no-auth

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

DEFAULT_PASSWORD="${DOCINTEL_DEMO_PASSWORD:-DocIntel@123}"

# Parse arguments
NO_AUTH=false
DO_BUILD=false
for arg in "$@"; do
    case $arg in
        --no-auth)
            NO_AUTH=true
            shift
            ;;
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
    echo "Warning: Ollama is not running on http://localhost:11434"
    echo "Please start Ollama before using the application."
    echo ""
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
# Start Authentik (if enabled)
# =============================================================================

AUTH_SETUP_OK=false

if [ "$NO_AUTH" = false ]; then
    echo ""
    echo "Starting Authentik identity provider..."
    docker compose --profile auth up -d
    
    # Run setup script (it handles waiting, blueprint verification, and prints credentials)
    echo ""
    if "$SCRIPT_DIR/setup-authentik.sh"; then
        AUTH_SETUP_OK=true
    else
        echo ""
        echo "Warning: Authentik setup did not complete successfully."
        echo "Web UI will start in dev mode (no auth). Re-run setup once Authentik is ready:"
        echo "  ./scripts/setup-authentik.sh"
    fi
fi

# =============================================================================
# Start Application Services
# =============================================================================

echo ""
if [ "$DO_BUILD" = true ]; then
    echo "Building application services..."
    docker compose --profile app build
fi

echo "Starting application services..."

if [ "$NO_AUTH" = false ] && [ "$AUTH_SETUP_OK" = true ]; then
    PUBLIC_AUTH_ENABLED=true docker compose --profile app up -d
else
    PUBLIC_AUTH_ENABLED=false docker compose --profile app up -d
    if [ "$NO_AUTH" = false ]; then
        echo ""
        echo "Note: Auth was requested but Authentik isn't ready yet."
        echo "Web UI started in dev mode. Restart once Authentik is ready."
    fi
fi

# Wait for services
echo "Waiting for application services..."
sleep 5

echo -n "API Gateway: "
if curl -s http://localhost:8080/actuator/health > /dev/null 2>&1; then
    echo "ready"
else
    echo "starting..."
fi

# =============================================================================
# Done
# =============================================================================

echo ""
echo "================================================"
echo "  DocIntel Started"
echo "================================================"
echo ""
echo "  Service              URL                              Credentials"
echo "  -------------------  -------------------------------  -------------------------"
echo "  Web UI               http://localhost:3001"
echo "  API Gateway          http://localhost:8080"
echo "  Langfuse             http://localhost:3000             admin@docintel.local / admin123"
echo "  MinIO Console        http://localhost:9001             minioadmin / minioadmin"
echo "  Qdrant               http://localhost:6333"
echo ""

if [ "$NO_AUTH" = false ] && [ "$AUTH_SETUP_OK" = true ]; then
    echo "  Authentication: ENABLED"
    echo ""
    echo "  Authentik Admin      http://localhost:9090/if/admin/  akadmin / ${AUTHENTIK_ADMIN_PASSWORD:-DocIntel@123}"
    echo ""
    echo "  Demo Users (password: ${DEFAULT_PASSWORD}):"
    echo "    demo-admin   (tenant: default)"
    echo "    demo-user    (tenant: default)"
    echo "    tenant-user  (tenant: demo)"
else
    echo "  Authentication: DISABLED (dev mode)"
fi

echo ""
echo "  Commands:"
echo "    ./scripts/stop.sh              Stop (preserves containers)"
echo "    ./scripts/logs.sh debug         View logs (query path - for stuck queries)"
echo "    ./scripts/start.sh --build     Rebuild after code changes"
echo "    ./scripts/cleanup.sh --all     Remove everything"
echo ""
