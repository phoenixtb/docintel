#!/bin/bash
# Seed Sample Data for DocIntel
# ==============================
# Loads sample documents via the ingestion-service using the same
# Docling → embed → Qdrant pipeline as real document uploads.
#
# Usage:
#   ./scripts/seed-data.sh                           # All datasets, alpha tenant
#   TENANT_ID=beta ./scripts/seed-data.sh            # Different tenant
#   SAMPLES=20 ./scripts/seed-data.sh techqa         # Single dataset, 20 samples

set -e

INGESTION_SERVICE_URL="${INGESTION_SERVICE_URL:-http://localhost:8001}"
TENANT_ID="${TENANT_ID:-alpha}"
SAMPLES="${SAMPLES:-10}"

echo "================================================"
echo "Seeding DocIntel with Sample Data"
echo "================================================"
echo "Ingestion Service: ${INGESTION_SERVICE_URL}"
echo "Tenant:            ${TENANT_ID}"
echo "Samples/dataset:   ${SAMPLES}"
echo ""

# Optional: restrict to specific datasets passed as args
DATASETS=("${@:-techqa hr_policies cuad}")
if [ "$#" -eq 0 ]; then
    DATASETS=("techqa" "hr_policies" "cuad")
fi

# =============================================================================
# Helper: load a single dataset via ingestion-service /ingest/dataset
# =============================================================================
load_dataset() {
    local key="$1"
    echo "Loading dataset: ${key}..."

    response=$(curl -s -w "\n%{http_code}" -X POST "${INGESTION_SERVICE_URL}/ingest/dataset" \
      -H "Content-Type: application/json" \
      -d "{
        \"dataset_key\": \"${key}\",
        \"tenant_id\": \"${TENANT_ID}\",
        \"samples\": ${SAMPLES},
        \"domain_hint\": \"auto\"
      }")

    body=$(echo "$response" | head -n -1)
    code=$(echo "$response" | tail -n1)

    if [ "$code" = "200" ]; then
        chunks=$(echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('total_chunks',0))" 2>/dev/null || echo "?")
        domain=$(echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('domain','?'))" 2>/dev/null || echo "?")
        echo "  ✓ ${key}: ${chunks} chunks indexed (domain: ${domain})"
    else
        echo "  ✗ ${key}: HTTP ${code}"
        echo "  Response: ${body}"
    fi
}

# =============================================================================
# Load datasets
# =============================================================================
for dataset in "${DATASETS[@]}"; do
    load_dataset "$dataset"
    echo ""
done

echo "================================================"
echo "Seed data loaded."
echo "================================================"
echo ""
echo "Test a query:"
echo "  curl -s -X POST http://localhost:8080/api/v1/query \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"question\": \"What is the vacation policy?\", \"tenant_id\": \"${TENANT_ID}\"}'"
echo ""
