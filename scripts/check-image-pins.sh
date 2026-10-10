#!/usr/bin/env bash
# Fails when any file pins an image tag different from the one docker-compose.yml
# runs. Tests start their own containers (they must not read compose), so the
# tags are duplicated on purpose; this keeps the copies honest. Run from
# anywhere; used by CI.
set -euo pipefail

cd "$(dirname "$0")/.."

# Images that tests start themselves. docs/part2*.md are historic specs, not pins.
images=("versity/versitygw" "qdrant/qdrant")

status=0
for image in "${images[@]}"; do
    pattern="${image//./\\.}:[A-Za-z0-9._-]+"
    expected=$(grep -Eo "$pattern" docker-compose.yml | sort -u)
    if [ "$(printf '%s\n' "$expected" | wc -l | tr -d ' ')" != "1" ] || [ -z "$expected" ]; then
        echo "docker-compose.yml must pin exactly one ${image} tag; found: ${expected:-none}" >&2
        status=1
        continue
    fi

    mismatches=$(git grep -nEo "$pattern" -- . ':!*.lock' ':!docs/part2*.md' \
        | grep -v ":${expected}\$" || true)
    if [ -n "$mismatches" ]; then
        echo "${image} pins differ from docker-compose.yml (${expected}):" >&2
        printf '%s\n' "$mismatches" >&2
        status=1
        continue
    fi
    echo "image pin consistent: ${expected}"
done
exit "$status"
