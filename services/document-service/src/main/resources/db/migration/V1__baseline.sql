-- Flyway V1: document-service schema baseline
-- Creates the documents schema tables if they do not already exist.
-- On fresh installs init.sql runs first and creates these tables; IF NOT EXISTS
-- makes this migration a safe no-op in that case.
-- The Flyway connection search_path includes 'documents' first, so unqualified
-- table names resolve to the documents schema.

CREATE TABLE IF NOT EXISTS data_sources (
    id             UUID PRIMARY KEY,
    tenant_id      VARCHAR(64) NOT NULL,
    source_type    VARCHAR(64) NOT NULL,
    source_config  JSONB,
    status         VARCHAR(32) NOT NULL DEFAULT 'LOADING',
    document_count INT         NOT NULL DEFAULT 0,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_data_sources_tenant ON data_sources(tenant_id);

CREATE TABLE IF NOT EXISTS documents (
    id              UUID PRIMARY KEY,
    tenant_id       VARCHAR(64)  NOT NULL,
    filename        VARCHAR(255) NOT NULL,
    content_type    VARCHAR(100),
    file_size       BIGINT       NOT NULL DEFAULT 0,
    file_path       VARCHAR(500) NOT NULL,
    status          VARCHAR(20)  NOT NULL DEFAULT 'PENDING',
    chunk_count     INT DEFAULT 0,
    chunking_config JSONB,
    metadata        JSONB DEFAULT '{}',
    error_message   TEXT,
    content_hash    VARCHAR(64),
    data_source_id  UUID REFERENCES data_sources(id) ON DELETE CASCADE,
    created_at      TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_documents_tenant       ON documents(tenant_id);
CREATE INDEX IF NOT EXISTS idx_documents_status       ON documents(status);
CREATE INDEX IF NOT EXISTS idx_documents_content_hash ON documents(tenant_id, content_hash);

CREATE TABLE IF NOT EXISTS chunks (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID        NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    tenant_id   VARCHAR(64) NOT NULL,
    content     TEXT        NOT NULL,
    chunk_index INT         NOT NULL,
    start_char  INT DEFAULT 0,
    end_char    INT DEFAULT 0,
    token_count INT DEFAULT 0,
    metadata    JSONB DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_tenant   ON chunks(tenant_id);

-- RLS for document-service role
DO $$ BEGIN
    ALTER TABLE data_sources ENABLE ROW LEVEL SECURITY;
    ALTER TABLE data_sources FORCE ROW LEVEL SECURITY;
    ALTER TABLE documents    ENABLE ROW LEVEL SECURITY;
    ALTER TABLE documents    FORCE ROW LEVEL SECURITY;
    ALTER TABLE chunks       ENABLE ROW LEVEL SECURITY;
    ALTER TABLE chunks       FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN NULL;
END $$;

DO $$ BEGIN
    CREATE POLICY tenant_isolation_data_sources ON data_sources
        AS PERMISSIVE FOR ALL TO docintel_app
        USING (
            current_setting('app.user_role', true) = 'platform_admin'
            OR tenant_id = current_setting('app.current_tenant', true)
        );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE POLICY tenant_isolation_documents ON documents
        AS PERMISSIVE FOR ALL TO docintel_app
        USING (
            current_setting('app.user_role', true) = 'platform_admin'
            OR tenant_id = current_setting('app.current_tenant', true)
        );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE POLICY tenant_isolation_chunks ON chunks
        AS PERMISSIVE FOR ALL TO docintel_app
        USING (
            current_setting('app.user_role', true) = 'platform_admin'
            OR tenant_id = current_setting('app.current_tenant', true)
        );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Grants for docintel_app (backward compat) and docintel_documents (service role)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'docintel_app') THEN
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA documents TO docintel_app';
        EXECUTE 'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA documents TO docintel_app';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'docintel_documents') THEN
        EXECUTE 'GRANT ALL ON ALL TABLES IN SCHEMA documents TO docintel_documents';
        EXECUTE 'GRANT ALL ON ALL SEQUENCES IN SCHEMA documents TO docintel_documents';
    END IF;
END $$;
