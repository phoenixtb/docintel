resource "minio_s3_bucket" "documents_raw" {
  bucket = "documents-raw"
  acl    = "private"
}

resource "minio_s3_bucket" "documents_processed" {
  bucket = "documents-processed"
  acl    = "private"
}

# Reserved for future vLLM-mlx model artefacts
resource "minio_s3_bucket" "models" {
  bucket = "models"
  acl    = "private"
}
