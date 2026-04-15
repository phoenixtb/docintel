-- Flyway V2: data_sources table and content_hash column
-- IF NOT EXISTS / ADD COLUMN IF NOT EXISTS — safe on installations where
-- V1 or init.sql already created these objects.

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

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS content_hash   VARCHAR(64),
    ADD COLUMN IF NOT EXISTS data_source_id UUID REFERENCES data_sources(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_documents_content_hash ON documents(tenant_id, content_hash);

-- RLS for data_sources
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'documents'
          AND tablename  = 'data_sources'
          AND policyname = 'tenant_isolation_data_sources'
    ) THEN
        ALTER TABLE data_sources ENABLE ROW LEVEL SECURITY;
        ALTER TABLE data_sources FORCE ROW LEVEL SECURITY;

        CREATE POLICY tenant_isolation_data_sources ON data_sources
            AS PERMISSIVE FOR ALL TO docintel_app
            USING (
                current_setting('app.user_role', true) = 'platform_admin'
                OR tenant_id = current_setting('app.current_tenant', true)
            );
    END IF;
END$$;

-- docintel_documents service role access
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'docintel_documents') THEN
        EXECUTE 'GRANT ALL ON data_sources TO docintel_documents';
        EXECUTE 'GRANT ALL ON documents TO docintel_documents';
        EXECUTE 'GRANT ALL ON chunks TO docintel_documents';
    END IF;
END $$;
