#!/bin/bash
# ==============================================================================
# DocIntel Build Tool
# ==============================================================================
# Interactive multi-select build tool for DocIntel services.
# Use arrow keys to navigate, space to select/deselect, enter to build.
#
# Usage:
#   ./scripts/build.sh                  # Interactive mode (auto-detect hardware)
#   ./scripts/build.sh --all            # Build all services
#   ./scripts/build.sh --profile=cpu    # Force CPU profile
#   PROFILE=cu130 ./scripts/build.sh    # Force cu130 profile via env
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# Load build-time env so `docker compose build` sees the torch wheel selection
# (TORCH_INDEX / TORCH_VERSION / PROFILE_TAG) set by torch_vars_for_profile.
# Builds run on the host's native arch by default; the Dockerfiles pin
# torch==<ver>+cpu, which has both x86_64 and aarch64 wheels, so no cross-arch
# forcing is needed. `set -a` exports every plain KEY=val to docker compose.
set -a
# shellcheck source=../config/defaults.env
source "$PROJECT_DIR/config/defaults.env"
# .env overrides defaults
[ -f "$PROJECT_DIR/.env" ] && source "$PROJECT_DIR/.env"
set +a

export DOCKER_BUILDKIT=1
export COMPOSE_DOCKER_CLI_BUILD=1

# ==============================================================================
# Docker engine — select context before any docker call (incl. GPU detection)
# ==============================================================================
# shellcheck source=lib/docker_context.sh
source "${SCRIPT_DIR}/lib/docker_context.sh"
ensure_docker_context

# ==============================================================================
# Hardware profile — detect or read override
# ==============================================================================

# shellcheck source=lib/profile_config.sh
source "${SCRIPT_DIR}/lib/profile_config.sh"
# shellcheck source=lib/install_nvidia_toolkit.sh
source "${SCRIPT_DIR}/lib/install_nvidia_toolkit.sh"

# Parse --profile= from args (before the --all check)
_FLAG_PROFILE=""
_ARGS_REMAINING=()
for _arg in "$@"; do
    case "$_arg" in
        --profile=*) _FLAG_PROFILE="${_arg#--profile=}" ;;
        *)           _ARGS_REMAINING+=("$_arg") ;;
    esac
done
set -- "${_ARGS_REMAINING[@]+"${_ARGS_REMAINING[@]}"}"

read_profile ${_FLAG_PROFILE:+--flag-profile="$_FLAG_PROFILE"}

# If GPU hardware present but Docker can't reach it, offer toolkit install (TTY only)
if [ "${PROFILE_DOCKER_GPU:-}" = "no_toolkit" ] && [ -t 0 ] && [ -t 1 ]; then
    source "${SCRIPT_DIR}/lib/install_nvidia_toolkit.sh"
    if offer_install_nvidia_toolkit; then
        # Retry detection after successful toolkit install
        read_profile ${_FLAG_PROFILE:+--flag-profile="$_FLAG_PROFILE"}
    fi
fi

# Emit profile info
if [ -t 1 ]; then
    # Interactive: show summary on first build, brief line on subsequent builds
    if [ ! -f "${PROJECT_DIR}/.docintel-profile-shown" ]; then
        print_profile_summary
        echo -n "  Proceed? [Y/n] "
        read -r _confirm </dev/tty
        if [[ "$_confirm" =~ ^[Nn]$ ]]; then
            echo "  Aborted."
            exit 0
        fi
        touch "${PROJECT_DIR}/.docintel-profile-shown"
    else
        echo "  [hardware] profile=${PROFILE} source=${PROFILE_SOURCE}"
    fi
else
    # Non-interactive / CI: single machine-readable line
    print_profile_summary_ci
fi

# Export build vars consumed by docker-compose.yml
torch_vars_for_profile "$PROFILE"

# Resolve the Docker target platform (single source of truth): native by
# default, or a persisted/env override for cross-builds. Exports DOCKER_DEFAULT_PLATFORM.
resolve_platform
echo "  [platform] ${DOCKER_PLATFORM_LABEL} (${DOCKER_PLATFORM_SOURCE})"

