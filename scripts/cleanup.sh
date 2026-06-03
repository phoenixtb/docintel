#!/bin/bash
# DocIntel Cleanup Script
# =======================
# Stops AND removes all DocIntel containers (plus orphans).
# Optionally removes volumes (data), the Docker build cache, and Ollama models.
#
# Use ./scripts/stop.sh if you just want to stop (not remove) containers.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# Load env (defaults + .env) so docker compose interpolation and the compose-file
# chain resolve exactly as they do in start.sh. Exported so child processes see them.
set -a
# shellcheck source=../config/defaults.env
source "$PROJECT_DIR/config/defaults.env"
[ -f "$PROJECT_DIR/.env" ] && source "$PROJECT_DIR/.env"
set +a

# Resolve the active Docker context (OrbStack/desktop/engine) so we clean up on
# the same daemon start.sh uses.
if [ -f "$SCRIPT_DIR/lib/docker_context.sh" ]; then
    # shellcheck source=lib/docker_context.sh
    source "$SCRIPT_DIR/lib/docker_context.sh"
    ensure_docker_context >/dev/null 2>&1 || true
fi

# Build the SAME -f chain as start.sh (compose.yml + override + gpu + storage),
# so `down` targets every container `up` created instead of leaving residuals.
# shellcheck source=lib/profile_config.sh
source "$SCRIPT_DIR/lib/profile_config.sh"
compose_file_chain "$PROJECT_DIR"   # exports COMPOSE_FILES

echo "================================================"
echo "DocIntel Cleanup"
echo "================================================"
echo ""

REMOVE_MODELS=false
REMOVE_VOLUMES=false
DATA_ONLY=false
PRUNE_CACHE=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --all)
            REMOVE_MODELS=true
            REMOVE_VOLUMES=true
            PRUNE_CACHE=true
            shift
            ;;
        --data)
            DATA_ONLY=true
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
        --cache)
            PRUNE_CACHE=true
            shift
            ;;
        -h|--help)
            echo "Usage: ./cleanup.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --data      Wipe all data volumes only (Qdrant, Postgres, Redis, MinIO)"
            echo "              Keeps Docker images, build cache, and Ollama models intact."
            echo "  --volumes   Remove containers AND Docker volumes (data loss!)"
            echo "  --cache     Prune the Docker build cache (reclaims GBs; next build is slower)"
            echo "  --models    Remove only Ollama models"
            echo "  --all       Remove containers, volumes, build cache, AND Ollama models"
            echo "  -h, --help  Show this help message"
            echo ""
            echo "Default (no options): removes containers + orphans + dangling images"
            echo "                      (preserves data, build cache, and models)"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information."
            exit 1
            ;;
    esac
done

# ── helpers ───────────────────────────────────────────────────────────────────

# Bring the whole project down on the resolved compose chain, removing orphan
# containers (the usual cause of "endpoint already exists" on the next start).
compose_down() {
    # shellcheck disable=SC2086
    docker compose $COMPOSE_FILES down --remove-orphans "$@" 2>/dev/null \
        || docker compose down --remove-orphans "$@" 2>/dev/null \
        || true
}

# Human-readable total build-cache size (column 4 of `docker system df`), or
# empty if unknown. Columns: TYPE | TOTAL | ACTIVE | SIZE | RECLAIMABLE.
build_cache_size() {
    docker system df 2>/dev/null \
        | awk -F'  +' '/Build Cache/ {print $4}' | head -1
}

prune_build_cache() {
    local before; before=$(build_cache_size)
    echo "Pruning Docker build cache (was ${before:-unknown})..."
    docker builder prune -af >/dev/null 2>&1 || true
    echo "Build cache pruned."
}

# =============================================================================
# Stop and Remove Docker Containers (+ orphans)
# =============================================================================

echo "Stopping and removing Docker containers (+ orphans)..."
if [ "$REMOVE_VOLUMES" = true ] || [ "$DATA_ONLY" = true ]; then
    : # volume removal handled in the dedicated sections below
fi
compose_down
echo "Containers removed."

