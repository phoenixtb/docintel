#!/bin/bash
# DocIntel Stop Script
# ====================
# Stops all containers but keeps them (fast restart with start.sh)
#
# Usage:
#   ./scripts/stop.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

echo "================================================"
echo "Stopping DocIntel"
echo "================================================"
echo ""
echo "Stopping all containers (keeping them for fast restart)..."

docker compose stop

echo ""
echo "Done! All containers stopped."
echo ""
echo "Commands:"
echo "  ./scripts/start.sh      # Start again (fast - containers already exist)"
echo "  ./scripts/cleanup.sh    # Remove containers (next start will be slower)"
echo ""
