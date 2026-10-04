# ADR-0002 — Python images install exactly what uv.lock pins

- Status: accepted
- Date: 2026-10-05

## Context

Each Python service commits a `uv.lock`, but its Dockerfile ran `uv pip install --no-sources .`,
which resolved `pyproject.toml` ranges against the newest releases at build time and ignored the
lock. The same commit therefore produced different images on different days and machines.

SQLAlchemy 2.1.0 (2026-09-24) changed the default driver for `postgresql://` URLs to psycopg v3,
which is not installed. Every image built after that date had a rag-service that answered every
`/conversations` call with 500, while an older image of the same commit worked.

## Decision

1. **The lock is the only source of versions.**
   - `scripts/docker/install-locked-deps.sh` runs `uv export --locked` and
     `uv pip install --no-deps --require-hashes`.
   - The build fails if `uv.lock` is stale relative to `pyproject.toml`, and installs nothing the
     lock does not name, verified by hash.
2. **The torch family stays a hardware decision.** torch, torchvision, torchaudio, triton and
   `nvidia-*` are excluded from the export.
   - The image pre-installs torch from `TORCH_INDEX` / `TORCH_VERSION` (CPU or a CUDA channel,
     chosen by `scripts/build.sh`).
   - `uv pip check` after installation fails the build if that torch does not satisfy the
     locked packages.
3. **The uv binary is pinned** (`ghcr.io/astral-sh/uv:0.12.22`), as is the version CI uses, so
   export semantics cannot drift either.
4. **CI job `python-locks`** runs `uv lock --check` for every Python project.

## Consequences

- **Upgrading any Python dependency is a lockfile change** (`uv lock --upgrade-package X`)
  reviewed like code. A version reaches an image only through a commit.
- **Adding a dependency** needs `uv lock` in the service directory, or the image build fails at
  the export step with a clear message.
- **The torch version is not taken from the lock.** The lock pins torch 2.12.1 (PyPI), while
  images run `TORCH_VERSION` (2.11.0 by default); `uv pip check` guards compatibility. Aligning
  them is a separate upgrade.
- **Defensive fix in rag-service.** The service also pins its driver explicitly
  (`postgresql+psycopg2`, see `services/rag-service/src/db.py`), so a future SQLAlchemy upgrade
  through the lock cannot reintroduce the failure.
