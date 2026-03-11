# Web UI

**Language/Framework:** TypeScript · SvelteKit 2.x · Tailwind CSS  
**Port:** `5173` (dev) / `3000` (Docker)  
**Source:** `services/web-ui/`

---

## Responsibilities

- OIDC authentication via Zitadel (PKCE flow, `oidc-client-ts`)
- Chat interface with SSE streaming and conversation history
- Document management (upload, list, delete, sample dataset loading)
- Settings page (usage stats, model selection per tenant)
- Admin panel (platform-level model override, tenant management) — visible to `platform_admin` only
- Feedback (like/dislike on answers)

---

## Authentication (`src/lib/auth.ts`)

Uses `oidc-client-ts` with Zitadel as the OIDC provider.

**Flow:**
1. `UserManager` created eagerly at module load (browser-only)
2. Tokens stored in `localStorage` — survive tab close, shared across tabs
3. PKCE authorization code flow (no client secret)
4. Scopes: `openid profile email tenant role offline_access`
5. `automaticSilentRenew: true` — refreshes access token before expiry using refresh token grant
6. `getAccessToken()` calls `signinSilent()` if stored token is expired before returning

**JWT claim extraction (`oidcUserToState`):**
- `tenant_id`: from `profile.tenant_id` → `profile.tenant.tenant_id` → `'default'`
- `role`: from `profile.role` → `'tenant_user'`

**Single source of truth:** `authStore` (Svelte writable store). All reads via `get(authStore)`.

**Role helpers:**
- `isPlatformAdmin()` — `role === 'platform_admin'`
- `isTenantAdmin()` — `role === 'tenant_admin' || 'platform_admin'`

---

## API Client (`src/lib/api.ts` — `apiFetch`)

Central fetch wrapper used by all pages:
- Injects `Authorization: Bearer <token>` on every request
- Injects `X-Tenant-Id` header from auth state
- On 401: performs one `signinSilent()` refresh and retries automatically
- On second 401: clears auth state, calls `triggerSessionExpired()` which redirects to login

---

## Routes

| Route | File | Role | Description |
|-------|------|------|-------------|
| `/` | `+page.svelte` | Any | Redirect to `/chat` |
| `/chat` | `chat/+page.svelte` | Authenticated | Chat interface with conversation sidebar |
| `/documents` | `documents/+page.svelte` | Authenticated | Document management |
| `/settings` | `settings/+page.svelte` | `tenant_admin`+ | Usage stats, model selection |
| `/admin` | `admin/+page.svelte` | `platform_admin` | Platform settings, tenant management |
| `/auth/callback` | `auth/callback/+page.svelte` | Public | OIDC redirect callback handler |
| _(any error)_ | `+error.svelte` | Any | Branded error page |

---

## Chat Page (`routes/chat/+page.svelte`)

### Features

- **Conversation sidebar**: Lists conversations from `/api/v1/conversations`, persists `activeConversationId` in `localStorage` so navigation away and back restores the session
- **URL state**: Active conversation reflected in `?id=` query param
- **SSE streaming**: Reads `text/event-stream` from `POST /api/v1/query/stream`
- **Auto-scroll**: `$effect` watches streaming tokens, scrolls to a DOM anchor at the bottom
- **Streaming decoder**: `TextDecoder` uses `{ stream: true }` to correctly handle tokens split across network reads
- **Source citations**: Sources returned in `{"type":"sources"}` SSE event; displayed after the answer

### SSE event types handled

| Type | Action |
|------|--------|
| `token` | Append to current assistant message |
| `sources` | Store source list, display citations |
| `done` | Close stream, save to conversation history |
| `error` | Display inline error |

### Concurrency UX

When the LLM semaphore is full (e.g. 3 concurrent requests), the service returns a `503` with a `Retry-After` header. The UI shows a "Server busy, try again" message.

---

## Documents Page (`routes/documents/+page.svelte`)

- Lists documents via `GET /api/v1/documents` (loaded in `onMount`, not `$effect`, to prevent re-fetching loops)
- Upload via multipart `POST /api/v1/documents` with optional `domain` and `metadata`
- Shows processing status: PENDING / COMPLETED / FAILED
- Delete individual documents
- Load sample datasets (TechQA, HR Policies, CUAD Contracts) via `POST /api/v1/sample-datasets/load`

---

## Settings Page (`routes/settings/+page.svelte`)

Tabs:
1. **Usage**: Document count, chunk count, query totals, queries last 24h, cache hit rate
2. **Documents**: Indexed document list (from admin stats)
3. **Users**: Tenant user list + role management (tenant_admin only)
4. **Model**: Select LLM model for this tenant. Shows platform default badge when no override is set. Saving calls `PATCH /api/v1/tenants/{tenantId}/settings` then clears tenant cache via `POST /api/v1/admin/cache/clear/{tenantId}`

---

## Admin Page (`routes/admin/+page.svelte`)

Only visible to `platform_admin` role (guard in layout).

- Platform-level LLM model override (affects all tenants unless overridden at tenant level)
- Tenant list with quotas
- Create / delete tenants

---

## Layout (`routes/+layout.svelte`)

- Navigation links: Chat, Documents, Settings, Admin (conditional)
- `UserMenu` component: shows username, role, tenant, logout button
- `isActive()` helper: exact match for root-level routes, prefix-plus-slash for nested routes (prevents false positive highlighting)
- Restores auth state on `onMount` via `restoreAuthState()`

---

## Key Files

| File | Purpose |
|------|---------|
| `src/lib/auth.ts` | OIDC authentication, `authStore`, token refresh, role helpers |
| `src/lib/api.ts` | `apiFetch` — central HTTP client with auth headers and 401 retry |
| `src/lib/components/MessageBubble.svelte` | Chat message rendering (user/assistant/sources) |
| `src/lib/components/ConfirmDialog.svelte` | Reusable confirmation modal |
| `src/lib/components/UserMenu.svelte` | User info + logout dropdown |
| `src/routes/+layout.svelte` | Root layout: nav, auth restore, `isActive()` |
| `src/routes/+error.svelte` | Branded error page for unhandled exceptions / 404s |
| `src/routes/+page.svelte` | Root redirect to `/chat` |
| `src/routes/chat/+page.svelte` | Chat UI, SSE streaming, conversation sidebar |
| `src/routes/documents/+page.svelte` | Document management |
| `src/routes/settings/+page.svelte` | Usage + model settings |
| `src/routes/admin/+page.svelte` | Platform admin panel |
| `src/routes/auth/callback/+page.svelte` | OIDC redirect callback |
| `src/hooks.client.ts` | Client hooks: session-expired redirect, silent renew error handling |
| `svelte.config.js` | SvelteKit adapter config |
| `vite.config.ts` | Vite config with API proxy for local dev |

---

## Environment Variables (Public)

| Variable | Example | Description |
|----------|---------|-------------|
| `PUBLIC_ZITADEL_ISSUER` | `http://localhost:9090` | OIDC issuer URL |
| `PUBLIC_ZITADEL_CLIENT_ID` | `docintel` | OIDC client ID |
| `PUBLIC_API_URL` | `http://localhost:8080` | API Gateway base URL |
| `PUBLIC_APP_NAME` | `DocIntel` | App display name |
