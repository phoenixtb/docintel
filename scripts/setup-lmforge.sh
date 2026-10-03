#!/bin/bash
# DocIntel Setup — LMForge Engine
# =================================
# Run once after cloning (or after changing models).
# Checks prerequisites, generates keys, creates .env, pulls Docker images,
# runs lmforge init, pulls chat + embed models, and writes LLM vars to .env.
# Does NOT start services — run ./scripts/start.sh for that.
#
# Usage:
#   ./scripts/setup-lmforge.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$PROJECT_DIR/.env"

# Load defaults first; .env may override, but LLM_MODEL is pinned from defaults
# (avoids stale model names surviving across .env edits)
source "$PROJECT_DIR/config/defaults.env"
_DEFAULT_CHAT_MODEL="$LLM_MODEL"
_DEFAULT_EMBED_MODEL="$LLM_EMBED_MODEL"
_DEFAULT_RERANK_MODEL="$LLM_RERANK_MODEL"
[ -f "$ENV_FILE" ] && source "$ENV_FILE"
LLM_MODEL="$_DEFAULT_CHAT_MODEL"
LLM_EMBED_MODEL="$_DEFAULT_EMBED_MODEL"
LLM_RERANK_MODEL="$_DEFAULT_RERANK_MODEL"

# Load engine-agnostic setup functions
# shellcheck source=lib/setup-common.sh
source "$SCRIPT_DIR/lib/setup-common.sh"

# Load LMForge installer
# shellcheck source=lib/install-lmforge.sh
source "$SCRIPT_DIR/lib/install-lmforge.sh"

echo "================================================"
echo "  DocIntel Setup — LMForge Engine"
echo "================================================"
echo ""

# =============================================================================
# Common prerequisites (Docker, compose, tofu, jq, openssl)
# =============================================================================

setup_common_prereqs

# =============================================================================
# LMForge prerequisite — install if missing
# =============================================================================

check_or_install_lmforge
echo ""

# =============================================================================
# Common: Zitadel keys, .env, generated.env stub, Docker pull
# =============================================================================

setup_common_zitadel_keys
setup_common_env
setup_common_docker_pull

# =============================================================================
# LMForge init — probe hardware, select engine, install runtime if needed
# =============================================================================

echo ""
echo "================================================"
echo "LMForge Init"
echo "================================================"

echo "Running lmforge init (hardware probe + runtime install)..."
lmforge init
ok "lmforge init complete"

# Linux: bind 0.0.0.0 so docker containers can reach the daemon via host-gateway.
# macOS (Darwin) is a no-op — host.docker.internal already reaches loopback there.
# keep_alive is all-platforms: default 5m idle-unload can race DocIntel's 30s
# query-embed timeout on cold reload. One restart covers both upserts.
_lf_cfg="$HOME/.lmforge/config.toml"
_lf_cfg_changed=0
if [ "$(uname -s)" = "Linux" ]; then
    if [ -f "$_lf_cfg" ] && grep -qE '^[[:space:]]*bind_address[[:space:]]*=[[:space:]]*"0\.0\.0\.0"' "$_lf_cfg"; then
        ok "LMForge already binds 0.0.0.0"
    elif [ -f "$_lf_cfg" ] && grep -qE '^[[:space:]]*bind_address[[:space:]]*=[[:space:]]*"127\.0\.0\.1"' "$_lf_cfg"; then
        sed -i.bak 's/^\([[:space:]]*bind_address[[:space:]]*=[[:space:]]*\)"127\.0\.0\.1"/\1"0.0.0.0"/' "$_lf_cfg" \
            && rm -f "${_lf_cfg}.bak"
        ok "LMForge bind_address set to 0.0.0.0 (was 127.0.0.1)"
        _lf_cfg_changed=1
    elif [ -f "$_lf_cfg" ] && grep -qE '^[[:space:]]*bind_address[[:space:]]*=' "$_lf_cfg"; then
        warn "LMForge bind_address is a custom value — leaving it unchanged"
    else
        # bind_address is a TOML top-level key — PREPEND so it can never land
        # inside a [section] appended at end-of-file (e.g. [orchestrator] below).
        mkdir -p "$(dirname "$_lf_cfg")"
        touch "$_lf_cfg"
        printf '%s\n%s\n%s' \
            '# DocIntel: bind on all interfaces so docker containers reach the daemon via host-gateway' \
            'bind_address = "0.0.0.0"' \
            "$(cat "$_lf_cfg")" > "${_lf_cfg}.tmp" && mv "${_lf_cfg}.tmp" "$_lf_cfg"
        ok "LMForge bind_address added: 0.0.0.0"
        _lf_cfg_changed=1
    fi
fi

# keep_alive lives under [orchestrator] in LMForge's config schema — a bare
# top-level key would be silently ignored.
if [ -f "$_lf_cfg" ] && grep -qE '^[[:space:]]*keep_alive[[:space:]]*=' "$_lf_cfg"; then
    ok "LMForge keep_alive already configured"
