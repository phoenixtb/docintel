package docintel_test

import rego.v1

import data.docintel.authz
import data.docintel.chunk

# ---------------------------------------------------------------------------
# Route RBAC tests (docintel.authz)
# ---------------------------------------------------------------------------

test_platform_admin_can_access_admin_routes if {
    authz.allow with input as {
        "user": {
            "roles":     ["documents:rw", "documents:delete_all", "analytics:r_all", "ingestion:rw", "admin:rw", "vector_stats:r", "models:r"],
            "clearance": "restricted",
            "tenant_id": "alpha",
            "user_id":   "u1",
        },
        "request": {"method": "GET", "path": "/api/v1/admin/tenants"},
    }
}

test_tenant_user_can_query if {
    authz.allow with input as {
        "user": {
            "roles":     ["documents:r", "query:execute", "conversations:rw", "analytics:feedback", "models:r"],
            "clearance": "internal",
            "tenant_id": "alpha",
            "user_id":   "u2",
        },
        "request": {"method": "POST", "path": "/api/v1/query"},
    }
}

test_tenant_user_cannot_access_admin if {
    not authz.allow with input as {
        "user": {
            "roles":     ["documents:r", "query:execute", "conversations:rw"],
            "clearance": "internal",
            "tenant_id": "alpha",
            "user_id":   "u2",
        },
        "request": {"method": "GET", "path": "/api/v1/admin/tenants"},
    }
}

test_tenant_user_cannot_delete_all_documents if {
    not authz.allow with input as {
        "user": {
            "roles":     ["documents:r", "documents:delete", "query:execute"],
            "clearance": "internal",
            "tenant_id": "alpha",
            "user_id":   "u2",
        },
        "request": {"method": "DELETE", "path": "/api/v1/documents/all"},
    }
}

test_tenant_admin_can_read_settings if {
    authz.allow with input as {
        "user": {
            "roles":     ["documents:rw", "admin.tenants.settings:rw", "models:r"],
            "clearance": "confidential",
            "tenant_id": "alpha",
            "user_id":   "u3",
        },
        "request": {"method": "GET", "path": "/api/v1/tenants/alpha/settings"},
    }
}

test_empty_roles_denied if {
    not authz.allow with input as {
        "user": {
            "roles":     [],
            "clearance": "internal",
            "tenant_id": "alpha",
            "user_id":   "u4",
        },
        "request": {"method": "GET", "path": "/api/v1/documents"},
    }
}

# ---------------------------------------------------------------------------
# Chunk ABAC tests (docintel.chunk)
# ---------------------------------------------------------------------------

# Open chunk (no role restriction) — any clearance that covers classification
test_open_internal_chunk_accessible_by_internal_user if {
    chunk.allow with input as {
        "user": {
            "user_id":    "u1",
            "tenant_id":  "alpha",
            "roles":      ["documents:r"],
            "clearance":  "internal",
            "department": null,
            "region":     "global",
        },
        "chunk": {
            "classification": "internal",
            "allowed_roles":  [],
            "allowed_users":  [],
            "department":     null,
            "region":         "global",
            "expires_at":     null,
        },
    }
}

# User clearance too low — confidential chunk denied to internal user
test_confidential_chunk_denied_to_internal_user if {
    not chunk.allow with input as {
        "user": {
            "user_id":    "u2",
            "tenant_id":  "alpha",
            "roles":      ["documents:r"],
            "clearance":  "internal",
            "department": null,
            "region":     "global",
        },
        "chunk": {
            "classification": "confidential",
            "allowed_roles":  [],
            "allowed_users":  [],
            "department":     null,
            "region":         "global",
            "expires_at":     null,
        },
    }
}

# Confidential chunk accessible to confidential-clearance user
test_confidential_chunk_accessible_to_confidential_user if {
    chunk.allow with input as {
        "user": {
            "user_id":    "u3",
            "tenant_id":  "alpha",
            "roles":      ["documents:rw"],
            "clearance":  "confidential",
            "department": null,
            "region":     "global",
        },
        "chunk": {
            "classification": "confidential",
            "allowed_roles":  [],
            "allowed_users":  [],
            "department":     null,
            "region":         "global",
            "expires_at":     null,
        },
    }
}

# Role-restricted chunk — user holds required role
test_role_restricted_chunk_accessible_with_correct_role if {
    chunk.allow with input as {
        "user": {
            "user_id":    "u4",
            "tenant_id":  "alpha",
            "roles":      ["documents:r", "analytics:r"],
            "clearance":  "internal",
            "department": null,
            "region":     "global",
        },
        "chunk": {
            "classification": "internal",
            "allowed_roles":  ["analytics:r"],
            "allowed_users":  [],
            "department":     null,
            "region":         "global",
            "expires_at":     null,
        },
    }
}

# Role-restricted chunk — user lacks required role
test_role_restricted_chunk_denied_without_role if {
    not chunk.allow with input as {
        "user": {
            "user_id":    "u5",
            "tenant_id":  "alpha",
            "roles":      ["documents:r"],
            "clearance":  "internal",
            "department": null,
            "region":     "global",
        },
        "chunk": {
            "classification": "internal",
            "allowed_roles":  ["analytics:r"],
            "allowed_users":  [],
            "department":     null,
            "region":         "global",
            "expires_at":     null,
        },
    }
}

# Explicit user grant overrides role restriction
test_explicit_user_grant_allows_access if {
    chunk.allow with input as {
        "user": {
            "user_id":    "special-user-uuid",
            "tenant_id":  "alpha",
            "roles":      ["documents:r"],
            "clearance":  "internal",
            "department": null,
            "region":     "global",
        },
        "chunk": {
            "classification": "internal",
            "allowed_roles":  ["analytics:r"],
            "allowed_users":  ["special-user-uuid"],
            "department":     null,
            "region":         "global",
            "expires_at":     null,
        },
    }
}

# Department restriction — mismatch denied
test_department_restricted_chunk_denied if {
    not chunk.allow with input as {
        "user": {
            "user_id":    "u6",
            "tenant_id":  "alpha",
            "roles":      ["documents:r"],
            "clearance":  "internal",
            "department": "engineering",
            "region":     "global",
        },
        "chunk": {
            "classification": "internal",
            "allowed_roles":  [],
            "allowed_users":  [],
            "department":     "finance",
            "region":         "global",
            "expires_at":     null,
        },
    }
}

# Region restriction — global chunk accessible from any region
test_global_chunk_accessible_from_any_region if {
    chunk.allow with input as {
        "user": {
            "user_id":    "u7",
            "tenant_id":  "alpha",
            "roles":      ["documents:r"],
            "clearance":  "internal",
            "department": null,
            "region":     "us-east",
        },
        "chunk": {
            "classification": "internal",
            "allowed_roles":  [],
            "allowed_users":  [],
            "department":     null,
            "region":         "global",
            "expires_at":     null,
        },
    }
}
