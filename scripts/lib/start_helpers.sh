#!/bin/bash
# scripts/lib/start_helpers.sh
# ==============================================================================
# Bootstrap helpers for start.sh: phase/ERR context, tofu apply with logs,
# infra volume-fingerprint stale-state recovery, MinIO import heal, Qdrant
# drift pre-check, identity conflict guidance, Phase 6 output validation.
#
# Source from start.sh (or a unit-test harness). Requires PROJECT_DIR.
# All functions are bash 3.2 compatible (no mapfile, no assoc arrays, no ${var,,}).
# ==============================================================================

[ -n "$_START_HELPERS_LOADED" ] && return 0
_START_HELPERS_LOADED=1

: "${TOFU_BIN:=tofu}"
: "${DOCKER_BIN:=docker}"
: "${CURRENT_PHASE:=pre-flight}"
: "${LAST_TOFU_LOG:=}"

# Compose volume names are <project>_<vol>. Project defaults to the directory
# name (typically "docintel"). Honour COMPOSE_PROJECT_NAME when set.
INFRA_VAR_FILE="../../environments/dev.infra.tfvars"
IDENTITY_VAR_FILE="../../environments/dev.identity.tfvars"

# ── log helpers (match start.sh style) ────────────────────────────────────────

log()  { echo "  $*"; }
ok()   { echo "  ✓ $*"; }
warn() { echo "  ⚠  $*"; }
fail() {
    if [ -n "${CURRENT_PHASE:-}" ]; then
        echo "  ✗ [$CURRENT_PHASE] $*" >&2
    else
        echo "  ✗ $*" >&2
    fi
    exit 1
}

# ── phase + ERR trap ──────────────────────────────────────────────────────────

phase() {
    CURRENT_PHASE="$1"
    echo ""
    echo "$2"
}

on_error() {
    local ec=$?
    local cmd="$BASH_COMMAND"
    {
        echo ""
        echo "════════════════════════════════════════════════════════════════"
        echo "  ✗ Bootstrap failed"
        echo "  Phase:      ${CURRENT_PHASE:-pre-flight}"
        echo "  Exit code:  $ec"
        echo "  Command:    $cmd"
        if [ -n "${LAST_TOFU_LOG:-}" ]; then
            echo "  Tofu log:   $LAST_TOFU_LOG"
        fi
        echo ""
        echo "  Next step:"
        case "$CURRENT_PHASE" in
            1/7)
                echo "    Check docker compose logs <failing service>; free disk/RAM."
                ;;
            2/7)
                echo "    Re-run ./scripts/start.sh (infra apply converges); see tofu log."
                ;;
            3/7)
                echo "    docker compose logs zitadel-api"
                echo "    Zitadel masterkey must be exactly 32 characters."
                ;;
            4/7)
                echo "    See tofu log; if conflicts, see printed guidance above."
                ;;
            5/7)
                echo "    docker compose logs zitadel-api"
                echo "    Verify the admin PAT at config/zitadel/bootstrap/admin.pat."
                ;;
            6/7)
                echo "    tofu -chdir=terraform/stacks/identity output"
                ;;
            7/7)
                echo "    docker compose ps"
                echo "    docker compose logs <service>"
                ;;
            *)
                echo "    Check the command above and prerequisite tools (docker, tofu, jq)."
                ;;
        esac
        echo ""
        echo "  Containers are left running for inspection:"
        echo "    docker compose ps"
        echo "    docker compose logs <service>"
        echo ""
        echo "  Re-run ./scripts/start.sh — the bootstrap is idempotent and converges."
        echo "════════════════════════════════════════════════════════════════"
    } >&2
}

# ── tofu apply plumbing ───────────────────────────────────────────────────────

_tofu_stack_dir() {
    echo "$PROJECT_DIR/terraform/stacks/$1"
}

_print_tofu_tail() {
    echo ""
    echo "  ── tofu log (last 30 lines) ────────────────────────────────────────"
    if [ -n "${LAST_TOFU_LOG:-}" ] && [ -f "$LAST_TOFU_LOG" ]; then
        tail -30 "$LAST_TOFU_LOG"
    else
        echo "  (no tofu log recorded)"
    fi
    echo "  ────────────────────────────────────────────────────────────────────"
    echo "  Full log: ${LAST_TOFU_LOG:-<none>}"
}

