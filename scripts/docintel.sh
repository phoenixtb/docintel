#!/bin/bash
# ==============================================================================
# DocIntel CLI
# ==============================================================================
# Interactive command-line tool for managing DocIntel services.
# Use arrow keys to navigate, enter to select.
#
# Usage:
#   ./scripts/docintel.sh                   # Interactive mode
#   ./scripts/docintel.sh build             # Build (non-interactive)
#   ./scripts/docintel.sh build --profile=cpu    # Force profile for this run
#   PROFILE=cu130 ./scripts/docintel.sh build    # Force profile via env
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# ==============================================================================
# Parse global --profile= flag before anything else (passed through to sub-scripts)
# ==============================================================================
_GLOBAL_PROFILE_FLAG=""
_DOCINTEL_ARGS_REMAINING=()
for _a in "$@"; do
    case "$_a" in
        --profile=*) _GLOBAL_PROFILE_FLAG="$_a" ;;
        *)           _DOCINTEL_ARGS_REMAINING+=("$_a") ;;
    esac
done
set -- "${_DOCINTEL_ARGS_REMAINING[@]+"${_DOCINTEL_ARGS_REMAINING[@]}"}"

# Pass profile flag through to sub-scripts via env (build.sh and start.sh read PROFILE env)
if [ -n "$_GLOBAL_PROFILE_FLAG" ]; then
    _PROFILE_VAL="${_GLOBAL_PROFILE_FLAG#--profile=}"
    export PROFILE="$_PROFILE_VAL"
fi

# ==============================================================================
# Load profile config for banner display (read-only, no prompt)
# ==============================================================================
# shellcheck source=lib/profile_config.sh
source "${SCRIPT_DIR}/lib/profile_config.sh"

# Quick profile read for banner — skip GPU Docker test to keep startup snappy
export DOCINTEL_SKIP_GPU_TEST=1
read_profile ${_GLOBAL_PROFILE_FLAG:+--flag-profile="${_GLOBAL_PROFILE_FLAG#--profile=}"}
unset DOCINTEL_SKIP_GPU_TEST

_BANNER_PROFILE="${PROFILE:-cpu}"

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
# pick_from_list — reusable arrow-key selector (bash 3.2 compatible)
# ==============================================================================
# Arguments:
#   $1  Title line shown above the list
#   $2  Name of array containing option keys   (e.g. MY_OPTS)
#   $3  Name of array containing display labels (e.g. MY_LABELS)
#   $4  Index of the item to pre-select (0-based, default 0)
#
# Returns: sets global PICK_RESULT to the selected key
# ==============================================================================
pick_from_list() {
    local title="$1"
    local opts_name="$2"
    local lbls_name="$3"
    local preselect="${4:-0}"

    local cur=$preselect
    local n
    eval "n=\${#${opts_name}[@]}"

    local _pick_saved_tty
    _pick_saved_tty=$(stty -g 2>/dev/null)

    _draw_pick() {
        local row=$1 i _lbl
        printf "\033[%s;0H" "$row"
        printf "\033[2K"
        echo -e "  ${BOLD}${title}${NC}"
        echo ""
        for (( i = 0; i < n; i++ )); do
            eval "_lbl=\${${lbls_name}[$i]}"
            printf "\033[2K"
            if [[ $i -eq $cur ]]; then
                printf "  ${CYAN}▸ ${BOLD}%s${NC}\n" "$_lbl"
            else
                printf "    %s\n" "$_lbl"
            fi
        done
        printf "\033[2K"
        printf "\n  ${DIM}↑↓ navigate • enter select${NC}"
    }

    local start_row=10
    printf "\033[?25l"
    stty -echo -icanon min 1 time 0 2>/dev/null

    _draw_pick "$start_row"

    local key seq
    while IFS= read -r -n1 -s key; do
        if [[ "$key" == $'\x1b' ]]; then
            # -t 1 is bash 3.2 compatible (macOS); fractional timeouts require bash 4+.
            # Arrow keys send ESC + 2 bytes almost instantaneously — the 1s timeout
            # is just a guard for a bare Escape keypress.
            IFS= read -r -n2 -s -t 1 seq || true
            case "$seq" in
                '[A'|'OA') [[ $cur -gt 0 ]]       && (( cur-- )) || true ;;
                '[B'|'OB') [[ $cur -lt $((n-1)) ]] && (( cur++ )) || true ;;
            esac
        elif [[ "$key" == '' ]]; then
            break
        fi
        _draw_pick "$start_row"
    done

    stty "$_pick_saved_tty" 2>/dev/null
    printf "\033[?25h"

    printf "\033[%s;0H" $((start_row + n + 3))

    eval "PICK_RESULT=\${${opts_name}[$cur]}"
}

