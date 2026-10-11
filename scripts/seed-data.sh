#!/bin/bash
# Seed Sample Data for DocIntel
# ==============================
# Loads sample datasets through data-loader (POST /datasets/load + SSE progress).
# data-loader uploads each file to the object store and publishes it on
# files.available; document-service registers it and ingestion-service indexes
# it asynchronously, so documents keep turning COMPLETED after this script ends.
# Requires the stack to be running (data-loader on :8002 via docker-compose.override.yml).
#
# Usage:
#   ./scripts/seed-data.sh                           # All datasets, alpha tenant
#   TENANT_ID=beta ./scripts/seed-data.sh            # Different tenant
#   SAMPLES=20 ./scripts/seed-data.sh techqa         # Single dataset, 20 samples
#   SAMPLES=20 ./scripts/seed-data.sh techqa hr_policies

set -euo pipefail

DATA_LOADER_URL="${DATA_LOADER_URL:-http://localhost:8002}"
TENANT_ID="${TENANT_ID:-alpha}"
SAMPLES="${SAMPLES:-10}"
USER_ID="seed-script"

RED='\033[0;31m'
GREEN='\033[0;32m'
BOLD='\033[1m'
NC='\033[0m'

fail() { echo -e "${RED}${BOLD}✗ $*${NC}" >&2; exit 1; }
ok()   { echo -e "  ${GREEN}✓${NC} $*"; }

if [[ $# -gt 0 ]]; then
    DATASETS=("$@")
else
    DATASETS=("techqa" "hr_policies" "cuad")
fi

echo "================================================"
echo "  Seeding DocIntel with Sample Data"
echo "================================================"
echo "  Service:  ${DATA_LOADER_URL}"
echo "  Tenant:   ${TENANT_ID}"
echo "  Datasets: ${DATASETS[*]}"
echo "  Samples:  ${SAMPLES} per dataset"
echo ""

curl -sf --max-time 5 "${DATA_LOADER_URL}/health" > /dev/null 2>&1 \
    || fail "Cannot reach data-loader at ${DATA_LOADER_URL}. Is the stack running?  ./scripts/docintel.sh → Start"
ok "data-loader reachable."

BODY=$(python3 -c 'import json,sys; print(json.dumps({"datasets": sys.argv[2:], "samples_per_dataset": int(sys.argv[1])}))' \
    "$SAMPLES" "${DATASETS[@]}")

RESPONSE=$(curl -s -w '\n%{http_code}' -X POST "${DATA_LOADER_URL}/datasets/load" \
    -H "Content-Type: application/json" \
    -H "X-Tenant-Id: ${TENANT_ID}" \
    -H "X-User-Id: ${USER_ID}" \
    -d "$BODY")
STATUS="${RESPONSE##*$'\n'}"
RESPONSE="${RESPONSE%$'\n'*}"
[[ "$STATUS" == "202" ]] || fail "data-loader refused the job (HTTP ${STATUS}): ${RESPONSE}"

# The job id is returned in `message` (kept there for web-UI compatibility).
JOB_ID=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("message", ""))' <<< "$RESPONSE")
[[ -n "$JOB_ID" ]] || fail "Could not parse the job id from: ${RESPONSE}"
ok "Load job started (job_id=${JOB_ID})"
echo ""

# SSE events: total {total} · progress {processed,total,filename,…} · done {registered,…} · error {reason}
curl -sN "${DATA_LOADER_URL}/datasets/load/${JOB_ID}/progress" \
    -H "X-Tenant-Id: ${TENANT_ID}" \
    -H "X-User-Id: ${USER_ID}" | \
python3 -c '
import json, sys
event, total = None, 0
for raw in sys.stdin:
    line = raw.rstrip("\n")
    if line.startswith("event: "):
        event = line[7:]
        continue
    if not line.startswith("data: "):
        continue
    data = json.loads(line[6:])
    if event == "total":
        total = data["total"]
    elif event == "progress" and total:
        done = data["processed"]
        bar = "█" * int(done / total * 20) + "░" * (20 - int(done / total * 20))
        name = data.get("filename", "")
        print(f"  [{bar}] {done}/{total}  {name}", flush=True)
    elif event == "done":
        registered = data.get("registered", 0)
        print(f"\n  ✓ {registered} file(s) published for ingestion.", flush=True)
        sys.exit(0)
    elif event == "error":
        reason = data.get("reason", "unknown error")
        print(f"\n  ✗ {reason}", file=sys.stderr)
        sys.exit(1)
sys.exit("  ✗ progress stream ended without a result")
'

echo ""
echo "================================================"
echo -e "  ${GREEN}${BOLD}Seed job complete.${NC}"
echo "================================================"
echo "  Ingestion continues in the background; documents appear as COMPLETED"
echo "  on the Documents page (tenant ${TENANT_ID}) as they finish."
echo ""
