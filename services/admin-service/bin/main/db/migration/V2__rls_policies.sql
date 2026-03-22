-- Flyway migration V2: Row Level Security policies with platform_admin bypass
-- ALTER TABLE ... ENABLE/FORCE ROW LEVEL SECURITY is idempotent — safe to re-run.
-- CREATE POLICY has no IF NOT EXISTS, so each statement is wrapped in a DO block
-- that swallows duplicate_object errors (policy already created by init.sql).

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
ALTER TABLE tenants       ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants       FORCE ROW LEVEL SECURITY;

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

DO $$ BEGIN
    CREATE POLICY tenant_isolation_conversations ON conversations
        AS PERMISSIVE FOR ALL TO docintel_app
        USING (
            current_setting('app.user_role', true) = 'platform_admin'
            OR tenant_id = current_setting('app.current_tenant', true)
        );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
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
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE POLICY tenant_isolation_query_log ON query_log
        AS PERMISSIVE FOR ALL TO docintel_app
        USING (
            current_setting('app.user_role', true) = 'platform_admin'
            OR tenant_id = current_setting('app.current_tenant', true)
        );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE POLICY tenant_isolation_tenants ON tenants
        AS PERMISSIVE FOR ALL TO docintel_app
        USING (
            current_setting('app.user_role', true) = 'platform_admin'
            OR id = current_setting('app.current_tenant', true)
        );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
