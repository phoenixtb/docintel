#!/bin/sh
# Inject runtime environment variables into the SPA config at container startup.
# Placeholders in env-config.js are replaced with actual container env var values.
# Falls back to defaults if the variable is not set.

ENV_FILE="/usr/share/nginx/html/env-config.js"

sed -i "s|__PUBLIC_API_URL__|${PUBLIC_API_URL:-http://localhost:8080}|g" "$ENV_FILE"
sed -i "s|__PUBLIC_ZITADEL_ISSUER__|${PUBLIC_ZITADEL_ISSUER:-http://localhost:9090}|g" "$ENV_FILE"
sed -i "s|__PUBLIC_ZITADEL_CLIENT_ID__|${PUBLIC_ZITADEL_CLIENT_ID:-}|g" "$ENV_FILE"

exec "$@"
