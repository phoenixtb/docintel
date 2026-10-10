#!/bin/bash
# ==============================================================================
# DocIntel Log Viewer
# ==============================================================================
# View logs for debugging. Use arrow keys to select, enter to follow.
#
# Usage:
#   ./scripts/logs.sh                    # Interactive menu
#   ./scripts/logs.sh debug              # rag-service + api-gateway (query path)
#   ./scripts/logs.sh rag-service        # Follow a specific service
#   ./scripts/logs.sh clear              # Clear all service logs (recreates containers)
#   ./scripts/logs.sh clear rag-service  # Clear logs for one service
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

# App services whose log buffers "clear" resets (infrastructure is left alone).
APP_SERVICES=(rag-service ingestion-service api-gateway document-service web-ui admin-service analytics-service data-loader docintel-actions)

# Resolve the same compose file chain and image tags start.sh uses (GPU overlay,
# DOCINTEL_DATA_DIR storage overlay, PROFILE_TAG). Recreating containers from the
# base file alone would drop those overlays — with DOCINTEL_DATA_DIR set the
# services would come back on empty named volumes.
load_compose_env() {
    set -a
    # shellcheck source=../config/defaults.env
    source "$PROJECT_DIR/config/defaults.env"
    [ -f "$PROJECT_DIR/.env" ] && source "$PROJECT_DIR/.env"
    set +a
    # shellcheck source=lib/profile_config.sh
    source "$SCRIPT_DIR/lib/profile_config.sh"
    read_profile > /dev/null
    torch_vars_for_profile "$PROFILE"
    compose_file_chain "$PROJECT_DIR"
}

# Recreate (only) the given running app containers: a fresh container starts
# with an empty log buffer. Services that are not running stay stopped.
clear_logs() {
    local targets=("$@") running=() svc
    if [ ${#targets[@]} -eq 0 ]; then targets=("${APP_SERVICES[@]}"); fi
    load_compose_env
    for svc in "${targets[@]}"; do
        # shellcheck disable=SC2086
        if [ -n "$(docker compose $COMPOSE_FILES ps -q "$svc" 2>/dev/null)" ]; then
            running+=("$svc")
        fi
    done
    if [ ${#running[@]} -eq 0 ]; then
        echo -e "${YELLOW}None of these services is running: ${targets[*]}${NC}"
        return 0
    fi
    echo -e "${YELLOW}Recreating ${running[*]} (clears their log buffers)...${NC}"
    # shellcheck disable=SC2086
    docker compose $COMPOSE_FILES up -d --force-recreate --no-deps "${running[@]}"
    echo -e "${GREEN}Done. Logs cleared.${NC}"
}

follow_logs() {
    local label="$1"; shift
    echo -e "${BOLD}Following: ${label}${NC}"
    echo -e "${DIM}Ctrl+C to exit${NC}"
    echo ""
    docker compose logs -f "$@" || {
        echo -e "${RED}No logs for ${label} — is it running? Start with ./scripts/start.sh${NC}"
        exit 1
    }
}

# Non-interactive
case "${1:-}" in
    clear) shift; clear_logs "$@"; exit 0 ;;
    debug) follow_logs "rag-service + api-gateway (query path)" rag-service api-gateway; exit 0 ;;
    "")    ;;
    *)     follow_logs "$1" "$1"; exit 0 ;;
esac

# Interactive menu
OPTIONS=(
    "debug"
    "rag-service"
    "ingestion-service"
    "api-gateway"
    "document-service"
    "web-ui"
    "admin-service"
    "analytics-service"
    "data-loader"
    "docintel-actions"
    "all"
    "clear"
)

LABELS=(
    "Debug              rag-service + api-gateway (query path - best for stuck queries)"
    "rag-service        RAG query pipeline, embeddings, LLM calls"
    "ingestion-service  Docling parse + embed + index pipeline"
    "api-gateway        Request routing, JWT validation"
    "document-service   Document upload and management"
    "web-ui             SvelteKit SPA frontend"
    "admin-service      Admin operations, tenant management"
    "analytics-service  Event ingestion, ClickHouse analytics"
    "data-loader        Sample dataset loading"
    "docintel-actions   Zitadel Actions v2 custom claims webhook"
    "All                All services"
    "Clear logs         Recreate running app containers (wipes their log buffers)"
)

# shellcheck source=lib/menu.sh
source "$SCRIPT_DIR/lib/menu.sh"

echo ""
echo -e "  ${BOLD}DocIntel Logs${NC}"
echo ""
menu_select choice "Select service to follow" OPTIONS LABELS 0 --cancel || exit 0
echo ""

case "$choice" in
    debug) follow_logs "rag-service + api-gateway (query path)" rag-service api-gateway ;;
    all)   follow_logs "all services" ;;
    clear) clear_logs ;;
    *)     follow_logs "$choice" "$choice" ;;
esac
