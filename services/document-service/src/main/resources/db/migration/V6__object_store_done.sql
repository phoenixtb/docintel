-- Flyway V6: vendor-neutral name for the deletion task's object-store cleanup flag.
-- The object store is any S3-compatible server (ADR-0001), no longer MinIO specifically.

ALTER TABLE deletion_tasks RENAME COLUMN minio_done TO object_store_done;
