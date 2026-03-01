-- DocIntel PostgreSQL Initialization
-- ==================================

-- Create Langfuse database (separate from main app)
CREATE DATABASE langfuse;

-- Create Authentik database (identity provider)
CREATE DATABASE authentik;

-- Enable extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- =============================================================================
-- Application Role (non-superuser, used by all app services for RLS enforcement)
-- =============================================================================
-- docintel (POSTGRES_USER) is a superuser — bypasses RLS
-- docintel_app is a non-superuser — RLS policies are enforced
CREATE ROLE docintel_app WITH LOGIN PASSWORD 'docintel_app_secret';
GRANT CONNECT ON DATABASE docintel TO docintel_app;
GRANT USAGE ON SCHEMA public TO docintel_app;

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
-- Documents Table
-- =============================================================================
CREATE TABLE documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
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
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_documents_tenant ON documents(tenant_id);
CREATE INDEX idx_documents_status ON documents(status);
CREATE INDEX idx_documents_created ON documents(created_at);

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
-- Grant table permissions to docintel_app
-- =============================================================================
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO docintel_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO docintel_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO docintel_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO docintel_app;

-- =============================================================================
-- Row-Level Security (RLS)
-- All tenant-scoped tables enforce isolation via SET LOCAL app.current_tenant
-- FORCE ROW LEVEL SECURITY applies even to table owners (docintel superuser bypasses
-- only if they are a PostgreSQL superuser AND the table was created by them,
-- but docintel_app is never a superuser so RLS always applies to it)
-- =============================================================================

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

-- RLS policies: row is visible only when tenant_id matches the session variable
-- current_setting returns NULL (not empty string) when not set — use coalesce
CREATE POLICY tenant_isolation_documents ON documents
    AS PERMISSIVE FOR ALL TO docintel_app
    USING (tenant_id = current_setting('app.current_tenant', true));

CREATE POLICY tenant_isolation_chunks ON chunks
    AS PERMISSIVE FOR ALL TO docintel_app
    USING (tenant_id = current_setting('app.current_tenant', true));

CREATE POLICY tenant_isolation_conversations ON conversations
    AS PERMISSIVE FOR ALL TO docintel_app
    USING (tenant_id = current_setting('app.current_tenant', true));

-- Messages don't have tenant_id directly; isolate by JOIN to conversations
CREATE POLICY tenant_isolation_messages ON messages
    AS PERMISSIVE FOR ALL TO docintel_app
    USING (
        EXISTS (
            SELECT 1 FROM conversations c
            WHERE c.id = messages.conversation_id
              AND c.tenant_id = current_setting('app.current_tenant', true)
        )
    );

CREATE POLICY tenant_isolation_query_log ON query_log
    AS PERMISSIVE FOR ALL TO docintel_app
    USING (tenant_id = current_setting('app.current_tenant', true));

-- tenants table: platform_admin sees all; others see their own tenant row only
ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_tenants ON tenants
    AS PERMISSIVE FOR ALL TO docintel_app
    USING (
        current_setting('app.current_role', true) = 'platform_admin'
        OR id = current_setting('app.current_tenant', true)
    );

-- =============================================================================
-- Seed Tenants
-- =============================================================================
-- 'default' is the fallback tenant_id for platform admins (no real tenant scope)
INSERT INTO tenants (id, name) VALUES ('default', 'DocIntel Platform');

INSERT INTO tenants (id, name) VALUES ('alpha', 'Alpha Corp');

INSERT INTO tenants (id, name) VALUES ('beta', 'Beta Corp');
