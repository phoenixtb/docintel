#!/bin/bash
# DocIntel Setup Script
# =====================
# Run this once after cloning the repository.
# Generates RSA key pair for Zitadel System API, pulls images, pulls Ollama models.
# Does NOT start services — run ./scripts/start.sh for that.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Load model defaults (single source of truth)
# shellcheck source=../config/defaults.env
source "$PROJECT_DIR/config/defaults.env"

echo "================================================"
echo "DocIntel Setup"
echo "================================================"

# =============================================================================
# Check Prerequisites
# =============================================================================

echo ""
echo "================================================"
echo "Checking Prerequisites"
echo "================================================"

prereq_ok=true
check_cmd() {
    local cmd="$1" install_hint="$2"
    if command -v "$cmd" &>/dev/null; then
        echo "  ✓ $cmd"
    else
        echo "  ✗ $cmd — not found"
        echo "    Install: $install_hint"
        prereq_ok=false
    fi
}

check_cmd docker   "https://www.docker.com/products/docker-desktop/"
check_cmd tofu     "macOS: brew install opentofu | Linux: https://opentofu.org/docs/intro/install/"
check_cmd jq       "macOS: brew install jq | Linux: apt install jq / yum install jq"
check_cmd openssl  "macOS: brew install openssl | Linux: apt install openssl"
check_cmd ollama   "macOS: brew install ollama | https://ollama.ai/download"

if [ "$prereq_ok" = false ]; then
    echo ""
    echo "Install the missing tools above, then re-run ./scripts/setup.sh"
    exit 1
fi

# Verify Docker Compose
if ! docker compose version &>/dev/null; then
    echo "  ✗ docker compose — plugin not available. Update Docker Desktop."
    exit 1
fi
echo "  ✓ docker compose"

echo ""


# =============================================================================
# Check Ollama (Native Installation Required)
# =============================================================================

echo ""
echo "Checking Ollama..."

OLLAMA_RUNNING=false

# Check if Ollama is installed
if command -v ollama &> /dev/null; then
    echo "Ollama is installed: $(ollama --version 2>/dev/null || echo 'version unknown')"
    
    # Check if Ollama is running by trying to reach its API
    if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
        echo "Ollama is running."
        OLLAMA_RUNNING=true
    else
        echo "Ollama is installed but not running."
        echo ""
        echo "Please start Ollama:"
        echo "  - On macOS: Open the Ollama app from Applications"
        echo "  - On Linux: Run 'ollama serve' in a separate terminal"
        echo ""
        read -p "Press Enter once Ollama is running, or Ctrl+C to abort..."
        
        # Verify again
        if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
            OLLAMA_RUNNING=true
        else
            echo "Error: Ollama is still not responding on http://localhost:11434"
            exit 1
        fi
    fi
else
    echo ""
    echo "================================================"
    echo "Ollama Not Found"
    echo "================================================"
    echo ""
    echo "Ollama is required for running LLMs locally."
    echo "Native installation is recommended for best performance."
    echo ""
    echo "Install Ollama:"
    echo "  macOS:   brew install ollama"
    echo "           OR download from https://ollama.ai/download"
    echo ""
    echo "  Linux:   curl -fsSL https://ollama.ai/install.sh | sh"
    echo ""
    echo "After installation, start Ollama and run this script again."
    exit 1
fi

# =============================================================================
# Generate RSA key pair for Zitadel System API authentication
# The private key signs JWTs that Terraform uses to authenticate against Zitadel.
# The public key is registered in config/zitadel/system-api.yaml (mounted into
# the zitadel-api container at startup).
# =============================================================================

SYSTEM_API_KEY_DIR="$PROJECT_DIR/config/zitadel"
SYSTEM_API_PRIVATE_KEY="$SYSTEM_API_KEY_DIR/system-api-private.pem"
SYSTEM_API_PUBLIC_KEY="$SYSTEM_API_KEY_DIR/system-api-public.pem"

echo ""
echo "================================================"
echo "Zitadel System API Key Pair"
echo "================================================"