# Full compose file chain (base + override + GPU overlay)
compose_file_chain "$PROJECT_DIR"

# Service definitions: name -> docker compose service name
SERVICES=(
  "web-ui"
  "api-gateway"
  "document-service"
  "ingestion-service"
  "rag-service"
  "data-loader"
  "admin-service"
  "analytics-service"
  "docintel-actions"
)

DESCRIPTIONS=(
  "SvelteKit SPA frontend"
  "Spring Cloud Gateway"
  "Document management (Kotlin)"
  "Docling parse + embed + index (Python)"
  "RAG query/retrieval (Python/Haystack)"
  "Dataset seeding / sample data loader (Python)"
  "Admin operations (Kotlin)"
  "Analytics + ClickHouse ingestion (Python)"
  "Zitadel Actions v2 custom claims webhook"
)

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

# ==============================================================================
# Build all (non-interactive)
# ==============================================================================
if [[ "$1" == "--all" ]]; then
    echo -e "${BOLD}Building all services in parallel...${NC}"
    echo ""
    # shellcheck disable=SC2086
    docker compose $COMPOSE_FILES build --parallel "${SERVICES[@]}"
    echo ""
    echo -e "${GREEN}All services built.${NC}"
    echo ""
    # Only ask on a terminal: without one `read` hits EOF and `set -e` would turn
    # a successful build into exit 1.
    if [ -t 0 ]; then
        read -rp "Restart services? (y/N): " restart || restart=""
        if [[ "$restart" =~ ^[Yy]$ ]]; then
            # shellcheck disable=SC2086
            docker compose $COMPOSE_FILES up -d
            echo -e "${GREEN}Services restarted.${NC}"
        fi
    fi
    exit 0
fi

# ==============================================================================
# Interactive multi-select menu
# ==============================================================================
MENU_LABELS=()
for i in "${!SERVICES[@]}"; do
    MENU_LABELS+=("$(printf '%-20s %s' "${SERVICES[$i]}" "${DESCRIPTIONS[$i]}")")
done

# shellcheck source=lib/menu.sh
source "${SCRIPT_DIR}/lib/menu.sh"

echo ""
echo -e "${BOLD}DocIntel Build Tool${NC}"
echo ""
to_build=()
if ! menu_multiselect to_build "Select services to build" SERVICES MENU_LABELS; then
    echo -e "${DIM}Cancelled.${NC}"
    exit 0
fi
echo ""

if [[ ${#to_build[@]} -eq 0 ]]; then
    echo -e "${YELLOW}No services selected.${NC}"
    exit 0
fi

# Build
echo ""
echo -e "${BOLD}Building ${#to_build[@]} service(s)...${NC}"
echo ""

failed=()
for i in "${!to_build[@]}"; do
    svc="${to_build[$i]}"
    echo -e "${BLUE}[$(($i + 1))/${#to_build[@]}]${NC} Building ${BOLD}${svc}${NC}..."
    # shellcheck disable=SC2086
    if docker compose $COMPOSE_FILES build "$svc"; then
        echo -e "  ${GREEN}Done${NC}"
    else
        echo -e "  ${RED}Failed${NC}"
        failed+=("$svc")
    fi
done

echo ""
if [[ ${#failed[@]} -gt 0 ]]; then
    echo -e "${RED}Failed: ${failed[*]}${NC}"
    exit 1
fi

echo -e "${GREEN}All builds successful.${NC}"
echo ""
read -rp "Restart built services? (y/N): " restart || restart=""
if [[ "$restart" =~ ^[Yy]$ ]]; then
    echo ""
    for svc in "${to_build[@]}"; do
        echo -e "Restarting ${BOLD}${svc}${NC}..."
        # shellcheck disable=SC2086
        docker compose $COMPOSE_FILES up -d "$svc" 2>&1 | grep -E "Started|Recreated" || true
    done
    echo ""
    echo -e "${GREEN}Done.${NC}"
fi
echo ""
