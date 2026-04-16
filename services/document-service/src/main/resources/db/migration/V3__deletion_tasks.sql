-- Flyway V3: deletion_tasks outbox table
-- Tracks async cleanup of Qdrant vectors + MinIO files after a document is marked for deletion.
-- Workers poll this table; each row is retired (DONE) once Qdrant + MinIO are clean.

CREATE TABLE IF NOT EXISTS deletion_tasks (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       VARCHAR(64) NOT NULL,
    document_id     UUID        NOT NULL,
    file_path       VARCHAR(500) NOT NULL,
    qdrant_done     BOOLEAN     NOT NULL DEFAULT FALSE,
    minio_done      BOOLEAN     NOT NULL DEFAULT FALSE,
    attempts        INT         NOT NULL DEFAULT 0,
    last_attempt_at TIMESTAMPTZ,
    task_status     VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_deletion_tasks_pending
    ON deletion_tasks (task_status, last_attempt_at)
    WHERE task_status = 'PENDING';

CREATE INDEX IF NOT EXISTS idx_deletion_tasks_document
    ON deletion_tasks (document_id);

DO $$ BEGIN
    ALTER TABLE deletion_tasks ENABLE ROW LEVEL SECURITY;
    ALTER TABLE deletion_tasks FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN NULL;
END $$;

DO $$ BEGIN
    CREATE POLICY tenant_isolation_deletion_tasks ON deletion_tasks
        AS PERMISSIVE FOR ALL TO docintel_app
        USING (
            current_setting('app.user_role', true) = 'platform_admin'
            OR tenant_id = current_setting('app.current_tenant', true)
        );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'docintel_app') THEN
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON deletion_tasks TO docintel_app';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'docintel_documents') THEN
        EXECUTE 'GRANT ALL ON deletion_tasks TO docintel_documents';
    END IF;
END $$;
