#!/usr/bin/env bash
# ==============================================================================
# detect_hardware.sh — Hardware profile auto-detection for DocIntel
# ==============================================================================
# Outputs ONE token to stdout: cpu | cu126 | cu128 | cu129 | cu130
# Human-readable detection info is written to stderr.
#
# Usage (standalone):
#   source scripts/lib/detect_hardware.sh
#   PROFILE=$(detect_hardware)
#
# Caller is responsible for deciding how to present the output.
# This script never prompts — the nvidia-container-toolkit install offer is in
# install_nvidia_toolkit.sh and must be called explicitly by the caller.
#
# Environment variables (inputs):
#   DOCINTEL_SKIP_GPU_TEST=1  — skip docker GPU passthrough test (faster, CI use)
#
# Structured stderr (one key=value per line, for machine parsing):
#   DETECT_OS           linux | darwin | windows
#   DETECT_ARCH         x86_64 | aarch64 | arm64
#   DETECT_DISTRO       Ubuntu 24.04 / macOS 15.4 / etc.
#   DETECT_GPU_NAME     "NVIDIA GeForce RTX 3050" | "" (empty = none)
#   DETECT_DRIVER       "580.126.09" | ""
#   DETECT_CUDA_COMPAT  "13.0" | ""
#   DETECT_DOCKER_GPU   ok | no_toolkit | no_hardware | darwin | windows
#   DETECT_PROFILE      cpu | cu126 | cu128 | cu129 | cu130
#   DETECT_SOURCE       auto
# ==============================================================================

