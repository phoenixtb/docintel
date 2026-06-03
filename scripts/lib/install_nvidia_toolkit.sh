#!/usr/bin/env bash
# ==============================================================================
# install_nvidia_toolkit.sh — Offer to install nvidia-container-toolkit
# ==============================================================================
# Called by build.sh when detect_hardware returns DETECT_DOCKER_GPU=no_toolkit.
# NEVER auto-installs — always prompts. Never called from non-TTY contexts.
#
# Returns 0 if toolkit was successfully installed and Docker GPU test now passes.
# Returns 1 if user declined, install failed, or distro unsupported.
#
# Globals read: PROFILE_GPU_NAME
# ==============================================================================

offer_install_nvidia_toolkit() {
    local _gpu_name="${PROFILE_GPU_NAME:-NVIDIA GPU}"

    # ── Colors ──────────────────────────────────────────────────────────────
    local _bold _yellow _green _red _nc
    _bold='\033[1m'; _yellow='\033[1;33m'; _green='\033[0;32m'
    _red='\033[0;31m'; _nc='\033[0m'

    echo ""
    echo -e "  ${_yellow}${_bold}NVIDIA GPU detected ($_gpu_name) but Docker cannot access it.${_nc}"
    echo -e "  The ${_bold}nvidia-container-toolkit${_nc} package is missing or misconfigured."
    echo ""

    # Detect distro
    local _distro_id=""
    if [ -f /etc/os-release ]; then
        # shellcheck source=/dev/null
        . /etc/os-release
        _distro_id="${ID_LIKE:-$ID}"
    fi

    # Determine install method
    local _install_cmd="" _configure_cmd="" _restart_cmd=""
    _configure_cmd="sudo nvidia-ctk runtime configure --runtime=docker"
    _restart_cmd="sudo systemctl restart docker"

    case "$_distro_id" in
        *debian*|*ubuntu*)
            _install_cmd="sudo apt-get install -y nvidia-container-toolkit"
            ;;
        *fedora*|*rhel*|*centos*|*rocky*|*almalinux*)
            _install_cmd="sudo dnf install -y nvidia-container-toolkit"
            ;;
        *arch*)
            echo -e "  ${_yellow}Arch Linux detected.${_nc} Install manually from AUR:"
            echo ""
            echo "    yay -S nvidia-container-toolkit"
            echo "    sudo nvidia-ctk runtime configure --runtime=docker"
            echo "    sudo systemctl restart docker"
            echo ""
            echo -e "  ${_yellow}Then re-run the build.${_nc}"
            echo ""
            return 1
            ;;
        *)
            echo -e "  ${_yellow}Unsupported distro.${_nc} Install nvidia-container-toolkit manually:"
            echo ""
            echo "    https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html"
            echo ""
            echo "  Then re-run: ./scripts/docintel.sh build"
            echo ""
            return 1
            ;;
    esac

    echo "  Install it now? This will run:"
    echo ""
    echo "    $_install_cmd"
    echo "    $_configure_cmd"
    echo "    $_restart_cmd"
    echo ""
    echo -e "  ${_bold}Requires sudo. [y/N]${_nc} "
    read -r _answer </dev/tty

    if [[ ! "$_answer" =~ ^[Yy]$ ]]; then
        echo ""
        echo -e "  ${_yellow}Skipped. Building with CPU profile.${_nc}"
        echo "  To install later, run:"
        echo "    $_install_cmd"
        echo "    $_configure_cmd"
        echo "    $_restart_cmd"
        echo "  Then re-run: ./scripts/docintel.sh build"
        echo ""
        return 1
    fi

    echo ""
    echo "  Installing nvidia-container-toolkit..."
    echo ""

    # Check sudo availability
    if ! command -v sudo >/dev/null 2>&1; then
        echo -e "  ${_red}sudo not found.${_nc} Run as root or install sudo, then:"
        echo "    $_install_cmd"
        echo "    $_configure_cmd"
        echo "    $_restart_cmd"
        return 1
    fi

    # Run install
    if ! eval "$_install_cmd"; then
        echo ""
        echo -e "  ${_red}Installation failed.${_nc} Check errors above."
        echo "  Building with CPU profile."
        return 1
    fi

    if ! eval "$_configure_cmd"; then
        echo ""
        echo -e "  ${_red}nvidia-ctk runtime configure failed.${_nc}"
        echo "  Building with CPU profile."
        return 1
    fi

    if ! eval "$_restart_cmd"; then
        echo ""
        echo -e "  ${_yellow}Docker restart failed (may need manual restart).${_nc}"
        echo "  Try: sudo systemctl restart docker"
        return 1
    fi

    # Retry Docker GPU passthrough test
    echo ""
    echo "  Verifying Docker GPU access..."
    if docker run --rm --gpus all alpine:latest true >/dev/null 2>&1; then
        echo -e "  ${_green}nvidia-container-toolkit installed and verified.${_nc}"
        echo ""
        return 0
    else
        echo -e "  ${_yellow}Docker GPU test still failing after installation.${_nc}"
        echo "  Try rebooting or checking: sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml"
        echo "  Building with CPU profile for now."
        return 1
    fi
}
