-- Flyway V4: add missing _doc RLS policy for deletion_tasks
--
-- V3 created `tenant_isolation_deletion_tasks` only for `docintel_app` (legacy user).
-- Other tables in this schema (documents, chunks, data_sources) also have a `_doc`
-- variant scoped TO `docintel_documents` (the per-service user actually used by
-- document-service connections). Without it, RLS hides every row from
-- `docintel_documents` — visible symptoms: DeletionTaskWorker never finds work,
-- and any user-initiated delete that inserts into deletion_tasks would also fail
-- the RLS WITH CHECK clause.
--
-- Idempotent: skipped if the policy already exists.

DO $$ BEGIN
    CREATE POLICY tenant_isolation_deletion_tasks_doc ON deletion_tasks
        AS PERMISSIVE FOR ALL TO docintel_documents
        USING (
            current_setting('app.user_role', true) = 'platform_admin'
            OR tenant_id = current_setting('app.current_tenant', true)
        );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