# ==============================================================================
# resolve_llm_engine — reads defaults.env then .env, returns engine name
# ==============================================================================
resolve_llm_engine() {
    local engine=""
    [ -f "$PROJECT_DIR/config/defaults.env" ] && \
        engine=$(grep -E "^LLM_ENGINE=" "$PROJECT_DIR/config/defaults.env" | cut -d= -f2 | tr -d '[:space:]')
    [ -f "$PROJECT_DIR/.env" ] && {
        local override
        override=$(grep -E "^LLM_ENGINE=" "$PROJECT_DIR/.env" | cut -d= -f2 | tr -d '[:space:]')
        [ -n "$override" ] && engine="$override"
    }
    echo "${engine:-ollama}"
}

# ==============================================================================
# upsert_env — write/replace a key=value in .env
# ==============================================================================
upsert_env() {
    local key="$1" val="$2" file="$PROJECT_DIR/.env"
    [ -f "$file" ] || touch "$file"
    if grep -q "^${key}=" "$file" 2>/dev/null; then
        sed -i.bak "s|^${key}=.*|${key}=${val}|" "$file" && rm -f "$file.bak"
    else
        echo "${key}=${val}" >> "$file"
    fi
}

# ==============================================================================
# Main menu
# ==============================================================================

ACTIONS=(
    "setup"
    "start"
    "start-build"
    "stop"
    "build"
    "status"
    "logs"
    "test"
    "seed"
    "backup"
    "hw-profile"
    "cleanup"
    "cleanup-data"
    "cleanup-all"
    "quit"
)

LABELS=(
    "Setup              First-time setup (engine + images + models)"
    "Start              Start all services"
    "Start (build)      Rebuild images then start all services"
    "Stop               Stop all services (preserves containers)"
    "Build              Rebuild services (interactive selector)"
    "Status             Show running containers and health"
    "Logs               Follow service logs"
    "Test               Run tests (interactive selector)"
    "Seed Data          Load sample data into running services"
    "Backup             Back up volumes to archive"
    "Hardware Profile   View/switch GPU build profile [$_BANNER_PROFILE]"
    "Cleanup            Stop and remove containers"
    "Cleanup (data)     Wipe all data volumes (keeps images + models)"
    "Cleanup (full)     Remove containers, volumes, and models"
    "Quit"
)

# Terminal control
cursor_to()  { printf "\033[%s;0H" "$1"; }
clear_line() { printf "\033[2K"; }

SAVED_TTY=$(stty -g 2>/dev/null)
cleanup() {
    stty "$SAVED_TTY" 2>/dev/null
    printf "\033[?25h"
    echo ""
}
trap cleanup EXIT INT TERM

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

# Header
clear
echo ""
echo -e "  ${BOLD}DocIntel CLI${NC}"
echo -e "  ${DIM}Manage your DocIntel environment${NC}"
echo -e "  ${DIM}Hardware profile: ${CYAN}${_BANNER_PROFILE}${NC}${DIM} (source: ${PROFILE_SOURCE})${NC}"
echo ""

start_row=5
printf "\033[?25l"
stty -echo -icanon min 1 time 0 2>/dev/null

draw_menu $start_row

