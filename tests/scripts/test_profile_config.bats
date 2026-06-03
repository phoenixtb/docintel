#!/usr/bin/env bats
# ==============================================================================
# test_profile_config.bats — Unit tests for scripts/lib/profile_config.sh
#
# Tests:
#   - read_profile precedence: flag > env > file > auto
#   - write_profile_override persists correctly
#   - auto-detection result NOT persisted to .docintel-profile
#   - torch_vars_for_profile exports correct TORCH_INDEX and PROFILE_TAG
# ==============================================================================

REPO_ROOT="$(cd "$(dirname "$BATS_TEST_FILENAME")/../.." && pwd)"
PROFILE_CONFIG="${REPO_ROOT}/scripts/lib/profile_config.sh"
DETECT_SCRIPT="${REPO_ROOT}/scripts/lib/detect_hardware.sh"

setup() {
    TMPDIR_TEST="$(mktemp -d)"
    # Override PROJECT_DIR so .docintel-profile writes go to temp dir
    export PROJECT_DIR="$TMPDIR_TEST"
    # Skip GPU Docker passthrough test in all profile_config tests
    export DOCINTEL_SKIP_GPU_TEST=1

    # Create a fake nvidia-smi that returns driver 580 so auto-detect → cu130
    MOCK_BIN="$(mktemp -d)"
    _ORIG_PATH="$PATH"
    PATH="${MOCK_BIN}:${PATH}"
    cat > "${MOCK_BIN}/nvidia-smi" <<'SCRIPT'
#!/usr/bin/env bash
case "$*" in
    *query-gpu=name*)            echo "NVIDIA Test GPU" ;;
    *query-gpu=driver_version*)  echo "580.00.00" ;;
    *query-gpu=compute_cap*)     echo "8.6" ;;
    *)                           exit 0 ;;
esac
SCRIPT
    chmod +x "${MOCK_BIN}/nvidia-smi"
}

teardown() {
    rm -rf "$TMPDIR_TEST" "$MOCK_BIN"
    PATH="$_ORIG_PATH"
    unset PROFILE PROFILE_SOURCE PROJECT_DIR DOCINTEL_SKIP_GPU_TEST
}

# Helper: source profile_config with PROJECT_DIR set and run read_profile
_read_profile() {
    bash -c "
        PROJECT_DIR='${TMPDIR_TEST}'
        DOCINTEL_SKIP_GPU_TEST=1
        PATH='${MOCK_BIN}:${_ORIG_PATH}'
        source '${PROFILE_CONFIG}'
        read_profile $*
        echo \"\$PROFILE:\$PROFILE_SOURCE\"
    "
}

# Helper: write_profile_override to temp dir
_write_override() {
    bash -c "
        PROJECT_DIR='${TMPDIR_TEST}'
        DOCINTEL_SKIP_GPU_TEST=1
        source '${PROFILE_CONFIG}'
        write_profile_override '$1'
    "
}

# ==============================================================================
# Precedence tests
# ==============================================================================

@test "tier 4 auto-detect: no env, no file, no flag → auto detects cu130" {
    result=$(_read_profile)
    profile="${result%%:*}"
    source="${result##*:}"
    [ "$profile" = "cu130" ]
    [ "$source" = "auto" ]
}

@test "tier 3 file: .docintel-profile file with PROFILE=cpu → returns cpu" {
    _write_override "cpu"
    result=$(_read_profile)
    profile="${result%%:*}"
    source="${result##*:}"
    [ "$profile" = "cpu" ]
    [ "$source" = "file" ]
}

