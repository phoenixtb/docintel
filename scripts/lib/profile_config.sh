#!/usr/bin/env bash
# ==============================================================================
# profile_config.sh — Hardware profile management for DocIntel build tooling
# ==============================================================================
# Source this file; do not execute directly.
#
# Functions exported:
#   read_profile [--flag-profile=VALUE]  — resolves profile via precedence chain
#   write_profile_override PROFILE       — persists user override to .docintel-profile
#   clear_profile_override               — removes .docintel-profile (restores auto-detect)
#   print_profile_summary                — formatted detection summary table
#   torch_vars_for_profile PROFILE       — exports TORCH_INDEX, TORCH_VERSION, PROFILE_TAG
#
# Precedence (highest → lowest):
#   1. --profile=<x> CLI flag  (passed as argument to read_profile)
#   2. PROFILE env var
#   3. .docintel-profile file  (written only when user sets explicitly)
#   4. Auto-detection via detect_hardware.sh
# ==============================================================================

# Source the detector if not already loaded
_PROFILE_CONFIG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=detect_hardware.sh
source "${_PROFILE_CONFIG_DIR}/detect_hardware.sh"

DOCINTEL_PROFILE_FILE="${PROJECT_DIR:-.}/.docintel-profile"
DOCINTEL_PROFILE_SHOWN_FILE="${PROJECT_DIR:-.}/.docintel-profile-shown"
TORCH_VERSION_DEFAULT="2.11.0"

# Valid profiles whitelist
_VALID_PROFILES="cpu cu126 cu128 cu129 cu130"

_is_valid_profile() {
    local p="$1"
    for v in $_VALID_PROFILES; do [ "$p" = "$v" ] && return 0; done
    return 1
}

# ------------------------------------------------------------------------------
# read_profile [--flag-profile=VALUE]
#
# Sets globals: PROFILE, PROFILE_SOURCE, PROFILE_GPU_NAME, PROFILE_DRIVER,
#               PROFILE_DOCKER_GPU, PROFILE_OS, PROFILE_DISTRO, PROFILE_ARCH
# ------------------------------------------------------------------------------
read_profile() {
    local _flag_profile=""

    # Parse --flag-profile= argument
    for arg in "$@"; do
        case "$arg" in
            --flag-profile=*) _flag_profile="${arg#--flag-profile=}" ;;
        esac
    done

    # Tier 1: CLI flag
    if [ -n "$_flag_profile" ]; then
        if ! _is_valid_profile "$_flag_profile"; then
            echo "  [profile] ERROR: invalid profile '$_flag_profile'. Valid: $_VALID_PROFILES" >&2
            exit 1
        fi
        PROFILE="$_flag_profile"
        PROFILE_SOURCE="flag"
        # Persist so subsequent builds without the flag remember
        write_profile_override "$PROFILE"
        _load_hw_info_stub
        return 0
    fi

    # Tier 2: PROFILE env var
    if [ -n "${PROFILE:-}" ]; then
        if ! _is_valid_profile "$PROFILE"; then
            echo "  [profile] ERROR: invalid PROFILE env var '$PROFILE'. Valid: $_VALID_PROFILES" >&2
            exit 1
        fi
        PROFILE_SOURCE="env"
        # Persist so subsequent builds without the env var remember
        write_profile_override "$PROFILE"
        _load_hw_info_stub
        return 0
    fi

    # Tier 3: .docintel-profile file
    if [ -f "$DOCINTEL_PROFILE_FILE" ]; then
        # shellcheck source=/dev/null
        local _file_profile=""
        _file_profile=$(grep "^PROFILE=" "$DOCINTEL_PROFILE_FILE" 2>/dev/null | cut -d= -f2 | tr -d '[:space:]')
        if [ -n "$_file_profile" ] && _is_valid_profile "$_file_profile"; then
            PROFILE="$_file_profile"
            PROFILE_SOURCE="file"
            _load_hw_info_stub
            return 0
        fi
    fi

    # Tier 4: Auto-detection
    PROFILE_SOURCE="auto"
    local _stderr_tmp
    _stderr_tmp=$(mktemp)

    PROFILE=$(detect_hardware 2>"$_stderr_tmp")

    # Parse structured stderr into globals
    PROFILE_OS=$(grep "^DETECT_OS=" "$_stderr_tmp" | cut -d= -f2)
    PROFILE_ARCH=$(grep "^DETECT_ARCH=" "$_stderr_tmp" | cut -d= -f2)
    PROFILE_DISTRO=$(grep "^DETECT_DISTRO=" "$_stderr_tmp" | cut -d= -f2-)
    PROFILE_GPU_NAME=$(grep "^DETECT_GPU_NAME=" "$_stderr_tmp" | cut -d= -f2-)
    PROFILE_DRIVER=$(grep "^DETECT_DRIVER=" "$_stderr_tmp" | cut -d= -f2)
    PROFILE_CUDA_COMPAT=$(grep "^DETECT_CUDA_COMPAT=" "$_stderr_tmp" | cut -d= -f2)
    PROFILE_DOCKER_GPU=$(grep "^DETECT_DOCKER_GPU=" "$_stderr_tmp" | cut -d= -f2)
    rm -f "$_stderr_tmp"
}

