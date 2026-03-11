# Admin Service

**Language/Framework:** Kotlin · Spring Boot 3.x · JDBC (`JdbcTemplate`)  
**Port:** `8082`  
**Source:** `services/admin-service/`

---

## Responsibilities

- Tenant lifecycle management (create, update, delete)
- User listing and role management (via Zitadel API)
- Platform-level and tenant-level LLM model settings
- System health checking (Qdrant, PostgreSQL, Redis, Ollama)
- System and tenant usage statistics
- Semantic cache management (Qdrant `response_cache` collection)
- Qdrant collection and MinIO bucket provisioning

---

## Multi-Tenancy Pattern

The admin service uses the same RLS pattern as the document service:

- `TenantContextFilter` reads `X-Tenant-Id` and `X-User-Role` headers (set by the gateway) and stores them in `TenantContextHolder` (thread-local).
- `TenantAwareDataSource` intercepts every JDBC connection and executes:
  ```sql
  SET app.current_tenant = '<tenant_id>';
  SET app.user_role = '<role>';
  ```
- PostgreSQL RLS policies enforce row visibility based on these session variables.

The admin service connects as `docintel_app` (non-superuser) so RLS is always active.

---

## API Endpoints

All routes are under `/internal/` (the gateway rewrites `/api/v1/admin/` → `/internal/` and `/api/v1/tenants/` → `/internal/tenants/`).

### Health & Stats

| Method | Path | OPA Role | Description |
|--------|------|----------|-------------|
| `GET` | `/internal/health` | Any | System component health (Qdrant, PG, Redis, Ollama) |
| `GET` | `/internal/stats` | `platform_admin` | Aggregate system stats |
| `GET` | `/internal/cache/stats` | `platform_admin` | Cache entry count and hit rate |
| `POST` | `/internal/cache/clear` | `platform_admin` | Clear all tenant caches |
| `POST` | `/internal/cache/clear/{tenantId}` | `platform_admin` | Clear one tenant's cache |

### Tenant Management

| Method | Path | OPA Role | Description |
|--------|------|----------|-------------|
| `GET` | `/internal/tenants` | `platform_admin` | List all tenants with quotas and settings |
| `POST` | `/internal/tenants` | `platform_admin` | Create tenant (PostgreSQL + Zitadel groups) |
| `PUT` | `/internal/tenants/{id}` | `platform_admin` | Update name and/or quotas |
| `DELETE` | `/internal/tenants/{id}` | `platform_admin` | Delete tenant and all data |
| `GET` | `/internal/tenants/{id}/usage` | `tenant_admin`+ | Tenant-scoped usage stats |
| `GET` | `/internal/tenants/{id}/users` | `tenant_admin`+ | List users (from Zitadel) |
| `PUT` | `/internal/tenants/{id}/users/{uid}/role` | `tenant_admin`+ | Change user role |

### Settings

| Method | Path | OPA Role | Description |
|--------|------|----------|-------------|
| `GET` | `/internal/platform/settings` | `platform_admin` | Get global LLM override |
| `PUT` | `/internal/platform/settings` | `platform_admin` | Set or clear global LLM override |
| `GET` | `/internal/tenants/{id}/settings` | `tenant_admin`+ | Get tenant model preference |
| `PATCH` | `/internal/tenants/{id}/settings` | `tenant_admin`+ | Set or clear tenant model preference |

---

## Service Components

### `TenantManagementService`

- **`createTenant`**: Inserts into `tenants` table, calls `ZitadelService.createTenantGroups()` to provision both `tenant-{id}` (users) and `tenant-{id}-admin` (admins) Zitadel groups.
- **`updateTenant`**: Updates name and quotas in `tenants`. Also writes quota limits to Redis (`quota:{tenant_id}:limit:*`) so `QuotaEnforcementFilter` in the gateway picks them up.
- **`deleteTenant`**: Removes from `tenants` (cascades to documents, chunks, conversations via FK), calls Zitadel to delete groups.
- **`getTenantUsers`**: Queries Zitadel API for group members of `tenant-{id}` and `tenant-{id}-admin`.
- **`updateUserRole`**: Moves user between Zitadel groups to change role.

### `PlatformSettingsService`

LLM model selection at two levels:

```
platform_settings.llm_model (global override, set by platform_admin)
  ↓ if null
tenants.settings->>'llm_model' (per-tenant preference, set by tenant_admin)
  ↓ if null
OLLAMA_LLM_MODEL env var (RAG service default)
```

- `getPlatformSettings()`: Reads `platform_settings` table; returns `PlatformSettings(llmModel=null)` on fresh system (graceful `EmptyResultDataAccessException` handling).
- `updatePlatformSettings()`: Upserts into `platform_settings` using `?::jsonb` parameterized bind (SQL injection safe).
- `getTenantSettings()`: Returns both the tenant's preference and the computed effective model.
- `updateTenantSettings()`: Uses `jsonb_set(..., to_jsonb(?::text))` — parameterized to prevent injection.

### `ZitadelService`

