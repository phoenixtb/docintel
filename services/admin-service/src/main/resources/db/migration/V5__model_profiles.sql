-- Flyway V5: Model profiles table
-- Per-model sampling parameter overrides (temperature, top_p, max_tokens, etc.).
-- scope='platform' rows apply to all tenants; scope='tenant' rows override for one tenant.
-- NULL param value = inherit from the next level in the resolution chain:
--   tenant DB profile → platform DB profile → built-in code defaults → env config fallback.

CREATE TABLE IF NOT EXISTS admin.model_profiles (
    id                    UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    scope                 VARCHAR(20)  NOT NULL CHECK (scope IN ('platform', 'tenant')),
    tenant_id             VARCHAR(64)  REFERENCES admin.tenants(id) ON DELETE CASCADE,
    model_pattern         VARCHAR(255) NOT NULL,
    display_name          VARCHAR(255),
    -- Standard (non-thinking) params — NULL = inherit from next level in chain
    temperature           DOUBLE PRECISION,
    top_p                 DOUBLE PRECISION,
    max_tokens            INTEGER,
    frequency_penalty     DOUBLE PRECISION,
    -- Thinking-mode params — NULL = inherit
    thinking_temperature  DOUBLE PRECISION,
    thinking_top_p        DOUBLE PRECISION,
    thinking_max_tokens   INTEGER,
    notes                 TEXT,
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT model_profiles_tenant_required
        CHECK (scope = 'platform' OR tenant_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_model_profiles_scope   ON admin.model_profiles(scope);
CREATE INDEX IF NOT EXISTS idx_model_profiles_tenant  ON admin.model_profiles(tenant_id);
CREATE INDEX IF NOT EXISTS idx_model_profiles_pattern ON admin.model_profiles(model_pattern);

GRANT SELECT, INSERT, UPDATE, DELETE ON admin.model_profiles TO docintel_admin;
GRANT SELECT ON admin.model_profiles TO docintel_rag;
