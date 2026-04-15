#!/bin/bash
# DocIntel — LMForge Engine Setup
# =================================
# Pulls required models and writes LLM_ENGINE / LLM_CHAT_URL to .env.
# LLM_MODEL is read from defaults.env (catalog tag format: model:size:quantization).
#
# Run once before first launch, or after changing LLM_MODEL in .env.
# After this script: run ./scripts/start.sh --build
#
# Usage:
#   ./scripts/setup-lmforge.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$PROJECT_DIR/.env"

# Load defaults first, then .env — but LLM_MODEL comes from defaults only
# (.env may have a stale model name; setup-lmforge.sh owns writing the correct one)
source "$PROJECT_DIR/config/defaults.env"
_DEFAULT_LLM_MODEL="$LLM_MODEL"
[ -f "$ENV_FILE" ] && source "$ENV_FILE"
LLM_MODEL="$_DEFAULT_LLM_MODEL"  # restore defaults.env value, ignore .env stale

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC} $*"; }
warn() { echo -e "  ${YELLOW}⚠${NC}  $*"; }
fail() { echo -e "  ${RED}✗${NC} $*" >&2; exit 1; }

echo "================================================"
echo "  DocIntel — LMForge Engine Setup"
echo "================================================"
echo ""

# =============================================================================
# Check prerequisites
# =============================================================================

if ! command -v lmforge &> /dev/null; then
    fail "lmforge binary not found. Install LMForge first, then re-run this script."
fi
ok "lmforge binary found: $(lmforge --version 2>/dev/null | head -1 || echo 'version unknown')"

# =============================================================================
# Resolve models from config
# LMForge catalog tag format: model:size:quantization  (e.g. qwen3.5:4b:4bit)
# =============================================================================

CHAT_MODEL="${LLM_MODEL}"
EMBED_MODEL="${LLM_EMBED_MODEL}"

echo ""
echo "  Chat model : $CHAT_MODEL"
echo "  Embed model: $EMBED_MODEL  (served by Infinity in Docker)"
echo ""

# =============================================================================
# Show installed models
# =============================================================================

echo "Installed LMForge models:"
lmforge models list 2>/dev/null || warn "Could not list models (daemon may not be running, that's OK)"
echo ""

# =============================================================================
# Pull chat model if not already installed
# =============================================================================

if lmforge models list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qx "$CHAT_MODEL"; then
    ok "Chat model '$CHAT_MODEL' already installed."
else
    echo "Pulling '$CHAT_MODEL' via LMForge (this may take a while)..."
    lmforge pull "$CHAT_MODEL"
    ok "Pulled: $CHAT_MODEL"
fi

# =============================================================================
# Write LLM engine vars to .env
# Only writes engine-specific vars — LLM_MODEL stays as-is (already correct format)
# =============================================================================

echo ""
echo "Writing LLM engine configuration to .env..."

[ -f "$ENV_FILE" ] || cp "$PROJECT_DIR/.env.example" "$ENV_FILE" 2>/dev/null || touch "$ENV_FILE"

_upsert_env() {
    local key="$1" val="$2"
    if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
        sed -i.bak "s|^${key}=.*|${key}=${val}|" "$ENV_FILE" && rm -f "$ENV_FILE.bak"
    else
        echo "${key}=${val}" >> "$ENV_FILE"
    fi
}

_upsert_env "LLM_ENGINE"   "lmforge"
_upsert_env "LLM_CHAT_URL" "http://host.docker.internal:11430/v1"
_upsert_env "LLM_MODEL"    "$CHAT_MODEL"

ok ".env updated (LLM_ENGINE=lmforge, LLM_CHAT_URL=:11430, LLM_MODEL=$CHAT_MODEL)"

# =============================================================================
# Done
# =============================================================================

echo ""
echo "================================================"
echo -e "  ${GREEN}${BOLD}LMForge engine setup complete.${NC}"
echo "================================================"
echo ""
echo "  Engine    : LMForge at http://localhost:11430"
echo "  Model     : $CHAT_MODEL"
echo "  Embeddings: Infinity (Docker, $EMBED_MODEL)"
echo ""
echo "  start.sh will auto-start the LMForge daemon if it isn't running."
echo "  Launch DocIntel:"
echo "    ./scripts/start.sh --build"
echo ""
echo "  To switch back to Ollama:"
echo "    Set LLM_ENGINE=ollama, LLM_CHAT_URL=http://host.docker.internal:11434/v1,"
echo "    and LLM_MODEL=qwen3.5:4b in .env, then: ./scripts/start.sh --build"
echo ""
