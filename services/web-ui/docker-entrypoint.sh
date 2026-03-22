#!/bin/sh
# Inject runtime environment variables into the SPA config at container startup.
# ZITADEL_CLIENT_ID and other generated values arrive via docker-compose env_file
# (config/zitadel/generated.env) — no shared volume dependency.

ENV_FILE="/usr/share/nginx/html/env-config.js"

sed -i "s|__PUBLIC_API_URL__|${PUBLIC_API_URL:-http://localhost:8080}|g" "$ENV_FILE"
sed -i "s|__PUBLIC_ZITADEL_ISSUER__|${PUBLIC_ZITADEL_ISSUER:-http://localhost:9090}|g" "$ENV_FILE"
sed -i "s|__PUBLIC_ZITADEL_CLIENT_ID__|${ZITADEL_CLIENT_ID:-}|g" "$ENV_FILE"

exec "$@"
