#!/bin/bash
# scripts/lib/docker_context.sh
# ==============================================================================
# Resolve & enforce which Docker engine (context) DocIntel talks to.
# ==============================================================================
# Mirrors the LLM_ENGINE / hardware-profile pattern: a DOCKER_CONTEXT_PREF value
# in .env drives selection.
#
#   DOCKER_CONTEXT_PREF=auto          → auto-detect (default)
#   DOCKER_CONTEXT_PREF=orbstack      → pin to a specific context
#
# Auto-detect order (macOS): orbstack > colima > desktop-linux > default
#                   (Linux): default  > orbstack > colima  > desktop-linux
# OrbStack is preferred on macOS for performance.
#
# Requires: PROJECT_DIR set by the caller before sourcing.
#
# Public functions:
#   read_docker_pref            — echo DOCKER_CONTEXT_PREF from .env (default "auto")
#   resolve_docker_context      — echo the context name we should use
#   ensure_docker_context       — switch active context to the resolved one
#   detect_docker_contexts      — echo available context names, one per line
#   docker_context_label        — short label for banners (e.g. "orbstack")

[ -n "$_DOCKER_CONTEXT_LOADED" ] && return 0
_DOCKER_CONTEXT_LOADED=1

# Colours — only define if the caller hasn't already.
: "${GREEN:=$'\033[0;32m'}"
: "${YELLOW:=$'\033[1;33m'}"
: "${DIM:=$'\033[2m'}"
: "${BOLD:=$'\033[1m'}"
: "${NC:=$'\033[0m'}"

_dctx_env_file() { echo "${PROJECT_DIR:-.}/.env"; }

# ── read_docker_pref ──────────────────────────────────────────────────────────
read_docker_pref() {
    local file pref=""
    file="$(_dctx_env_file)"
    if [ -f "$file" ]; then
        pref=$(grep -E "^DOCKER_CONTEXT_PREF=" "$file" 2>/dev/null | tail -n1 | cut -d= -f2 | tr -d '[:space:]')
    fi
    echo "${pref:-auto}"
}

# ── low-level docker context helpers ──────────────────────────────────────────
_dctx_exists()   { docker context inspect "$1" >/dev/null 2>&1; }
_dctx_active()   { docker context show 2>/dev/null; }
# Engine reachable through this context? (cheap-ish; ~0.3-0.6s)
_dctx_responds() { docker --context "$1" info >/dev/null 2>&1; }

detect_docker_contexts() {
    docker context ls --format '{{.Name}}' 2>/dev/null
}

# ── _is_wsl — running inside a WSL2 distro? ───────────────────────────────────
_is_wsl() {
    grep -qiE "microsoft|wsl" /proc/version 2>/dev/null
}

# ── _dctx_auto_order — OS-aware candidate list ────────────────────────────────
# macOS prefers OrbStack for performance. Linux/WSL2 prefer the native daemon
# (`default`), then common alternatives (Rancher Desktop, etc.).
_dctx_auto_order() {
    case "$(uname -s)" in
        Darwin) echo "orbstack colima desktop-linux default" ;;
        *)      echo "default rancher-desktop orbstack colima desktop-linux" ;;
    esac
}

# ── resolve_docker_context ────────────────────────────────────────────────────
# Explicit pref (exists) wins. Otherwise pick the first auto-order candidate
# that exists AND has a reachable engine. Falls back to the active context.
resolve_docker_context() {
    local pref candidate

    # DOCKER_HOST overrides contexts entirely (remote/rootless/custom daemon).
    # Respect it and don't fight the user's explicit endpoint.
    if [ -n "${DOCKER_HOST:-}" ]; then
        _dctx_active
        return 0
    fi

    pref="$(read_docker_pref)"

    if [ "$pref" != "auto" ] && [ -n "$pref" ]; then
        if _dctx_exists "$pref"; then
            echo "$pref"; return 0
        fi
        # Stale pin — fall through to auto-detect.
    fi

    for candidate in $(_dctx_auto_order); do
        if _dctx_exists "$candidate" && _dctx_responds "$candidate"; then
            echo "$candidate"; return 0
        fi
    done

    # Nothing reachable in the preferred order — keep whatever is active.
    _dctx_active
}

docker_context_label() {
    local active; active="$(_dctx_active)"
    echo "${active:-unknown}"
}

# ── ensure_docker_context ─────────────────────────────────────────────────────
# Switch the active context to the resolved one (idempotent). Never exits the
# caller on failure — just warns, so a broken docker setup surfaces naturally
# at the first real docker command.
ensure_docker_context() {
    if ! command -v docker >/dev/null 2>&1; then
        return 0
    fi

    # DOCKER_HOST set → honour it as-is; switching contexts would be ignored
    # (and would emit a "conflicting options" warning on some docker builds).
    if [ -n "${DOCKER_HOST:-}" ]; then
        echo -e "  ${GREEN}✓${NC} Docker engine: ${BOLD}DOCKER_HOST${NC} ${DIM}(${DOCKER_HOST})${NC}"
        return 0
    fi

    local resolved active pref
    pref="$(read_docker_pref)"
    resolved="$(resolve_docker_context)"
    active="$(_dctx_active)"

    if [ -z "$resolved" ]; then
        echo -e "  ${YELLOW}⚠${NC}  Could not resolve a Docker context; using active '${active:-default}'." >&2
        return 0
    fi

    if [ "$resolved" != "$active" ]; then
        if docker context use "$resolved" >/dev/null 2>&1; then
            echo -e "  ${GREEN}✓${NC} Docker engine: ${BOLD}${resolved}${NC} ${DIM}(was: ${active:-none}, pref: ${pref})${NC}"
        else
            echo -e "  ${YELLOW}⚠${NC}  Failed to switch to Docker context '${resolved}'; using '${active}'." >&2
        fi
    else
        echo -e "  ${GREEN}✓${NC} Docker engine: ${BOLD}${resolved}${NC} ${DIM}(pref: ${pref})${NC}"
    fi

    # Warn if the chosen engine isn't actually reachable.
    if ! _dctx_responds "$resolved"; then
        echo -e "  ${YELLOW}⚠${NC}  Docker engine '${resolved}' is not responding — is it running?" >&2
        if _is_wsl; then
            echo -e "  ${DIM}     WSL2 detected. Start the native daemon: 'sudo service docker start'${NC}" >&2
            echo -e "  ${DIM}     or enable Docker Desktop → Settings → Resources → WSL integration.${NC}" >&2
        fi
    fi
}