if [ ! -f "$SYSTEM_API_PRIVATE_KEY" ]; then
    echo "Generating RSA-4096 key pair..."
    openssl genrsa -out "$SYSTEM_API_PRIVATE_KEY" 4096 2>/dev/null
    openssl rsa -in "$SYSTEM_API_PRIVATE_KEY" -pubout -out "$SYSTEM_API_PUBLIC_KEY" 2>/dev/null
    chmod 600 "$SYSTEM_API_PRIVATE_KEY"
    echo "  Private key: config/zitadel/system-api-private.pem (gitignored)"
    echo "  Public key:  config/zitadel/system-api-public.pem"
else
    echo "Key pair already exists — skipping."
fi

# =============================================================================
# Create .env
# =============================================================================

if [ ! -f .env ]; then
    echo ""
    echo "================================================"
    echo "Creating .env"
    echo "================================================"
    cp .env.example .env
    echo ""
    echo "  IMPORTANT: Edit .env and set:"
    echo "    ZITADEL_MASTERKEY=<exactly 32 chars>   (generate: openssl rand -hex 16)"
    echo ""
    echo "  NOTE: ZITADEL_CLIENT_ID, ZITADEL_SERVICE_ACCOUNT_PAT, and"
    echo "  ZITADEL_ACTIONS_SIGNING_KEY are auto-generated by start.sh via"
    echo "  Terraform and written to config/zitadel/generated.env."
    echo ""
else
    echo ""
    echo ".env already exists, skipping."
fi

# =============================================================================
# Generate Internal Gateway Secret
# =============================================================================

echo ""
echo "================================================"
echo "Internal Gateway Secret"
echo "================================================"

ENV_FILE="$PROJECT_DIR/.env"

if grep -q "^INTERNAL_GATEWAY_SECRET=.\+" "$ENV_FILE" 2>/dev/null; then
    echo "INTERNAL_GATEWAY_SECRET already set — skipping."
else
    secret=$(openssl rand -hex 32)
    if grep -q "^INTERNAL_GATEWAY_SECRET=" "$ENV_FILE" 2>/dev/null; then
        # Replace empty placeholder
        sed -i.bak "s/^INTERNAL_GATEWAY_SECRET=.*/INTERNAL_GATEWAY_SECRET=$secret/" "$ENV_FILE" && rm -f "$ENV_FILE.bak"
    else
        echo "INTERNAL_GATEWAY_SECRET=$secret" >> "$ENV_FILE"
    fi
    echo "  Generated INTERNAL_GATEWAY_SECRET (32-byte hex) → .env"
fi

# =============================================================================
# Pull Docker Images (without starting)
# =============================================================================

echo ""
echo "================================================"
echo "Pulling Docker Images"
echo "================================================"

# Pull all images without starting containers
docker compose pull

echo "Docker images pulled."

# =============================================================================
# Pull Ollama Models
# =============================================================================

echo ""
echo "================================================"
echo "Pulling Ollama Models"
echo "================================================"
echo "This will download ~8-10GB of models. Please wait..."
echo ""

# List of required models
MODELS=("$DEFAULT_LLM_MODEL" "$DEFAULT_FALLBACK_MODEL" "$DEFAULT_EMBED_MODEL")

for model in "${MODELS[@]}"; do
    echo "Pulling ${model}..."
    if ollama list | grep -q "^${model}"; then
        echo "  ${model} already exists, skipping."
    else
        ollama pull "${model}"
        echo "  ${model} pulled successfully."
    fi
done

echo ""
echo "All models ready!"

# =============================================================================
# Setup Complete
# =============================================================================

echo ""
echo "================================================"
echo "Setup Complete!"
echo "================================================"
echo ""
echo "Ollama models installed:"
for model in "${MODELS[@]}"; do
    echo "  - ${model}"
done
echo ""
echo "Docker images pulled and ready."
echo ""
echo "Next step - Start the application:"
echo "  ./scripts/start.sh           # Start all services + run Terraform provisioning"
echo "  ./scripts/start.sh --build   # Rebuild images then start"
echo ""
echo "Other commands:"
echo "  ./scripts/stop.sh                  # Stop containers (preserves data)"
echo "  ./scripts/cleanup.sh               # Remove containers only"
echo "  ./scripts/cleanup.sh --data        # Wipe all data volumes"
echo "  ./scripts/cleanup.sh --all         # Full wipe (volumes + Ollama models)"
echo ""
