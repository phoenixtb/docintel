# Contract — object storage

Producers and consumers of document blobs agree on the layout below. Any S3-compatible server
satisfies it ([ADR-0001](../adr/0001-object-storage-versitygw.md)).

## Layout

| Item | Value | Owner |
|---|---|---|
| Tenant bucket | `docintel-{tenantId}` | created by document-service on first upload (`StorageService`), admin-service at tenant creation (`ProvisioningService.createTenantBucket`), data-loader before upload (`docintel_common.object_store.tenant_bucket`) |
| Document key | `docs/{contentHash}/original.{ext}` | `StorageService.contentAddressablePath` (Kotlin), `storage.object_path` (data-loader) |
| `contentHash` | hex SHA-256 of `tenantId` bytes followed by file bytes | document-service `computeContentId`, data-loader `compute_content_hash` |
| `ext` | file extension of the original name; `bin` when absent | both writers |
| Derived artefacts | any key under `docs/{contentHash}/` | deleted with the document |
| Langfuse bucket | `langfuse` | created by compose service `object-store-init` |

Tenant ids must therefore be valid S3 bucket-name fragments: lowercase letters, digits and hyphens,
with `docintel-{tenantId}` at most 63 characters.

## Semantics relied on

| Behaviour | Relied on by | Proved by |
|---|---|---|
| PUT to an existing key overwrites (idempotent re-upload) | uploads, data-loader re-runs | `StorageServiceTest` "should idempotently store same content at same path"; `test_overwriting_the_same_key_keeps_the_latest_bytes` |
| GET of a missing key fails with `NoSuchKey` (404) | ingestion terminal path | `test_missing_key_raises_object_not_found`; `StorageServiceTest` "getFile on a missing key throws NoSuchKeyException" |
| DeleteBucket on a non-empty bucket fails with `BucketNotEmpty` | tenant deletion leaves data for an operator | `ProvisioningServiceObjectStoreTest` "deleteTenantBucket leaves a non-empty bucket in place without throwing" |
| CreateBucket on an owned bucket fails with `BucketAlreadyOwnedByYou` | concurrent bucket creation | `test_ensure_bucket_accepts_losing_a_create_race`; `ProvisioningServiceObjectStoreTest` "createTenantBucket is idempotent" |
| DeleteObjects accepts ≤ 1000 keys per call | document deletion | `StorageServiceTest` "deleteDocumentFiles deletes more than one DeleteObjects batch" |

## Client settings (every service)

| Variable | Default (compose) | Meaning |
|---|---|---|
| `OBJECT_STORE_ENDPOINT` | `http://object-store:7070` | S3 endpoint URL (`http://` or `https://`) |
| `OBJECT_STORE_REGION` | `us-east-1` | SigV4 signing region; non-`us-east-1` adds a location constraint on bucket create |
| `OBJECT_STORE_ACCESS_KEY` / `OBJECT_STORE_SECRET_KEY` | none; startup fails if blank | credentials |
| `OBJECT_STORE_FORCE_PATH_STYLE` | `true` | `host/bucket/key` addressing; set `false` only for virtual-host-only providers |
