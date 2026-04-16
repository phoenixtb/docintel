#!/bin/bash
# DocIntel Setup — Ollama Engine
# ================================
# Run once after cloning. Checks prerequisites, generates keys, creates .env,
# pulls Docker images, and pulls Ollama models.
# Does NOT start services — run ./scripts/start.sh for that.
#
# For LMForge engine: run ./scripts/setup-lmforge.sh instead.
# The docintel.sh menu dispatches automatically based on LLM_ENGINE in .env.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Load defaults (single source of truth for model names)
# shellcheck source=../config/defaults.env
source "$PROJECT_DIR/config/defaults.env"

# Load engine-agnostic setup functions
# shellcheck source=lib/setup-common.sh
source "$SCRIPT_DIR/lib/setup-common.sh"

echo "================================================"
echo "DocIntel Setup — Ollama Engine"
echo "================================================"

# =============================================================================
# Common prerequisites (Docker, compose, tofu, jq, openssl)
# =============================================================================

setup_common_prereqs

# =============================================================================
# Ollama prerequisite
# =============================================================================

echo "================================================"
echo "Checking Ollama"
echo "================================================"

if ! command -v ollama &>/dev/null; then
    echo ""
    echo "Ollama not found."
    echo "  macOS:  brew install ollama  OR  https://ollama.ai/download"
    echo "  Linux:  curl -fsSL https://ollama.ai/install.sh | sh"
    echo ""
    echo "After installation, start Ollama and re-run this script."
    echo "OR: set LLM_ENGINE=lmforge in .env and run ./scripts/setup-lmforge.sh"
    exit 1
fi

ok "ollama $(ollama --version 2>/dev/null | head -1 || echo '')"

if ! curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo ""
    warn "Ollama is installed but not running."
    echo "  - macOS: Open the Ollama app from Applications"
    echo "  - Linux: run 'ollama serve' in a separate terminal"
    echo ""
    read -rp "Press Enter once Ollama is running, or Ctrl+C to abort..."
    if ! curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
        fail "Ollama still not responding at http://localhost:11434"
    fi
fi

ok "Ollama is running"
echo ""

# =============================================================================
# Common: Zitadel keys, .env, generated.env stub, Docker pull
# =============================================================================

setup_common_zitadel_keys
setup_common_env
setup_common_docker_pull

# =============================================================================
# Pull Ollama models
# =============================================================================

echo ""
echo "================================================"
echo "Pulling Ollama Models"
echo "================================================"
echo "This will download ~8-10GB of models. Please wait..."
echo ""

MODELS=("$DEFAULT_LLM_MODEL" "$DEFAULT_FALLBACK_MODEL" "$DEFAULT_EMBED_MODEL")

for model in "${MODELS[@]}"; do
    if ollama list | grep -q "^${model}"; then
        ok "'${model}' already installed."
    else
        echo "Pulling '${model}'..."
        ollama pull "${model}"
        ok "Pulled: ${model}"
    fi
done

# =============================================================================
# Done
# =============================================================================

echo ""
echo "================================================"
echo -e "  ${GREEN}${BOLD}Setup complete — Ollama engine.${NC}"
echo "================================================"
echo ""
echo "  Models ready:"
for model in "${MODELS[@]}"; do
    echo "    - ${model}"
done
echo ""
echo "  Next step:"
echo "    ./scripts/start.sh           # Start all services"
echo "    ./scripts/start.sh --build   # Rebuild images then start"
echo ""
