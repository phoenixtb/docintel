package docintel.authz

default allow = false

# Platform admin: unrestricted access to all routes
allow {
    input.role == "platform_admin"
}

# Tenant admin: all routes except platform-only management routes
allow {
    input.role == "tenant_admin"
    not is_platform_only
}

# Tenant admin: access own tenant's usage stats
allow {
    input.role == "tenant_admin"
    input.path == concat("", ["/api/v1/tenants/", input.tenant_id, "/usage"])
}

# Tenant admin: access and manage own tenant's users
allow {
    input.role == "tenant_admin"
    startswith(input.path, concat("", ["/api/v1/tenants/", input.tenant_id, "/users"]))
}

# Tenant admin: read available Ollama models (for dropdown)
allow {
    input.role == "tenant_admin"
    input.method == "GET"
    input.path == "/api/v1/models"
}

# Tenant admin: read and update own tenant's model settings
allow {
    input.role == "tenant_admin"
    input.method == "GET"
    input.path == concat("", ["/api/v1/tenants/", input.tenant_id, "/settings"])
}

allow {
    input.role == "tenant_admin"
    input.method == "PATCH"
    input.path == concat("", ["/api/v1/tenants/", input.tenant_id, "/settings"])
}

# Tenant user: documents (read), queries, conversations, feedback, analytics
allow {
    input.role == "tenant_user"
    is_tenant_user_permitted
}

# Platform-only routes (admin management, tenant management)
is_platform_only {
    glob.match("/api/v1/admin*", [], input.path)
}
is_platform_only {
    glob.match("/api/v1/tenants*", [], input.path)
}

# Tenant user permitted routes
is_tenant_user_permitted {
    input.method == "GET"
    glob.match("/api/v1/documents*", [], input.path)
}
# Tenant users can upload and delete their own documents
is_tenant_user_permitted {
    input.method == "POST"
    input.path == "/api/v1/documents"
}
is_tenant_user_permitted {
    input.method == "DELETE"
    glob.match("/api/v1/documents/*", [], input.path)
    # Tenant-scoped: document-service enforces tenant ownership via tenantId path/header
}
# Tenant users can fetch model list (needed by settings/chat pages)
is_tenant_user_permitted {
    input.method == "GET"
    input.path == "/api/v1/models"
}
is_tenant_user_permitted {
    input.method == "POST"
    glob.match("/api/v1/query*", [], input.path)
}
is_tenant_user_permitted {
    input.method == "GET"
    glob.match("/api/v1/query*", [], input.path)
}
is_tenant_user_permitted {
    glob.match("/api/v1/conversations*", [], input.path)
}
is_tenant_user_permitted {
    input.method == "POST"
    glob.match("/api/v1/feedback*", [], input.path)
}
is_tenant_user_permitted {
    input.method == "GET"
    glob.match("/api/v1/analytics*", [], input.path)
}
is_tenant_user_permitted {
    input.method == "GET"
    glob.match("/api/v1/vector-stats*", [], input.path)
}
is_tenant_user_permitted {
    input.method == "POST"
    glob.match("/api/v1/classify-domain*", [], input.path)
}
is_tenant_user_permitted {
    input.method == "GET"
    glob.match("/api/v1/sample-datasets*", [], input.path)
}
is_tenant_user_permitted {
    input.method == "POST"
    glob.match("/api/v1/sample-datasets*", [], input.path)
}
