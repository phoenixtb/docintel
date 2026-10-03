#!/usr/bin/env bats
# ==============================================================================
# test_start_helpers_minio_state.bats — forget_retired_minio_state
# (scripts/lib/start_helpers.sh). A fake tofu records its arguments, so the
# tests need neither OpenTofu nor network access.
# ==============================================================================

REPO_ROOT="$(cd "$(dirname "$BATS_TEST_FILENAME")/../.." && pwd)"

setup() {
    export PROJECT_DIR="$(mktemp -d)"
    mkdir -p "$PROJECT_DIR/terraform/stacks/infra"
    STATE="$PROJECT_DIR/terraform/stacks/infra/terraform.tfstate"
    CALLS="$PROJECT_DIR/tofu-calls"
    : > "$CALLS"

    export TOFU_BIN="$PROJECT_DIR/fake-tofu"
    cat > "$TOFU_BIN" <<SCRIPT
#!/usr/bin/env bash
echo "\$*" >> "$CALLS"
case "\$*" in
    *"state list"*)
        printf '%s\n' minio_s3_bucket.documents_raw minio_s3_bucket.models terraform_data.qdrant_documents ;;
esac
SCRIPT
    chmod +x "$TOFU_BIN"

    unset _START_HELPERS_LOADED
    # shellcheck source=../../scripts/lib/start_helpers.sh
    source "$REPO_ROOT/scripts/lib/start_helpers.sh"
}

teardown() {
    rm -rf "$PROJECT_DIR"
}

@test "does nothing when there is no infra state" {
    run forget_retired_minio_state
    [ "$status" -eq 0 ]
    [ ! -s "$CALLS" ]
}

@test "does nothing when the state has no MinIO buckets" {
    echo '{"resources":[{"type": "terraform_data","name":"qdrant_documents"}]}' > "$STATE"
    run forget_retired_minio_state
    [ "$status" -eq 0 ]
    [ ! -s "$CALLS" ]
}

@test "removes only the minio_s3_bucket addresses from state" {
    echo '{"resources":[{"type": "minio_s3_bucket","name":"documents_raw"}]}' > "$STATE"
    run forget_retired_minio_state
    [ "$status" -eq 0 ]
    grep -q "init -input=false" "$CALLS"
    grep -qx -- "-chdir=$PROJECT_DIR/terraform/stacks/infra state rm minio_s3_bucket.documents_raw minio_s3_bucket.models" "$CALLS"
    ! grep -q "terraform_data" "$CALLS"
}
