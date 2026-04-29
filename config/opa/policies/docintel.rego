package docintel.authz

import future.keywords.in

# Route-level RBAC — evaluated by the API Gateway OpaAuthorizationFilter.
#
# Input shape (sent by OpaAuthorizationFilter):
#   input.user.roles     = list of fine-grained permissions from docintel-actions
#                          e.g. ["documents:rw", "query:execute", "conversations:rw"]
#   input.user.clearance = "public" | "internal" | "confidential" | "restricted"
#   input.user.tenant_id = tenant identifier
#   input.user.user_id   = user UUID (sub)
#   input.request.method = HTTP method ("GET", "POST", ...)
#   input.request.path   = raw request path
#
# Evaluation:
#   allow = true iff the route requires a role AND the user holds that role.
#   Unknown / unmatched routes default to deny (fail-closed).

default allow = false

# ---------------------------------------------------------------------------
# Route → required role mapping
# ---------------------------------------------------------------------------

# Documents — read
route_requires_role(method, path, "documents:r") {
    method == "GET"
    glob.match("/api/v1/documents*", [], path)
}

# Documents — write (create, bulk)
route_requires_role(method, path, "documents:rw") {
    method == "POST"
    glob.match("/api/v1/documents*", [], path)
}

# Documents — delete single
route_requires_role(method, path, "documents:delete") {
    method == "DELETE"
    glob.match("/api/v1/documents/*", [], path)
    not glob.match("/api/v1/documents/all*", [], path)
    not glob.match("/api/v1/documents/cleanup/jobs/*", [], path)
}

# Documents — delete all (platform_admin only)
route_requires_role(method, path, "documents:delete_all") {
    method == "DELETE"
    glob.match("/api/v1/documents/all*", [], path)
}

# Cleanup — preview (dry-run count; aligned with read permission)
route_requires_role(method, path, "documents:r") {
    method == "POST"
    path == "/api/v1/documents/cleanup/preview"
}

# Cleanup — start job (same gate as single-doc delete; cross-tenant check is in the service)
route_requires_role(method, path, "documents:delete") {
    method == "POST"
    path == "/api/v1/documents/cleanup/jobs"
}

# Cleanup — job status poll
route_requires_role(method, path, "documents:r") {
    method == "GET"
    glob.match("/api/v1/documents/cleanup/jobs/*", [], path)
    not glob.match("/api/v1/documents/cleanup/jobs/*/events", [], path)
}

# Cleanup — job SSE stream
route_requires_role(method, path, "documents:r") {
    method == "GET"
    glob.match("/api/v1/documents/cleanup/jobs/*/events", [], path)
}

# Cleanup — cancel job
route_requires_role(method, path, "documents:delete") {
    method == "DELETE"
    glob.match("/api/v1/documents/cleanup/jobs/*", [], path)
}

# Query
route_requires_role(method, path, "query:execute") {
    method == "POST"
    glob.match("/api/v1/query*", [], path)
}
route_requires_role(method, path, "query:execute") {
    method == "GET"
    glob.match("/api/v1/query*", [], path)
}

# Conversations
route_requires_role(method, path, "conversations:rw") {
    glob.match("/api/v1/conversations*", [], path)
}

# Feedback
route_requires_role(method, path, "analytics:feedback") {
    method == "POST"
    glob.match("/api/v1/feedback*", [], path)
}

# Analytics — read (tenant scoped)
route_requires_role(method, path, "analytics:r") {
    method == "GET"
    glob.match("/api/v1/analytics*", [], path)
}

# Analytics — read all tenants (platform_admin)
route_requires_role(method, path, "analytics:r_all") {
    method == "GET"
    glob.match("/api/v1/analytics*", [], path)
}

# Ingestion — trigger ingest / sample datasets
route_requires_role(method, path, "ingestion:rw") {
    method == "POST"
    glob.match("/api/v1/ingest*", [], path)
}
route_requires_role(method, path, "ingestion:rw") {
    method == "POST"
    glob.match("/api/v1/sample-datasets*", [], path)
}

# Sample datasets — read list (all authenticated) — legacy path
route_requires_role(method, path, "documents:r") {
    method == "GET"
    glob.match("/api/v1/sample-datasets*", [], path)
}

