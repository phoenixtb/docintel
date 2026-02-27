#!/bin/bash
# DocIntel Setup Script
# =====================
# Run this once after cloning the repository

set -e

echo "================================================"
echo "DocIntel Setup"
echo "================================================"

# =============================================================================
# Check Prerequisites
# =============================================================================

# Check Docker
if ! command -v docker &> /dev/null; then
    echo "Error: Docker is not installed. Please install Docker Desktop."
    exit 1
fi

# Check Docker Compose
if ! docker compose version &> /dev/null; then
    echo "Error: Docker Compose is not available. Please update Docker Desktop."
    exit 1
fi

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
# Create .env
# =============================================================================

if [ ! -f .env ]; then
    echo "Creating .env from .env.example..."
    cp .env.example .env
    echo "Created .env - customize as needed."
else
    echo ".env already exists, skipping."
fi

# =============================================================================
# Pull Docker Images (without starting)
# =============================================================================

echo ""
echo "================================================"
echo "Pulling Docker Images"
echo "================================================"

# Pull all images without starting containers
docker compose --profile app --profile auth pull

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
MODELS=("qwen3.5:27b" "phi3:mini" "nomic-embed-text")

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
echo "  ./scripts/start.sh           # Start all services (with authentication)"
echo "  ./scripts/start.sh --no-auth # Start without authentication (dev mode)"
echo ""
echo "Other commands:"
echo "  ./scripts/stop.sh            # Stop application services"
echo "  ./scripts/cleanup.sh         # Stop all containers (preserves data)"
echo "  ./scripts/cleanup.sh --all   # Stop + remove volumes + Ollama models"
echo ""
