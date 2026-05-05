-- Flyway V5: processing_checkpoints table for crash-resume in sharded PDF ingestion
--
-- Stores the progress of an ongoing multi-shard PDF ingestion. A checkpoint is created
-- at the start of each shard and deleted when the document reaches COMPLETED/FAILED.
-- ReconciliationSweeper cleans up orphan checkpoints for old COMPLETED/FAILED docs.

CREATE TABLE IF NOT EXISTS processing_checkpoints (
    document_id          UUID        PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    tenant_id            VARCHAR(64) NOT NULL,
    last_completed_page  INT         NOT NULL DEFAULT -1,
    chunk_count_so_far   INT         NOT NULL DEFAULT 0,
    total_pages          INT         NOT NULL DEFAULT 0,
    profile              JSONB,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_processing_checkpoints_tenant ON processing_checkpoints(tenant_id);

-- RLS mirrors the documents table
DO $$ BEGIN
    ALTER TABLE processing_checkpoints ENABLE ROW LEVEL SECURITY;
    ALTER TABLE processing_checkpoints FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN NULL;
END $$;

DO $$ BEGIN
    CREATE POLICY tenant_isolation_checkpoints ON processing_checkpoints
        AS PERMISSIVE FOR ALL TO docintel_documents
        USING (
            current_setting('app.user_role', true) = 'platform_admin'
            OR tenant_id = current_setting('app.current_tenant', true)
        );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE POLICY tenant_isolation_checkpoints_app ON processing_checkpoints
        AS PERMISSIVE FOR ALL TO docintel_app
        USING (
            current_setting('app.user_role', true) = 'platform_admin'
            OR tenant_id = current_setting('app.current_tenant', true)
        );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'docintel_documents') THEN
        EXECUTE 'GRANT ALL ON processing_checkpoints TO docintel_documents';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'docintel_app') THEN
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON processing_checkpoints TO docintel_app';
    END IF;
END $$;
