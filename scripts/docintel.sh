#!/bin/bash
# ==============================================================================
# DocIntel CLI
# ==============================================================================
# Interactive command-line tool for managing DocIntel services.
# Use arrow keys to navigate, enter to select.
#
# Usage:
#   ./scripts/docintel.sh       # Interactive mode
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

# Menu options
ACTIONS=(
    "setup"
    "start"
    "start-no-auth"
    "stop"
    "build"
    "status"
    "logs"
    "cleanup"
    "cleanup-all"
    "quit"
)

LABELS=(
    "Setup             First-time setup (pull images + models)"
    "Start             Start all services with authentication"
    "Start (no auth)   Start services without authentication (dev mode)"
    "Stop              Stop all services (preserves containers)"
    "Build             Rebuild services (interactive selector)"
    "Status            Show running containers and health"
    "Logs              Follow service logs"
    "Cleanup           Stop and remove containers"
    "Cleanup (full)    Remove containers, volumes, and models"
    "Quit"
)

# Terminal control
cursor_to()    { printf "\033[%s;0H" "$1"; }
clear_line()   { printf "\033[2K"; }
cursor_hide()  { printf "\033[?25l"; }
cursor_show()  { printf "\033[?25h"; }

cleanup() {
    cursor_show
    stty echo 2>/dev/null
}
trap cleanup EXIT

cursor=0

draw_menu() {
    local start_row=$1

    for i in "${!ACTIONS[@]}"; do
        cursor_to $((start_row + i))
        clear_line

        if [[ $i -eq $cursor ]]; then
            printf "  ${CYAN}▸ ${BOLD}%s${NC}\n" "${LABELS[$i]}"
        else
            if [[ "${ACTIONS[$i]}" == "quit" ]]; then
                printf "    ${DIM}%s${NC}\n" "${LABELS[$i]}"
            else
                printf "    %s\n" "${LABELS[$i]}"
            fi
        fi
    done

    cursor_to $((start_row + ${#ACTIONS[@]} + 1))
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

# Header
clear
echo ""
echo -e "  ${BOLD}DocIntel CLI${NC}"
echo -e "  ${DIM}Manage your DocIntel environment${NC}"
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
            ((cursor < ${#ACTIONS[@]} - 1)) && ((cursor++))
            ;;
        enter)
            break
            ;;
        quit)
            cursor_show
            stty echo 2>/dev/null
            cursor_to $((start_row + ${#ACTIONS[@]} + 3))
            exit 0
            ;;
    esac
    draw_menu $start_row
done

cursor_show
stty echo 2>/dev/null

# Move below menu
cursor_to $((start_row + ${#ACTIONS[@]} + 3))

action="${ACTIONS[$cursor]}"

echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

case "$action" in
    setup)
        exec "$SCRIPT_DIR/setup.sh"
        ;;
    start)
        exec "$SCRIPT_DIR/start.sh"
        ;;
    start-no-auth)
        exec "$SCRIPT_DIR/start.sh" --no-auth
        ;;
    stop)
        exec "$SCRIPT_DIR/stop.sh"
        ;;
    build)
        exec "$SCRIPT_DIR/build.sh"
        ;;
    status)
        echo -e "${BOLD}Container Status${NC}"
        echo ""
        docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" --filter "name=docintel" 2>/dev/null || echo "No containers running"
        echo ""
        ;;
    logs)
        exec "$SCRIPT_DIR/logs.sh"
        ;;
    cleanup)
        exec "$SCRIPT_DIR/cleanup.sh"
        ;;
    cleanup-all)
        exec "$SCRIPT_DIR/cleanup.sh" --all
        ;;
    quit)
        exit 0
        ;;
esac