# Load stub hardware info (for flag/env/file cases where auto-detect was skipped)
_load_hw_info_stub() {
    PROFILE_OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
    PROFILE_ARCH="$(uname -m)"
    PROFILE_DISTRO="${PROFILE_DISTRO:-unknown}"
    PROFILE_GPU_NAME="${PROFILE_GPU_NAME:-}"
    PROFILE_DRIVER="${PROFILE_DRIVER:-}"
    PROFILE_CUDA_COMPAT="${PROFILE_CUDA_COMPAT:-}"
    PROFILE_DOCKER_GPU="${PROFILE_DOCKER_GPU:-}"
}

# ------------------------------------------------------------------------------
# write_profile_override PROFILE
# ------------------------------------------------------------------------------
write_profile_override() {
    local _p="$1"
    cat > "$DOCINTEL_PROFILE_FILE" <<EOF
# Auto-generated by DocIntel. Override the auto-detected hardware profile.
# Delete this file to restore auto-detection on the next build.
PROFILE=${_p}
SOURCE=user
SET_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date -u)
EOF
}

# ------------------------------------------------------------------------------
# clear_profile_override
# ------------------------------------------------------------------------------
clear_profile_override() {
    rm -f "$DOCINTEL_PROFILE_FILE" "$DOCINTEL_PROFILE_SHOWN_FILE"
    echo "  Profile override cleared. Auto-detection will run on next build."
}

