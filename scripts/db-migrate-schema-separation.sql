-- DocIntel: Schema Separation Live Migration
-- =============================================
-- Run as the superuser on an existing installation to move tables from
-- the public schema into their service-owned schemas.
--
-- Usage:
--   docker exec -i docintel-postgres-1 psql -U docintel -d docintel \
--     -f /path/to/scripts/db-migrate-schema-separation.sql
--
-- Idempotent: safe to run multiple times (all operations check existence first).

-- =============================================================================
-- 1. Create service schemas
-- =============================================================================
CREATE SCHEMA IF NOT EXISTS admin;
CREATE SCHEMA IF NOT EXISTS documents;
CREATE SCHEMA IF NOT EXISTS conversations;

-- =============================================================================
-- 2. Create service-specific roles (if not already present)
-- =============================================================================
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'docintel_admin') THEN
        CREATE ROLE docintel_admin WITH LOGIN PASSWORD 'docintel_admin_secret';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'docintel_documents') THEN
        CREATE ROLE docintel_documents WITH LOGIN PASSWORD 'docintel_documents_secret';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'docintel_rag') THEN
        CREATE ROLE docintel_rag WITH LOGIN PASSWORD 'docintel_rag_secret';
    END IF;
END $$;

GRANT CONNECT ON DATABASE docintel TO docintel_admin, docintel_documents, docintel_rag;

-- =============================================================================
-- 3. Move tables from public to their service schemas
--    Only moves if the table is still in public; skips if already moved.
-- =============================================================================
DO $$
BEGIN
    -- admin schema
    IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'public' AND tablename = 'tenants') THEN
        ALTER TABLE public.tenants SET SCHEMA admin;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'public' AND tablename = 'platform_settings') THEN
        ALTER TABLE public.platform_settings SET SCHEMA admin;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'public' AND tablename = 'users') THEN
        ALTER TABLE public.users SET SCHEMA admin;
    END IF;

    -- documents schema
    -- data_sources must move before documents (FK dependency)
    IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'public' AND tablename = 'data_sources') THEN
        ALTER TABLE public.data_sources SET SCHEMA documents;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'public' AND tablename = 'documents') THEN
        ALTER TABLE public.documents SET SCHEMA documents;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'public' AND tablename = 'chunks') THEN
        ALTER TABLE public.chunks SET SCHEMA documents;
    END IF;

    -- conversations schema
    IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'public' AND tablename = 'conversations') THEN
        ALTER TABLE public.conversations SET SCHEMA conversations;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'public' AND tablename = 'messages') THEN
        ALTER TABLE public.messages SET SCHEMA conversations;
    END IF;
END $$;

-- =============================================================================
-- 4. Move Flyway schema history to admin schema
--    admin-service owns its DDL; this keeps history co-located with its schema.
-- =============================================================================
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'public' AND tablename = 'flyway_schema_history') THEN
        ALTER TABLE public.flyway_schema_history SET SCHEMA admin;
    END IF;
END $$;

-- =============================================================================
-- 5. Drop dead tables
-- =============================================================================
DROP TABLE IF EXISTS public.query_log;
DROP TABLE IF EXISTS admin.query_log;

-- =============================================================================
-- 6. Schema ownership grants for new service roles
-- =============================================================================
GRANT ALL ON SCHEMA admin         TO docintel_admin;
GRANT ALL ON SCHEMA documents     TO docintel_documents;
GRANT ALL ON SCHEMA conversations TO docintel_rag;

GRANT ALL ON ALL TABLES    IN SCHEMA admin         TO docintel_admin;
GRANT ALL ON ALL SEQUENCES IN SCHEMA admin         TO docintel_admin;
GRANT ALL ON ALL TABLES    IN SCHEMA documents     TO docintel_documents;
GRANT ALL ON ALL SEQUENCES IN SCHEMA documents     TO docintel_documents;
GRANT ALL ON ALL TABLES    IN SCHEMA conversations TO docintel_rag;
GRANT ALL ON ALL SEQUENCES IN SCHEMA conversations TO docintel_rag;

ALTER DEFAULT PRIVILEGES IN SCHEMA admin         GRANT ALL ON TABLES    TO docintel_admin;
ALTER DEFAULT PRIVILEGES IN SCHEMA admin         GRANT ALL ON SEQUENCES TO docintel_admin;
ALTER DEFAULT PRIVILEGES IN SCHEMA documents     GRANT ALL ON TABLES    TO docintel_documents;
ALTER DEFAULT PRIVILEGES IN SCHEMA documents     GRANT ALL ON SEQUENCES TO docintel_documents;
ALTER DEFAULT PRIVILEGES IN SCHEMA conversations GRANT ALL ON TABLES    TO docintel_rag;
ALTER DEFAULT PRIVILEGES IN SCHEMA conversations GRANT ALL ON SEQUENCES TO docintel_rag;

-- =============================================================================
-- 7. Cross-schema read grants
-- =============================================================================
-- admin reads documents for stats
GRANT USAGE  ON SCHEMA documents TO docintel_admin;
GRANT SELECT ON documents.documents TO docintel_admin;

