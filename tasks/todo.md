# Plan: DocIntel-side changes for LMForge v0.2.x (awaiting go)

Context: LMForge v0.2.x (new README + tarballs being tested on the Linux box).
Client audit result: openai-SDK paths (embed/chat) already retry 503 with
Retry-After; VLM httpx client handles 503/400/413 correctly; rerank 501
degrades gracefully. No changes needed for concurrency or image errors.

## P1 tasks (user approved; engine-agnostic revision)

Revisions after user review: SGLang is NEVER auto-selected (engines.toml L67,
tier=experimental) — engine pinning dropped as moot. /metrics scrape dropped
(LMForge-native surface; keep DocIntel engine-agnostic). Warm-up switched from
/lf/model/switch (LMForge-native) to standard OpenAI-endpoint dummy requests.

- [x] 1. start.sh LMForge branch — engine-agnostic warm-up after health
      check: tiny real requests to /v1/embeddings (LLM_EMBED_MODEL),
      /v1/chat/completions max_tokens=1 (LLM_MODEL), /v1/rerank one-doc
      (LLM_RERANK_MODEL). Non-fatal (warn on failure). Skip VLM (ingestion
      background path, load-on-demand acceptable). Also extend the
      /v1/models presence check (chat-only today) to embed + rerank.
- [x] 2. setup-lmforge.sh — upsert `keep_alive = "30m"` into
      ~/.lmforge/config.toml when unset (same idempotent pattern as
      bind_address); avoids 5m idle-unload vs 30s query-embed timeout race.
      No service-code changes (VLM already sends per-request keep_alive).

Review fix (post-subagent): keep_alive belongs under [orchestrator] in
LMForge's config schema (src/config/mod.rs OrchestratorConfig) — bare
top-level append would be silently ignored. Rewrote upsert to be
section-aware (insert after existing [orchestrator], else append the
section). Also changed the bind_address append to a PREPEND so a top-level
key can never land inside a section appended at EOF. Verified all shapes
parse via python tomllib; bash -n both scripts OK.

Dropped: llamacpp pinning via engines.toml (SGLang never auto-selected);
Prometheus lmforge scrape (engine coupling). Deferred as before: LiteLLM
expansion 503 handling; min-version check.

Upstream (LMForge repo, user's list): README stale text says SGLang is
default/auto-installed on Linux NVIDIA ≥8GB (README L61, L85-86, L286-295)
— contradicts engines.toml "NEVER auto-selected".

---

# Fix: start.sh phase-failure handling (pre-merge, before Ubuntu retest)

Prior batch (fresh-Ubuntu install-path fixes) done + reviewed — see git diff and
tasks/lessons.md. This plan covers the remaining blocker: phase/terraform
failure handling in scripts/start.sh.

Ground truth (audited): set -e only, no ERR trap, no phase context on death.
Infra stack (Phase 2) has zero stale-state recovery; identity (Phase 4) has a
narrow admin.pat-mtime heuristic only. Phase 5 feature-flag PUT is `curl -s`
without -f (false ok on HTTP error). Phase 6 writes generated.env without
validating tofu outputs (empty/null values written silently). cleanup.sh
--data already resets both tfstates (audit claim outdated).

Design: crash-only convergence — recovery is always "re-run ./scripts/start.sh".
No checkpoint/resume machinery. Every failure must print: phase, cause, log
path, exact next command.

## Tasks

- [x] 1. ERR trap + phase context (start.sh): `CURRENT_PHASE` set by a
      `phase N "title"` helper at each phase boundary; `trap on_error ERR`
      prints a banner — failed phase, exit code, `$BASH_COMMAND`, log path if
      any, per-phase remediation hint (map phase → hint text), and note that
      containers are left running for inspection (`docker compose ps/logs`).
      Add `set -o pipefail` (audit pipelines first; keep `set -e`; skip -u).
- [x] 2. Shared `run_tofu` helper for both stacks: init+apply with output
      tee'd to `logs/bootstrap/tofu-<stack>-<timestamp>.log` (mkdir -p;
      gitignore `logs/`); on failure print `tail -30` of log + full path.
      Auto-handle stale state lock: if apply fails matching "state lock" /
      "lock.info", force-unlock (or rm .terraform.tfstate.lock.info for local
      backend) once with a warning, retry apply once. No blanket retries.
- [x] 3. Infra stack stale-state recovery (parity with identity):
      volume-fingerprint heuristic — after successful infra apply, record
      `docker volume inspect -f '{{.CreatedAt}}'` of the minio + qdrant
      volumes to `terraform/stacks/infra/.volume-fingerprint` (gitignored);
      before apply, if tfstate exists and fingerprint mismatches (volumes
      recreated) → rm infra tfstate (+backup) + fingerprint, log why, apply
      creates fresh. Covers wiped-volumes-with-kept-state incl. the Qdrant
      terraform_data blind spot.