while IFS= read -r -n1 -s key; do
    if [[ "$key" == $'\x1b' ]]; then
        _seq=''
        IFS= read -r -n2 -s -t 1 _seq || true
        case "$_seq" in
            '[A'|'OA') [[ $cursor -gt 0 ]]                    && (( cursor-- )) || true ;;
            '[B'|'OB') [[ $cursor -lt $((${#ACTIONS[@]}-1)) ]] && (( cursor++ )) || true ;;
        esac
    elif [[ "$key" == '' ]]; then
        break
    elif [[ "$key" == 'q' || "$key" == 'Q' ]]; then
        cursor_to $((start_row + ${#ACTIONS[@]} + 3))
        exit 0
    fi
    draw_menu $start_row
done

# Move below menu
cursor_to $((start_row + ${#ACTIONS[@]} + 3))

action="${ACTIONS[$cursor]}"

echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# Restore terminal before exec / sub-menus
stty "$SAVED_TTY" 2>/dev/null
printf "\033[?25h"

# ==============================================================================
# Action dispatch
# ==============================================================================

case "$action" in
    setup)
        # ── Engine sub-selector ────────────────────────────────────────────────
        _current_engine=$(resolve_llm_engine)

        ENGINE_OPTS=("lmforge" "ollama" "vllm")
        ENGINE_LBLS=(
            "LMForge    Apple Silicon / macOS — local inference, recommended"
            "Ollama     Any platform — local model runner"
            "vLLM       External / server-managed (Linux / NVIDIA)"
        )

        # Pre-select the current engine
        _preselect=0
        for _i in "${!ENGINE_OPTS[@]}"; do
            [[ "${ENGINE_OPTS[$_i]}" == "$_current_engine" ]] && _preselect=$_i
        done

        echo -e "  ${BOLD}Select LLM Engine${NC}"
        echo -e "  ${DIM}Current: ${_current_engine}${NC}"
        echo ""

        pick_from_list "LLM Engine" ENGINE_OPTS ENGINE_LBLS "$_preselect"
        _chosen_engine="$PICK_RESULT"

        echo ""
        echo -e "  ${GREEN}✓${NC} Engine: ${BOLD}${_chosen_engine}${NC}"
        echo ""

        # Persist to .env so start.sh and future menu runs see it
        upsert_env "LLM_ENGINE" "$_chosen_engine"

        # Dispatch to engine-specific setup script
        case "$_chosen_engine" in
            lmforge)
                exec "$SCRIPT_DIR/setup-lmforge.sh"
                ;;
            vllm)
                echo -e "  ${YELLOW}vLLM is user-managed.${NC}"
                echo "  Ensure your vLLM server is running and set in .env:"
                echo "    LLM_CHAT_URL=http://<host>:8000/v1"
                echo "    LLM_EMBED_URL=http://<host>:8001/v1"
                echo ""
                echo "  Running common setup (keys, .env, Docker images)..."
                echo ""
                source "$SCRIPT_DIR/lib/setup-common.sh"
                setup_common_prereqs
                setup_common_zitadel_keys
                setup_common_env
                setup_common_docker_pull
                echo ""
                echo -e "  ${GREEN}${BOLD}Common setup complete.${NC}"
                echo "  Configure your vLLM URLs in .env, then: ./scripts/start.sh"
                echo ""
                ;;
            *)
                exec "$SCRIPT_DIR/setup.sh"
                ;;
        esac
        ;;
    start)
        exec "$SCRIPT_DIR/start.sh"
        ;;
    start-build)
        exec "$SCRIPT_DIR/start.sh" --build
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
    test)
        exec "$SCRIPT_DIR/test.sh"
        ;;
    seed)
        exec "$SCRIPT_DIR/seed-data.sh"
        ;;
    backup)
        exec "$SCRIPT_DIR/backup.sh"
        ;;
    hw-profile)
        # ── Hardware profile viewer/switcher ──────────────────────────────────
        # Re-detect with full Docker GPU test (skip flag cleared for this flow)
        unset DOCINTEL_SKIP_GPU_TEST
        read_profile
        print_profile_summary

        PROFILE_OPTS=("auto" "cpu" "cu126" "cu128" "cu129" "cu130" "clear")
        PROFILE_LBLS=(
            "Auto-detect       Re-run hardware detection on next build"
            "cpu               CPU-only PyTorch (~3.4 GB lighter images)"
            "cu126             NVIDIA CUDA 12.6 (driver >= 545)"
            "cu128             NVIDIA CUDA 12.8 (driver >= 555)"
            "cu129             NVIDIA CUDA 12.9 (driver >= 565)"
            "cu130             NVIDIA CUDA 13.0 (driver >= 580)"
            "Clear override    Remove .docintel-profile (restore auto-detect)"
        )

        # Pre-select current profile
        _pre=0
        for _i in "${!PROFILE_OPTS[@]}"; do
            if [ "${PROFILE_OPTS[$_i]}" = "$PROFILE" ] && [ "$PROFILE_SOURCE" != "auto" ]; then
                _pre=$_i
            fi
        done

        echo ""
        pick_from_list "Select hardware profile" PROFILE_OPTS PROFILE_LBLS "$_pre"
        _chosen="$PICK_RESULT"

        echo ""
        case "$_chosen" in
            auto)
                clear_profile_override
                ;;
            clear)
                clear_profile_override
                ;;
            cpu|cu126|cu128|cu129|cu130)
                write_profile_override "$_chosen"
                # Reset the "first build" shown marker so new profile gets a fresh summary
                rm -f "${PROJECT_DIR}/.docintel-profile-shown"
                echo -e "  ${GREEN}Profile set to: ${BOLD}${_chosen}${NC}"
                echo -e "  ${DIM}Next build will use this profile.${NC}"
                ;;
        esac
        echo ""
        ;;
    cleanup)
        exec "$SCRIPT_DIR/cleanup.sh"
        ;;
    cleanup-data)
        exec "$SCRIPT_DIR/cleanup.sh" --data
        ;;
    cleanup-all)
        exec "$SCRIPT_DIR/cleanup.sh" --all
        ;;
    quit)
        exit 0
        ;;
esac
