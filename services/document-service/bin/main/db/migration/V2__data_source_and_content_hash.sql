-- Phase 1 data-pipeline redesign:
--   1. Add data_sources table for tracking external data source loads as
--      first-class lifecycle objects (HuggingFace, S3, etc.).
--   2. Add content_hash and data_source_id to documents for content-addressed
--      dedup and data-source cascading deletes.
--
-- Uses IF NOT EXISTS / ADD COLUMN IF NOT EXISTS so this migration is safe to
-- apply on fresh installs where init.sql has already created these objects.

CREATE TABLE IF NOT EXISTS data_sources (
    id              UUID PRIMARY KEY,
    tenant_id       VARCHAR(64)  NOT NULL REFERENCES tenants(id),
    source_type     VARCHAR(64)  NOT NULL,
    source_config   JSONB,
    status          VARCHAR(32)  NOT NULL DEFAULT 'LOADING',
    document_count  INT          NOT NULL DEFAULT 0,
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS idx_data_sources_tenant ON data_sources(tenant_id);

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS content_hash   VARCHAR(64),
    ADD COLUMN IF NOT EXISTS data_source_id UUID REFERENCES data_sources(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_documents_content_hash ON documents(tenant_id, content_hash);

-- RLS policy for data_sources (docintel_ingestion has BYPASSRLS so no policy needed for it)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'data_sources'
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

-- Ensure docintel_ingestion has DML access to the new table
GRANT SELECT, INSERT, UPDATE, DELETE ON data_sources TO docintel_ingestion;
