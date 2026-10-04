#!/bin/sh
# Install a Python service's dependencies exactly as pinned in its uv.lock.
#
#   install-locked-deps.sh <project-dir>
#
# Builds used to run `uv pip install .`, which re-resolves against the newest
# releases at build time: two machines building the same commit got different
# libraries (SQLAlchemy 2.1 broke rag-service that way). Now the lockfile is
# the single source of versions, with two deliberate exclusions:
#   - torch and its CUDA family (torchvision, torchaudio, triton, nvidia-*):
#     the image pre-installs torch from a hardware-specific index
#     (TORCH_INDEX / TORCH_VERSION); the lock's PyPI pins would pull ~5 GB of
#     CUDA wheels into CPU images.
#   - docintel-common: a local path dependency, installed from source with
#     --no-deps after this script (its dependencies are pinned here).
# --locked fails the build if uv.lock is out of date with pyproject.toml.
# Dockerfiles run `uv pip check` after installing docintel-common, so a
# pre-installed torch that does not satisfy the locked packages fails the build.
set -eu

project="$1"

torch_family=$(
    uv export --project "$project" --locked --no-dev --no-emit-project --no-hashes \
        --format requirements.txt \
    | sed -nE 's/^((torch|torchvision|torchaudio|triton|nvidia-[a-z0-9-]+))==.*/\1/p'
)

set -- --no-emit-package docintel-common
for pkg in $torch_family; do
    set -- "$@" --no-emit-package "$pkg"
done

uv export --project "$project" --locked --no-dev --no-emit-project \
    --format requirements.txt "$@" -o /tmp/locked-requirements.txt
uv pip install --system --no-deps --require-hashes -r /tmp/locked-requirements.txt
