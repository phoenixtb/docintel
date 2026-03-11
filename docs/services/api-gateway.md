# API Gateway

**Language/Framework:** Kotlin · Spring Cloud Gateway (WebFlux / Netty)  
**Port:** `8080`  
**Source:** `services/api-gateway/`

---

## Responsibilities

- Single entry point for all client traffic
- JWT authentication via Zitadel OIDC
- RBAC authorization via OPA (Open Policy Agent)
- Per-tenant rate limiting and quota enforcement
- Request routing with path rewriting to downstream services
- Header injection (tenant claims extracted from JWT)

---

## Filter Execution Order

Filters run in priority order before any route handler:

| Order | Filter | Purpose |
|-------|--------|---------|
| `-100` | `TenantFilter` | Validates JWT, extracts `tenant_id`/`role`/user fields, injects as `X-*` headers |
| `-99` | `OpaAuthorizationFilter` | Calls OPA `/v1/data/docintel/authz/allow` — fails closed (403 if OPA is down) |
| `-98` | `QuotaEnforcementFilter` | Enforces Redis-backed per-tenant document and daily query quotas |
| Spring Cloud Gateway default | Rate limiter | Token bucket per tenant via `RedisRateLimiter` |

---

## JWT Claim Extraction (`TenantFilter`)

Tries the following sources in order to resolve `tenant_id`:
1. Direct `tenant_id` JWT claim
2. Nested `tenant.tenant_id` map (Zitadel scope format)
3. First group matching `tenant-*` pattern from `groups` claim

Role is extracted from the `role` JWT claim (default: `tenant_user`).

Headers forwarded downstream:

| Header | Source |
|--------|--------|
| `X-Tenant-Id` | Resolved tenant ID |
| `X-User-Role` | JWT `role` claim |
| `X-User-Id` | JWT `sub` |
| `X-User-Email` | JWT `email` |
| `X-User-Name` | JWT `name` / `preferred_username` |
| `Authorization` | Original JWT, passed through |

---

## OPA Authorization (`OpaAuthorizationFilter`)

All non-health requests are evaluated against the OPA policy at `config/opa/policies/docintel.rego`.

Input sent to OPA:
```json
{
  "method": "PATCH",
  "path": "/api/v1/tenants/alpha/settings",
  "role": "tenant_admin",
  "tenant_id": "alpha"
}
```

**Policy summary:**

| Role | Access |
|------|--------|
| `platform_admin` | Unrestricted |
| `tenant_admin` | All routes except `/api/v1/admin*` and `/api/v1/tenants*` (platform-only), plus own tenant's `/settings` and `/users` |
| `tenant_user` | Documents (GET), queries, conversations, feedback, analytics, vector-stats, sample-datasets |

OPA container runs with `--watch` so policy file changes hot-reload automatically.

---

## Quota Enforcement (`QuotaEnforcementFilter`)

Redis keys used:

| Key | Meaning |
|-----|---------|
| `quota:{tenant_id}:limit:documents` | Max document count (set by admin-service on tenant create/update) |
| `quota:{tenant_id}:doc_count` | Current document count |
| `quota:{tenant_id}:limit:queries_per_day` | Max daily queries |
| `quota:{tenant_id}:daily_queries:{date}` | Today's query count (TTL ~25h) |

Returns `429 Too Many Requests` with `X-Quota-Exceeded` header when exceeded.

---

## Rate Limiting

Two Redis rate limiters (token bucket):

| Limiter Bean | Routes | Rate | Burst |
|---|---|---|---|
| `defaultRedisRateLimiter` | All routes (default) | 100 req/s | 150 |
| `queryRedisRateLimiter` | `/api/v1/query`, `/api/v1/query/stream` | 20 req/s | 30 |

Key resolver: `X-Tenant-Id` header (tenant-scoped).

---

## Route Table (docker profile)

| Gateway Path | Rewrites To | Service |
|---|---|---|
| `GET,PATCH /api/v1/tenants/*/settings` | `/internal/tenants/{tid}/settings` | admin-service:8082 |
| `/api/v1/tenants`, `/api/v1/tenants/**` | `/internal/tenants{segment}` | admin-service:8082 |
| `/api/v1/admin/**` | `/internal/{segment}` | admin-service:8082 |
| `/api/v1/documents`, `/api/v1/documents/**` | `/internal/documents{segment}` | document-service:8081 |
| `/api/v1/documents/all` (DELETE) | `/internal/documents/all` | document-service:8081 |
| `/api/v1/query` | `/query` | rag-service:8000 |
| `/api/v1/query/stream` | `/query/stream` | rag-service:8000 |
| `/api/v1/conversations`, `/api/v1/conversations/**` | `/conversations{segment}` | rag-service:8000 |
| `/api/v1/models` | `/models` | rag-service:8000 |
| `/api/v1/vector-stats` | `/vector-stats` | rag-service:8000 |
| `/api/v1/classify-domain` | `/classify-domain` | rag-service:8000 |
| `/api/v1/sample-datasets/**` | `/sample-datasets{segment}` | rag-service:8000 |
| `/api/v1/feedback` | `/events/feedback` | analytics-service:8001 |
| `/api/v1/analytics/**` | `/analytics/{segment}` | analytics-service:8001 |

---

## Security (Spring Security)

- Profile `!dev`: JWT validation active. `jwk-set-uri` points to Zitadel's JWKS endpoint (internal Docker URL to avoid issuer mismatch).
- Profile `dev`: `permitAll` — no auth required for local IDE development.
- CSRF disabled.
- CORS: all origins, all methods including `PATCH`, `allowCredentials=true`.

---

## Key Files

| File | Purpose |
|------|---------|
| `config/GatewayConfig.kt` | OPA WebClient bean, rate limiter beans, key resolver |
| `config/SecurityConfig.kt` | Spring Security, CORS, JWT resource server |
| `filter/TenantFilter.kt` | JWT extraction, header injection (order -100) |
| `filter/OpaAuthorizationFilter.kt` | OPA RBAC enforcement (order -99) |
| `filter/QuotaEnforcementFilter.kt` | Redis-backed quota enforcement (order -98) |
| `controller/HealthController.kt` | `GET /api/v1/health` |
| `application.yml` | Base routes (dev/local) |
| `application-docker.yml` | Docker routes, rate limiters, OPA URL |

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ZITADEL_JWK_SET_URI` | `http://zitadel-server:9000jwks/` | JWKS endpoint for JWT validation |
| `ADMIN_SERVICE_URL` | `http://admin-service:8082` | Admin service URL |
| `OPA_URL` | `http://opa:8181` | OPA policy engine URL |
| `SPRING_DATA_REDIS_PASSWORD` | `redissecret` | Redis password |
