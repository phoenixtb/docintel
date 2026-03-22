locals {
  qdrant_url = var.qdrant_url

  # Vector config for main document collection (nomic-embed-text → 768 dims)
  documents_config = {
    vectors           = { size = 768, distance = "Cosine" }
    hnsw_config       = { m = 16, ef_construct = 100 }
    optimizers_config = { indexing_threshold = 10000 }
  }

  # Semantic cache collection — same embedding model
  response_cache_config = {
    vectors     = { size = 768, distance = "Cosine" }
    hnsw_config = { m = 16, ef_construct = 100 }
  }
}

resource "terraform_data" "qdrant_documents" {
  triggers_replace = sha256(jsonencode(local.documents_config))

  provisioner "local-exec" {
    command = <<-EOT
      curl -sf -X PUT "${local.qdrant_url}/collections/documents" \
        -H "Content-Type: application/json" \
        -d '${jsonencode(local.documents_config)}'

      curl -sf -X PUT "${local.qdrant_url}/collections/documents/index" \
        -H "Content-Type: application/json" \
        -d '{"field_name": "tenant_id", "field_schema": "keyword"}'

      curl -sf -X PUT "${local.qdrant_url}/collections/documents/index" \
        -H "Content-Type: application/json" \
        -d '{"field_name": "document_type", "field_schema": "keyword"}'

      curl -sf -X PUT "${local.qdrant_url}/collections/documents/index" \
        -H "Content-Type: application/json" \
        -d '{"field_name": "allowed_roles", "field_schema": "keyword"}'

      curl -sf -X PUT "${local.qdrant_url}/collections/documents/index" \
        -H "Content-Type: application/json" \
        -d '{"field_name": "allowed_users", "field_schema": "keyword"}'
    EOT
  }
}

resource "terraform_data" "qdrant_response_cache" {
  triggers_replace = sha256(jsonencode(local.response_cache_config))

  provisioner "local-exec" {
    command = <<-EOT
      curl -sf -X PUT "${local.qdrant_url}/collections/response_cache" \
        -H "Content-Type: application/json" \
        -d '${jsonencode(local.response_cache_config)}'

      curl -sf -X PUT "${local.qdrant_url}/collections/response_cache/index" \
        -H "Content-Type: application/json" \
        -d '{"field_name": "tenant_id", "field_schema": "keyword"}'
    EOT
  }
}
