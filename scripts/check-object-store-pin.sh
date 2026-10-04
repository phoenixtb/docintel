#!/usr/bin/env bash
# Fails when any file pins a VersityGW image tag different from the
# object-store service in docker-compose.yml. Tests start their own container
# (they must not read compose), so the tag is duplicated on purpose; this
# keeps the copies honest. Run from anywhere; used by CI.
set -euo pipefail

cd "$(dirname "$0")/.."

expected=$(grep -Eo 'versity/versitygw:[A-Za-z0-9._-]+' docker-compose.yml | sort -u)
if [ "$(printf '%s\n' "$expected" | wc -l | tr -d ' ')" != "1" ] || [ -z "$expected" ]; then
    echo "docker-compose.yml must pin exactly one versity/versitygw tag; found: ${expected:-none}" >&2
    exit 1
fi

mismatches=$(git grep -nEo 'versity/versitygw:[A-Za-z0-9._-]+' -- . ':!*.lock' \
    | grep -v ":${expected}\$" || true)
if [ -n "$mismatches" ]; then
    echo "Object-store image pins differ from docker-compose.yml (${expected}):" >&2
    printf '%s\n' "$mismatches" >&2
    exit 1
fi
echo "object-store image pin consistent: ${expected}"