# Run tofu apply with live tee. Returns apply's exit code (pipefail + tee).
_tofu_apply() {
    local stack_dir="$1"
    shift
    "$TOFU_BIN" -chdir="$stack_dir" apply -auto-approve -input=false "$@" 2>&1 | tee -a "$LAST_TOFU_LOG"
}

_log_matches() {
    # $1 = ERE (case-insensitive). Returns 0 if LAST_TOFU_LOG matches.
    [ -n "${LAST_TOFU_LOG:-}" ] && [ -f "$LAST_TOFU_LOG" ] || return 1
    grep -qiE "$1" "$LAST_TOFU_LOG"
}

_heal_stale_lock() {
    local stack="$1"
    local stack_dir
    stack_dir=$(_tofu_stack_dir "$stack")
    local lock_file="$stack_dir/.terraform.tfstate.lock.info"
    if [ ! -f "$lock_file" ]; then
        return 1
    fi
    warn "Stale Terraform state lock detected — removing $lock_file and retrying once"
    rm -f "$lock_file"
    return 0
}

_import_minio_buckets() {
    local stack_dir
    stack_dir=$(_tofu_stack_dir "infra")
    warn "MinIO buckets exist but are missing from state — importing"
    # || true per-import: an actually-missing bucket must not abort the heal.
    "$TOFU_BIN" -chdir="$stack_dir" import -input=false -var-file="$INFRA_VAR_FILE" \
        minio_s3_bucket.documents_raw documents-raw 2>&1 | tee -a "$LAST_TOFU_LOG" || true
    "$TOFU_BIN" -chdir="$stack_dir" import -input=false -var-file="$INFRA_VAR_FILE" \
        minio_s3_bucket.documents_processed documents-processed 2>&1 | tee -a "$LAST_TOFU_LOG" || true
    "$TOFU_BIN" -chdir="$stack_dir" import -input=false -var-file="$INFRA_VAR_FILE" \
        minio_s3_bucket.models models 2>&1 | tee -a "$LAST_TOFU_LOG" || true
}

_fail_minio_heal() {
    echo "" >&2
    echo "  ✗ MinIO buckets exist but remain missing from state after import retry." >&2
    echo "    Manual imports:" >&2
    echo "      tofu -chdir=terraform/stacks/infra import -input=false -var-file=\"../../environments/dev.infra.tfvars\" minio_s3_bucket.documents_raw documents-raw" >&2
    echo "      tofu -chdir=terraform/stacks/infra import -input=false -var-file=\"../../environments/dev.infra.tfvars\" minio_s3_bucket.documents_processed documents-processed" >&2
    echo "      tofu -chdir=terraform/stacks/infra import -input=false -var-file=\"../../environments/dev.infra.tfvars\" minio_s3_bucket.models models" >&2
    echo "    Destructive reset: ./scripts/cleanup.sh --data   # wipes all data volumes" >&2
    echo "    Log: ${LAST_TOFU_LOG:-<none>}" >&2
    fail "MinIO conflict auto-heal failed (see commands above)"
}

_fail_identity_conflict() {
    echo "" >&2
    echo "  ✗ Zitadel contains resources not tracked in Terraform state" >&2
    echo "    (interrupted previous run or manually created objects)." >&2
    echo "    Options:" >&2
    echo "      (1) fresh local reset: ./scripts/cleanup.sh --data" >&2
    echo "          [DESTRUCTIVE — wipes all data volumes]" >&2
    echo "      (2) manual: tofu -chdir=terraform/stacks/identity import <addr> <id>" >&2
    echo "          for each conflicting resource — see log: ${LAST_TOFU_LOG:-<none>}" >&2
    fail "Identity apply failed due to existing Zitadel resources (see above)"
}

