output "minio_buckets" {
  description = "Names of provisioned MinIO buckets"
  value = {
    documents_raw       = minio_s3_bucket.documents_raw.bucket
    documents_processed = minio_s3_bucket.documents_processed.bucket
    models              = minio_s3_bucket.models.bucket
  }
}

output "qdrant_collections" {
  description = "Names of provisioned Qdrant collections"
  value       = ["documents", "response_cache"]
}
