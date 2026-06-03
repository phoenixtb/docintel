#!/usr/bin/env bats
# ==============================================================================
# test_detect_hardware.bats — Unit tests for scripts/lib/detect_hardware.sh
#
# Uses bats-core (https://github.com/bats-core/bats-core).
# Install: brew install bats-core  |  apt install bats
#
# Tests use PATH shimming: a temporary directory of fake binaries is prepended
# to PATH, overriding nvidia-smi, docker, uname, etc. for each test.
# ==============================================================================

REPO_ROOT="$(cd "$(dirname "$BATS_TEST_FILENAME")/../.." && pwd)"
DETECT_SCRIPT="${REPO_ROOT}/scripts/lib/detect_hardware.sh"

# ==============================================================================
# Helpers
# ==============================================================================

# Create a temporary bin dir with mock executables, prepend to PATH.
setup() {
    MOCK_BIN="$(mktemp -d)"
    # Keep original PATH accessible via _PATH
    _ORIG_PATH="$PATH"
    PATH="${MOCK_BIN}:${PATH}"
}

teardown() {
    rm -rf "$MOCK_BIN"
    PATH="$_ORIG_PATH"
}

# Write a fake nvidia-smi that outputs fixed values
_mock_nvidia_smi() {
    local name="$1" driver="$2"
    cat > "${MOCK_BIN}/nvidia-smi" <<SCRIPT
#!/usr/bin/env bash
case "\$*" in
    *query-gpu=name*)      echo "${name}" ;;
    *query-gpu=driver_version*) echo "${driver}" ;;
    *query-gpu=compute_cap*)    echo "8.6" ;;
    *)                     exit 0 ;;
esac
SCRIPT
    chmod +x "${MOCK_BIN}/nvidia-smi"
}

# Write a fake docker that passes --gpus all test
_mock_docker_gpu_ok() {
    cat > "${MOCK_BIN}/docker" <<'SCRIPT'
#!/usr/bin/env bash
if [[ "$*" == *"--gpus all"* ]]; then exit 0; fi
exec /usr/bin/docker "$@" 2>/dev/null || true
SCRIPT
    chmod +x "${MOCK_BIN}/docker"
}

# Write a fake docker that fails --gpus all test
_mock_docker_gpu_fail() {
    cat > "${MOCK_BIN}/docker" <<'SCRIPT'
#!/usr/bin/env bash
if [[ "$*" == *"--gpus all"* ]]; then exit 1; fi
exec /usr/bin/docker "$@" 2>/dev/null || true
SCRIPT
    chmod +x "${MOCK_BIN}/docker"
}

# Fake uname that returns Darwin
_mock_uname_darwin() {
    cat > "${MOCK_BIN}/uname" <<'SCRIPT'
#!/usr/bin/env bash
case "$1" in
    -s) echo "Darwin" ;;
    -m) echo "arm64" ;;
    *)  echo "Darwin" ;;
esac
SCRIPT
    chmod +x "${MOCK_BIN}/uname"
}

# Fake sw_vers for macOS path
_mock_sw_vers() {
    cat > "${MOCK_BIN}/sw_vers" <<'SCRIPT'
#!/usr/bin/env bash
echo "15.4"
SCRIPT
    chmod +x "${MOCK_BIN}/sw_vers"
}

# Source the detector and run, capturing stdout (profile) and stderr (structured)
_run_detect() {
    local _out _err _profile
    _err=$(bash -c "source '${DETECT_SCRIPT}'; detect_hardware" 2>&1 >/dev/null)
    _profile=$(bash -c "source '${DETECT_SCRIPT}'; detect_hardware" 2>/dev/null)
    echo "$_profile"
}

# ==============================================================================
# Tests — OS detection
# ==============================================================================

@test "macOS → profile=cpu" {
    _mock_uname_darwin
    _mock_sw_vers
    result=$(_run_detect)
    [ "$result" = "cpu" ]
}

@test "macOS → structured output has DETECT_OS=darwin" {
    _mock_uname_darwin
    _mock_sw_vers
    stderr=$(bash -c "source '${DETECT_SCRIPT}'; detect_hardware" 2>&1 >/dev/null)
    echo "$stderr" | grep -q "DETECT_OS=darwin"
}

@test "macOS → structured output has DETECT_SOURCE=auto" {
    _mock_uname_darwin
    _mock_sw_vers
    stderr=$(bash -c "source '${DETECT_SCRIPT}'; detect_hardware" 2>&1 >/dev/null)
    echo "$stderr" | grep -q "DETECT_SOURCE=auto"
}

# ==============================================================================
# Tests — Linux without nvidia-smi
# ==============================================================================

@test "Linux no nvidia-smi → profile=cpu" {
    # No nvidia-smi mock = command not found
    result=$(_run_detect)
    [ "$result" = "cpu" ]
}

