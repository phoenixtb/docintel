#!/bin/bash
# Initialize Qdrant collections for DocIntel
# Run this after Qdrant is healthy

QDRANT_URL="${QDRANT_URL:-http://localhost:6333}"

echo "Initializing Qdrant collections..."

# =============================================================================
# Documents Collection
# =============================================================================
curl -X PUT "${QDRANT_URL}/collections/documents" \
  -H "Content-Type: application/json" \
  -d '{
    "vectors": {
      "size": 768,
      "distance": "Cosine"
    },
    "hnsw_config": {
      "m": 16,
      "ef_construct": 100
    },
    "optimizers_config": {
      "indexing_threshold": 10000
    }
  }'

echo ""

# Create payload indexes for filtering
curl -X PUT "${QDRANT_URL}/collections/documents/index" \
  -H "Content-Type: application/json" \
  -d '{
    "field_name": "tenant_id",
    "field_schema": "keyword"
  }'

curl -X PUT "${QDRANT_URL}/collections/documents/index" \
  -H "Content-Type: application/json" \
  -d '{
    "field_name": "document_type",
    "field_schema": "keyword"
  }'

curl -X PUT "${QDRANT_URL}/collections/documents/index" \
  -H "Content-Type: application/json" \
  -d '{
    "field_name": "allowed_roles",
    "field_schema": "keyword"
  }'

curl -X PUT "${QDRANT_URL}/collections/documents/index" \
  -H "Content-Type: application/json" \
  -d '{
    "field_name": "allowed_users",
    "field_schema": "keyword"
  }'

echo ""
echo "Documents collection created with indexes."

# =============================================================================
# Semantic Cache Collection
# =============================================================================
curl -X PUT "${QDRANT_URL}/collections/response_cache" \
  -H "Content-Type: application/json" \
  -d '{
    "vectors": {
      "size": 768,
      "distance": "Cosine"
    },
    "hnsw_config": {
      "m": 16,
      "ef_construct": 100
    }
  }'

echo ""

curl -X PUT "${QDRANT_URL}/collections/response_cache/index" \
  -H "Content-Type: application/json" \
  -d '{
    "field_name": "tenant_id",
    "field_schema": "keyword"
  }'

echo ""
echo "Response cache collection created."

# =============================================================================
# Verify
# =============================================================================
echo ""
echo "Collections:"
curl -s "${QDRANT_URL}/collections" | python3 -m json.tool

echo ""
echo "Qdrant initialization complete!"
