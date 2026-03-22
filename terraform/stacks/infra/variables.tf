variable "minio_server" {
  description = "MinIO server address (host:port, no scheme)"
  default     = "localhost:19000"
}

variable "minio_root_user" {
  description = "MinIO root user"
  default     = "minioadmin"
}

variable "minio_root_password" {
  description = "MinIO root password"
  sensitive   = true
  default     = "minioadmin"
}

variable "qdrant_url" {
  description = "Qdrant base URL"
  default     = "http://localhost:6333"
}