- [x] 4. Infra apply conflict auto-heal: on apply failure matching MinIO
      "already own it"/"already exists" → auto `tofu import` the 3 known
      bucket addresses (documents-raw, documents-processed, models), retry
      apply once; if still failing → actionable fail (exact import/cleanup
      commands printed). Covers lost-state-with-live-buckets.
- [x] 5. Qdrant drift pre-check (belt-and-braces for partial wipes): before
      infra apply, if tfstate lists the qdrant terraform_data resources but
      `GET /collections/{documents,response_cache}` returns 404 →
      `tofu state rm` those addresses so apply re-runs the local-exec PUTs.
- [x] 6. Identity conflict guidance (no auto-destroy — orgs/users are not
      safe to auto-heal): on identity apply failure matching already-exists /
      conflict errors, print actionable block: cause (Zitadel has resources
      not in state), options (./scripts/cleanup.sh --data for fresh local dev
      — destructive, wipes data; or manual tofu import), log path. Keep the
      existing admin.pat-mtime stale-state clear as-is.
- [x] 7. Phase 5 fix: feature-flag PUT → `curl -sf` + explicit failure check
      with actionable fail (check zitadel-api logs / PAT validity). Validate
      actions-signing-key file non-empty before use (currently
      `cat ... || echo ""` silently proceeds).
- [x] 8. Phase 6 validation + atomic write: assert CLIENT_ID, PROJECT_ID,
      SA_PAT are non-empty and != "null" before writing; write generated.env
      to a temp file then `mv` (never leave a partial file); on validation
      failure → actionable fail pointing at Phase 4 outputs + log. Also
      restart-check: after Phase 6 restart, verify docintel-actions left
      key-pending mode (its /health or /claims endpoint) — warn if not.
- [x] 9. .gitignore: `logs/`, `terraform/stacks/infra/.volume-fingerprint`.

Out of scope: remote state backends, phase checkpoint files, auto-destroy of
identity resources, changing qdrant.tf provider (terraform_data stays; drift
handled in start.sh), retry loops beyond the single lock/import retry.

Verification plan: bash -n + shellcheck; dry simulations on macOS where
possible (fingerprint mismatch → state rm path; Phase 6 validation with
mocked empty outputs; ERR trap banner via forced failure in a sandboxed copy);
full end-to-end validation deferred to the Ubuntu machine run.

## Review

Implemented crash-only bootstrap failure handling without running start.sh /
tofu apply / docker compose on this machine.

- Helpers live in `scripts/lib/start_helpers.sh` (sourced by `scripts/start.sh`).
- `set -eE -o pipefail` + `phase` + ERR banner; `fail()` stays targeted (phase
  prefix only, no banner). Identity admin.pat-mtime heuristic unchanged.
- `run_tofu` tees init+apply to `logs/bootstrap/`, lock-rm retry once, MinIO
  import heal, identity conflict guidance (no auto-heal).
- Infra fingerprint + Qdrant drift pre-check before apply; fingerprint written
  after successful apply. Compose project: `COMPOSE_PROJECT_NAME` or
  `basename $PROJECT_DIR` (volumes `<project>_minio-data` / `_qdrant-data`).
- Phase 5: feature-flag `curl -sf`; empty signing key fails. Phase 6: validate
  outputs, atomic `generated.env` write, /claims probe via compose exec
  (actions has no host port) — warn on lingering 503.
- Verified: `bash -n` both files (shellcheck not installed); 45 isolated
  helper tests (success/fail/lock/MinIO import, Phase 6 empty+null, ERR
  banner); fingerprint match/mismatch/first-run with mocked docker. /tmp
  sandboxes cleaned. E2E deferred to Ubuntu.

Post-implementation review — 2 bugs found and fixed (supersedes two lines
above):
1. pipefail regression: profile_config.sh/detect_hardware.sh use
   VAR=$(grep … | cut …) where grep misses are expected (e.g. no
   DETECT_DRIVER on non-NVIDIA); a global `set -o pipefail` made those fatal
   during pre-flight. Now: `set -eE` at top, pipefail enabled only after
   ensure_docker_context (all lib call sites are pre-flight; verified).
   Regression demonstrated in a sandbox before fixing.
2. Feature-flag idempotency regression: plain `curl -sf` fails on Zitadel's
   documented 400 "No changes" re-run response, breaking crash-only
   convergence. Now: capture http_code, accept 2xx|400, fail otherwise with
   the code in the message.
Also verified: no log/ok/fail redefinition by sourced libs; volume names
minio-data/qdrant-data correct in compose; docintel-actions image ships wget
(its own healthcheck uses it) so the compose-exec /claims probe works;
`cd "$PROJECT_DIR"` precedes all relative paths; ERR-trap banner re-tested
in a sandbox after the set-flag change (phase, command, hint, exit 1 all
correct).

Remaining validation: full end-to-end on the Ubuntu machine (fresh install +
interrupt/re-run drills) — test plan to be discussed.