elif [ -f "$_lf_cfg" ] && grep -qE '^\[orchestrator\]' "$_lf_cfg"; then
    # Section exists without keep_alive — insert right after the header.
    sed -i.bak '/^\[orchestrator\]/a\
keep_alive = "30m" # DocIntel: default 5m idle-unload races the 30s query-embed timeout on cold reload
' "$_lf_cfg" && rm -f "${_lf_cfg}.bak"
    ok "LMForge keep_alive set to 30m (in existing [orchestrator])"
    _lf_cfg_changed=1
else
    mkdir -p "$(dirname "$_lf_cfg")"
    {
        echo ''
        echo '# DocIntel: default 5m idle-unload can race the 30s query-embed timeout on cold reload'
        echo '[orchestrator]'
        echo 'keep_alive = "30m"'
    } >> "$_lf_cfg"
    ok "LMForge keep_alive appended: 30m ([orchestrator])"
    _lf_cfg_changed=1
fi

if [ "$_lf_cfg_changed" = "1" ]; then
    if [ "$(uname -s)" = "Linux" ]; then
        if systemctl --user is-active --quiet lmforge 2>/dev/null; then
            systemctl --user restart lmforge
            ok "Restarted LMForge user service to apply config"
        elif curl -sm2 http://127.0.0.1:11430/health >/dev/null 2>&1; then
            warn "LMForge is running with the old config — restart it (lmforge stop / start.sh will restart it) to apply"
        fi
    elif curl -sm2 http://127.0.0.1:11430/health >/dev/null 2>&1; then
        echo "  Note: LMForge config updated — restart the daemon to apply it"
    fi
fi

# =============================================================================
# Resolve and pull models
# =============================================================================

CHAT_MODEL="$LLM_MODEL"
EMBED_MODEL="$LLM_EMBED_MODEL"
RERANK_MODEL="$LLM_RERANK_MODEL"

# Select chat model based on hardware.
# Apple Silicon (arm64 macOS) → LLM_MODEL (4B 4-bit by default).
# Everything else             → LLM_FALLBACK_MODEL (2B 4-bit by default).
if [[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]]; then
    ok "Apple Silicon detected — chat model: $CHAT_MODEL"
else
    CHAT_MODEL="$LLM_FALLBACK_MODEL"
    ok "Non-Apple Silicon detected — using fallback chat model: $CHAT_MODEL"
fi

echo ""
echo "================================================"
echo "Pulling LMForge Models"
echo "================================================"
echo ""
echo "  Chat model  : $CHAT_MODEL"
echo "  Embed model : $EMBED_MODEL"
echo "  Rerank model: $RERANK_MODEL"
echo ""

echo "Installed models:"
lmforge models list 2>/dev/null || warn "Could not list models (daemon may not be running yet)"
echo ""

if lmforge models list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qx "$CHAT_MODEL"; then
    ok "Chat model '$CHAT_MODEL' already installed."
else
    echo "Pulling '$CHAT_MODEL'..."
    lmforge pull "$CHAT_MODEL"
    ok "Pulled: $CHAT_MODEL"
fi

if lmforge models list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qx "$EMBED_MODEL"; then
    ok "Embed model '$EMBED_MODEL' already installed."
else
    echo "Pulling '$EMBED_MODEL'..."
    lmforge pull "$EMBED_MODEL"
    ok "Pulled: $EMBED_MODEL"
fi

if lmforge models list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qx "$RERANK_MODEL"; then
    ok "Rerank model '$RERANK_MODEL' already installed."
else
    echo "Pulling '$RERANK_MODEL'..."
    lmforge pull "$RERANK_MODEL"
    ok "Pulled: $RERANK_MODEL"
fi

# =============================================================================
# Write LLM engine vars to .env
# =============================================================================

echo ""
echo "================================================"
echo "Writing LLM engine configuration to .env"
echo "================================================"

_upsert_env() {
    local key="$1" val="$2"
    if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
        sed -i.bak "s|^${key}=.*|${key}=${val}|" "$ENV_FILE" && rm -f "$ENV_FILE.bak"
    else
        echo "${key}=${val}" >> "$ENV_FILE"
    fi
}

_upsert_env "LLM_ENGINE"      "lmforge"
_upsert_env "LLM_CHAT_URL"    "http://host.docker.internal:11430/v1"
_upsert_env "LLM_EMBED_URL"   "http://host.docker.internal:11430/v1"
_upsert_env "LLM_MODEL"       "$CHAT_MODEL"
_upsert_env "LLM_EMBED_MODEL" "$EMBED_MODEL"

ok ".env updated (LLM_ENGINE=lmforge, :11430, model=$CHAT_MODEL, embed=$EMBED_MODEL)"

# =============================================================================
# Done
# =============================================================================

echo ""
echo "================================================"
echo -e "  ${GREEN}${BOLD}Setup complete — LMForge engine.${NC}"
echo "================================================"
echo ""
echo "  Engine    : LMForge at http://localhost:11430"
echo "  Chat model: $CHAT_MODEL"
echo "  Embed     : $EMBED_MODEL (LMForge, http://localhost:11430)"
echo ""
echo "  start.sh will auto-start the LMForge daemon if it isn't running."
echo "  Next step:"
echo "    ./scripts/start.sh           # Start all services"
echo "    ./scripts/start.sh --build   # Rebuild images then start"
echo ""
echo "  To switch to Ollama:"
echo "    Set LLM_ENGINE=ollama in .env, then: ./scripts/setup.sh"
echo ""
