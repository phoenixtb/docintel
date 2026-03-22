#!/bin/sh
# ZITADEL_ACTIONS_SIGNING_KEY arrives via docker-compose env_file
# (config/zitadel/generated.env) — no shared volume dependency.
set -e
exec "$@"
