#!/bin/sh
# ZITADEL_SERVICE_ACCOUNT_PAT and ZITADEL_PROJECT_ID arrive via docker-compose
# env_file (config/zitadel/generated.env) — no shared volume dependency.
exec "$@"
