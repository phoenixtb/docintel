#!/bin/bash
# ==============================================================================
# DocIntel CLI
# ==============================================================================
# Interactive command-line tool for managing DocIntel services.
# Use arrow keys to navigate, enter to select.
#
# Usage:
#   ./scripts/docintel.sh                              # Interactive menu (TTY)
#   ./scripts/docintel.sh setup [lmforge|ollama|vllm]  # First-time setup
#   ./scripts/docintel.sh start | start-build | stop | build
#   ./scripts/docintel.sh status | logs | test | seed | backup
#   ./scripts/docintel.sh cleanup | cleanup-data | cleanup-all
#   ./scripts/docintel.sh --profile=cpu build          # Force profile for this run
#   PROFILE=cu130 ./scripts/docintel.sh build          # Force profile via env
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
# shellcheck source=lib/docker_context.sh
source "${SCRIPT_DIR}/lib/docker_context.sh"

# Quick profile read for banner — skip GPU Docker test to keep startup snappy
export DOCINTEL_SKIP_GPU_TEST=1
read_profile ${_GLOBAL_PROFILE_FLAG:+--flag-profile="${_GLOBAL_PROFILE_FLAG#--profile=}"}
unset DOCINTEL_SKIP_GPU_TEST

_BANNER_PROFILE="${PROFILE:-cpu}"
_BANNER_DCTX="$(docker_context_label)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

# Arrow-key menus (menu_select / menu_multiselect), shared with the other scripts
# shellcheck source=lib/menu.sh
source "${SCRIPT_DIR}/lib/menu.sh"

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
    if [ ! -f "$file" ]; then
        if [ -f "$PROJECT_DIR/.env.example" ]; then
            cp "$PROJECT_DIR/.env.example" "$file"
        else
            touch "$file"
        fi
    fi
    if grep -q "^${key}=" "$file" 2>/dev/null; then
        sed -i.bak "s|^${key}=.*|${key}=${val}|" "$file" && rm -f "$file.bak"
    else
        echo "${key}=${val}" >> "$file"
    fi
}

usage() {
    cat <<'EOF'
Usage: ./scripts/docintel.sh [action] [--profile=NAME]

Actions:
  setup [lmforge|ollama|vllm]  First-time setup (engine + images + models)
  start                        Start all services
  start-build                  Rebuild images then start all services
  stop                         Stop all services (preserves containers)
  build                        Rebuild services (interactive selector)
  status                       Show running containers and health
  logs                         Follow service logs
  test                         Run tests (interactive selector)
  seed                         Load sample data into running services
  backup                       Back up volumes to archive
  cleanup                      Stop and remove containers
  cleanup-data                 Wipe all data volumes (keeps images + models)
  cleanup-all                  Remove containers, volumes, and models
  help, -h, --help             Show this help

  --profile=NAME               Force hardware profile for this run

No action + TTY: interactive menu.
hw-profile and docker-engine are menu-only.
EOF
}

# ==============================================================================
# Action dispatch (shared by CLI argv and the interactive menu)
# ==============================================================================

