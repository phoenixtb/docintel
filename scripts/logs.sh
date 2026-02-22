#!/bin/bash
# ==============================================================================
# DocIntel Log Viewer
# ==============================================================================
# View logs for debugging. Use arrow keys to select, enter to follow.
#
# Usage:
#   ./scripts/logs.sh           # Interactive mode
#   ./scripts/logs.sh debug    # Debug mode: rag-service + api-gateway (query path)
#   ./scripts/logs.sh rag-service
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
        echo "Valid: rag-service, api-gateway, document-service, web-ui, admin-service"
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
    "all"
)

LABELS=(
    "Debug          rag-service + api-gateway (query path - best for stuck queries)"
    "rag-service    RAG pipeline, embeddings, LLM calls"
    "api-gateway    Request routing, JWT validation"
    "document-service  Document upload, chunking"
    "web-ui         SvelteKit frontend"
    "admin-service  Admin operations"
    "All            All app + auth services"
)

cursor=0

cursor_to()    { printf "\033[%s;0H" "$1"; }
clear_line()   { printf "\033[2K"; }
cursor_hide()  { printf "\033[?25l"; }
cursor_show()  { printf "\033[?25h"; }

cleanup() {
    cursor_show
    stty echo 2>/dev/null
}
trap cleanup EXIT

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

read_key() {
    local key
    IFS= read -rsn1 key
    case "$key" in
        $'\x1b')
            read -rsn2 -t 0.1 key
            case "$key" in
                '[A') echo "up" ;;
                '[B') echo "down" ;;
            esac
            ;;
        '') echo "enter" ;;
        'q'|'Q') echo "quit" ;;
    esac
}

clear
echo ""
echo -e "  ${BOLD}DocIntel Logs${NC}"
echo -e "  ${DIM}Select service to follow (Ctrl+C to exit logs)${NC}"
echo ""

start_row=5
cursor_hide
stty -echo 2>/dev/null
draw_menu $start_row

while true; do
    key=$(read_key)
    case "$key" in
        up)
            ((cursor > 0)) && ((cursor--))
            ;;
        down)
            ((cursor < ${#OPTIONS[@]} - 1)) && ((cursor++))
            ;;
        enter)
            break
            ;;
        quit)
            cursor_show
            stty echo 2>/dev/null
            exit 0
            ;;
    esac
    draw_menu $start_row
done

cursor_show
stty echo 2>/dev/null

choice="${OPTIONS[$cursor]}"
echo ""
echo -e "${BOLD}Following: ${choice}${NC}"
echo -e "${DIM}Ctrl+C to exit${NC}"
echo ""

if [[ "$choice" == "debug" ]]; then
    docker compose --profile app logs -f rag-service api-gateway
elif [[ "$choice" == "all" ]]; then
    docker compose --profile app --profile auth logs -f
else
    docker compose --profile app logs -f "$choice"
fi