# data-loader dataset endpoints (replaces /api/v1/sample-datasets* in Phase 2)
route_requires_role(method, path, "ingestion:rw") {
    method == "POST"
    glob.match("/api/v1/datasets*", [], path)
}
route_requires_role(method, path, "documents:r") {
    method == "GET"
    glob.match("/api/v1/datasets*", [], path)
}

# Vector stats — read
route_requires_role(method, path, "vector_stats:r") {
    method == "GET"
    glob.match("/api/v1/vector-stats*", [], path)
}

# Models — read (all authenticated)
route_requires_role(method, path, "models:r") {
    method == "GET"
    path == "/api/v1/models"
}

# Admin — full admin panel access (platform_admin)
route_requires_role(method, path, "admin:rw") {
    glob.match("/api/v1/admin*", [], path)
}

# Cache management — tenant_admin can manage their own tenant's cache
route_requires_role(method, path, "admin.cache:rw") {
    method == "GET"
    glob.match("/api/v1/admin/cache/stats*", [], path)
}
route_requires_role(method, path, "admin.cache:rw") {
    method == "POST"
    glob.match("/api/v1/admin/cache/clear*", [], path)
}

# Tenant settings — read/write (tenant_admin + platform_admin)
route_requires_role(method, path, "admin.tenants.settings:rw") {
    method == "GET"
    glob.match("/api/v1/tenants/*/settings*", [], path)
}
route_requires_role(method, path, "admin.tenants.settings:rw") {
    method == "PATCH"
    glob.match("/api/v1/tenants/*/settings*", [], path)
}
route_requires_role(method, path, "admin.tenants.settings:rw") {
    method == "DELETE"
    glob.match("/api/v1/tenants/*/settings*", [], path)
}

# Tenant users + usage (tenant_admin can manage their own tenant)
route_requires_role(method, path, "admin.tenants.settings:rw") {
    method == "GET"
    glob.match("/api/v1/tenants/*/users*", [], path)
}
route_requires_role(method, path, "admin.tenants.settings:rw") {
    method == "GET"
    glob.match("/api/v1/tenants/*/usage", [], path)
}
route_requires_role(method, path, "admin.tenants.settings:rw") {
    method == "PUT"
    glob.match("/api/v1/tenants/*/users/*/role", [], path)
}

# Tenant model profiles — tenant_admin can manage their own tenant's sampling overrides
route_requires_role(method, path, "admin.tenants.settings:rw") {
    method == "GET"
    glob.match("/api/v1/tenants/*/model-profiles*", [], path)
}
route_requires_role(method, path, "admin.tenants.settings:rw") {
    method == "POST"
    glob.match("/api/v1/tenants/*/model-profiles*", [], path)
}
route_requires_role(method, path, "admin.tenants.settings:rw") {
    method == "PUT"
    glob.match("/api/v1/tenants/*/model-profiles/*", [], path)
}
route_requires_role(method, path, "admin.tenants.settings:rw") {
    method == "DELETE"
    glob.match("/api/v1/tenants/*/model-profiles/*", [], path)
}
route_requires_role(method, path, "admin.tenants.settings:rw") {
    method == "DELETE"
    glob.match("/api/v1/tenants/*/model-profiles-cache*", [], path)
}

# Resolve effective params — rag-service (tenant_admin for own tenant, platform_admin for any)
route_requires_role(method, path, "admin.tenants.settings:rw") {
    method == "GET"
    glob.match("/api/v1/tenants/*/model-profiles/resolve*", [], path)
}

# Tenant management (platform_admin only — list/create/delete tenants)
route_requires_role(method, path, "admin:rw") {
    glob.match("/api/v1/tenants*", [], path)
}

# User preferences — any authenticated user (tenant_user, tenant_admin, platform_admin)
route_requires_role(method, path, "user.preferences:rw") {
    method == "GET"
    path == "/api/v1/users/me/preferences"
}
route_requires_role(method, path, "user.preferences:rw") {
    method == "PATCH"
    path == "/api/v1/users/me/preferences"
}
route_requires_role(method, path, "user.preferences:rw") {
    method == "DELETE"
    path == "/api/v1/users/me/preferences/invalidate-cache"
}

# ---------------------------------------------------------------------------
# Allow rule — user must hold at least one matching role
# ---------------------------------------------------------------------------

allow {
    some role
    role = input.user.roles[_]
    route_requires_role(input.request.method, input.request.path, role)
}