dispatch_action() {
    local action="$1"

    case "$action" in
    setup)
        if [ -n "${_CLI_MODE:-}" ]; then
            _chosen_engine="${_SETUP_ENGINE:-$(resolve_llm_engine)}"
            echo -e "  ${GREEN}✓${NC} Engine: ${BOLD}${_chosen_engine}${NC}"
            echo ""
        else
            # ── Engine sub-selector (interactive only) ─────────────────────────
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

            menu_select _chosen_engine "LLM Engine" ENGINE_OPTS ENGINE_LBLS "$_preselect" --cancel || return 0

            echo ""
            echo -e "  ${GREEN}✓${NC} Engine: ${BOLD}${_chosen_engine}${NC}"
            echo ""
        fi

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

        PROFILE_OPTS=("auto" "cpu" "cu126" "cu128" "cu129" "cu130")
        PROFILE_LBLS=(
            "Auto-detect       Remove the override; re-detect hardware on next build"
            "cpu               CPU-only PyTorch (~3.4 GB lighter images)"
            "cu126             NVIDIA CUDA 12.6 (driver >= 545)"
            "cu128             NVIDIA CUDA 12.8 (driver >= 555)"
            "cu129             NVIDIA CUDA 12.9 (driver >= 565)"
            "cu130             NVIDIA CUDA 13.0 (driver >= 580)"
        )

        # Pre-select current profile
        _pre=0
        for _i in "${!PROFILE_OPTS[@]}"; do
            if [ "${PROFILE_OPTS[$_i]}" = "$PROFILE" ] && [ "$PROFILE_SOURCE" != "auto" ]; then
                _pre=$_i
            fi
        done

        echo ""
        menu_select _chosen "Select hardware profile" PROFILE_OPTS PROFILE_LBLS "$_pre" --cancel || return 0

        echo ""
        case "$_chosen" in
            auto)
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
    docker-engine)
        # ── Docker context viewer/switcher ────────────────────────────────────
        _current_pref="$(read_docker_pref)"
        _active_ctx="$(_dctx_active)"

        echo -e "  ${BOLD}Select Docker Engine${NC}"
        echo -e "  ${DIM}Active context: ${_active_ctx:-none} • preference: ${_current_pref}${NC}"
        echo ""

        # Build option list: "auto" + every detected context (with reachability).
        DCTX_OPTS=("auto")
        DCTX_LBLS=("Auto-detect       Prefer OrbStack, fall back automatically")
        while IFS= read -r _ctx; do
            [ -z "$_ctx" ] && continue
            if _dctx_responds "$_ctx"; then
                _status="running"
            else
                _status="stopped"
            fi
            DCTX_OPTS+=("$_ctx")
            DCTX_LBLS+=("$(printf '%-18s%s' "$_ctx" "[$_status]")")
        done < <(detect_docker_contexts)

        # Pre-select current preference (or "auto").
        _pre=0
        for _i in "${!DCTX_OPTS[@]}"; do
            [ "${DCTX_OPTS[$_i]}" = "$_current_pref" ] && _pre=$_i
        done

        menu_select _chosen_ctx "Docker Engine" DCTX_OPTS DCTX_LBLS "$_pre" --cancel || return 0

        echo ""
        upsert_env "DOCKER_CONTEXT_PREF" "$_chosen_ctx"
        echo -e "  ${GREEN}✓${NC} Preference: ${BOLD}${_chosen_ctx}${NC}"
        ensure_docker_context
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
}

# ==============================================================================
# CLI argv dispatch — no TTY / no stty / no clear
# ==============================================================================

if [ -n "${1:-}" ]; then
    _CLI_MODE=1
    case "$1" in
        help|-h|--help)
            usage
            exit 0
            ;;
        setup)
            if [ -n "${2:-}" ]; then
                case "$2" in
                    lmforge|ollama|vllm)
                        _SETUP_ENGINE="$2"
                        ;;
                    *)
                        echo "Unknown engine: $2" >&2
                        echo "" >&2
                        usage >&2
                        exit 1
                        ;;
                esac
            fi
            dispatch_action setup
            exit $?
            ;;
        start|start-build|stop|build|status|logs|test|seed|backup|cleanup|cleanup-data|cleanup-all)
            dispatch_action "$1"
            exit $?
            ;;
        *)
            echo "Unknown action: $1" >&2
            echo "" >&2
            usage >&2
            exit 1
            ;;
    esac
fi

if [ ! -t 0 ]; then
    usage >&2
    exit 1
fi

# ==============================================================================
# Interactive menu
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
    "docker-engine"
    "cleanup"
    "cleanup-data"
    "cleanup-all"
    "quit"
)

build_labels() {
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
        "Docker Engine      View/switch Docker context [$_BANNER_DCTX]"
        "Cleanup            Stop and remove containers"
        "Cleanup (data)     Wipe all data volumes (keeps images + models)"
        "Cleanup (full)     Remove containers, volumes, and models"
        "Quit"
    )
}

# Actions that run in this process (status, hw-profile, docker-engine, vLLM
# setup) come back to the menu; the others exec their script and replace it.
trap 'printf "\033[?25h"' EXIT
cursor=0
while :; do
    build_labels
    clear
    echo ""
    echo -e "  ${BOLD}DocIntel CLI${NC}"
    echo -e "  ${DIM}Manage your DocIntel environment${NC}"
    echo -e "  ${DIM}Hardware profile: ${CYAN}${_BANNER_PROFILE}${NC}${DIM} (source: ${PROFILE_SOURCE})${NC}"
    echo -e "  ${DIM}Docker engine: ${CYAN}${_BANNER_DCTX}${NC}${DIM} (pref: $(read_docker_pref))${NC}"
    echo ""

    menu_select action "Actions" ACTIONS LABELS "$cursor" --cancel || exit 0
    cursor=$MENU_INDEX

    echo ""
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    dispatch_action "$action"

    # Refresh banner values an in-process action may have changed.
    DOCINTEL_SKIP_GPU_TEST=1 read_profile ${_GLOBAL_PROFILE_FLAG:+--flag-profile="${_GLOBAL_PROFILE_FLAG#--profile=}"}
    _BANNER_PROFILE="${PROFILE:-cpu}"
    _BANNER_DCTX="$(docker_context_label)"

    echo ""
    printf "  ${DIM}Press any key to return to the menu…${NC}"
    IFS= read -r -s -n1 _ || exit 0
done
