#!/usr/bin/env bash
# DocIntel Backup Script
# ======================
# Backs up PostgreSQL, Qdrant, and MinIO data.
# Run daily via cron or manually: ./scripts/backup.sh
#
# Usage:
#   ./scripts/backup.sh [--destination /path/to/backups]
#
# Defaults:
#   BACKUP_DIR=./backups/<timestamp>
#   RETENTION_DAYS=7 (auto-prune old backups)

set -euo pipefail

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="${BACKUP_DESTINATION:-./backups}/${TIMESTAMP}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"

mkdir -p "${BACKUP_DIR}"

echo "==> DocIntel Backup — ${TIMESTAMP}"
echo "==> Destination: ${BACKUP_DIR}"

# =============================================================================
# PostgreSQL Backup
# =============================================================================
echo ""
echo "[1/3] Backing up PostgreSQL..."

docker compose -f "${COMPOSE_FILE}" exec -T postgres \
    pg_dump \
    -U docintel \
    -d docintel \
    --format=custom \
    --compress=9 \
    > "${BACKUP_DIR}/postgres_docintel.pgdump"

echo "      Saved: ${BACKUP_DIR}/postgres_docintel.pgdump"

# Dump Langfuse DB as well
docker compose -f "${COMPOSE_FILE}" exec -T postgres \
    pg_dump -U docintel -d langfuse --format=custom --compress=9 \
    > "${BACKUP_DIR}/postgres_langfuse.pgdump" 2>/dev/null || true

echo "      Saved: ${BACKUP_DIR}/postgres_langfuse.pgdump (if exists)"

# =============================================================================
# Qdrant Snapshot
# =============================================================================
echo ""
echo "[2/3] Backing up Qdrant vector collections..."

QDRANT_URL="${QDRANT_URL:-http://localhost:6333}"

# List all collections
COLLECTIONS=$(curl -sf "${QDRANT_URL}/collections" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for c in data.get('result', {}).get('collections', []):
    print(c['name'])
" 2>/dev/null || echo "")

if [ -z "${COLLECTIONS}" ]; then
    echo "      No Qdrant collections found or Qdrant unreachable — skipping."
else
    mkdir -p "${BACKUP_DIR}/qdrant"
    for COLLECTION in ${COLLECTIONS}; do
        echo "      Snapshotting collection: ${COLLECTION}"
        SNAPSHOT_RESPONSE=$(curl -sf -X POST "${QDRANT_URL}/collections/${COLLECTION}/snapshots" \
            -H "Content-Type: application/json" || echo "{}")
        SNAPSHOT_NAME=$(echo "${SNAPSHOT_RESPONSE}" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(data.get('result', {}).get('name', ''))
" 2>/dev/null || echo "")

        if [ -n "${SNAPSHOT_NAME}" ]; then
            curl -sf "${QDRANT_URL}/collections/${COLLECTION}/snapshots/${SNAPSHOT_NAME}" \
                -o "${BACKUP_DIR}/qdrant/${COLLECTION}_${TIMESTAMP}.snapshot"
            echo "      Saved: ${BACKUP_DIR}/qdrant/${COLLECTION}_${TIMESTAMP}.snapshot"
        fi
    done
fi

# =============================================================================
# MinIO Backup
# =============================================================================
echo ""
echo "[3/3] Backing up MinIO document storage..."

MINIO_ENDPOINT="${MINIO_ENDPOINT:-http://localhost:9000}"
MINIO_ACCESS_KEY="${MINIO_ACCESS_KEY:-minioadmin}"
MINIO_SECRET_KEY="${MINIO_SECRET_KEY:-minioadmin}"

mkdir -p "${BACKUP_DIR}/minio"

# Use mc (MinIO client) if available, otherwise skip with a warning
if command -v mc &>/dev/null; then
    mc alias set docintel-backup "${MINIO_ENDPOINT}" "${MINIO_ACCESS_KEY}" "${MINIO_SECRET_KEY}" \
        --api S3v4 2>/dev/null || true
    mc mirror --preserve docintel-backup/ "${BACKUP_DIR}/minio/" 2>/dev/null || true
    echo "      Saved: ${BACKUP_DIR}/minio/"
else
    echo "      WARNING: 'mc' (MinIO client) not found. Install from https://min.io/docs/minio/linux/reference/minio-mc.html"
    echo "      Skipping MinIO backup."
fi

# =============================================================================
# Prune old backups
# =============================================================================
echo ""
echo "[Cleanup] Removing backups older than ${RETENTION_DAYS} days..."
find "$(dirname "${BACKUP_DIR}")" -maxdepth 1 -type d -mtime "+${RETENTION_DAYS}" -exec rm -rf {} + 2>/dev/null || true

echo ""
echo "==> Backup complete: ${BACKUP_DIR}"
echo "==> Total size: $(du -sh "${BACKUP_DIR}" | cut -f1)"
