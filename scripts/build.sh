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

export DOCKER_BUILDKIT=1
export COMPOSE_DOCKER_CLI_BUILD=1

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

# Service definitions: name -> docker compose service name
SERVICES=(
  "web-ui"
  "api-gateway"
  "document-service"
  "ingestion-service"
  "rag-service"
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
    docker compose build --parallel "${SERVICES[@]}"
    echo ""
    echo -e "${GREEN}All services built.${NC}"
    echo ""
    read -rp "Restart services? (y/N): " restart
    if [[ "$restart" =~ ^[Yy]$ ]]; then
        docker compose up -d
        echo -e "${GREEN}Services restarted.${NC}"
    fi
    exit 0
fi

# ==============================================================================
# Interactive multi-select menu
# ==============================================================================

# State
selected=()
for i in "${!SERVICES[@]}"; do
    selected+=("false")
done
cursor=0

# Terminal control
cursor_to()  { printf "\033[%s;0H" "$1"; }
clear_line() { printf "\033[2K"; }
bold()       { printf "\033[1m%s\033[0m" "$1"; }

SAVED_TTY=$(stty -g 2>/dev/null)
cleanup() {
    stty "$SAVED_TTY" 2>/dev/null
    printf "\033[?25h"
    echo ""
}
trap cleanup EXIT INT TERM

draw_menu() {
    local start_row=$1

    for i in "${!SERVICES[@]}"; do
        cursor_to $((start_row + i))
        clear_line

        local prefix="  "
        local check="[ ]"
        local name_color="${NC}"
        local desc_color="${DIM}"

        if [[ "${selected[$i]}" == "true" ]]; then
            check="${GREEN}[x]${NC}"
        fi

        if [[ $i -eq $cursor ]]; then
            prefix="${CYAN}▸ ${NC}"
            name_color="${BOLD}${CYAN}"
        fi

        printf "  %b %b ${name_color}%-20s${NC} ${desc_color}%s${NC}\n" \
            "$prefix" "$check" "${SERVICES[$i]}" "${DESCRIPTIONS[$i]}"
    done

    # Footer
    cursor_to $((start_row + ${#SERVICES[@]} + 1))
    clear_line
    
    local count=0
    for s in "${selected[@]}"; do [[ "$s" == "true" ]] && ((count++)); done

    if [[ $count -gt 0 ]]; then
        printf "  ${YELLOW}${count} selected${NC}  "
    fi
    printf "${DIM}↑↓ navigate • space select • a all • enter build • q quit${NC}"
}

# Draw header
echo ""
echo -e "${BOLD}DocIntel Build Tool${NC}"
echo -e "${DIM}Select services to build:${NC}"
echo ""

start_row=5
printf "\033[?25l"
stty -echo -icanon min 1 time 0 2>/dev/null

draw_menu $start_row

while IFS= read -r -n1 -s key; do
    if [[ "$key" == $'\x1b' ]]; then
        IFS= read -r -n2 -s -t 1 seq
        case "$seq" in
            '[A') ((cursor > 0)) && ((cursor--)) ;;
            '[B') ((cursor < ${#SERVICES[@]} - 1)) && ((cursor++)) ;;
        esac
    elif [[ "$key" == '' ]]; then
        break
    elif [[ "$key" == ' ' ]]; then
        [[ "${selected[$cursor]}" == "true" ]] && selected[$cursor]="false" || selected[$cursor]="true"
    elif [[ "$key" == 'a' || "$key" == 'A' ]]; then
        any_unselected=false
        for s in "${selected[@]}"; do [[ "$s" == "false" ]] && any_unselected=true; done
        for i in "${!selected[@]}"; do
            [[ "$any_unselected" == "true" ]] && selected[$i]="true" || selected[$i]="false"
        done
    elif [[ "$key" == 'q' || "$key" == 'Q' ]]; then
        for i in $(seq 0 $((${#SERVICES[@]} + 2))); do cursor_to $((start_row + i)); clear_line; done
        cursor_to $start_row
        echo -e "${DIM}Cancelled.${NC}"
        exit 0
    fi
    draw_menu $start_row
done

# Restore terminal before any further reads (e.g. "Restart?" prompt)
stty "$SAVED_TTY" 2>/dev/null
printf "\033[?25h"

# Move cursor below menu
cursor_to $((start_row + ${#SERVICES[@]} + 3))

# Collect selected services
to_build=()
for i in "${!SERVICES[@]}"; do
    if [[ "${selected[$i]}" == "true" ]]; then
        to_build+=("${SERVICES[$i]}")
    fi
done

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
    if docker compose build "$svc"; then
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
read -rp "Restart built services? (y/N): " restart
if [[ "$restart" =~ ^[Yy]$ ]]; then
    echo ""
    for svc in "${to_build[@]}"; do
        echo -e "Restarting ${BOLD}${svc}${NC}..."
        docker compose up -d "$svc" 2>&1 | grep -E "Started|Recreated" || true
    done
    echo ""
    echo -e "${GREEN}Done.${NC}"
fi
echo ""
