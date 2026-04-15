-- Flyway migration V3: Schema separation — admin schema owns its DDL
-- Applied after tables have been moved to their service schemas via the
-- live migration script (scripts/db-migrate-schema-separation.sql).
--
-- This migration:
--   1. Drops the dead query_log table (analytics-service uses ClickHouse exclusively)
--   2. Ensures cross-schema read grants for docintel_admin
--   3. Ensures docintel_app can still reach all schemas during the transition period

-- 1. Drop dead tables
DROP TABLE IF EXISTS query_log;
DROP TABLE IF EXISTS admin.query_log;
DROP TABLE IF EXISTS public.query_log;

-- 2. Cross-schema read grant: admin-service reads documents.documents for stats
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'documents') THEN
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'docintel_admin') THEN
            EXECUTE 'GRANT USAGE ON SCHEMA documents TO docintel_admin';
            EXECUTE 'GRANT SELECT ON documents.documents TO docintel_admin';
        END IF;
    END IF;
END $$;

-- 3. Ensure docintel_app reaches all schemas (backward compat during transition)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'documents') THEN
        EXECUTE 'GRANT USAGE ON SCHEMA documents TO docintel_app';
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA documents TO docintel_app';
        EXECUTE 'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA documents TO docintel_app';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'conversations') THEN
        EXECUTE 'GRANT USAGE ON SCHEMA conversations TO docintel_app';
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA conversations TO docintel_app';
        EXECUTE 'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA conversations TO docintel_app';
    END IF;
END $$;
