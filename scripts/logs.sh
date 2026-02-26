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

# Non-interactive: clear logs
if [[ "$1" == "clear" ]]; then
    TARGET="${2:-}"
    if [[ -n "$TARGET" ]]; then
        echo -e "${YELLOW}Clearing logs for: ${BOLD}$TARGET${NC}"
        docker compose --profile app --profile auth stop "$TARGET" 2>/dev/null
        docker compose --profile app --profile auth rm -f "$TARGET" 2>/dev/null
        docker compose --profile app --profile auth up -d "$TARGET" 2>/dev/null
        echo -e "${GREEN}Done. Logs cleared (container recreated).${NC}"
    else
        echo -e "${YELLOW}Clearing logs for all app services...${NC}"
        SERVICES=(rag-service api-gateway document-service web-ui admin-service analytics-service)
        for svc in "${SERVICES[@]}"; do
            docker rm -f "$(docker compose ps -q "$svc" 2>/dev/null)" 2>/dev/null || true
        done
        docker compose --profile app up -d 2>/dev/null
        echo -e "${GREEN}Done. Logs cleared (containers recreated).${NC}"
    fi
    exit 0
fi

# Non-interactive: debug or single service
if [[ "$1" == "debug" ]]; then
    echo -e "${BOLD}Debug mode: rag-service + api-gateway (query path)${NC}"
    echo -e "${DIM}Ctrl+C to exit${NC}"
    echo ""
    docker compose --profile app logs -f rag-service api-gateway 2>/dev/null || {
        echo -e "${RED}Services not running. Start with ./scripts/start.sh${NC}"
        exit 1
    }
    exit 0
fi

if [[ -n "$1" ]]; then
    docker compose --profile app --profile auth logs -f "$1" 2>/dev/null || {
        echo -e "${RED}Service '$1' not found or not running.${NC}"
        echo "Valid: rag-service, api-gateway, document-service, web-ui, admin-service, analytics-service"
        exit 1
    }
    exit 0
fi

# Interactive menu
OPTIONS=(
    "debug"
    "rag-service"
    "api-gateway"
    "document-service"
    "web-ui"
    "admin-service"
    "analytics-service"
    "all"
    "clear"
)

LABELS=(
    "Debug              rag-service + api-gateway (query path - best for stuck queries)"
    "rag-service        RAG pipeline, embeddings, LLM calls"
    "api-gateway        Request routing, JWT validation"
    "document-service   Document upload, chunking"
    "web-ui             SvelteKit frontend"
    "admin-service      Admin operations"
    "analytics-service  Event ingestion, ClickHouse analytics"
    "All                All app + auth services"
    "Clear logs         Recreate all containers (wipes log buffers)"
)

cursor=0

cursor_to()  { printf "\033[%s;0H" "$1"; }
clear_line() { printf "\033[2K"; }

# Save terminal state upfront; restore fully on any exit
SAVED_TTY=$(stty -g 2>/dev/null)
cleanup() {
    stty "$SAVED_TTY" 2>/dev/null
    printf "\033[?25h"  # show cursor
    echo ""
}
trap cleanup EXIT INT TERM

draw_menu() {
    local start_row=$1
    for i in "${!OPTIONS[@]}"; do
        cursor_to $((start_row + i))
        clear_line
        if [[ $i -eq $cursor ]]; then
            printf "  ${CYAN}▸ ${BOLD}%s${NC}\n" "${LABELS[$i]}"
        else
            printf "    %s\n" "${LABELS[$i]}"
        fi
    done
    cursor_to $((start_row + ${#OPTIONS[@]} + 1))
    clear_line
    printf "  ${DIM}↑↓ navigate • enter select • q quit${NC}"
}

clear
echo ""
echo -e "  ${BOLD}DocIntel Logs${NC}"
echo -e "  ${DIM}Select service to follow (Ctrl+C to exit logs)${NC}"
echo ""

start_row=5
printf "\033[?25l"                        # hide cursor
stty -echo -icanon min 1 time 0 2>/dev/null  # raw mode: no echo, no line buffer
draw_menu $start_row

while IFS= read -r -n1 -s key; do
    if [[ "$key" == $'\x1b' ]]; then
        # Read remainder of escape sequence without subshell
        IFS= read -r -n2 -s -t 1 seq
        case "$seq" in
            '[A') ((cursor > 0)) && ((cursor--)) ;;
            '[B') ((cursor < ${#OPTIONS[@]} - 1)) && ((cursor++)) ;;
        esac
    elif [[ "$key" == '' ]]; then
        # Enter key
        break
    elif [[ "$key" == 'q' || "$key" == 'Q' ]]; then
        exit 0
    fi
    draw_menu $start_row
done

choice="${OPTIONS[$cursor]}"
echo ""
echo -e "${BOLD}Following: ${choice}${NC}"
echo -e "${DIM}Ctrl+C to exit${NC}"
echo ""

if [[ "$choice" == "debug" ]]; then
    docker compose --profile app logs -f rag-service api-gateway
elif [[ "$choice" == "all" ]]; then
    docker compose --profile app --profile auth logs -f
elif [[ "$choice" == "clear" ]]; then
    echo -e "${YELLOW}Recreating all app containers (clears log buffers)...${NC}"
    SERVICES=(rag-service api-gateway document-service web-ui admin-service analytics-service)
    for svc in "${SERVICES[@]}"; do
        cid=$(docker compose ps -q "$svc" 2>/dev/null || true)
        [[ -n "$cid" ]] && docker rm -f "$cid" 2>/dev/null || true
    done
    docker compose --profile app up -d
    echo -e "${GREEN}Done.${NC}"
else
    docker compose --profile app logs -f "$choice"
fi
