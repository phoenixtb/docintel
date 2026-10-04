#!/usr/bin/env bash
# DocIntel Backup Script
# ======================
# Backs up PostgreSQL, Qdrant, and object-store (document blob) data.
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
# Object store backup (local VersityGW)
# =============================================================================
# VersityGW stores each object as a plain file with its S3 metadata (content type,
# ETag) in user.* xattrs, so a tar that keeps xattrs is a complete backup.
# busybox tar in the gateway image drops xattrs; GNU tar in a throwaway
# container reads the same volume (named volume or DOCINTEL_DATA_DIR bind).
# Objects are immutable and content-addressed, so archiving while running is safe.
# Managed S3 (OBJECT_STORE_ENDPOINT elsewhere): use the provider's versioning or
# replication instead — this step only covers the local object-store service.
echo ""
echo "[3/3] Backing up object store..."

OBJECT_STORE_CID=$(docker compose -f "${COMPOSE_FILE}" ps -q object-store)
if [ -n "${OBJECT_STORE_CID}" ]; then
    BACKUP_DIR_ABS=$(cd "${BACKUP_DIR}" && pwd)
    docker run --rm \
        --volumes-from "${OBJECT_STORE_CID}:ro" \
        -v "${BACKUP_DIR_ABS}:/backup" \
        debian:13-slim \
        tar --xattrs --xattrs-include='user.*' -czf /backup/object-store.tar.gz -C /data .
    echo "      Saved: ${BACKUP_DIR}/object-store.tar.gz"
    echo "      Restore (object-store stopped): docker run --rm -v <volume>:/data -v <dir>:/backup:ro \\"
    echo "        debian:13-slim tar --xattrs --xattrs-include='user.*' -xzf /backup/object-store.tar.gz -C /data"
else
    echo "      WARNING: object-store container is not running — skipping."
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
