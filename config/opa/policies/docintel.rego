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
