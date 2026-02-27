#!/bin/bash
# DocIntel Cleanup Script
# =======================
# Stops AND removes all DocIntel containers.
# Optionally removes volumes (data) and Ollama models.
#
# Use ./scripts/stop.sh if you just want to stop (not remove) containers.

set -e

echo "================================================"
echo "DocIntel Cleanup"
echo "================================================"
echo ""

REMOVE_MODELS=false
REMOVE_VOLUMES=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --all)
            REMOVE_MODELS=true
            REMOVE_VOLUMES=true
            shift
            ;;
        --models)
            REMOVE_MODELS=true
            shift
            ;;
        --volumes)
            REMOVE_VOLUMES=true
            shift
            ;;
        -h|--help)
            echo "Usage: ./cleanup.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --all       Remove containers, volumes, AND Ollama models"
            echo "  --models    Remove only Ollama models"
            echo "  --volumes   Remove containers AND Docker volumes (data loss!)"
            echo "  -h, --help  Show this help message"
            echo ""
            echo "Default (no options): Stops and removes containers only (preserves data)"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information."
            exit 1
            ;;
    esac
done

# =============================================================================
# Stop and Remove Docker Containers
# =============================================================================

echo "Stopping and removing Docker containers (all profiles)..."
# Must specify all profiles to stop all containers
docker compose --profile app --profile auth down 2>/dev/null || true
echo "Containers removed."

if [ "$REMOVE_VOLUMES" = true ]; then
    echo ""
    echo "WARNING: This will delete ALL data (PostgreSQL, Qdrant, Redis, MinIO, Authentik)!"
    read -p "Are you sure? (y/N): " confirm
    
    if [[ "$confirm" =~ ^[Yy]$ ]]; then
        echo "Removing Docker volumes..."
        docker compose --profile app --profile auth down -v 2>/dev/null || true
        
        # Also remove any orphaned volumes with docintel prefix
        docker volume ls -q | grep -E "^docintel" | xargs -r docker volume rm 2>/dev/null || true
        
        echo "Docker volumes removed."
        
        # Clean up unused Docker resources (images, build cache, etc.)
        echo ""
        echo "Cleaning up unused Docker resources..."
        docker system prune -f -a --volumes 2>/dev/null || true
        echo "Docker system cleanup complete."
    else
        echo "Skipped volume removal."
    fi
fi

# =============================================================================
# Remove Ollama Models
# =============================================================================

if [ "$REMOVE_MODELS" = true ]; then
    echo ""
    echo "================================================"
    echo "Removing Ollama Models"
    echo "================================================"
    echo ""
    echo "This will free up approximately 8-10GB of disk space."
    echo ""
    
    MODELS=("qwen3:8b" "phi3:mini" "nomic-embed-text")
    
    # Check if Ollama is available
    if ! command -v ollama &> /dev/null; then
        echo "Ollama CLI not found. Models may need to be removed manually."
        echo ""
        echo "Manual removal locations:"
        echo "  macOS: rm -rf ~/.ollama/models"
        echo "  Linux: rm -rf ~/.ollama/models"
        echo ""
    else
        for model in "${MODELS[@]}"; do
            echo "Removing ${model}..."
            if ollama list 2>/dev/null | grep -q "${model}"; then
                ollama rm "${model}" 2>/dev/null || echo "  Warning: Could not remove ${model}"
            else
                echo "  ${model} not found, skipping."
            fi
        done
        echo ""
        echo "Models removed."
    fi
fi

# =============================================================================
# Summary
# =============================================================================

echo ""
echo "================================================"
echo "Cleanup Complete"
echo "================================================"
echo ""
echo "Removed:"
echo "  [x] Docker containers (stopped and removed)"
if [ "$REMOVE_VOLUMES" = true ]; then
    echo "  [x] Docker volumes (all data deleted)"
    echo "  [x] Docker system prune (images, build cache cleaned)"
else
    echo "  [ ] Docker volumes (data preserved, use --volumes to remove)"
fi
if [ "$REMOVE_MODELS" = true ]; then
    echo "  [x] Ollama models (~8-10GB freed)"
else
    echo "  [ ] Ollama models (use --all or --models to remove)"
fi
echo ""

# Show remaining disk usage if possible
if [ "$REMOVE_MODELS" = false ] && command -v ollama &> /dev/null; then
    echo "Disk usage by Ollama models:"
    du -sh ~/.ollama 2>/dev/null || echo "  Unable to calculate"
    echo ""
fi

echo "Commands:"
echo "  ./scripts/start.sh    # Start fresh (will recreate containers)"
echo "  ./scripts/setup.sh    # Re-pull images if needed"
echo ""