# run_tofu <stack-name> <apply-args...>
# init + apply, tee'd to logs/bootstrap/tofu-<stack>-<timestamp>.log
run_tofu() {
    local stack="$1"
    shift
    local stack_dir
    stack_dir=$(_tofu_stack_dir "$stack")
    local log_dir="$PROJECT_DIR/logs/bootstrap"
    mkdir -p "$log_dir"
    local ts
    ts=$(date +%Y%m%d-%H%M%S)
    LAST_TOFU_LOG="$log_dir/tofu-${stack}-${ts}.log"
    : > "$LAST_TOFU_LOG"

    log "tofu init ($stack)..."
    if ! "$TOFU_BIN" -chdir="$stack_dir" init -input=false 2>&1 | tee -a "$LAST_TOFU_LOG"; then
        _print_tofu_tail
        return 1
    fi

    log "tofu apply ($stack)..."
    if _tofu_apply "$stack_dir" "$@"; then
        return 0
    fi

    # Stale local lock from a killed apply — rm lock.info and retry once.
    if _log_matches "Error acquiring the state lock"; then
        if _heal_stale_lock "$stack"; then
            if _tofu_apply "$stack_dir" "$@"; then
                return 0
            fi
        fi
        # Retry failed or lock file wasn't there — fall through.
    fi

    if [ "$stack" = "infra" ] && _log_matches "already own it|BucketAlreadyOwnedByYou|already exists"; then
        _import_minio_buckets
        if _tofu_apply "$stack_dir" "$@"; then
            return 0
        fi
        _print_tofu_tail
        _fail_minio_heal
    fi

    if [ "$stack" = "identity" ] && _log_matches "already exists|AlreadyExists|Errors\..*AlreadyExists"; then
        _print_tofu_tail
        _fail_identity_conflict
    fi

    _print_tofu_tail
    return 1
}

# ── infra volume-fingerprint ──────────────────────────────────────────────────

compose_project_name() {
    if [ -n "${COMPOSE_PROJECT_NAME:-}" ]; then
        echo "$COMPOSE_PROJECT_NAME"
    else
        basename "$PROJECT_DIR"
    fi
}

# Echo CreatedAt, or "missing" if inspect fails.
volume_created_at() {
    local vol="$1"
    local created
    created=$("$DOCKER_BIN" volume inspect -f '{{.CreatedAt}}' "$vol" 2>/dev/null) || created="missing"
    if [ -z "$created" ]; then
        created="missing"
    fi
    echo "$created"
}

volume_fingerprint_path() {
    echo "$PROJECT_DIR/terraform/stacks/infra/.volume-fingerprint"
}

write_volume_fingerprint() {
    local project
    project=$(compose_project_name)
    local fp
    fp=$(volume_fingerprint_path)
    mkdir -p "$(dirname "$fp")"
    {
        echo "minio-data=$(volume_created_at "${project}_minio-data")"
        echo "qdrant-data=$(volume_created_at "${project}_qdrant-data")"
    } > "$fp"
}

# BEFORE infra apply: if tfstate + fingerprint exist and volumes were
# recreated (or are missing), clear stale infra state. If tfstate exists
# but fingerprint does not (first run after this feature), leave state.
check_volume_fingerprint() {
    local state="$PROJECT_DIR/terraform/stacks/infra/terraform.tfstate"
    local fp
    fp=$(volume_fingerprint_path)
    [ -f "$state" ] || return 0
    [ -f "$fp" ] || return 0

    local project rec_minio rec_qdrant cur_minio cur_qdrant
    project=$(compose_project_name)
    rec_minio=$(grep '^minio-data=' "$fp" 2>/dev/null | head -1 | cut -d= -f2- || true)
    rec_qdrant=$(grep '^qdrant-data=' "$fp" 2>/dev/null | head -1 | cut -d= -f2- || true)
    cur_minio=$(volume_created_at "${project}_minio-data")
    cur_qdrant=$(volume_created_at "${project}_qdrant-data")

    if [ "$cur_minio" = "$rec_minio" ] && [ "$cur_qdrant" = "$rec_qdrant" ]; then
        return 0
    fi

    log "Backing volumes were recreated since last apply — clearing stale infra state"
    rm -f "$state" "${state}.backup" "$fp"
}

# ── Qdrant drift pre-check ────────────────────────────────────────────────────