@test "tier 2 env var: PROFILE=cu128 env overrides file" {
    _write_override "cpu"  # file says cpu
    result=$(bash -c "
        PROJECT_DIR='${TMPDIR_TEST}'
        DOCINTEL_SKIP_GPU_TEST=1
        PATH='${MOCK_BIN}:${_ORIG_PATH}'
        PROFILE=cu128
        source '${PROFILE_CONFIG}'
        read_profile
        echo \"\$PROFILE:\$PROFILE_SOURCE\"
    ")
    profile="${result%%:*}"
    source="${result##*:}"
    [ "$profile" = "cu128" ]
    [ "$source" = "env" ]
}

@test "tier 1 flag: --flag-profile=cu129 overrides env and file" {
    _write_override "cpu"  # file says cpu
    result=$(bash -c "
        PROJECT_DIR='${TMPDIR_TEST}'
        DOCINTEL_SKIP_GPU_TEST=1
        PATH='${MOCK_BIN}:${_ORIG_PATH}'
        PROFILE=cu128       # env says cu128
        source '${PROFILE_CONFIG}'
        read_profile --flag-profile=cu129
        echo \"\$PROFILE:\$PROFILE_SOURCE\"
    ")
    profile="${result%%:*}"
    source="${result##*:}"
    [ "$profile" = "cu129" ]
    [ "$source" = "flag" ]
}

# ==============================================================================
# Persistence tests
# ==============================================================================

@test "auto-detect does NOT write .docintel-profile" {
    _read_profile
    [ ! -f "${TMPDIR_TEST}/.docintel-profile" ]
}

@test "flag override writes .docintel-profile" {
    bash -c "
        PROJECT_DIR='${TMPDIR_TEST}'
        DOCINTEL_SKIP_GPU_TEST=1
        PATH='${MOCK_BIN}:${_ORIG_PATH}'
        source '${PROFILE_CONFIG}'
        read_profile --flag-profile=cu126
    "
    [ -f "${TMPDIR_TEST}/.docintel-profile" ]
    grep -q "PROFILE=cu126" "${TMPDIR_TEST}/.docintel-profile"
}

@test "env override writes .docintel-profile" {
    bash -c "
        PROJECT_DIR='${TMPDIR_TEST}'
        DOCINTEL_SKIP_GPU_TEST=1
        PATH='${MOCK_BIN}:${_ORIG_PATH}'
        PROFILE=cu128
        source '${PROFILE_CONFIG}'
        read_profile
    "
    [ -f "${TMPDIR_TEST}/.docintel-profile" ]
    grep -q "PROFILE=cu128" "${TMPDIR_TEST}/.docintel-profile"
}

@test "clear_profile_override removes .docintel-profile" {
    _write_override "cpu"
    [ -f "${TMPDIR_TEST}/.docintel-profile" ]
    bash -c "
        PROJECT_DIR='${TMPDIR_TEST}'
        source '${PROFILE_CONFIG}'
        clear_profile_override
    "
    [ ! -f "${TMPDIR_TEST}/.docintel-profile" ]
}

@test "invalid profile in env var causes exit 1" {
    run bash -c "
        PROJECT_DIR='${TMPDIR_TEST}'
        DOCINTEL_SKIP_GPU_TEST=1
        PATH='${MOCK_BIN}:${_ORIG_PATH}'
        PROFILE=rocm_unknown
        source '${PROFILE_CONFIG}'
        read_profile
    "
    [ "$status" -ne 0 ]
}

# ==============================================================================
# torch_vars_for_profile tests
# ==============================================================================

@test "torch_vars_for_profile cpu sets TORCH_INDEX to whl/cpu" {
    result=$(bash -c "
        PROJECT_DIR='${TMPDIR_TEST}'
        source '${PROFILE_CONFIG}'
        torch_vars_for_profile cpu
        echo \"\$TORCH_INDEX\"
    ")
    [ "$result" = "https://download.pytorch.org/whl/cpu" ]
}

@test "torch_vars_for_profile cu130 sets TORCH_INDEX to whl/cu130" {
    result=$(bash -c "
        PROJECT_DIR='${TMPDIR_TEST}'
        source '${PROFILE_CONFIG}'
        torch_vars_for_profile cu130
        echo \"\$TORCH_INDEX\"
    ")
    [ "$result" = "https://download.pytorch.org/whl/cu130" ]
}

@test "torch_vars_for_profile sets PROFILE_TAG to profile name" {
    result=$(bash -c "
        PROJECT_DIR='${TMPDIR_TEST}'
        source '${PROFILE_CONFIG}'
        torch_vars_for_profile cu129
        echo \"\$PROFILE_TAG\"
    ")
    [ "$result" = "cu129" ]
}

@test "torch_vars_for_profile cpu sets TORCH_VERSION to 2.11.0+cpu" {
    result=$(bash -c "
        PROJECT_DIR='${TMPDIR_TEST}'
        source '${PROFILE_CONFIG}'
        torch_vars_for_profile cpu
        echo \"\$TORCH_VERSION\"
    ")
    [ "$result" = "2.11.0+cpu" ]
}

@test "torch_vars_for_profile cu128 sets TORCH_VERSION to 2.11.0+cu128" {
    result=$(bash -c "
        PROJECT_DIR='${TMPDIR_TEST}'
        source '${PROFILE_CONFIG}'
        torch_vars_for_profile cu128
        echo \"\$TORCH_VERSION\"
    ")
    [ "$result" = "2.11.0+cu128" ]
}

# ==============================================================================
# resolve_platform tests (single source of truth for Docker target platform)
# ==============================================================================

@test "resolve_platform default: no env, no file → native (empty DOCKER_DEFAULT_PLATFORM)" {
    result=$(bash -c "
        PROJECT_DIR='${TMPDIR_TEST}'
        unset DOCKER_DEFAULT_PLATFORM
        source '${PROFILE_CONFIG}'
        resolve_platform
        echo \"[\$DOCKER_DEFAULT_PLATFORM]:\$DOCKER_PLATFORM_SOURCE\"
    ")
    [ "$result" = "[]:native" ]
}

@test "resolve_platform env override is used and persisted to .docintel-profile" {
    result=$(bash -c "
        PROJECT_DIR='${TMPDIR_TEST}'
        DOCKER_DEFAULT_PLATFORM=linux/amd64
        source '${PROFILE_CONFIG}'
        resolve_platform
        echo \"\$DOCKER_DEFAULT_PLATFORM:\$DOCKER_PLATFORM_SOURCE\"
    ")
    [ "$result" = "linux/amd64:env" ]
    grep -q "PLATFORM=linux/amd64" "${TMPDIR_TEST}/.docintel-profile"
}

@test "resolve_platform reads persisted PLATFORM from .docintel-profile" {
    bash -c "
        PROJECT_DIR='${TMPDIR_TEST}'
        source '${PROFILE_CONFIG}'
        write_platform_override linux/amd64
    "
    result=$(bash -c "
        PROJECT_DIR='${TMPDIR_TEST}'
        unset DOCKER_DEFAULT_PLATFORM
        source '${PROFILE_CONFIG}'
        resolve_platform
        echo \"\$DOCKER_DEFAULT_PLATFORM:\$DOCKER_PLATFORM_SOURCE\"
    ")
    [ "$result" = "linux/amd64:file" ]
}

@test "write_platform_override preserves an existing PROFILE line" {
    bash -c "
        PROJECT_DIR='${TMPDIR_TEST}'
        source '${PROFILE_CONFIG}'
        write_profile_override cpu
        write_platform_override linux/amd64
    "
    grep -q "PROFILE=cpu" "${TMPDIR_TEST}/.docintel-profile"
    grep -q "PLATFORM=linux/amd64" "${TMPDIR_TEST}/.docintel-profile"
}
