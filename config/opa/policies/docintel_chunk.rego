package docintel.chunk

import future.keywords.in

# Chunk-level ABAC — evaluated by OpaChunkValidator in rag-service after retrieval.
#
# Input shape (sent by OpaChunkValidator per document chunk):
#   input.user.user_id   = user UUID
#   input.user.tenant_id = tenant identifier
#   input.user.roles     = list of fine-grained permissions
#   input.user.clearance = "public" | "internal" | "confidential" | "restricted"
#   input.user.department = optional department string
#   input.user.region    = "global" or specific region (default: "global")
#
#   input.chunk.classification = "public"|"internal"|"confidential"|"restricted"
#   input.chunk.allowed_roles  = list[str] (empty = open to all tenant members)
#   input.chunk.allowed_users  = list[str] user UUIDs with explicit access
#   input.chunk.department     = optional department string (null = any dept)
#   input.chunk.region         = "global" or specific region
#   input.chunk.expires_at     = ISO8601 string or null
#
# A chunk is accessible only if ALL conditions pass:
#   clearance_ok ∧ role_or_user_ok ∧ department_ok ∧ region_ok ∧ ¬expired

default allow = false

# Numeric clearance ordering — higher = more sensitive
order := {
    "public":       0,
    "internal":     1,
    "confidential": 2,
    "restricted":   3,
}

allow {
    clearance_ok
    role_or_user_ok
    department_ok
    region_ok
    not expired
}

# ---------------------------------------------------------------------------
# Clearance check — user clearance must cover the chunk classification
# ---------------------------------------------------------------------------

clearance_ok {
    order[input.user.clearance] >= order[input.chunk.classification]
}

# Missing clearance / classification defaults: public is readable by all
clearance_ok {
    not input.chunk.classification
}

# ---------------------------------------------------------------------------
# Role / user check — open doc, role match, or explicit user grant
# ---------------------------------------------------------------------------

# Open document — no role restriction; any authenticated tenant member may read
role_or_user_ok {
    count(input.chunk.allowed_roles) == 0
}

# User holds at least one of the required roles
role_or_user_ok {
    input.chunk.allowed_roles[_] == input.user.roles[_]
}

# Explicit per-user grant (overrides role check)
role_or_user_ok {
    input.chunk.allowed_users[_] == input.user.user_id
}

# ---------------------------------------------------------------------------
# Department check
# ---------------------------------------------------------------------------

# No department restriction on the chunk
department_ok {
    not input.chunk.department
}

# User's department matches the chunk's department restriction
department_ok {
    input.chunk.department == input.user.department
}

# ---------------------------------------------------------------------------
# Region check
# ---------------------------------------------------------------------------

# Global chunks are accessible from any region
region_ok {
    input.chunk.region == "global"
}

# No region set on chunk defaults to global access
region_ok {
    not input.chunk.region
}

# User's region matches chunk's region restriction
region_ok {
    input.chunk.region == input.user.region
}

# ---------------------------------------------------------------------------
# Expiry check
# ---------------------------------------------------------------------------

# No expiry set — chunk is permanently accessible
not_expired {
    not input.chunk.expires_at
}

# Chunk is not yet expired
not_expired {
    input.chunk.expires_at
    time.now_ns() <= time.parse_rfc3339_ns(input.chunk.expires_at)
}

expired {
    input.chunk.expires_at
    time.now_ns() > time.parse_rfc3339_ns(input.chunk.expires_at)
}