detect_hardware() {
    local _os _arch _distro _gpu_name _driver _cuda_compat _docker_gpu _profile

    # ── OS / Architecture ─────────────────────────────────────────────────────
    _os_raw="$(uname -s 2>/dev/null)"
    _arch="$(uname -m 2>/dev/null)"

    case "$_os_raw" in
        Darwin)
            _os=darwin
            _distro="macOS $(sw_vers -productVersion 2>/dev/null)"
            _gpu_name=""
            _driver=""
            _cuda_compat=""
            _docker_gpu=darwin
            _profile=cpu
            _emit_structured _os "$_os" _arch "$_arch" _distro "$_distro" \
                _gpu_name "$_gpu_name" _driver "$_driver" \
                _cuda_compat "$_cuda_compat" _docker_gpu "$_docker_gpu" \
                _profile "$_profile"
            echo "$_profile"; return 0
            ;;
        MINGW*|MSYS*|CYGWIN*)
            _os=windows
            _distro="Windows (native)"
            _gpu_name=""
            _driver=""
            _cuda_compat=""
            _docker_gpu=windows
            _profile=cpu
            _emit_structured _os "$_os" _arch "$_arch" _distro "$_distro" \
                _gpu_name "$_gpu_name" _driver "$_driver" \
                _cuda_compat "$_cuda_compat" _docker_gpu "$_docker_gpu" \
                _profile "$_profile"
            echo "$_profile"; return 0
            ;;
        Linux)
            _os=linux
            ;;
        *)
            _os=unknown
            _profile=cpu
            echo "$_profile"; return 0
            ;;
    esac

    # ── Linux / WSL2 path ─────────────────────────────────────────────────────

    # Distro name
    if [ -f /etc/os-release ]; then
        # shellcheck source=/dev/null
        . /etc/os-release
        _distro="${PRETTY_NAME:-Linux}"
    else
        _distro="Linux"
    fi

    # WSL2 detection (informational only — detection still treats it as Linux)
    if grep -qiE "microsoft|wsl" /proc/version 2>/dev/null; then
        _distro="WSL2 / $_distro"
    fi

    # ── NVIDIA GPU hardware ───────────────────────────────────────────────────
    if ! command -v nvidia-smi >/dev/null 2>&1; then
        _gpu_name=""
        _driver=""
        _cuda_compat=""
        _docker_gpu=no_hardware
        _profile=cpu
        _emit_structured _os "$_os" _arch "$_arch" _distro "$_distro" \
            _gpu_name "$_gpu_name" _driver "$_driver" \
            _cuda_compat "$_cuda_compat" _docker_gpu "$_docker_gpu" \
            _profile "$_profile"
        echo "$_profile"; return 0
    fi

    # nvidia-smi is present; try to query hardware info
    _gpu_name="$(nvidia-smi --query-gpu=name --format=csv,noheader,nounits 2>/dev/null | head -1 | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    _driver="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader,nounits 2>/dev/null | head -1 | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    _cuda_compat="$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader,nounits 2>/dev/null | head -1 | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"

    if [ -z "$_gpu_name" ] && ! nvidia-smi >/dev/null 2>&1; then
        # nvidia-smi present but no functional GPU
        _gpu_name=""
        _driver=""
        _cuda_compat=""
        _docker_gpu=no_hardware
        _profile=cpu
        _emit_structured _os "$_os" _arch "$_arch" _distro "$_distro" \
            _gpu_name "$_gpu_name" _driver "$_driver" \
            _cuda_compat "$_cuda_compat" _docker_gpu "$_docker_gpu" \
            _profile "$_profile"
        echo "$_profile"; return 0
    fi

    # ── Driver version → CUDA profile mapping ─────────────────────────────────
    # Extract major.minor as integer (e.g. 580.126.09 → 580)
    local _driver_major
    _driver_major="$(echo "$_driver" | cut -d. -f1)"

    if [ -z "$_driver_major" ] || ! [ "$_driver_major" -eq "$_driver_major" ] 2>/dev/null; then
        # Can't parse driver version
        _profile=cpu
    elif [ "$_driver_major" -ge 580 ]; then
        _profile=cu130
    elif [ "$_driver_major" -ge 565 ]; then
        _profile=cu129
    elif [ "$_driver_major" -ge 555 ]; then
        _profile=cu128
    elif [ "$_driver_major" -ge 545 ]; then
        _profile=cu126
    else
        # Driver too old for torch 2.8+ CUDA wheels (requires driver >= 545)
        echo "  [hardware] WARNING: NVIDIA driver $_driver is too old for PyTorch 2.8+ CUDA wheels." >&2
        echo "             Minimum required: driver >= 545 (for cu126)." >&2
        echo "             Update your NVIDIA driver or use PROFILE=cpu." >&2
        _profile=cpu
        _docker_gpu=driver_too_old
        _emit_structured _os "$_os" _arch "$_arch" _distro "$_distro" \
            _gpu_name "$_gpu_name" _driver "$_driver" \
            _cuda_compat "$_cuda_compat" _docker_gpu "$_docker_gpu" \
            _profile "$_profile"
        echo "$_profile"; return 0
    fi

    # ── Docker GPU passthrough test ───────────────────────────────────────────
    if [ "${DOCINTEL_SKIP_GPU_TEST:-0}" = "1" ]; then
        _docker_gpu=skipped
    elif docker run --rm --gpus all alpine:latest true >/dev/null 2>&1; then
        _docker_gpu=ok
    else
        # GPU hardware + driver present but Docker can't access it.
        # Caller can invoke install_nvidia_toolkit.sh to offer a fix.
        _docker_gpu=no_toolkit
        _profile=cpu
        _emit_structured _os "$_os" _arch "$_arch" _distro "$_distro" \
            _gpu_name "$_gpu_name" _driver "$_driver" \
            _cuda_compat "$_cuda_compat" _docker_gpu "$_docker_gpu" \
            _profile "$_profile"
        echo "$_profile"; return 0
    fi

    _emit_structured _os "$_os" _arch "$_arch" _distro "$_distro" \
        _gpu_name "$_gpu_name" _driver "$_driver" \
        _cuda_compat "$_cuda_compat" _docker_gpu "$_docker_gpu" \
        _profile "$_profile"

    echo "$_profile"
}

# ------------------------------------------------------------------------------
# _emit_structured — write key=value pairs to stderr
# Usage: _emit_structured _os linux _arch x86_64 ...
# ------------------------------------------------------------------------------
_emit_structured() {
    while [ $# -ge 2 ]; do
        local _k="${1#_}"  # strip leading underscore from var name
        local _v="$2"
        # Uppercase key: DETECT_OS, DETECT_GPU_NAME, etc.
        local _KEY
        _KEY="DETECT_$(echo "$_k" | tr '[:lower:]' '[:upper:]')"
        echo "${_KEY}=${_v}" >&2
        shift 2
    done
    echo "DETECT_SOURCE=auto" >&2
}

# ------------------------------------------------------------------------------
# torch_index_for_profile — returns the PyTorch wheel index URL for a profile
# ------------------------------------------------------------------------------
torch_index_for_profile() {
    case "${1:-cpu}" in
        cu130) echo "https://download.pytorch.org/whl/cu130" ;;
        cu129) echo "https://download.pytorch.org/whl/cu129" ;;
        cu128) echo "https://download.pytorch.org/whl/cu128" ;;
        cu126) echo "https://download.pytorch.org/whl/cu126" ;;
        *)     echo "https://download.pytorch.org/whl/cpu"   ;;
    esac
}