# ------------------------------------------------------------------------------
# print_profile_summary
#
# Reads globals: PROFILE, PROFILE_SOURCE, PROFILE_GPU_NAME, PROFILE_DRIVER,
#                PROFILE_CUDA_COMPAT, PROFILE_DOCKER_GPU, PROFILE_OS,
#                PROFILE_DISTRO, PROFILE_ARCH
# ------------------------------------------------------------------------------
print_profile_summary() {
    local _idx
    _idx=$(torch_index_for_profile "$PROFILE")

    # Colors (only when stdout is a terminal)
    local _bold _dim _green _yellow _cyan _nc
    if [ -t 1 ]; then
        _bold='\033[1m'; _dim='\033[2m'; _green='\033[0;32m'
        _yellow='\033[1;33m'; _cyan='\033[0;36m'; _nc='\033[0m'
    else
        _bold=''; _dim=''; _green=''; _yellow=''; _cyan=''; _nc=''
    fi

    echo ""
    echo -e "  ${_bold}==========================================================${_nc}"
    echo -e "  ${_bold}  DocIntel Build — Hardware Detection${_nc}"
    echo -e "  ${_bold}==========================================================${_nc}"
    echo ""

    # OS / Arch
    printf "  %-26s %s\n" "OS:" "${PROFILE_DISTRO:-${PROFILE_OS:-unknown}}"
    printf "  %-26s %s\n" "Architecture:" "${PROFILE_ARCH:-unknown}"

    # GPU block
    if [ -n "${PROFILE_GPU_NAME:-}" ]; then
        printf "  %-26s %s\n" "NVIDIA GPU:" "$PROFILE_GPU_NAME"
        printf "  %-26s %s\n" "Driver:" "${PROFILE_DRIVER:-unknown}"
        if [ -n "${PROFILE_CUDA_COMPAT:-}" ]; then
            printf "  %-26s %s\n" "CUDA capable:" "${PROFILE_CUDA_COMPAT}"
        fi
        case "${PROFILE_DOCKER_GPU:-}" in
            ok)           printf "  %-26s %b\n" "Docker GPU:" "${_green}nvidia-container-toolkit OK${_nc}" ;;
            no_toolkit)   printf "  %-26s %b\n" "Docker GPU:" "${_yellow}nvidia-container-toolkit missing${_nc}" ;;
            driver_too_old) printf "  %-26s %b\n" "Docker GPU:" "${_yellow}driver too old (need >=545)${_nc}" ;;
            skipped)      printf "  %-26s %s\n" "Docker GPU:" "test skipped (DOCINTEL_SKIP_GPU_TEST=1)" ;;
            *)            printf "  %-26s %s\n" "Docker GPU:" "${PROFILE_DOCKER_GPU:-unknown}" ;;
        esac
    else
        printf "  %-26s %s\n" "NVIDIA GPU:" "(none detected)"
    fi

    echo ""

    # Profile line
    local _profile_color="$_cyan"
    [ "$PROFILE" = "cpu" ] && _profile_color="$_dim"
    printf "  %-26s %b\n" "Selected profile:" "${_profile_color}${_bold}${PROFILE}${_nc}    (${PROFILE_SOURCE})"

    echo ""

    # Effect blurb
    if [ "$PROFILE" = "cpu" ]; then
        echo -e "  ${_dim}CPU-only PyTorch: ~3.4 GB lighter images, no GPU in containers.${_nc}"
    else
        echo -e "  ${_dim}GPU profile $PROFILE: CUDA libs included, Docker GPU reservation active.${_nc}"
        echo -e "  ${_dim}Image size comparable to PyPI default but from pinned $PROFILE index.${_nc}"
    fi

    echo ""

    # Override hint (only when auto-detected)
    if [ "$PROFILE_SOURCE" = "auto" ]; then
        echo -e "  ${_dim}Override with:${_nc}"
        echo -e "  ${_dim}  PROFILE=cpu ./scripts/docintel.sh build${_nc}"
        echo -e "  ${_dim}  ./scripts/docintel.sh build --profile=cpu${_nc}"
    elif [ "$PROFILE_SOURCE" = "file" ]; then
        echo -e "  ${_dim}Profile loaded from .docintel-profile. Delete it to restore auto-detection.${_nc}"
    fi

    echo ""
}

# ------------------------------------------------------------------------------
# print_profile_summary_ci
# Machine-parseable single-line for non-TTY / CI
# ------------------------------------------------------------------------------
print_profile_summary_ci() {
    local _gpu="${PROFILE_GPU_NAME:-none}"
    echo "[hardware] profile=${PROFILE} source=${PROFILE_SOURCE} driver=${PROFILE_DRIVER:-n/a} gpu=\"${_gpu}\""
}

# ------------------------------------------------------------------------------
# torch_vars_for_profile PROFILE
# Exports TORCH_INDEX, TORCH_VERSION, PROFILE_TAG for use by docker compose build
# ------------------------------------------------------------------------------
torch_vars_for_profile() {
    local _p="${1:-cpu}"
    export TORCH_INDEX
    TORCH_INDEX="$(torch_index_for_profile "$_p")"
    export TORCH_VERSION="${TORCH_VERSION_DEFAULT}"
    export PROFILE_TAG="${_p}"
}

# ------------------------------------------------------------------------------
# compose_file_chain [PROJECT_ROOT]
#
# Sets COMPOSE_FILES to the correct -f chain for the active profile:
#   1. docker-compose.yml           (always)
#   2. docker-compose.override.yml  (always, if it exists — restores Docker Compose
#                                    auto-load semantics that explicit -f flags break)
#   3. docker-compose.gpu.yml       (only when PROFILE != cpu)
#
# Call after read_profile + torch_vars_for_profile.
# ------------------------------------------------------------------------------
compose_file_chain() {
    local _root="${1:-${PROJECT_DIR:-.}}"

    COMPOSE_FILES="-f ${_root}/docker-compose.yml"

    if [ -f "${_root}/docker-compose.override.yml" ]; then
        COMPOSE_FILES="${COMPOSE_FILES} -f ${_root}/docker-compose.override.yml"
    fi

    if [ "${PROFILE:-cpu}" != "cpu" ] && [ -f "${_root}/docker-compose.gpu.yml" ]; then
        COMPOSE_FILES="${COMPOSE_FILES} -f ${_root}/docker-compose.gpu.yml"
    fi

    export COMPOSE_FILES
}
