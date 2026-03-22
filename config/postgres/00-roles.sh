#!/bin/bash
# PostgreSQL role initialization — runs before init.sql via Docker initdb mechanism.
# Shell scripts in /docker-entrypoint-initdb.d/ have access to environment variables,
# allowing role passwords to be parameterized rather than hardcoded in SQL.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
  -- Application role: non-superuser, RLS enforced, used by document/rag/admin services
  CREATE ROLE docintel_app WITH LOGIN PASSWORD '${DOCINTEL_APP_DB_PASSWORD}';
  GRANT CONNECT ON DATABASE docintel TO docintel_app;
  GRANT USAGE ON SCHEMA public TO docintel_app;

  -- Ingestion role: BYPASSRLS so the ingestion pipeline can write across all tenants
  -- without session-variable tricks. This is the PostgreSQL-native mechanism for
  -- platform backend services that operate outside any single tenant's scope.
  CREATE ROLE docintel_ingestion WITH LOGIN PASSWORD '${DOCINTEL_INGESTION_DB_PASSWORD}' BYPASSRLS;
  GRANT CONNECT ON DATABASE docintel TO docintel_ingestion;
  GRANT USAGE ON SCHEMA public TO docintel_ingestion;
EOSQL