read_qdrant_url() {
    local tfvars="$PROJECT_DIR/terraform/environments/dev.infra.tfvars"
    local raw
    raw=$(grep -E '^[[:space:]]*qdrant_url[[:space:]]*=' "$tfvars" 2>/dev/null | head -1 || true)
    raw=${raw#*=}
    raw=$(printf '%s' "$raw" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//;s/^"//;s/"$//')
    if [ -z "$raw" ]; then
        echo "http://localhost:6333"
    else
        echo "$raw"
    fi
}

# If infra state lists qdrant terraform_data resources but the collection
# 404s, state-rm so apply re-runs the local-exec PUT. Addresses from qdrant.tf:
#   terraform_data.qdrant_documents       → documents
#   terraform_data.qdrant_response_cache  → response_cache
qdrant_drift_precheck() {
    local state="$PROJECT_DIR/terraform/stacks/infra/terraform.tfstate"
    [ -f "$state" ] || return 0

    local stack_dir
    stack_dir=$(_tofu_stack_dir "infra")
    local state_list
    state_list=$("$TOFU_BIN" -chdir="$stack_dir" state list 2>/dev/null || true)

    local have_docs=false have_cache=false
    if printf '%s\n' "$state_list" | grep -qx 'terraform_data.qdrant_documents'; then
        have_docs=true
    fi
    if printf '%s\n' "$state_list" | grep -qx 'terraform_data.qdrant_response_cache'; then
        have_cache=true
    fi
    if [ "$have_docs" = false ] && [ "$have_cache" = false ]; then
        return 0
    fi

    local qurl docs_ok=false cache_ok=false
    qurl=$(read_qdrant_url)
    if curl -sf "$qurl/collections/documents" >/dev/null 2>&1; then
        docs_ok=true
    fi
    if curl -sf "$qurl/collections/response_cache" >/dev/null 2>&1; then
        cache_ok=true
    fi

    if [ "$docs_ok" = false ] && [ "$cache_ok" = false ]; then
        if ! curl -sf "$qurl/healthz" >/dev/null 2>&1; then
            warn "Qdrant not reachable — skipping collection drift pre-check"
            return 0
        fi
    fi

    if [ "$have_docs" = true ] && [ "$docs_ok" = false ]; then
        warn "Qdrant collection 'documents' missing but present in state — removing terraform_data.qdrant_documents so apply recreates it"
        "$TOFU_BIN" -chdir="$stack_dir" state rm terraform_data.qdrant_documents >/dev/null 2>&1 || true
    fi
    if [ "$have_cache" = true ] && [ "$cache_ok" = false ]; then
        warn "Qdrant collection 'response_cache' missing but present in state — removing terraform_data.qdrant_response_cache so apply recreates it"
        "$TOFU_BIN" -chdir="$stack_dir" state rm terraform_data.qdrant_response_cache >/dev/null 2>&1 || true
    fi
}

# ── Phase 6 identity outputs ──────────────────────────────────────────────────

extract_identity_outputs() {
    CLIENT_ID=$(echo "$TF_OUTPUTS" | jq -r '.client_id.value')
    PROJECT_ID=$(echo "$TF_OUTPUTS" | jq -r '.project_id.value')
    SA_PAT=$(echo "$TF_OUTPUTS" | jq -r '.service_account_pat.value')
    E2E_SA_KEY=$(echo "$TF_OUTPUTS" | jq -r '.e2e_sa_key_json.value')
}

_tf_output_bad() {
    [ -z "$1" ] || [ "$1" = "null" ]
}

validate_identity_outputs() {
    local missing=""
    if _tf_output_bad "$CLIENT_ID"; then
        missing="${missing} client_id"
    fi
    if _tf_output_bad "$PROJECT_ID"; then
        missing="${missing} project_id"
    fi
    if _tf_output_bad "$SA_PAT"; then
        missing="${missing} service_account_pat"
    fi
    if [ -n "$missing" ]; then
        fail "identity apply did not complete — missing Terraform output(s):${missing}. Re-run ./scripts/start.sh; inspect: tofu -chdir=terraform/stacks/identity output"
    fi
}

write_generated_env() {
    local dest="${GENERATED_ENV:-$PROJECT_DIR/config/zitadel/generated.env}"
    local tmp="${dest}.tmp"
    mkdir -p "$(dirname "$dest")"
    cat > "$tmp" <<EOF
# Auto-generated by start.sh — do not edit manually.
ZITADEL_CLIENT_ID=${CLIENT_ID}
ZITADEL_PROJECT_ID=${PROJECT_ID}
ZITADEL_SERVICE_ACCOUNT_PAT=${SA_PAT}
ZITADEL_ACTIONS_SIGNING_KEY=${ACTIONS_SIGNING_KEY}
E2E_SA_KEY_FILE=config/zitadel/e2e-sa-key.json
E2E_TENANT_ID=e2e
EOF
    mv "$tmp" "$dest"
}
