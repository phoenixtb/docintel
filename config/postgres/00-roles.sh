#!/bin/bash
# PostgreSQL role initialization — runs before init.sql via Docker initdb mechanism.
# Shell scripts in /docker-entrypoint-initdb.d/ have access to environment variables,
# allowing role passwords to be parameterized rather than hardcoded in SQL.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
  -- Backward-compat role: used during transition until all services switch to service-specific roles.
  CREATE ROLE docintel_app WITH LOGIN PASSWORD '${DOCINTEL_APP_DB_PASSWORD}';
  GRANT CONNECT ON DATABASE docintel TO docintel_app;
  GRANT USAGE ON SCHEMA public TO docintel_app;

  -- admin-service runtime role — owns admin schema
  CREATE ROLE docintel_admin WITH LOGIN PASSWORD '${DOCINTEL_ADMIN_DB_PASSWORD}';
  GRANT CONNECT ON DATABASE docintel TO docintel_admin;

  -- document-service runtime role — owns documents schema
  CREATE ROLE docintel_documents WITH LOGIN PASSWORD '${DOCINTEL_DOCUMENTS_DB_PASSWORD}';
  GRANT CONNECT ON DATABASE docintel TO docintel_documents;

  -- rag-service runtime role — owns conversations schema
  CREATE ROLE docintel_rag WITH LOGIN PASSWORD '${DOCINTEL_RAG_DB_PASSWORD}';
  GRANT CONNECT ON DATABASE docintel TO docintel_rag;
EOSQL
