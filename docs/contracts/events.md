# Contract — Redis Streams events (document pipeline)

Payloads are JSON in the `payload` field of each stream entry, with camelCase keys. Topic names
live in `docintel_common.messaging` (Python) and `StreamTopics` (Kotlin). This page covers the
three topics that carry document and object-store references. `documents.progress` and
`analytics.query` are not catalogued yet.

## `files.available` — data-loader → document-service

| Field | Type | Required | Meaning |
|---|---|---|---|
| `objectPath` | string | yes | key in the tenant bucket: `docs/{contentHash}/original.{ext}` ([object storage](object-storage.md)) |
| `contentHash` | string (64 hex) | yes | SHA-256 of tenant id + bytes; the document id derives from it |
| `tenantId` | string | yes | owning tenant; the bucket is `docintel-{tenantId}` |
| `filename` | string | yes | original file name |
| `contentType` | string | no (`application/octet-stream`) | MIME type |
| `fileSize` | integer | no (`0`) | bytes |
| `dataSourceId` | UUID string | no | data source batch |
| `domainHint` | string | no (`auto`) | routing hint |
| `metadata` | object of strings | no (`{}`) | free-form |

A payload without `objectPath`, including the retired `minioPath` shape, is malformed: the consumer
logs it and acks it without registering anything.

Producer: `services/data-loader/src/api/main.py` (`_load_dataset_background`).
Consumer: `FilesAvailableConsumer` → `FilesAvailableEvent`.
Proved by:
- `test_publishes_object_path_of_the_uploaded_file` (producer);
- `StreamConsumerTest` "FilesAvailableConsumer should carry the data-loader objectPath into registration and DocumentReady"
  and "FilesAvailableConsumer should ack and skip a payload still using the retired minioPath field" (consumer).

## `documents.ready` — document-service → ingestion-service

| Field | Type | Required | Meaning |
|---|---|---|---|
| `documentId` | UUID string | yes | document row id |
| `tenantId` | string | yes | owning tenant |
| `bucket` | string | yes | `docintel-{tenantId}` |
| `objectPath` | string | yes | key of the original file |
| `filename` | string | yes | original name; its suffix selects the parser |
| `domainHint` | string | no (`auto`) | routing hint |
| `metadata` | object | no (`{}`) | copied from the document |

Missing object (`NoSuchKey`/`NoSuchBucket`) is terminal: ingestion publishes `ingestion.complete`
with `FAILED` and acks. Any other object-store error publishes `FAILED` without acking, so the
entry is redelivered up to the retry limit. Proved by:
- `test_missing_source_object_fails_document_and_acks_without_retry`;
- `test_object_store_outage_fails_document_but_leaves_message_for_redelivery`.

## `ingestion.complete` — ingestion-service → document-service

| Field | Type | Required | Meaning |
|---|---|---|---|
| `documentId` | UUID string | yes | document row id |
| `tenantId` | string | yes | owning tenant |
| `chunkCount` | integer | yes | chunks indexed |
| `domain` | string | yes | classified domain |
| `status` | `COMPLETED` \| `FAILED` | yes | outcome |
| `errorMessage` | string | no | failure reason |

Shape checks for all three topics: `tests/contract/test_contracts.py` (`TestStreamEventContracts`).
