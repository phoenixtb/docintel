#!/usr/bin/env bash
# Sync all DocIntel Python projects with UV (Python 3.12).
# Run from repo root: ./scripts/uv-sync-all.sh [--extra dev]
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PYTHON_VERSION="${DOCINTEL_PYTHON:-3.12}"
EXTRA="${1:---extra dev}"

if ! command -v uv &>/dev/null; then
    echo "error: uv not found — install with: brew install uv" >&2
    exit 1
fi

PROJECTS=(
    "$PROJECT_DIR/lib/docintel-common"
    "$PROJECT_DIR/services/data-loader"
    "$PROJECT_DIR/services/ingestion-service"
    "$PROJECT_DIR/services/rag-service"
    "$PROJECT_DIR/services/analytics-service-py"
    "$PROJECT_DIR/tests/integration"
)

echo "DocIntel UV sync (Python $PYTHON_VERSION)"
for dir in "${PROJECTS[@]}"; do
    if [[ ! -f "$dir/pyproject.toml" ]]; then
        echo "skip: $dir (no pyproject.toml)"
        continue
    fi
    echo "→ $(basename "$(dirname "$dir")")/$(basename "$dir")"
    (cd "$dir" && uv sync --python "$PYTHON_VERSION" $EXTRA)
done
echo "done"
