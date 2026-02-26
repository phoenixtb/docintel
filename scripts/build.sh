#!/bin/bash
# ==============================================================================
# DocIntel Build Tool
# ==============================================================================
# Interactive multi-select build tool for DocIntel services.
# Use arrow keys to navigate, space to select/deselect, enter to build.
#
# Usage:
#   ./scripts/build.sh          # Interactive mode
#   ./scripts/build.sh --all    # Build all services
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# Service definitions: name -> docker compose service name
SERVICES=(
  "web-ui"
  "api-gateway"
  "document-service"
  "rag-service"
  "admin-service"
  "analytics-service"
)

DESCRIPTIONS=(
  "SvelteKit chat interface"
  "Spring Cloud Gateway"
  "Document management (Kotlin)"
  "RAG pipeline (Python/Haystack)"
  "Admin operations (Kotlin)"
  "Analytics + ClickHouse ingestion (Kotlin)"
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
    echo -e "${BOLD}Building all services...${NC}"
    echo ""
    for i in "${!SERVICES[@]}"; do
        echo -e "${BLUE}[${i+1}/${#SERVICES[@]}]${NC} Building ${BOLD}${SERVICES[$i]}${NC}..."
        docker compose --profile app build "${SERVICES[$i]}" 2>&1 | tail -1
    done
    echo ""
    echo -e "${GREEN}All services built.${NC}"
    echo ""
    read -p "Restart services? (y/N): " restart
    if [[ "$restart" =~ ^[Yy]$ ]]; then
        docker compose --profile app up -d
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
    if docker compose --profile app build "$svc" 2>&1 | tail -1; then
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
read -p "Restart built services? (y/N): " restart
if [[ "$restart" =~ ^[Yy]$ ]]; then
    echo ""
    for svc in "${to_build[@]}"; do
        echo -e "Restarting ${BOLD}${svc}${NC}..."
        docker compose --profile app up -d "$svc" 2>&1 | grep -E "Started|Recreated" || true
    done
    echo ""
    echo -e "${GREEN}Done.${NC}"
fi
echo ""