-- rag reads admin for model resolver
GRANT USAGE  ON SCHEMA admin TO docintel_rag;
GRANT SELECT ON admin.tenants TO docintel_rag;
GRANT SELECT ON admin.platform_settings TO docintel_rag;

-- documents reads admin.tenants (FK validation at application level)
GRANT USAGE  ON SCHEMA admin TO docintel_documents;
GRANT SELECT ON admin.tenants TO docintel_documents;

-- =============================================================================
-- 8. docintel_app broad access (backward compat during transition)
-- =============================================================================
GRANT USAGE ON SCHEMA admin, documents, conversations TO docintel_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES    IN SCHEMA admin         TO docintel_app;
GRANT USAGE, SELECT                  ON ALL SEQUENCES IN SCHEMA admin         TO docintel_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES    IN SCHEMA documents     TO docintel_app;
GRANT USAGE, SELECT                  ON ALL SEQUENCES IN SCHEMA documents     TO docintel_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES    IN SCHEMA conversations TO docintel_app;
GRANT USAGE, SELECT                  ON ALL SEQUENCES IN SCHEMA conversations TO docintel_app;

ALTER DEFAULT PRIVILEGES IN SCHEMA admin         GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES    TO docintel_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA admin         GRANT USAGE, SELECT ON SEQUENCES TO docintel_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA documents     GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES    TO docintel_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA documents     GRANT USAGE, SELECT ON SEQUENCES TO docintel_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA conversations GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES    TO docintel_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA conversations GRANT USAGE, SELECT ON SEQUENCES TO docintel_app;

-- docintel_ingestion: keep access on documents schema until Phase 2 is deployed
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'docintel_ingestion') THEN
        EXECUTE 'GRANT USAGE ON SCHEMA documents TO docintel_ingestion';
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA documents TO docintel_ingestion';
        EXECUTE 'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA documents TO docintel_ingestion';
    END IF;
END $$;

-- =============================================================================
-- 9. Default search paths — unqualified names resolve in order per role
-- =============================================================================
ALTER ROLE docintel_admin     SET search_path = admin, documents, public;
ALTER ROLE docintel_documents SET search_path = documents, admin, public;
ALTER ROLE docintel_rag       SET search_path = conversations, admin, public;
ALTER ROLE docintel_app       SET search_path = admin, documents, conversations, public;

-- =============================================================================
-- 10. RLS policies for new service roles (idempotent)
-- =============================================================================
DO $$ BEGIN
    CREATE POLICY tenant_isolation_tenants_admin ON admin.tenants
        AS PERMISSIVE FOR ALL TO docintel_admin
        USING (
            current_setting('app.user_role', true) = 'platform_admin'
            OR id = current_setting('app.current_tenant', true)
        );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE POLICY tenant_isolation_data_sources_doc ON documents.data_sources
        AS PERMISSIVE FOR ALL TO docintel_documents
        USING (
            current_setting('app.user_role', true) = 'platform_admin'
            OR tenant_id = current_setting('app.current_tenant', true)
        );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE POLICY tenant_isolation_documents_doc ON documents.documents
        AS PERMISSIVE FOR ALL TO docintel_documents
        USING (
            current_setting('app.user_role', true) = 'platform_admin'
            OR tenant_id = current_setting('app.current_tenant', true)
        );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE POLICY tenant_isolation_chunks_doc ON documents.chunks
        AS PERMISSIVE FOR ALL TO docintel_documents
        USING (
            current_setting('app.user_role', true) = 'platform_admin'
            OR tenant_id = current_setting('app.current_tenant', true)
        );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE POLICY tenant_isolation_conversations_rag ON conversations.conversations
        AS PERMISSIVE FOR ALL TO docintel_rag
        USING (
            current_setting('app.user_role', true) = 'platform_admin'
            OR tenant_id = current_setting('app.current_tenant', true)
        );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE POLICY tenant_isolation_messages_rag ON conversations.messages
        AS PERMISSIVE FOR ALL TO docintel_rag
        USING (
            current_setting('app.user_role', true) = 'platform_admin'
            OR EXISTS (
                SELECT 1 FROM conversations.conversations c
                WHERE c.id = messages.conversation_id
                  AND c.tenant_id = current_setting('app.current_tenant', true)
            )
        );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Grant trigger function to service roles
GRANT USAGE  ON SCHEMA public TO docintel_admin, docintel_documents, docintel_rag;
GRANT EXECUTE ON FUNCTION public.update_updated_at() TO docintel_admin, docintel_documents, docintel_rag;

-- =============================================================================
-- 11. Drop docintel_ingestion role (no longer needs DB access)
--     ingestion-service now calls document-service HTTP API for chunk persistence.
-- =============================================================================
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'docintel_ingestion') THEN
        REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA documents FROM docintel_ingestion;
        REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public   FROM docintel_ingestion;
        REVOKE USAGE ON SCHEMA documents FROM docintel_ingestion;
        REVOKE USAGE ON SCHEMA public    FROM docintel_ingestion;
        REVOKE CONNECT ON DATABASE docintel FROM docintel_ingestion;
        DROP ROLE docintel_ingestion;
        RAISE NOTICE 'Dropped docintel_ingestion role';
    END IF;
END $$;

\echo 'Schema separation migration complete.'