HTTP client (`WebClient`) to Zitadel API with 10s response timeout.

- `createTenantGroups(tenantId)`: Creates `tenant-{id}` and `tenant-{id}-admin` groups.
- `deleteTenantGroup(tenantId)`: Deletes both groups.
- `getGroupMembers(groupId)`: Lists users in a group.
- `addUserToGroup` / `removeUserFromGroup`: Role change operations.

### `StatsService`

- `getSystemStats()`: Aggregates total documents (`SUM(chunk_count)` from documents table, not the empty `chunks` table), queries, tenant count, cache stats.
- `listTenants()`: Joins `tenants` with `documents` and `query_log`, returns `TenantSummary` including `quotaDocuments`, `quotaQueriesPerDay`, `settings`.
- `getTenantUsage()`: Per-tenant breakdown with 24h query count and cache hit rate.

### `CacheService`

- `getCacheStats()`: Queries Qdrant for total cache entry count; computes real `hitRate` from `query_log` table (`cached = true` ratio); configures `RestTemplate` with 5s connection / 10s read timeout.
- `clearAllCache()` / `clearTenantCache()`: Deletes Qdrant points from `response_cache` collection filtered by `tenant_id`.

### `ProvisioningService`

- `ensureQdrantCollection()`: Creates Qdrant collection if it doesn't exist (REST API call).
- `ensureMinIOBucket()`: Creates per-tenant MinIO bucket if missing.
- Uses `RestTemplate` with 5s connect / 10s read timeout.

### `HealthService`

Checks: Qdrant (REST ping), PostgreSQL (JDBC `SELECT 1`), Redis (ping), Ollama (HTTP GET `/`). Returns `SystemHealth` with per-component latency.

---

## Key DTOs (`dto/AdminDto.kt`)

| DTO | Fields |
|-----|--------|
| `TenantSummary` | `tenantId`, `name`, `documentCount`, `queryCount`, `quotaDocuments`, `quotaQueriesPerDay`, `settings` |
| `TenantUsage` | `tenantId`, `documentCount`, `chunkCount`, `totalQueries`, `queriesLast24h`, `cacheHitRate`, `storageBytes`, `lastQueryAt` |
| `CreateTenantRequest` | `id` (validated: lowercase alphanumeric), `name`, `quotaDocuments`, `quotaQueriesPerDay` |
| `TenantSettings` | `llmModel` (tenant pref), `effectiveModel` (resolved) |
| `PlatformSettings` | `llmModel` (null = Tenant Choice) |
| `SystemHealth` | `status`, `components` (map), `timestamp` |

Input validation via `jakarta.validation` (`@NotBlank`, `@Size`, `@Pattern`). Controller uses `@Validated` + `@Valid`.

---

## Key Files

| File | Purpose |
|------|---------|
| `controller/AdminController.kt` | All REST endpoints (`/internal/**`) |
| `dto/AdminDto.kt` | Request/response DTOs with validation annotations |
| `service/TenantManagementService.kt` | Tenant CRUD + Zitadel integration |
| `service/PlatformSettingsService.kt` | LLM model settings (platform + tenant level) |
| `service/ZitadelService.kt` | Zitadel API client (WebClient, 10s timeout) |
| `service/StatsService.kt` | Usage and system statistics |
| `service/CacheService.kt` | Qdrant cache management + hit rate from query_log |
| `service/ProvisioningService.kt` | Qdrant / MinIO provisioning |
| `service/HealthService.kt` | Component health checks |
| `tenant/TenantContextFilter.kt` | Reads X-* headers, populates TenantContextHolder |
| `tenant/TenantAwareDataSource.kt` | Sets PostgreSQL session variables for RLS |
| `config/TenantDataSourceConfig.kt` | HikariCP pool, wraps with TenantAwareDataSource |

---

## Database Tables Used

| Table | Operations |
|-------|-----------|
| `tenants` | SELECT, INSERT, UPDATE, DELETE; RLS: platform_admin sees all, others see own row |
| `platform_settings` | SELECT, UPSERT; not tenant-scoped |
| `documents` | SELECT (aggregate counts) |
| `query_log` | SELECT (usage stats, cache hit rate) |
| `conversations` | SELECT (count for stats) |

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SPRING_DATASOURCE_URL` | `jdbc:postgresql://postgres:5432/docintel?user=docintel_app&password=docintel_app_secret` | PostgreSQL (as `docintel_app`, RLS enforced) |
| `ZITADEL_URL` | `http://zitadel-server:9000` | Zitadel base URL |
| `ZITADEL_TOKEN` | _(required)_ | Zitadel API token |
| `MINIO_ENDPOINT` | `http://minio:9000` | MinIO URL |
| `MINIO_ACCESS_KEY` | `minioadmin` | MinIO access key |
| `MINIO_SECRET_KEY` | `minioadmin` | MinIO secret key |
| `QDRANT_URL` | `http://qdrant:6333` | Qdrant URL |
| `REDIS_HOST` | `redis` | Redis host |
| `REDIS_PORT` | `6379` | Redis port |