# =============================================================================
# Wipe Data Volumes Only (--data)
# =============================================================================

if [ "$DATA_ONLY" = true ]; then
    echo ""
    echo "WARNING: This will delete all application data (Qdrant, PostgreSQL, Redis, MinIO, ClickHouse)."
    echo "Docker images, build cache, and Ollama models are preserved."
    if [ -n "${DOCINTEL_DATA_DIR:-}" ]; then
        echo ""
        echo "NOTE: DOCINTEL_DATA_DIR is set ($DOCINTEL_DATA_DIR)."
        echo "      Data lives in bind mounts there, not named volumes — this will NOT"
        echo "      delete it. Remove it manually if intended:  rm -rf \"$DOCINTEL_DATA_DIR\"/*"
    fi
    read -p "Are you sure? (y/N): " confirm

    if [[ "$confirm" =~ ^[Yy]$ ]]; then
        echo "Removing data volumes..."
        for vol in qdrant-data postgres-data redis-data minio-data clickhouse-data clickhouse-logs huggingface-cache docling-cache prometheus-data grafana-data; do
            docker volume rm "docintel_${vol}" 2>/dev/null && echo "  removed docintel_${vol}" || echo "  skipped docintel_${vol} (not found / bind mount)"
        done
        echo "Data volumes removed."

        # Reset Terraform state (must match wiped Docker volumes or next tofu apply breaks)
        echo "Resetting Terraform state..."
        rm -f "$PROJECT_DIR/terraform/stacks/infra/terraform.tfstate" \
              "$PROJECT_DIR/terraform/stacks/infra/terraform.tfstate.backup" \
              "$PROJECT_DIR/terraform/stacks/identity/terraform.tfstate" \
              "$PROJECT_DIR/terraform/stacks/identity/terraform.tfstate.backup" 2>/dev/null || true

        # Reset generated runtime config files
        echo "Resetting generated runtime config..."
        rm -f "$PROJECT_DIR/config/zitadel/actions-target-id" \
              "$PROJECT_DIR/config/zitadel/actions-signing-key" 2>/dev/null || true
        # Reset generated.env to empty stub so Docker Compose still starts
        cat > "$PROJECT_DIR/config/zitadel/generated.env" <<'EOF'
# Auto-generated by start.sh after terraform apply — do not edit manually.
# Run ./scripts/start.sh to regenerate.
ZITADEL_CLIENT_ID=
ZITADEL_PROJECT_ID=
ZITADEL_SERVICE_ACCOUNT_PAT=
ZITADEL_ACTIONS_SIGNING_KEY=
EOF
        echo "Terraform state and generated config reset."

        # Prune stopped containers, unused networks, dangling images.
        # Build cache is preserved unless --cache was also passed.
        echo ""
        echo "Pruning dangling Docker resources..."
        docker container prune -f >/dev/null 2>&1 || true
        docker image prune -f >/dev/null 2>&1 || true
        docker network prune -f >/dev/null 2>&1 || true
        echo "Docker prune complete."
        [ "$PRUNE_CACHE" = true ] && prune_build_cache
    else
        echo "Skipped."
    fi

    echo ""
    echo "================================================"
    echo "Cleanup Complete"
    echo "================================================"
    echo ""
    echo "  [x] Docker containers + orphans removed"
    echo "  [x] Data volumes wiped"
    echo "  [x] Dangling images pruned"
    [ "$PRUNE_CACHE" = true ] && echo "  [x] Build cache pruned" || echo "  [ ] Build cache preserved (use --cache to reclaim)"
    echo "  [ ] Named Docker images preserved"
    echo "  [ ] Ollama models preserved"
    echo ""
    echo "Commands:"
    echo "  ./scripts/start.sh    # Start fresh"
    echo ""
    exit 0
fi

