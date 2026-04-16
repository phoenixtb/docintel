-- Flyway migration V4: Per-user preferences table
-- Stores user-specific settings (e.g. thinking_mode) scoped by (user_id, tenant_id).
-- RLS enforces that each service can only read/write the calling user's own row.

CREATE TABLE IF NOT EXISTS admin.user_preferences (
    user_id    TEXT         NOT NULL,
    tenant_id  TEXT         NOT NULL REFERENCES admin.tenants(id) ON DELETE CASCADE,
    key        TEXT         NOT NULL,
    value      JSONB        NOT NULL DEFAULT 'null',
    updated_at TIMESTAMPTZ  DEFAULT NOW(),
    PRIMARY KEY (user_id, tenant_id, key)
);

CREATE INDEX IF NOT EXISTS idx_user_pref_tenant ON admin.user_preferences(tenant_id);

ALTER TABLE admin.user_preferences ENABLE ROW LEVEL SECURITY;
ALTER TABLE admin.user_preferences FORCE ROW LEVEL SECURITY;

-- docintel_admin (admin-service runtime role): own user + own tenant
DO $$ BEGIN
    CREATE POLICY user_pref_admin ON admin.user_preferences
        AS PERMISSIVE FOR ALL TO docintel_admin
        USING (
            user_id   = current_setting('app.current_user_id', true)
            AND tenant_id = current_setting('app.current_tenant',  true)
        )
        WITH CHECK (
            user_id   = current_setting('app.current_user_id', true)
            AND tenant_id = current_setting('app.current_tenant',  true)
        );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- docintel_app (legacy role): same scope
DO $$ BEGIN
    CREATE POLICY user_pref_app ON admin.user_preferences
        AS PERMISSIVE FOR ALL TO docintel_app
        USING (
            user_id   = current_setting('app.current_user_id', true)
            AND tenant_id = current_setting('app.current_tenant',  true)
        )
        WITH CHECK (
            user_id   = current_setting('app.current_user_id', true)
            AND tenant_id = current_setting('app.current_tenant',  true)
        );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- docintel_rag (RAG resolver): SELECT only, same scope
DO $$ BEGIN
    CREATE POLICY user_pref_rag ON admin.user_preferences
        AS PERMISSIVE FOR SELECT TO docintel_rag
        USING (
            user_id   = current_setting('app.current_user_id', true)
            AND tenant_id = current_setting('app.current_tenant',  true)
        );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

GRANT SELECT, INSERT, UPDATE, DELETE ON admin.user_preferences TO docintel_admin;
GRANT SELECT, INSERT, UPDATE, DELETE ON admin.user_preferences TO docintel_app;
GRANT SELECT ON admin.user_preferences TO docintel_rag;
