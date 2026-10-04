# ADR-0001 — Object storage: S3 API everywhere, VersityGW as the bundled server

- Status: accepted
- Date: 2026-10-04
- Supersedes: MinIO (implicit, never recorded)

## Context

Document blobs (uploads and data-loader samples) and Langfuse event/media blobs live in an object
store. Until now that was MinIO, accessed through MinIO's own SDKs (`io.minio` in the Kotlin
services, `minio-py` in the Python ones), MinIO-specific health paths, the `aminueza/minio`
OpenTofu provider and `mc` for backups.

MinIO Community Edition stopped publishing binaries and images in October 2025 and its repository
was archived in April 2026: no images, no security fixes. The stack could no longer pull its pinned
image, and the document-service integration tests failed for the same reason.

The application uses only plain S3: HEAD/CREATE/DELETE bucket, PUT, GET, ListObjectsV2 and
DeleteObjects. It does not use presigned URLs, bucket policies, lifecycle, versioning, notifications
or per-tenant IAM users.

Requirements from the owner: permissive licence, small, performant, production-viable, and
replaceable by managed S3 through configuration alone.

## Decision

1. **The S3 API is the only contract.** Kotlin services use the AWS SDK for Java v2 (`S3Client`).
   Python services use boto3 through one shared adapter, `docintel_common.object_store`. The server
   is chosen by `OBJECT_STORE_ENDPOINT`, `OBJECT_STORE_REGION`, `OBJECT_STORE_ACCESS_KEY`,
   `OBJECT_STORE_SECRET_KEY` and `OBJECT_STORE_FORCE_PATH_STYLE`; no code names a vendor.
2. **The bundled server is VersityGW** (`versity/versitygw`, Apache-2.0) with its POSIX backend:
   - a stateless S3 gateway that stores each object as a plain file, with its metadata in xattrs;
   - local development, CI and single-node self-hosting run it;
   - production may point the same settings at AWS S3, R2, Ceph RGW or any S3-compatible store.
3. **Vendor names leave the contracts**, renamed in the same change because dev data and streams
   are wiped (no dual-read window):
   - `minioPath` becomes `objectPath` (`files.available` event, `FromPathRequest`);
   - `deletion_tasks.minio_done` becomes `object_store_done` (Flyway V6);
   - the ingestion source type `minio` becomes `s3`;
   - `MINIO_*` env vars become `OBJECT_STORE_*`.

## Options considered

Measured locally (Docker on Apple Silicon, single run, indicative only), driving the exact S3
calls the app makes with minio-py and boto3 (default checksums):

| | VersityGW 1.8.0 | RustFS 1.0.1 | SeaweedFS 4.48 | Garage | Ceph RGW |
|---|---|---|---|---|---|
| Licence | Apache-2.0 | Apache-2.0 | Apache-2.0 | AGPL-3.0 | LGPL |
| Image | 63 MB | 251 MB | 502 MB | — | — |
| RAM idle / after test | 9 / 17 MiB | 221 / 280 MiB | 68 / 528 MiB | — | 16 GB+ recommended |
| PUT 256 KiB | 2.0–3.0 ms | 11.1 ms | 4.6 ms | — | — |
| App S3 surface | all pass | all pass | deleted a non-empty bucket | not run | not run |
| Rejected because | — | GA 17 days old; 20+ advisories in 2026 | violates S3 DeleteBucket semantics | licence requirement | operational weight |

## Consequences

- **Durability and HA come from the filesystem under VersityGW.**
  - VersityGW itself has no erasure coding or replication.
  - Single node: use ZFS/RAID plus the off-box backup in `scripts/backup.sh`.
  - HA: run several gateways over a shared filesystem, or switch to managed S3.
- **Backups are files.** `scripts/backup.sh` tars the volume with xattrs, which keeps
  `Content-Type` and `ETag` (verified by a restore round trip); `mc` is no longer needed.
- **No server-specific provisioning.**
  - The OpenTofu infra stack no longer manages buckets; tenant buckets are created by the
    application.
  - `object-store-init` creates the one third-party bucket (`langfuse`) with SigV4 `curl`.
- **The data directory must be dedicated.** Every top-level directory under the POSIX root is
  served as a bucket.
- **One image tag, four copies.** The VersityGW tag is pinned in compose and in three test suites,
  kept equal by `scripts/check-object-store-pin.sh` (CI job `object-store-pin`).
- **Upgrading the server** means bumping that tag; switching servers means changing
  `OBJECT_STORE_*`.