if [ "$REMOVE_VOLUMES" = true ]; then
    echo ""
    echo "WARNING: This will delete ALL data (PostgreSQL, Qdrant, Redis, MinIO, Zitadel)!"
    if [ -n "${DOCINTEL_DATA_DIR:-}" ]; then
        echo ""
        echo "NOTE: DOCINTEL_DATA_DIR is set ($DOCINTEL_DATA_DIR)."
        echo "      Data lives in bind mounts there — remove manually if intended:"
        echo "      rm -rf \"$DOCINTEL_DATA_DIR\"/*"
    fi
    read -p "Are you sure? (y/N): " confirm

    if [[ "$confirm" =~ ^[Yy]$ ]]; then
        echo "Removing Docker volumes..."
        compose_down -v

        # Also remove any orphaned volumes with docintel prefix
        docker volume ls -q | grep -E "^docintel" | xargs -r docker volume rm 2>/dev/null || true

        echo "Docker volumes removed."

        # Reset Terraform state
        echo "Resetting Terraform state..."
        rm -f "$PROJECT_DIR/terraform/stacks/infra/terraform.tfstate" \
              "$PROJECT_DIR/terraform/stacks/infra/terraform.tfstate.backup" \
              "$PROJECT_DIR/terraform/stacks/identity/terraform.tfstate" \
              "$PROJECT_DIR/terraform/stacks/identity/terraform.tfstate.backup" 2>/dev/null || true

        # Reset generated runtime config files
        echo "Resetting generated runtime config..."
        rm -f "$PROJECT_DIR/config/zitadel/actions-target-id" \
              "$PROJECT_DIR/config/zitadel/actions-signing-key" \
              "$PROJECT_DIR/config/zitadel/system-api-private.pem" 2>/dev/null || true
        cat > "$PROJECT_DIR/config/zitadel/generated.env" <<'EOF'
# Auto-generated by start.sh after terraform apply — do not edit manually.
# Run ./scripts/start.sh to regenerate.
ZITADEL_CLIENT_ID=
ZITADEL_PROJECT_ID=
ZITADEL_SERVICE_ACCOUNT_PAT=
ZITADEL_ACTIONS_SIGNING_KEY=
EOF
        echo "Terraform state and generated config reset."

        # Prune unused images and networks. Build cache only when --cache/--all.
        echo ""
        echo "Pruning unused images and networks..."
        docker image prune -f -a >/dev/null 2>&1 || true
        docker network prune -f >/dev/null 2>&1 || true
        echo "Docker cleanup complete."
        [ "$PRUNE_CACHE" = true ] && prune_build_cache
    else
        echo "Skipped volume removal."
    fi
fi

# =============================================================================
# Default path: prune dangling images so repeated rebuilds don't pile up.
# (Skipped when a volume path above already handled pruning.)
# =============================================================================

if [ "$REMOVE_VOLUMES" = false ]; then
    echo ""
    echo "Pruning dangling images (old layers from rebuilds)..."
    docker image prune -f >/dev/null 2>&1 || true
    docker network prune -f >/dev/null 2>&1 || true
    echo "Dangling images pruned."
    [ "$PRUNE_CACHE" = true ] && prune_build_cache
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

    MODELS=("$DEFAULT_LLM_MODEL" "$DEFAULT_FALLBACK_MODEL" "$DEFAULT_EMBED_MODEL")

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
echo "  [x] Docker containers + orphans (stopped and removed)"
echo "  [x] Dangling images pruned"
if [ "$REMOVE_VOLUMES" = true ]; then
    echo "  [x] Docker volumes (all data deleted)"
else
    echo "  [ ] Docker volumes (data preserved, use --volumes to remove)"
fi
if [ "$PRUNE_CACHE" = true ]; then
    echo "  [x] Build cache pruned"
else
    echo "  [ ] Build cache preserved ($(build_cache_size) reclaimable, use --cache)"
fi
if [ "$REMOVE_MODELS" = true ]; then
    echo "  [x] Ollama models (~8-10GB freed)"
else
    echo "  [ ] Ollama models (use --all or --models to remove)"
fi
echo ""

echo "Commands:"
echo "  ./scripts/start.sh            # Start fresh (will recreate containers)"
echo "  ./scripts/cleanup.sh --data   # Wipe data only (keeps images + cache + models)"
echo "  ./scripts/cleanup.sh --cache  # Reclaim build cache (GBs) when low on disk"
echo ""
