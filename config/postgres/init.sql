-- DocIntel PostgreSQL Initialization
-- ==================================

-- Create Langfuse database (separate from main app)
CREATE DATABASE langfuse;

-- Create Zitadel database (identity provider — Zitadel manages its own schema)
CREATE DATABASE zitadel;

-- Enable extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- =============================================================================
-- Application Roles — created by 00-roles.sh (parameterized with env vars)
-- =============================================================================
-- docintel         (POSTGRES_USER) — superuser, bypasses RLS; used by Flyway/Zitadel
-- docintel_app     — non-superuser, RLS enforced; used by document/rag/admin services
-- docintel_ingestion — BYPASSRLS; used by ingestion-service to write across all tenants

-- =============================================================================
-- Tenants Table
-- =============================================================================
CREATE TABLE tenants (
    id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    quota_documents INT DEFAULT 1000,
    quota_queries_per_day INT DEFAULT 10000,
    settings JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================================
-- Platform Settings Table
-- =============================================================================
-- Key-value store for platform-wide configuration.
-- llm_model = null (JSON null) means "Tenant Choice" — each tenant uses its own preference.
-- llm_model = "qwen3.5:4b" overrides all tenants unconditionally.
CREATE TABLE platform_settings (
    key        VARCHAR(128) PRIMARY KEY,
    value      JSONB        NOT NULL DEFAULT 'null',
    updated_at TIMESTAMPTZ  DEFAULT NOW()
);

-- Seed: null = "Tenant Choice" (no global override)
INSERT INTO platform_settings (key, value) VALUES ('llm_model', 'null');

-- platform_settings is not tenant-scoped — only the superuser (docintel) and
-- admin-service (connecting as docintel_app with platform_admin role) may write it.
GRANT SELECT, INSERT, UPDATE ON platform_settings TO docintel_app;

-- =============================================================================
-- Data Sources Table
-- Tracks external data source loads (HuggingFace, S3, etc.) as first-class
-- lifecycle objects. Documents can be associated with a data source for bulk
-- deletion, re-loading, and observability.
-- =============================================================================
CREATE TABLE data_sources (
    id              UUID PRIMARY KEY,
    tenant_id       VARCHAR(64)  NOT NULL REFERENCES tenants(id),
    source_type     VARCHAR(64)  NOT NULL,  -- 'huggingface', 's3', 'manual', ...
    source_config   JSONB,                  -- { dataset_key, samples, ... }
    status          VARCHAR(32)  NOT NULL DEFAULT 'LOADING',
    document_count  INT          NOT NULL DEFAULT 0,
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMP WITH TIME ZONE
);

CREATE INDEX idx_data_sources_tenant ON data_sources(tenant_id);

-- =============================================================================
-- Documents Table
-- =============================================================================
CREATE TABLE documents (
    id UUID PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(id),
    filename VARCHAR(255) NOT NULL,
    content_type VARCHAR(100),
    file_size BIGINT NOT NULL,
    file_path VARCHAR(500) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    chunk_count INT DEFAULT 0,
    chunking_config JSONB,
    metadata JSONB DEFAULT '{}',
    error_message TEXT,
    -- Content-addressed identity: full 64-char sha256 hex for observability/dedup queries.
    -- The id column itself is UUID(sha256[:32]) — content_hash is the full digest.
    content_hash VARCHAR(64),
    -- Null for manual browser uploads; set for data-loader-originated documents.
    data_source_id UUID REFERENCES data_sources(id) ON DELETE CASCADE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_documents_tenant ON documents(tenant_id);
CREATE INDEX idx_documents_status ON documents(status);
CREATE INDEX idx_documents_created ON documents(created_at);
CREATE INDEX idx_documents_content_hash ON documents(tenant_id, content_hash);

-- =============================================================================
-- Chunks Table (metadata only - vectors stored in Qdrant)
-- =============================================================================
CREATE TABLE chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(id),
    content TEXT NOT NULL,
    chunk_index INT NOT NULL,
    start_char INT,
    end_char INT,
    token_count INT,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_chunks_document ON chunks(document_id);
CREATE INDEX idx_chunks_tenant ON chunks(tenant_id);

-- =============================================================================
-- Query Audit Log
-- =============================================================================
CREATE TABLE query_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(id),
    user_id VARCHAR(64),
    question TEXT NOT NULL,
    answer TEXT,
    source_chunk_ids UUID[],
    cached BOOLEAN DEFAULT FALSE,
    latency_ms INT,
    model_used VARCHAR(100),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_query_log_tenant ON query_log(tenant_id);
CREATE INDEX idx_query_log_created ON query_log(created_at);
CREATE INDEX idx_query_log_tenant_created ON query_log(tenant_id, created_at);

-- =============================================================================
-- Users Table (simplified for demo)
-- =============================================================================
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) REFERENCES tenants(id),
    email VARCHAR(255) NOT NULL UNIQUE,
    roles TEXT[] DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_users_tenant ON users(tenant_id);

-- =============================================================================
-- Conversations Table (chat history)
-- =============================================================================
CREATE TABLE conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(id),
    user_id VARCHAR(64),
    title VARCHAR(500) NOT NULL DEFAULT 'New Conversation',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_conversations_tenant ON conversations(tenant_id);
CREATE INDEX idx_conversations_user ON conversations(tenant_id, user_id);
CREATE INDEX idx_conversations_updated ON conversations(updated_at DESC);

-- =============================================================================
-- Messages Table (conversation messages)
-- =============================================================================
CREATE TABLE messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    sources JSONB,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_messages_conversation ON messages(conversation_id);
CREATE INDEX idx_messages_created ON messages(conversation_id, created_at);

-- =============================================================================
-- Update Triggers
-- =============================================================================
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER documents_updated_at
    BEFORE UPDATE ON documents
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER tenants_updated_at
    BEFORE UPDATE ON tenants
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER conversations_updated_at
    BEFORE UPDATE ON conversations
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- =============================================================================
-- Grant table permissions
-- =============================================================================
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO docintel_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO docintel_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO docintel_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO docintel_app;

-- docintel_ingestion: full DML access (BYPASSRLS handles tenant isolation at role level)
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO docintel_ingestion;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO docintel_ingestion;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO docintel_ingestion;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO docintel_ingestion;

-- =============================================================================
-- Row-Level Security (RLS)
-- All tenant-scoped tables enforce isolation via SET LOCAL app.current_tenant
-- FORCE ROW LEVEL SECURITY applies even to table owners (docintel superuser bypasses
-- only if they are a PostgreSQL superuser AND the table was created by them,
-- but docintel_app is never a superuser so RLS always applies to it)
-- =============================================================================

ALTER TABLE data_sources  ENABLE ROW LEVEL SECURITY;
ALTER TABLE data_sources  FORCE ROW LEVEL SECURITY;
ALTER TABLE documents     ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents     FORCE ROW LEVEL SECURITY;
ALTER TABLE chunks        ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunks        FORCE ROW LEVEL SECURITY;
ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversations FORCE ROW LEVEL SECURITY;
ALTER TABLE messages      ENABLE ROW LEVEL SECURITY;
ALTER TABLE messages      FORCE ROW LEVEL SECURITY;
ALTER TABLE query_log     ENABLE ROW LEVEL SECURITY;
ALTER TABLE query_log     FORCE ROW LEVEL SECURITY;

-- Messages isolation is via conversation (JOIN), but add direct policy too
-- For messages we check via the conversations table through the FK

-- RLS policies: platform_admin sees all rows; others are scoped to their tenant.
-- current_setting(..., true) returns NULL when not set — safe for boolean short-circuit.
CREATE POLICY tenant_isolation_data_sources ON data_sources
    AS PERMISSIVE FOR ALL TO docintel_app
    USING (
        current_setting('app.user_role', true) = 'platform_admin'
        OR tenant_id = current_setting('app.current_tenant', true)
    );

CREATE POLICY tenant_isolation_documents ON documents
    AS PERMISSIVE FOR ALL TO docintel_app
    USING (
        current_setting('app.user_role', true) = 'platform_admin'
        OR tenant_id = current_setting('app.current_tenant', true)
    );

CREATE POLICY tenant_isolation_chunks ON chunks
    AS PERMISSIVE FOR ALL TO docintel_app
    USING (
        current_setting('app.user_role', true) = 'platform_admin'
        OR tenant_id = current_setting('app.current_tenant', true)
    );

CREATE POLICY tenant_isolation_conversations ON conversations
    AS PERMISSIVE FOR ALL TO docintel_app
    USING (
        current_setting('app.user_role', true) = 'platform_admin'
        OR tenant_id = current_setting('app.current_tenant', true)
    );

-- Messages don't have tenant_id directly; isolate by JOIN to conversations
CREATE POLICY tenant_isolation_messages ON messages
    AS PERMISSIVE FOR ALL TO docintel_app
    USING (
        current_setting('app.user_role', true) = 'platform_admin'
        OR EXISTS (
            SELECT 1 FROM conversations c
            WHERE c.id = messages.conversation_id
              AND c.tenant_id = current_setting('app.current_tenant', true)
        )
    );

CREATE POLICY tenant_isolation_query_log ON query_log
    AS PERMISSIVE FOR ALL TO docintel_app
    USING (
        current_setting('app.user_role', true) = 'platform_admin'
        OR tenant_id = current_setting('app.current_tenant', true)
    );

-- tenants table: platform_admin sees all; others see their own tenant row only
ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_tenants ON tenants
    AS PERMISSIVE FOR ALL TO docintel_app
    USING (
        current_setting('app.user_role', true) = 'platform_admin'
        OR id = current_setting('app.current_tenant', true)
    );

-- =============================================================================
-- Seed Tenants
-- =============================================================================
-- 'default' is the fallback tenant_id for platform admins (no real tenant scope)
INSERT INTO tenants (id, name) VALUES ('default', 'DocIntel Platform');

INSERT INTO tenants (id, name) VALUES ('alpha', 'Alpha Corp');

INSERT INTO tenants (id, name) VALUES ('beta', 'Beta Corp');

INSERT INTO tenants (id, name) VALUES ('e2e', 'E2E Test Tenant');