@test "Linux no nvidia-smi → DETECT_DOCKER_GPU=no_hardware" {
    stderr=$(bash -c "source '${DETECT_SCRIPT}'; detect_hardware" 2>&1 >/dev/null)
    echo "$stderr" | grep -q "DETECT_DOCKER_GPU=no_hardware"
}

# ==============================================================================
# Tests — Linux with GPU, driver → profile mapping
# ==============================================================================

@test "Linux driver 580 + docker GPU ok → profile=cu130" {
    _mock_nvidia_smi "NVIDIA GeForce RTX 3050" "580.126.09"
    _mock_docker_gpu_ok
    result=$(_run_detect)
    [ "$result" = "cu130" ]
}

@test "Linux driver 565 + docker GPU ok → profile=cu129" {
    _mock_nvidia_smi "NVIDIA RTX 4070" "565.00.00"
    _mock_docker_gpu_ok
    result=$(_run_detect)
    [ "$result" = "cu129" ]
}

@test "Linux driver 555 + docker GPU ok → profile=cu128" {
    _mock_nvidia_smi "NVIDIA RTX 3080" "555.00.00"
    _mock_docker_gpu_ok
    result=$(_run_detect)
    [ "$result" = "cu128" ]
}

@test "Linux driver 545 + docker GPU ok → profile=cu126" {
    _mock_nvidia_smi "NVIDIA GTX 1080 Ti" "545.00.00"
    _mock_docker_gpu_ok
    result=$(_run_detect)
    [ "$result" = "cu126" ]
}

@test "Linux driver 470 (too old) → profile=cpu with warning" {
    _mock_nvidia_smi "NVIDIA GTX 970" "470.00.00"
    _mock_docker_gpu_ok
    result=$(_run_detect)
    [ "$result" = "cpu" ]
}

@test "Linux driver 470 → DETECT_DOCKER_GPU=driver_too_old" {
    _mock_nvidia_smi "NVIDIA GTX 970" "470.00.00"
    _mock_docker_gpu_ok
    stderr=$(bash -c "source '${DETECT_SCRIPT}'; detect_hardware" 2>&1 >/dev/null)
    echo "$stderr" | grep -q "DETECT_DOCKER_GPU=driver_too_old"
}

# ==============================================================================
# Tests — Docker GPU passthrough failure
# ==============================================================================

@test "Linux driver 580 + docker GPU fail → profile=cpu" {
    _mock_nvidia_smi "NVIDIA RTX 3050" "580.126.09"
    _mock_docker_gpu_fail
    result=$(_run_detect)
    [ "$result" = "cpu" ]
}

@test "Linux driver 580 + docker GPU fail → DETECT_DOCKER_GPU=no_toolkit" {
    _mock_nvidia_smi "NVIDIA RTX 3050" "580.126.09"
    _mock_docker_gpu_fail
    stderr=$(bash -c "source '${DETECT_SCRIPT}'; detect_hardware" 2>&1 >/dev/null)
    echo "$stderr" | grep -q "DETECT_DOCKER_GPU=no_toolkit"
}

# ==============================================================================
# Tests — DOCINTEL_SKIP_GPU_TEST=1
# ==============================================================================

@test "SKIP_GPU_TEST=1: driver 580 → cu130 without docker test" {
    _mock_nvidia_smi "NVIDIA RTX 3050" "580.126.09"
    # No docker mock (docker would fail if called) — test asserts we skip it
    result=$(bash -c "source '${DETECT_SCRIPT}'; DOCINTEL_SKIP_GPU_TEST=1 detect_hardware" 2>/dev/null)
    [ "$result" = "cu130" ]
}

# ==============================================================================
# Tests — torch_index_for_profile helper
# ==============================================================================

@test "torch_index_for_profile cpu" {
    result=$(bash -c "source '${DETECT_SCRIPT}'; torch_index_for_profile cpu")
    [ "$result" = "https://download.pytorch.org/whl/cpu" ]
}

@test "torch_index_for_profile cu130" {
    result=$(bash -c "source '${DETECT_SCRIPT}'; torch_index_for_profile cu130")
    [ "$result" = "https://download.pytorch.org/whl/cu130" ]
}

@test "torch_index_for_profile unknown → cpu fallback" {
    result=$(bash -c "source '${DETECT_SCRIPT}'; torch_index_for_profile unknown_future")
    [ "$result" = "https://download.pytorch.org/whl/cpu" ]
}

@test "torch_index_for_profile empty → cpu fallback" {
    result=$(bash -c "source '${DETECT_SCRIPT}'; torch_index_for_profile ''")
    [ "$result" = "https://download.pytorch.org/whl/cpu" ]
}
