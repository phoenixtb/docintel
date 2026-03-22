#!/bin/bash
# Seed Sample Data for DocIntel
# ==============================
# Loads sample datasets via the ingestion-service async bulk-load API.
# Requires the DocIntel stack to be running (docker compose up).
#
# Usage:
#   ./scripts/seed-data.sh                           # All datasets, alpha tenant
#   TENANT_ID=beta ./scripts/seed-data.sh            # Different tenant
#   SAMPLES=20 ./scripts/seed-data.sh techqa         # Single dataset, 20 samples
#   SAMPLES=20 ./scripts/seed-data.sh techqa hr_policies

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

INGESTION_URL="${INGESTION_SERVICE_URL:-http://localhost:8001}"
TENANT_ID="${TENANT_ID:-alpha}"
SAMPLES="${SAMPLES:-10}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

fail() { echo -e "${RED}${BOLD}✗ $*${NC}" >&2; exit 1; }
ok()   { echo -e "  ${GREEN}✓${NC} $*"; }
warn() { echo -e "  ${YELLOW}⚠  $*${NC}"; }

echo "================================================"
echo "  Seeding DocIntel with Sample Data"
echo "================================================"
echo "  Service: ${INGESTION_URL}"
echo "  Tenant:  ${TENANT_ID}"
echo "  Samples: ${SAMPLES} per dataset"
echo ""

# =============================================================================
# Pre-flight: ingestion-service reachable
# =============================================================================
if ! curl -sf --max-time 5 "${INGESTION_URL}/health" > /dev/null 2>&1; then
    fail "Cannot reach ingestion-service at ${INGESTION_URL}.\n  Is the stack running?  ./scripts/docintel.sh → Start"
fi
ok "Ingestion service reachable."
echo ""

# =============================================================================
# Load INTERNAL_GATEWAY_SECRET
# =============================================================================
ENV_FILE="$PROJECT_DIR/.env"
if [[ -f "$ENV_FILE" ]]; then
    # shellcheck source=/dev/null
    set -a; source "$ENV_FILE"; set +a
fi

if [[ -z "${INTERNAL_GATEWAY_SECRET:-}" ]]; then
    fail "INTERNAL_GATEWAY_SECRET is not set.\n  Source .env or run ./scripts/docintel.sh → Start first."
fi

# =============================================================================
# Compute HMAC-SHA256 inter-service token
# Format: HMAC("{request_id}:{tenant_id}:{user_id}", INTERNAL_GATEWAY_SECRET)
# =============================================================================
compute_token() {
    local request_id="$1" tenant_id="$2" user_id="$3" secret="$4"
    python3 - <<EOF
import hashlib, hmac as _hmac
msg = "${request_id}:${tenant_id}:${user_id}".encode()
print(_hmac.new("${secret}".encode(), msg, hashlib.sha256).hexdigest())
EOF
}

REQUEST_ID=$(python3 -c "import uuid; print(str(uuid.uuid4()))")
USER_ID="seed-script"
TOKEN=$(compute_token "$REQUEST_ID" "$TENANT_ID" "$USER_ID" "$INTERNAL_GATEWAY_SECRET")

# =============================================================================
# Datasets to load
# =============================================================================
if [[ $# -gt 0 ]]; then
    DATASETS=("$@")
else
    DATASETS=("techqa" "hr_policies" "cuad")
fi

# Build JSON array of dataset keys
DATASETS_JSON=$(python3 -c "import json, sys; print(json.dumps(sys.argv[1:]))" "${DATASETS[@]}")

echo "  Datasets: ${DATASETS[*]}"
echo ""

# =============================================================================
# POST /ingest/dataset/load — start async job
# =============================================================================
RESPONSE=$(curl -sf -X POST "${INGESTION_URL}/ingest/dataset/load" \
    -H "Content-Type: application/json" \
    -H "X-Tenant-Id: ${TENANT_ID}" \
    -H "X-User-Id: ${USER_ID}" \
    -H "X-Request-Id: ${REQUEST_ID}" \
    -H "X-Internal-Service-Token: ${TOKEN}" \
    -d "{\"datasets\": ${DATASETS_JSON}, \"samples_per_dataset\": ${SAMPLES}}" \
    2>&1) || {
    echo "$RESPONSE" >&2
    fail "Failed to start seed job. Is the ingestion-service healthy?"
}

JOB_ID=$(python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('message',''))" <<< "$RESPONSE" 2>/dev/null)

if [[ -z "$JOB_ID" ]]; then
    echo "Response: $RESPONSE"
    fail "Could not parse job_id from response."
fi

ok "Seed job started (job_id=${JOB_ID})"
echo ""

# =============================================================================
# Stream SSE progress  GET /ingest/dataset/load/{job_id}/progress
# Each event: "event: {type}\ndata: {json}\n\n"
# Types: total | progress | done | error
# =============================================================================
PROGRESS_REQUEST_ID=$(python3 -c "import uuid; print(str(uuid.uuid4()))")
PROGRESS_TOKEN=$(compute_token "$PROGRESS_REQUEST_ID" "$TENANT_ID" "$USER_ID" "$INTERNAL_GATEWAY_SECRET")

echo "  Progress:"
DONE=false

# curl -N = no buffering (required for SSE)
curl -sN "${INGESTION_URL}/ingest/dataset/load/${JOB_ID}/progress" \
    -H "X-Tenant-Id: ${TENANT_ID}" \
    -H "X-User-Id: ${USER_ID}" \
    -H "X-Request-Id: ${PROGRESS_REQUEST_ID}" \
    -H "X-Internal-Service-Token: ${PROGRESS_TOKEN}" | \
python3 - <<'PYEOF'
import json, sys

total = 0
for raw in sys.stdin:
    line = raw.rstrip("\n")
    if not line.startswith("data: "):
        continue
    try:
        d = json.loads(line[6:])
    except json.JSONDecodeError:
        continue

    event_type = d.get("type") or d.get("event", "")

    if "total" in d and total == 0:
        total = d["total"]

    if "processed" in d and total > 0:
        processed = d["processed"]
        filename  = d.get("filename", "")
        chunks    = d.get("chunk_count", "")
        bar_fill  = int((processed / total) * 20)
        bar = "█" * bar_fill + "░" * (20 - bar_fill)
        print(f"  [{bar}] {processed}/{total}  {filename} ({chunks} chunks)", flush=True)

    if d.get("status") in ("done", "error", "failed") or d.get("type") in ("done", "error"):
        chunks = d.get("total_chunks", d.get("chunks", "?"))
        if d.get("status") == "done" or d.get("type") == "done":
            print(f"\n  ✓ Done — {chunks} total chunks indexed.", flush=True)
        else:
            print(f"\n  ✗ Error: {d.get('error', d.get('detail', 'unknown'))}", file=sys.stderr)
            sys.exit(1)
        break
PYEOF

echo ""
echo "================================================"
echo -e "  ${GREEN}${BOLD}Seed complete.${NC}"
echo "================================================"
echo ""
echo "  Test a query:"
echo "    curl -s -X POST http://localhost:8080/api/v1/query \\"
echo "      -H 'Content-Type: application/json' \\"
echo "      -H 'Authorization: Bearer <token>' \\"
echo "      -d '{\"question\": \"What is the vacation policy?\", \"tenant_id\": \"${TENANT_ID}\"}'"
echo ""
