-- Flyway migration V1: Initial schema
-- Mirrors the schema defined in config/postgres/init.sql (which bootstraps the DB container).
-- Flyway manages subsequent schema changes; init.sql is for first-time Docker setup only.

-- Tenants
CREATE TABLE IF NOT EXISTS tenants (
    id                   VARCHAR(64) PRIMARY KEY,
    name                 VARCHAR(255) NOT NULL,
    quota_documents      INT DEFAULT 1000,
    quota_queries_per_day INT DEFAULT 10000,
    settings             JSONB DEFAULT '{}',
    created_at           TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at           TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- Platform Settings
CREATE TABLE IF NOT EXISTS platform_settings (
    key        VARCHAR(128) PRIMARY KEY,
    value      JSONB        NOT NULL DEFAULT 'null',
    updated_at TIMESTAMPTZ  DEFAULT NOW()
);

INSERT INTO platform_settings (key, value)
VALUES ('llm_model', 'null')
ON CONFLICT DO NOTHING;

-- Documents
CREATE TABLE IF NOT EXISTS documents (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    VARCHAR(64) NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    filename     VARCHAR(512) NOT NULL,
    content_type VARCHAR(128),
    file_size    BIGINT DEFAULT 0,
    file_path    TEXT NOT NULL,
    status       VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    chunk_count  INT DEFAULT 0,
    metadata     JSONB DEFAULT '{}',
    error_message TEXT,
    created_at   TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_documents_tenant_id ON documents(tenant_id);
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);

-- Chunks
CREATE TABLE IF NOT EXISTS chunks (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    tenant_id   VARCHAR(64) NOT NULL,
    content     TEXT NOT NULL,
    chunk_index INT NOT NULL,
    start_char  INT DEFAULT 0,
    end_char    INT DEFAULT 0,
    token_count INT DEFAULT 0,
    metadata    JSONB DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_chunks_document_id ON chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_tenant_id ON chunks(tenant_id);

-- Conversations
CREATE TABLE IF NOT EXISTS conversations (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  VARCHAR(64) NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id    VARCHAR(255),
    title      VARCHAR(500) DEFAULT 'New Conversation',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- Messages
CREATE TABLE IF NOT EXISTS messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            VARCHAR(32) NOT NULL,
    content         TEXT NOT NULL,
    sources         JSONB DEFAULT '[]',
    created_at      TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- Query Log
CREATE TABLE IF NOT EXISTS query_log (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  VARCHAR(64) NOT NULL,
    user_id    VARCHAR(255),
    question   TEXT,
    answer     TEXT,
    cache_hit  BOOLEAN DEFAULT FALSE,
    latency_ms INT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- Grants
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO docintel_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO docintel_app;
