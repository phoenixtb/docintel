# DocIntel — maintainer mental model

What a developer or agent joining today needs in order to work on the repo without asking. README
is for users. This file is for maintainers. Coverage grows capability by capability: sections cover
what has been documented so far, and §8 lists the gaps.

## 1. Start here

DocIntel is a multi-tenant document Q&A system.
- Documents go in through document-service, either uploaded by users or loaded by data-loader.
  Their bytes land in an S3-compatible object store.
- ingestion-service parses, chunks and embeds them into Qdrant.
- rag-service answers questions with hybrid retrieval and an LLM served by LMForge on the host.
- Kotlin services own the HTTP edge and document records. Python services own ML work.
- Redis Streams connect the pipeline stages. PostgreSQL row-level security isolates tenants.

Reading order: this file §2 → [ADR index](#8-decisions-index) → [contracts](../contracts/) →
the flow in §4 for the capability being changed.

Run and demo (5 minutes once images exist):

```bash
./scripts/setup.sh          # once, and after pulls that add .env keys: backfills .env from .env.example
./scripts/start.sh --build  # whole stack; refuses to start if object-store keys are missing
open http://localhost:3001  # log in, upload a PDF on Documents, ask about it in Chat
open http://localhost:19001 # object store WebUI: the file under docintel-<tenant>/docs/<hash>/
```

When something fails: `./scripts/logs.sh debug` (gateway + rag), `docker compose logs <service>`,
`docker compose ps` for health, the bootstrap logs in `logs/bootstrap/`, and §9.

## 2. System map

```mermaid
flowchart LR
  UI[web-ui :3001] -->|REST/SSE| GW[api-gateway :8080]
  GW -->|REST + HMAC| DOC[document-service]
  GW -->|REST/SSE| RAG[rag-service]
  GW -->|REST| DL[data-loader :8002]
  GW -->|REST| ADM[admin-service]
  DL -->|S3| OS[(object-store<br/>VersityGW)]
  DOC -->|S3| OS
  ADM -->|S3 buckets| OS
  ING[ingestion-service :8001] -->|S3 GET| OS
  DL -- files.available --> R[(Redis Streams)]
  R -- files.available --> DOC
  DOC -- documents.ready --> R
  R -- documents.ready --> ING
  ING -- ingestion.complete --> R
  R -- ingestion.complete --> DOC
  ING --> Q[(Qdrant)]
  RAG --> Q
  DOC --> PG[(PostgreSQL)]
  ADM --> PG
  ING -->|embed| LMF[LMForge on host]
  RAG -->|LLM| LMF
  LF[Langfuse] -->|S3| OS
```

Rule of thumb: **REST for questions and commands that need an answer now; streams for facts that
later stages react to.** Every hop into an object (upload, ingest, delete) carries the
`{bucket, objectPath}` pair defined in [contracts/object-storage.md](../contracts/object-storage.md).

| Folder | Holds |
|---|---|
| `services/` | one directory per deployable: Kotlin (`api-gateway`, `document-service`, `admin-service`), Python (`rag-service`, `ingestion-service`, `data-loader`, `analytics-service-py`), `web-ui` (SvelteKit) |
| `lib/docintel-common/` | shared Python mechanisms: messaging, errors, tracing, internal auth, object store |
| `config/` | Postgres init, Zitadel blueprints, OPA policies, Prometheus/Grafana |
| `scripts/` | setup/start/stop/cleanup/backup; `scripts/lib/` holds the sourced helpers |
| `terraform/` | OpenTofu stacks run by `start.sh`: `infra` (Qdrant collections), `identity` (Zitadel) |
| `tests/` | cross-service contract, e2e, integration and script (bats) tests |
| `docs/` | ADRs, contracts, versions, this file |

## 3. Cross-cutting mechanisms

### Object storage

The idea to own: **the S3 API is the contract; the server is configuration**
([ADR-0001](../adr/0001-object-storage-versitygw.md)).
- Locally the `object-store` compose service runs VersityGW. It stores each object as a plain file
  under `/data/<bucket>/<key>`, with `Content-Type`/`ETag` in xattrs.
- Pointing `OBJECT_STORE_ENDPOINT` at AWS S3, R2 or Ceph RGW replaces it; nothing else changes.

| File | Role |
|---|---|
| `services/document-service/.../config/ObjectStoreProperties.kt` | validated `object-store.*` settings; startup fails on a blank key or a non-http endpoint |
| `services/document-service/.../config/ObjectStoreConfig.kt` | `S3Client`: endpoint override, path style, Apache HTTP client timeouts, SDK `STANDARD` retries |
| `services/document-service/.../service/StorageService.kt` | bucket naming, content-addressable keys, ensure-bucket, upload, prefix delete in 1000-key batches |
| `services/admin-service/.../service/ProvisioningService.kt` | best-effort tenant bucket create/delete with error-code mapping |
| `lib/docintel-common/docintel_common/object_store.py` | Python adapter: `ObjectStoreConfig.from_env`, `ObjectStore`, `ObjectNotFoundError` / `ObjectStoreError` |
| `services/data-loader/src/storage.py` | hash, key and upload for sample datasets |
| `services/ingestion-service/src/adapters/object_store_adapter.py` | download one object to a temp file; cleans up on failure |
| `docker-compose.yml` (`object-store`, `object-store-init`) | server, healthcheck, Langfuse bucket bootstrap |
| `scripts/check-image-pins.sh` | keeps each image tag (VersityGW, Qdrant) identical in compose and the tests that start their own container |

Errors are typed at the edge:
- Python callers see `ObjectNotFoundError` (missing bucket or key: terminal) or `ObjectStoreError`
  (retryable from the caller's point of view).
- Kotlin callers see the SDK's `NoSuchKeyException`, `NoSuchBucketException` and `S3Exception`.

Timeouts: connect 5 s, socket/read 60 s; a Kotlin call is capped at 5 min including retries in document-service (uploads) and 30 s in admin-service.

### Messaging

Redis Streams with consumer groups. Topic names live in `docintel_common.messaging` and Kotlin
`StreamTopics`. A consumer acks only on terminal outcomes; unacked entries are reclaimed
(`XAUTOCLAIM`) and retried up to a delivery limit. Payload contracts:
[contracts/events.md](../contracts/events.md).

### Config and secrets

Settings come from the environment. `.env.example` holds labelled dev defaults, and `setup.sh` /
`setup-lmforge.sh` backfill any key missing from `.env` (`setup_common_env` in
`scripts/lib/setup-common.sh`). Object-store credentials have no in-code defaults: services and
`start.sh` refuse to start without them.

### Tenancy and internal auth

The idea to own: **identity is decided once, at the gateway, from the validated JWT; backends trust
only headers the gateway signed.**

Gateway filter chain for a routed request (order values in brackets):
1. Spring Security verifies the bearer JWT against the IdP's JWKS (`jwk-set-uri`); none or
   invalid → 401 `UNAUTHORIZED`.
2. `RequestCorrelationFilter` [`HIGHEST_PRECEDENCE`] sets `X-Request-Id`.
3. `TenantFilter` [-100] removes every client-supplied identity header (`X-Tenant-Id`,
   `X-User-Id/-Email/-Name/-Roles/-Clearance`) and sets them from the claims `tenant_id`, `sub`,
   `email`, `name`, `roles`, `clearance`. A JWT without a non-blank `tenant_id` gets 403
   `TENANT_CLAIM_MISSING`: a fallback tenant would pool every such caller into one tenant's data.
4. `InternalAuthFilter` [-99] adds `X-Internal-Service-Token` = HMAC-SHA256 of
   `{requestId}:{tenantId}:{userId}` keyed with `INTERNAL_GATEWAY_SECRET`; without the secret
   every request gets 503 (fail-secure).
5. `OpaAuthorizationFilter` [-98] asks OPA (decisions cached 60 s); a denial or an OPA outage → 403.
6. `QuotaEnforcementFilter` [-97] checks Redis quotas on uploads and queries → 429.

The `dev` profile permits every request without a JWT; requests then carry tenant `default` and
no user identity. Per-tenant isolation behind the gateway:
- PostgreSQL row-level security, with session variables set by `TenantAwareDataSource`;
- per-tenant Qdrant collections (`documents_{tenant}`);
- per-tenant buckets.

| File | Role |
|---|---|
| `services/api-gateway/.../config/SecurityConfig.kt` | JWT resource-server chain (`!dev`), permit-all chain (`dev`), CORS |
| `services/api-gateway/.../config/JwtDecoderConfig.kt` | Nimbus decoder with a Caffeine cache keyed by token hash |
| `services/api-gateway/.../filter/TenantFilter.kt` | claims → identity headers; header stripping; `TENANT_CLAIM_MISSING` |
| `services/api-gateway/.../filter/InternalAuthFilter.kt` | HMAC service token |
| `services/api-gateway/.../error/ErrorEnvelope.kt` | `{"error":{"code","message","request_id"}}` for every gateway-emitted error |
| `lib/docintel-common/docintel_common/internal_auth.py` | Python side: verifies the HMAC |
| `config/zitadel/actions-v2/main.py` | Zitadel action that puts `tenant_id`, `role`, `roles`, `clearance` into every token |

## 4. Flows

### Document upload and ingestion

Trigger: `POST /internal/documents` (multipart, via the gateway), or data-loader publishing
`files.available`.

```mermaid
sequenceDiagram
  participant GW as api-gateway
  participant DOC as document-service
  participant OS as object-store
  participant R as Redis Streams
  participant ING as ingestion-service
  GW->>DOC: POST /internal/documents
  DOC->>OS: HEAD/CREATE docintel-{tenant}; PUT docs/{hash}/original.ext
  DOC->>DOC: insert document PENDING
  DOC->>R: documents.ready {bucket, objectPath}
  R->>ING: documents.ready
  ING->>OS: GET object → temp file
  ING->>ING: parse, chunk, embed, write Qdrant
  ING->>DOC: persist chunks (REST)
  ING->>R: ingestion.complete
  R->>DOC: ingestion.complete → COMPLETED / FAILED
```

1. document-service hashes tenant id + bytes. A known hash returns the existing document
   (dedup); FAILED or DELETING ones are re-processed.
2. `StorageService.storeFile` ensures the tenant bucket and PUTs at the content-addressable key.
3. The document row is saved PENDING. `processDocument` publishes `documents.ready`.
4. The data-loader path is the same from step 3:
   - data-loader uploads with `docintel_common.object_store`, then publishes `files.available`;
   - `FilesAvailableConsumer` registers the document and publishes `documents.ready`.
5. ingestion-service downloads through `ObjectStoreAdapter`, runs the pipeline, persists chunks
   and publishes `ingestion.complete`.

| When things go wrong | Effect |
|---|---|
| object store down during upload | upload request fails (5xx); nothing registered |
| object missing at ingest | `FAILED` + ack, no retry (`ObjectNotFoundError`) |
| other object-store / pipeline error at ingest | `FAILED`, not acked → redelivered up to the limit |
| `files.available` without `objectPath` | logged, acked, skipped |

Demo: upload in the UI, then find the object in the WebUI at `http://localhost:19001`.

### Document deletion

Trigger: `DELETE /internal/documents/{id}`, which returns 202.

1. `markForDeletion` sets the document to `DELETING` and inserts a `deletion_tasks` row (outbox) in
   one transaction.
2. `DeletionTaskWorker` polls every 30 s:
   - deletes the Qdrant vectors (`qdrant_done`);
   - calls `StorageService.deleteDocumentFiles`, which lists `docs/{hash}/` and runs batched
     `DeleteObjects` (`object_store_done`);
   - when both flags are set, deletes the chunk and document rows.
3. A failed step retries with exponential backoff (2^attempts min, capped at 60). After 10
   attempts the task is `DEAD`.
4. A missing tenant bucket counts as deleted. A per-key failure throws `ObjectDeletionException`
   and is retried.

### Tenant bucket lifecycle

- Tenant creation calls `ProvisioningService.createTenantBucket`.
  - It is best-effort: document-service creates the bucket lazily anyway.
- Tenant deletion calls `deleteTenantBucket` after queueing document deletion.
  - S3 refuses to delete a non-empty bucket. That is logged as `BucketNotEmpty`, and the bucket
    stays for an operator (see §8).

## 5. Data and contracts

- [contracts/object-storage.md](../contracts/object-storage.md): buckets, keys, hash, client
  settings, relied-on S3 semantics.
- [contracts/events.md](../contracts/events.md): `files.available`, `documents.ready`,
  `ingestion.complete`.
- REST: `docs/api/openapi.json` (generated by `scripts/generate-openapi.py`).
- document-service schema: Flyway `services/document-service/src/main/resources/db/migration/`.
  - `deletion_tasks.object_store_done` comes from V6.

## 6. Recipes

**Add an object-store operation (Kotlin):**
1. Add a method to `StorageService` (or `ProvisioningService`) using the injected `S3Client`.
2. Map the specific SDK exceptions it can raise; never `catch (Exception)`.
3. Add a test in `StorageServiceTest` (real VersityGW through `BaseIntegrationTest`).

**Add an object-store operation (Python):**
1. Add a method to `ObjectStore` in `docintel_common/object_store.py` that translates
   `ClientError` codes through `_translate`.
2. Add a Stubber test in `tests/test_object_store.py` and a container test in
   `tests/test_object_store_integration.py`.

**Add a bucket a third-party service needs:** append its name to the `for bucket in …` list in
`object-store-init` (`docker-compose.yml`).

**Run against managed S3:**
- Set `OBJECT_STORE_ENDPOINT`, `OBJECT_STORE_REGION` and the keys in `.env`.
- Set `OBJECT_STORE_FORCE_PATH_STYLE=false` only if the provider rejects path style.
- The `object-store` service can then be removed from the deployment's compose chain.

**Add or upgrade a Python dependency:**
1. Edit `pyproject.toml` in the service, or use `uv lock --upgrade-package <name>`.
2. Run `uv lock` there and commit `uv.lock`.
3. Rebuild the image.

A stale lock fails the image build (`uv export --locked`) and the CI job `python-locks`.

**Write an integration test that needs S3:** start `versity/versitygw:<pinned tag>` with
`--port :7070 --health /health --quiet posix /tmp`, then wait for HTTP 200 on `/health`:
- Kotlin: `BaseIntegrationTest`;
- Python: `lib/docintel-common/tests/test_object_store_integration.py`.

Use the tag from compose; CI fails otherwise.

**Write a rag-service test that needs Qdrant:** take the `qdrant_settings` fixture
(`services/rag-service/tests/conftest.py`). It points `Settings` at a session-wide Testcontainers
Qdrant and clears the per-tenant retriever cache. Size vectors with
`qdrant_settings.qdrant_embedding_dim`. Example: `TestSecureRetriever` in `tests/test_components.py`.

**Write an api-gateway integration test:** annotate `@SpringBootTest(RANDOM_PORT)`,
`@ActiveProfiles("test")`, and call `GatewayTestInfra.registerProperties` from a
`@DynamicPropertySource`. Then:
- sign tokens with `GatewayTestInfra.token(tenantId = …)`;
- stub the backend path on `GatewayTestInfra.wireMock`, which also serves the JWKS and OPA;
- call `resetStubs()` in `@BeforeEach`.

The routes are the real ones from `application.yml`. Example: `GatewayIntegrationTest`.

**Write a web-ui e2e test:** import `test`/`expect` from `tests/support/fixtures.ts`. Then:
- `await signIn('/path')` drives the real OIDC login against the fake IdP;
- `api.on(method, path, handler)` overrides a gateway response, and `api.requestsTo(...)`
  returns what the UI sent;
- end the page test with `expectNoA11yViolations(page)`;
- `test.use({ user: userWithRole('platform_admin') })` switches role.

Examples: `tests/chat.spec.ts`, `tests/auth.spec.ts`.

**Keep rag-service CI green locally** (CI runs exactly these):
`uv run ruff check src/ && uv run ruff format --check src/ && uv run mypy src/ --ignore-missing-imports && uv run pytest tests/`.

## 7. What proves what

| Claim | Test |
|---|---|
| Uploads round-trip and are tenant-isolated by bucket | `StorageServiceTest` "should store and retrieve text file", "should isolate files by tenant" |
| Document deletion removes every key under the prefix, spares siblings, crosses the 1000-key batch boundary, tolerates a missing bucket, is idempotent | `StorageServiceTest` "deleteDocumentFiles …" (five tests) |
| Startup fails on missing/invalid object-store settings; secrets are not printed | `ObjectStoreConfigTest` |
| Flyway V6 renamed `minio_done` | `DeletionTaskMigrationTest` |
| Tenant bucket create is idempotent; delete keeps non-empty buckets and tolerates missing ones; an unreachable store does not fail tenant creation | `ProvisioningServiceObjectStoreTest` |
| Python adapter maps S3 codes to typed errors and works against a real server | `lib/docintel-common/tests/test_object_store.py`, `test_object_store_integration.py` |
| Missing source object is terminal; outages are retried | `test_missing_source_object_fails_document_and_acks_without_retry`, `test_object_store_outage_fails_document_but_leaves_message_for_redelivery` |
| `files.available` carries `objectPath` end to end | `test_publishes_object_path_of_the_uploaded_file`; `StreamConsumerTest` "FilesAvailableConsumer should carry the data-loader objectPath …" |
| Retired MinIO entries are dropped from OpenTofu state, nothing else | `tests/scripts/test_start_helpers_minio_state.bats` |
| The gateway verifies JWT signatures against the JWKS; no token or a forged one gets 401 | `GatewayIntegrationTest` "rejects a request without a bearer token with 401", "rejects a token whose signature does not match the published key with 401" |
| A JWT without `tenant_id` never reaches a backend | `GatewayIntegrationTest` "rejects a token without a tenant_id claim with 403 before reaching any backend"; `TenantFilterTest` "rejects a JWT …" (two) |
| Client-supplied identity headers never reach a backend | `TenantFilterTest` "overwrites a client-supplied X-Tenant-Id …", "drops client-supplied identity headers …", "replaces every client-supplied identity header …"; `GatewayIntegrationTest` "does not forward a client-supplied X-User-Email …" |
| Backends can verify the gateway's HMAC | `GatewayIntegrationTest` "signs forwarded requests with an HMAC …"; `InternalAuthFilterTest` |
| OPA denial blocks the request | `GatewayIntegrationTest` "returns 403 when OPA denies the request" |
| The web UI logs in with PKCE (S256 verifier checked by the fake IdP) and sends the token and tenant | `tests/auth.spec.ts` "sends a visitor to a protected page …", "returns the user to the page they asked for …", "sends the access token and tenant on API calls" |
| Role guards hide and block admin pages | `tests/auth.spec.ts` "Role guards" |
| Chat streams an answer with cited sources from the SSE wire format rag-service emits | `tests/chat.spec.ts` "streams an answer with its cited source" |
| Pages meet WCAG 2.1 AA except colour contrast | `expectNoA11yViolations` in `tests/chat.spec.ts`, `tests/documents.spec.ts` |
| Tenant retrieval works against a real Qdrant at the configured dimension | `test_components.py::TestSecureRetriever*` |
| Every rag-service module imports (no runtime-evaluated `TYPE_CHECKING` names) | `tests/test_imports.py` |

## 8. Decisions index

| Decision | Record |
|---|---|
| S3 API as the storage contract; VersityGW bundled; vendor names out of contracts | [ADR-0001](../adr/0001-object-storage-versitygw.md) |
| Python images install exactly `uv.lock`; torch family from the hardware index | [ADR-0002](../adr/0002-python-images-from-lockfiles.md) |

Open decisions:
- **Upload transaction.** `DocumentService.uploadDocument` is `@Transactional` and performs the
  S3 PUT inside it, holding a DB transaction across a remote call. Moving the PUT before the
  transaction, or using an outbox, is undecided.
- **Tenant deletion leaks buckets.** `deleteTenantBucket` runs before asynchronous document
  deletion finishes, so the bucket is usually non-empty and is kept. The options are a final
  sweep, a prefix purge, or a deletion task per tenant.
- **Delete racing a fresh upload.** Upload launches `processDocument` in a background coroutine.
  Its `markDocumentProcessing` can overwrite a `DELETING` set by an immediate DELETE with
  `PROCESSING`. `DeletionTaskWorker` then treats the document as resurrected and cancels the task,
  so the document survives. A guarded status transition (only `PENDING → PROCESSING`) would fix it.
- **Shared Kotlin config.** `ObjectStoreProperties`/`ObjectStoreConfig` are duplicated in
  document-service and admin-service because there is no shared Kotlin module yet.
- **Web UI tokens in `localStorage`.** `auth.ts` stores OIDC tokens in `localStorage`, which the
  Svelte security checklist forbids. Moving to memory plus refresh (or a BFF) is undecided.
- **Colour contrast.** The glass theme's muted text fails WCAG AA contrast on every page; the
  e2e axe gate excludes only that rule until the palette is revisited.

## 9. Gotchas and runbook

- **Images ignore `pyproject.toml` ranges.**
  - What a Python image contains is exactly `uv.lock`, so a dependency added without `uv lock`
    fails the build.
  - torch is the exception: it comes from `TORCH_INDEX`/`TORCH_VERSION`, and `uv pip check`
    guards compatibility.
  - Before ADR-0002, images re-resolved at build time. SQLAlchemy 2.1, whose `postgresql://` now
    means psycopg v3, broke rag-service conversations on any fresh build.
- **Gradle needs JDK 21.** Gradle 8.11.1 fails on JDK 25+ with a bare version string as the error.
  Set `JAVA_HOME` to a 21 JDK before `./gradlew`.
- **Testcontainers ≥ 1.21.4 (Java).** Older versions speak Docker API 1.32, which Docker Engine 29
  rejects ("client version 1.32 is too old"), so every container test fails.
- **VersityGW data directory.** Every top-level directory under its root is served as a bucket.
  Never store anything else in `object-store-data` / `${DOCINTEL_DATA_DIR}/object-store`.
- **xattrs are metadata.** Copy object-store data only with xattr-preserving tools. busybox `tar`
  inside the gateway image drops them; `scripts/backup.sh` uses GNU tar from `debian:13-slim`.
- **Retired MinIO OpenTofu state.** `removed {}` blocks cannot drop `minio_s3_bucket` entries,
  because OpenTofu still configures the provider. `forget_retired_minio_state` in `start.sh` runs
  `tofu state rm` once instead.
- **A secret read with `System.getenv` cannot be set by a test.** The gateway's
  `InternalAuthFilter` did this, so every routed request in tests got its fail-secure 503. Read
  secrets through `@Value("\${NAME:}")`; the env var still feeds it in deployments.
- **Annotating a FastAPI route changes its serialization.** A return annotation (even `-> Any`)
  makes FastAPI build a response model and validate through pydantic instead of
  `jsonable_encoder`. rag-service routes without a `response_model` therefore declare
  `response_model=None` explicitly.
- **`TYPE_CHECKING` imports in runtime annotations.** rag-service modules mostly lack
  `from __future__ import annotations`, so a function annotation naming a `TYPE_CHECKING`-only
  import raises `NameError` at import time; quote it. `tests/test_imports.py` catches this.
- **mypy targets 3.12, the image runs 3.11.** numpy's stubs use 3.12-only syntax that mypy rejects
  under `python_version = "3.11"`. ruff's `target-version = "py311"` still guards syntax until the
  image moves to 3.12.
- **`env-config.js` is only filled in by the container.** `docker-entrypoint.sh` substitutes the
  `__PUBLIC_*__` placeholders at nginx start. Under `vite preview` (and so in Playwright) the SPA
  sees the literal placeholders and OIDC discovery 404s; the e2e fixture routes `/env-config.js`.
- **Retries turned failures into a "hang".** 54 failing Playwright specs × 3 attempts × a 30 s
  timeout ran the Web UI job for 47 minutes. Playwright now runs with `retries: 0`, a 20 s test
  timeout, and the job with `timeout-minutes: 15`.
- **A red first step hides the steps after it.** RAG CI stopped at lint for months, so its mypy
  step (188 errors), its tests and `rag-service.yml`'s Docker build (wrong build context) were
  broken without anyone seeing it. After fixing a gate, check that every later step really ran.
- **A skipping fixture hid broken tests.** rag-service's old `qdrant_url` fixture skipped when
  Qdrant was unreachable, which was always the case in CI. The tests had been sending 768-dim
  vectors to 1024-dim collections. Integration fixtures now start their dependency or fail.
- **document-service tests and the JDBC URL.** `TenantDataSourceConfig.flyway()` strips
  `user`/`password` with a regex that also eats the `?` when other parameters follow them.
  `BaseIntegrationTest` appends credentials after the container's own parameters for this reason.

Runbook:

| Task | Procedure |
|---|---|
| Wipe all local data | `./scripts/cleanup.sh --data` (also removes a leftover `minio-data` volume) |
| Back up the object store | `./scripts/backup.sh` → `backups/<ts>/object-store.tar.gz` |
| Restore it | stop `object-store`; `docker run --rm -v <volume>:/data -v <dir>:/backup:ro debian:13-slim tar --xattrs --xattrs-include='user.*' -xzf /backup/object-store.tar.gz -C /data`; start it |
| Rotate object-store keys | change both keys in `.env`; `docker compose up -d` recreates the gateway and every client |
| Browse objects | WebUI `http://localhost:19001`, S3 API `http://localhost:19000` (keys from `.env`) |

## 10. Glossary

| Term | Meaning |
|---|---|
| Object store | the S3-compatible blob store; `object-store` compose service locally |
| VersityGW | Versity S3 Gateway: an S3 server over a POSIX filesystem (Apache-2.0) |
| Content hash | SHA-256 of tenant id + file bytes; names the object key and derives the document id |
| Path-style addressing | `http://host/bucket/key` rather than `http://bucket.host/key` |
| Outbox (deletion task) | `deletion_tasks` row written with the state change and driven later by a worker |
| Tenant bucket | `docintel-{tenantId}` |
| PKCE | Proof Key for Code Exchange: the SPA sends `S256(code_verifier)` with the authorization request and the verifier with the code exchange |
| JWKS | the IdP's published public keys; the gateway verifies JWT signatures against it |
| OPA | Open Policy Agent; answers the gateway's per-request allow/deny question |
| Identity headers | `X-Tenant-Id` and `X-User-*`, set only by the gateway's `TenantFilter` |

## 11. Changelog

- **PR-01 (2026-10-04):**
  - MinIO replaced by an S3-API object store with VersityGW bundled (ADR-0001).
  - AWS SDK v2 / boto3 adapters; contract renames `minioPath` → `objectPath` and
    `minio_done` → `object_store_done`.
  - Langfuse bucket bootstrap; xattr-preserving backups.
  - Docs bootstrap: ADR folder, contracts, versions, this file.
  - rag-service pins its Postgres driver to psycopg2. Python images now build from `uv.lock`
    (ADR-0002), with CI `python-locks`.
- **PR #6 — CI green (2026-10-11):**
  - api-gateway tests run through the production JWT chain and route table (signed tokens, JWKS
    and OPA on WireMock, Redis on Testcontainers). The gateway now rejects a JWT without
    `tenant_id` (403 `TENANT_CLAIM_MISSING`) and strips client-supplied identity headers.
    `INTERNAL_GATEWAY_SECRET` is read through `@Value`.
  - rag-service passes ruff and strict mypy. The Qdrant tests use a real Qdrant, and
    `test_imports.py` guards module imports.
  - The web UI e2e suite was rewritten around a fake IdP and gateway, with an axe gate; a11y
    defects axe found are fixed.
  - Every CI job has a timeout. `check-object-store-pin.sh` became `check-image-pins.sh`, which
    also pins Qdrant.
  - ingestion-service's three failing tests are fixed.
  - `rag-service.yml` now builds its image with the repo root as the build context.
