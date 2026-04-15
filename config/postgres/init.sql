-- DocIntel PostgreSQL Initialization
-- ====================================
-- Runs once when the postgres container initializes a fresh volume.
-- Creates schemas, tables, roles, RLS policies, and seed data.
-- Service-specific schema evolution is handled by each service's Flyway migrations.

-- Separate databases for third-party services (Zitadel, Langfuse manage their own schemas)
CREATE DATABASE langfuse;
CREATE DATABASE zitadel;

-- Extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- =============================================================================
-- Schemas — one per service domain
-- =============================================================================
CREATE SCHEMA IF NOT EXISTS admin;
CREATE SCHEMA IF NOT EXISTS documents;
CREATE SCHEMA IF NOT EXISTS conversations;

-- =============================================================================
-- Shared utility — triggers in each schema call this function
-- =============================================================================
CREATE OR REPLACE FUNCTION public.update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- =============================================================================
-- admin schema — owned by admin-service
-- Holds multi-tenant identity and platform-wide configuration.
-- =============================================================================

CREATE TABLE admin.tenants (
    id                    VARCHAR(64) PRIMARY KEY,
    name                  VARCHAR(255) NOT NULL,
    quota_documents       INT DEFAULT 1000,
    quota_queries_per_day INT DEFAULT 10000,
    settings              JSONB DEFAULT '{}',
    created_at            TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at            TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE admin.platform_settings (
    key        VARCHAR(128) PRIMARY KEY,
    value      JSONB        NOT NULL DEFAULT 'null',
    updated_at TIMESTAMPTZ  DEFAULT NOW()
);

-- null = "Tenant Choice" (no global LLM override)
INSERT INTO admin.platform_settings (key, value) VALUES ('llm_model', 'null');

CREATE TABLE admin.users (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  VARCHAR(64) REFERENCES admin.tenants(id),
    email      VARCHAR(255) NOT NULL UNIQUE,
    roles      TEXT[] DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_admin_users_tenant ON admin.users(tenant_id);

CREATE TRIGGER tenants_updated_at
    BEFORE UPDATE ON admin.tenants
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();

-- =============================================================================
-- documents schema — owned by document-service
-- Tracks ingested files and their chunks (metadata only; vectors in Qdrant).
-- =============================================================================

CREATE TABLE documents.data_sources (
    id             UUID PRIMARY KEY,
    tenant_id      VARCHAR(64) NOT NULL REFERENCES admin.tenants(id),
    source_type    VARCHAR(64) NOT NULL,
    source_config  JSONB,
    status         VARCHAR(32) NOT NULL DEFAULT 'LOADING',
    document_count INT         NOT NULL DEFAULT 0,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at   TIMESTAMPTZ
);

CREATE INDEX idx_data_sources_tenant ON documents.data_sources(tenant_id);

CREATE TABLE documents.documents (
    id             UUID PRIMARY KEY,
    tenant_id      VARCHAR(64)  NOT NULL REFERENCES admin.tenants(id),
    filename       VARCHAR(255) NOT NULL,
    content_type   VARCHAR(100),
    file_size      BIGINT       NOT NULL,
    file_path      VARCHAR(500) NOT NULL,
    status         VARCHAR(20)  NOT NULL DEFAULT 'PENDING',
    chunk_count    INT DEFAULT 0,
    chunking_config JSONB,
    metadata       JSONB DEFAULT '{}',
    error_message  TEXT,
    content_hash   VARCHAR(64),
    data_source_id UUID REFERENCES documents.data_sources(id) ON DELETE CASCADE,
    created_at     TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_documents_tenant       ON documents.documents(tenant_id);
CREATE INDEX idx_documents_status       ON documents.documents(status);
CREATE INDEX idx_documents_created      ON documents.documents(created_at);
CREATE INDEX idx_documents_content_hash ON documents.documents(tenant_id, content_hash);

CREATE TABLE documents.chunks (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID        NOT NULL REFERENCES documents.documents(id) ON DELETE CASCADE,
    tenant_id   VARCHAR(64) NOT NULL REFERENCES admin.tenants(id),
    content     TEXT        NOT NULL,
    chunk_index INT         NOT NULL,
    start_char  INT,
    end_char    INT,
    token_count INT,
    metadata    JSONB DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_chunks_document ON documents.chunks(document_id);
CREATE INDEX idx_chunks_tenant   ON documents.chunks(tenant_id);

CREATE TRIGGER documents_updated_at
    BEFORE UPDATE ON documents.documents
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();

-- =============================================================================
-- conversations schema — owned by rag-service
-- Holds chat sessions and their messages.
-- =============================================================================

CREATE TABLE conversations.conversations (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           VARCHAR(64) NOT NULL REFERENCES admin.tenants(id),
    user_id             VARCHAR(64),
    title               VARCHAR(500) NOT NULL DEFAULT 'New Conversation',
    session_summary     TEXT,
    summary_upto_count  INTEGER NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_conversations_tenant  ON conversations.conversations(tenant_id);
CREATE INDEX idx_conversations_user    ON conversations.conversations(tenant_id, user_id);
CREATE INDEX idx_conversations_updated ON conversations.conversations(updated_at DESC);

CREATE TABLE conversations.messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations.conversations(id) ON DELETE CASCADE,
    role            VARCHAR(20) NOT NULL CHECK (role IN ('user', 'assistant', 'system', 'context_summary')),
    content         TEXT NOT NULL,
    sources         JSONB,
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_messages_conversation ON conversations.messages(conversation_id);
CREATE INDEX idx_messages_created      ON conversations.messages(conversation_id, created_at);

CREATE TRIGGER conversations_updated_at
    BEFORE UPDATE ON conversations.conversations
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();

-- =============================================================================
-- RLS policies — tenant isolation enforced for all service roles
-- current_setting('app.current_tenant') is set per-transaction by the application.
-- platform_admin role bypasses isolation to see all tenants.
-- =============================================================================

-- admin schema
ALTER TABLE admin.tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE admin.tenants FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_tenants ON admin.tenants
    AS PERMISSIVE FOR ALL TO docintel_app
    USING (
        current_setting('app.user_role', true) = 'platform_admin'
        OR id = current_setting('app.current_tenant', true)
    );

CREATE POLICY tenant_isolation_tenants_admin ON admin.tenants
    AS PERMISSIVE FOR ALL TO docintel_admin
    USING (
        current_setting('app.user_role', true) = 'platform_admin'
        OR id = current_setting('app.current_tenant', true)
    );

-- documents schema
ALTER TABLE documents.data_sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents.data_sources FORCE ROW LEVEL SECURITY;
ALTER TABLE documents.documents    ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents.documents    FORCE ROW LEVEL SECURITY;
ALTER TABLE documents.chunks       ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents.chunks       FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_data_sources ON documents.data_sources
    AS PERMISSIVE FOR ALL TO docintel_app
    USING (
        current_setting('app.user_role', true) = 'platform_admin'
        OR tenant_id = current_setting('app.current_tenant', true)
    );

CREATE POLICY tenant_isolation_data_sources_doc ON documents.data_sources
    AS PERMISSIVE FOR ALL TO docintel_documents
    USING (
        current_setting('app.user_role', true) = 'platform_admin'
        OR tenant_id = current_setting('app.current_tenant', true)
    );

CREATE POLICY tenant_isolation_documents ON documents.documents
    AS PERMISSIVE FOR ALL TO docintel_app
    USING (
        current_setting('app.user_role', true) = 'platform_admin'
        OR tenant_id = current_setting('app.current_tenant', true)
    );

CREATE POLICY tenant_isolation_documents_doc ON documents.documents
    AS PERMISSIVE FOR ALL TO docintel_documents
    USING (
        current_setting('app.user_role', true) = 'platform_admin'
        OR tenant_id = current_setting('app.current_tenant', true)
    );

CREATE POLICY tenant_isolation_chunks ON documents.chunks
    AS PERMISSIVE FOR ALL TO docintel_app
    USING (
        current_setting('app.user_role', true) = 'platform_admin'
        OR tenant_id = current_setting('app.current_tenant', true)
    );

CREATE POLICY tenant_isolation_chunks_doc ON documents.chunks
    AS PERMISSIVE FOR ALL TO docintel_documents
    USING (
        current_setting('app.user_role', true) = 'platform_admin'
        OR tenant_id = current_setting('app.current_tenant', true)
    );

-- conversations schema
ALTER TABLE conversations.conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversations.conversations FORCE ROW LEVEL SECURITY;
ALTER TABLE conversations.messages      ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversations.messages      FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_conversations ON conversations.conversations
    AS PERMISSIVE FOR ALL TO docintel_app
    USING (
        current_setting('app.user_role', true) = 'platform_admin'
        OR tenant_id = current_setting('app.current_tenant', true)
    );

CREATE POLICY tenant_isolation_conversations_rag ON conversations.conversations
    AS PERMISSIVE FOR ALL TO docintel_rag
    USING (
        current_setting('app.user_role', true) = 'platform_admin'
        OR tenant_id = current_setting('app.current_tenant', true)
    );

CREATE POLICY tenant_isolation_messages ON conversations.messages
    AS PERMISSIVE FOR ALL TO docintel_app
    USING (
        current_setting('app.user_role', true) = 'platform_admin'
        OR EXISTS (
            SELECT 1 FROM conversations.conversations c
            WHERE c.id = messages.conversation_id
              AND c.tenant_id = current_setting('app.current_tenant', true)
        )
    );

CREATE POLICY tenant_isolation_messages_rag ON conversations.messages
    AS PERMISSIVE FOR ALL TO docintel_rag
    USING (
        current_setting('app.user_role', true) = 'platform_admin'
        OR EXISTS (
            SELECT 1 FROM conversations.conversations c
            WHERE c.id = messages.conversation_id
              AND c.tenant_id = current_setting('app.current_tenant', true)
        )
    );

-- =============================================================================
-- Role grants
-- Roles are created by 00-roles.sh (parameterized with env var passwords).
-- =============================================================================

-- docintel_admin: owns admin schema, read-only on documents.documents for stats
GRANT USAGE ON SCHEMA admin, documents, public TO docintel_admin;
GRANT ALL   ON ALL TABLES    IN SCHEMA admin TO docintel_admin;
GRANT ALL   ON ALL SEQUENCES IN SCHEMA admin TO docintel_admin;
GRANT SELECT ON documents.documents TO docintel_admin;
ALTER DEFAULT PRIVILEGES IN SCHEMA admin GRANT ALL ON TABLES    TO docintel_admin;
ALTER DEFAULT PRIVILEGES IN SCHEMA admin GRANT ALL ON SEQUENCES TO docintel_admin;
GRANT EXECUTE ON FUNCTION public.update_updated_at() TO docintel_admin;

-- docintel_documents: owns documents schema, reads admin.tenants for FK validation
GRANT USAGE ON SCHEMA documents, admin, public TO docintel_documents;
GRANT ALL   ON ALL TABLES    IN SCHEMA documents TO docintel_documents;
GRANT ALL   ON ALL SEQUENCES IN SCHEMA documents TO docintel_documents;
GRANT SELECT ON admin.tenants TO docintel_documents;
ALTER DEFAULT PRIVILEGES IN SCHEMA documents GRANT ALL ON TABLES    TO docintel_documents;
ALTER DEFAULT PRIVILEGES IN SCHEMA documents GRANT ALL ON SEQUENCES TO docintel_documents;
GRANT EXECUTE ON FUNCTION public.update_updated_at() TO docintel_documents;

-- docintel_rag: owns conversations schema, reads admin for model resolver
GRANT USAGE ON SCHEMA conversations, admin, public TO docintel_rag;
GRANT ALL   ON ALL TABLES    IN SCHEMA conversations TO docintel_rag;
GRANT ALL   ON ALL SEQUENCES IN SCHEMA conversations TO docintel_rag;
GRANT SELECT ON admin.tenants, admin.platform_settings TO docintel_rag;
ALTER DEFAULT PRIVILEGES IN SCHEMA conversations GRANT ALL ON TABLES    TO docintel_rag;
ALTER DEFAULT PRIVILEGES IN SCHEMA conversations GRANT ALL ON SEQUENCES TO docintel_rag;
GRANT EXECUTE ON FUNCTION public.update_updated_at() TO docintel_rag;

-- docintel_app: backward-compat role used during service role transition
GRANT USAGE ON SCHEMA admin, documents, conversations, public TO docintel_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES    IN SCHEMA admin         TO docintel_app;
GRANT USAGE, SELECT                  ON ALL SEQUENCES IN SCHEMA admin         TO docintel_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES    IN SCHEMA documents     TO docintel_app;
GRANT USAGE, SELECT                  ON ALL SEQUENCES IN SCHEMA documents     TO docintel_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES    IN SCHEMA conversations TO docintel_app;
GRANT USAGE, SELECT                  ON ALL SEQUENCES IN SCHEMA conversations TO docintel_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA admin         GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES    TO docintel_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA admin         GRANT USAGE, SELECT ON SEQUENCES TO docintel_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA documents     GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES    TO docintel_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA documents     GRANT USAGE, SELECT ON SEQUENCES TO docintel_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA conversations GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES    TO docintel_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA conversations GRANT USAGE, SELECT ON SEQUENCES TO docintel_app;
GRANT EXECUTE ON FUNCTION public.update_updated_at() TO docintel_app;

-- =============================================================================
-- Default search paths per role — unqualified table names resolve in order
-- =============================================================================
ALTER ROLE docintel_admin     SET search_path = admin, documents, public;
ALTER ROLE docintel_documents SET search_path = documents, admin, public;
ALTER ROLE docintel_rag       SET search_path = conversations, admin, public;
ALTER ROLE docintel_app       SET search_path = admin, documents, conversations, public;

-- =============================================================================
-- Seed tenants
-- =============================================================================
INSERT INTO admin.tenants (id, name) VALUES ('default', 'DocIntel Platform');
INSERT INTO admin.tenants (id, name) VALUES ('alpha',   'Alpha Corp');
INSERT INTO admin.tenants (id, name) VALUES ('beta',    'Beta Corp');
INSERT INTO admin.tenants (id, name) VALUES ('e2e',     'E2E Test Tenant');
