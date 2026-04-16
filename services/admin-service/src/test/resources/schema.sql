-- Test schema for admin-service integration tests.
-- Uses Testcontainers (PostgreSQL), so real schema-qualified DDL works here.

CREATE SCHEMA IF NOT EXISTS admin;
CREATE SCHEMA IF NOT EXISTS documents;

-- ===== admin schema =====
CREATE TABLE IF NOT EXISTS admin.tenants (
    id                    VARCHAR(64) PRIMARY KEY,
    name                  VARCHAR(255) NOT NULL,
    settings              JSONB DEFAULT '{}',
    quota_documents       INT DEFAULT 1000,
    quota_queries_per_day INT DEFAULT 10000,
    created_at            TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at            TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS admin.user_preferences (
    user_id    TEXT         NOT NULL,
    tenant_id  TEXT         NOT NULL REFERENCES admin.tenants(id) ON DELETE CASCADE,
    key        TEXT         NOT NULL,
    value      JSONB        NOT NULL DEFAULT 'null',
    updated_at TIMESTAMPTZ  DEFAULT NOW(),
    PRIMARY KEY (user_id, tenant_id, key)
);

-- Unqualified alias — admin-service sets search_path = admin, ... so plain
-- "tenants" resolves here in the test container as well.
SET search_path = admin, documents, public;

-- ===== documents schema =====
CREATE TABLE IF NOT EXISTS documents.documents (
    id            UUID PRIMARY KEY,
    tenant_id     VARCHAR(64) NOT NULL,
    filename      VARCHAR(255) NOT NULL,
    content_type  VARCHAR(255),
    file_size     BIGINT DEFAULT 0,
    file_path     VARCHAR(1024) DEFAULT '',
    status        VARCHAR(50) DEFAULT 'PENDING',
    chunk_count   INT DEFAULT 0,
    metadata      JSONB DEFAULT '{}',
    content_hash  VARCHAR(64),
    error_message TEXT,
    created_at    TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_doc_tenant ON documents.documents(tenant_id);
