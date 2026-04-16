#!/bin/bash
# scripts/lib/install-lmforge.sh
# ================================
# Provides check_or_install_lmforge() — idempotent LMForge binary installer.
#
# Usage — source from setup-lmforge.sh:
#   source "$SCRIPT_DIR/lib/install-lmforge.sh"
#   check_or_install_lmforge
#
# Install target: $HOME/.local/bin  (user-local, no sudo required)
# Override:       LMFORGE_INSTALL_DIR=/usr/local/bin check_or_install_lmforge

# Guard against double-sourcing
[ -n "$_INSTALL_LMFORGE_LOADED" ] && return 0
_INSTALL_LMFORGE_LOADED=1

_LMFORGE_REPO="phoenixtb/lmforge"
_LMFORGE_INSTALL_DIR="${LMFORGE_INSTALL_DIR:-$HOME/.local/bin}"

# ── check_or_install_lmforge ──────────────────────────────────────────────────
# If lmforge is already on PATH — done.
# Otherwise prompt, then download & install the official pre-built binary
# (falls back to source build via Rust/cargo if no binary for this platform).
# ─────────────────────────────────────────────────────────────────────────────
check_or_install_lmforge() {
    echo ""
    echo "================================================"
    echo "Checking LMForge"
    echo "================================================"

    if command -v lmforge &>/dev/null; then
        local ver
        ver=$(lmforge --version 2>/dev/null | head -1 || echo "unknown version")
        ok "lmforge $ver"
        return 0
    fi

    warn "lmforge not found on PATH."
    echo ""
    printf "  Install LMForge now? [Y/n] "
    read -r _ans </dev/tty
    case "${_ans:-Y}" in
        [Yy]*|"") : ;;
        *) fail "LMForge is required. Install it manually, then re-run setup." ;;
    esac

    echo ""
    _install_lmforge_binary
}

_install_lmforge_binary() {
    local os arch os_name arch_name target

    os="$(uname -s)"
    arch="$(uname -m)"

    case "$os" in
        Darwin) os_name="apple-darwin" ;;
        Linux)  os_name="unknown-linux-gnu" ;;
        *) fail "Unsupported OS: $os. LMForge supports macOS and Linux." ;;
    esac

    case "$arch" in
        arm64|aarch64) arch_name="aarch64" ;;
        x86_64|amd64)  arch_name="x86_64" ;;
        *) fail "Unsupported architecture: $arch." ;;
    esac

    target="${arch_name}-${os_name}"

    echo "  Detecting platform: ${os} / ${arch} → ${target}"

    # ── Fetch latest release tag ──────────────────────────────────────────────
    local latest
    echo "  Fetching latest LMForge release..."
    if command -v curl &>/dev/null; then
        latest=$(curl -sSf "https://api.github.com/repos/${_LMFORGE_REPO}/releases/latest" \
                 | grep '"tag_name"' | sed 's/.*"tag_name": *"\([^"]*\)".*/\1/')
    elif command -v wget &>/dev/null; then
        latest=$(wget -qO- "https://api.github.com/repos/${_LMFORGE_REPO}/releases/latest" \
                 | grep '"tag_name"' | sed 's/.*"tag_name": *"\([^"]*\)".*/\1/')
    else
        fail "Neither curl nor wget found. Install one and retry."
    fi

    if [ -z "$latest" ]; then
        warn "Could not fetch latest release tag — falling back to source build."
        _install_lmforge_from_source
        return
    fi

    echo "  Latest release: ${latest}"

    # ── Download pre-built binary ─────────────────────────────────────────────
    local tarball download_url tmp_dir
    tarball="lmforge-${target}.tar.gz"
    download_url="https://github.com/${_LMFORGE_REPO}/releases/download/${latest}/${tarball}"
    tmp_dir="$(mktemp -d)"
    trap 'rm -rf "$tmp_dir"' RETURN

    echo "  Downloading ${tarball}..."
    if command -v curl &>/dev/null; then
        curl -sSfL "$download_url" -o "$tmp_dir/$tarball" 2>/dev/null || {
            warn "Pre-built binary not available for ${target}. Falling back to source build."
            _install_lmforge_from_source
            return
        }
    else
        wget -qO "$tmp_dir/$tarball" "$download_url" 2>/dev/null || {
            warn "Pre-built binary not available for ${target}. Falling back to source build."
            _install_lmforge_from_source
            return
        }
    fi

    tar -xzf "$tmp_dir/$tarball" -C "$tmp_dir"

    mkdir -p "$_LMFORGE_INSTALL_DIR"
    install -m 755 "$tmp_dir/lmforge" "$_LMFORGE_INSTALL_DIR/lmforge"

    ok "Installed lmforge ${latest} → ${_LMFORGE_INSTALL_DIR}/lmforge"

    # ── Data directories ──────────────────────────────────────────────────────
    mkdir -p "$HOME/.lmforge/models" "$HOME/.lmforge/engines" "$HOME/.lmforge/logs"

    # ── PATH hint ─────────────────────────────────────────────────────────────
    _lmforge_ensure_path
}

_install_lmforge_from_source() {
    echo "  Building LMForge from source..."

    if ! command -v cargo &>/dev/null; then
        echo "  Rust toolchain not found — installing rustup..."
        curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --no-modify-path
        # shellcheck source=/dev/null
        source "$HOME/.cargo/env"
    fi

    local tmp_dir
    tmp_dir="$(mktemp -d)"
    trap 'rm -rf "$tmp_dir"' RETURN

    git clone --depth 1 "https://github.com/${_LMFORGE_REPO}.git" "$tmp_dir"
    cargo build --release --manifest-path "$tmp_dir/Cargo.toml"
    mkdir -p "$_LMFORGE_INSTALL_DIR"
    install -m 755 "$tmp_dir/target/release/lmforge" "$_LMFORGE_INSTALL_DIR/lmforge"

    ok "Built and installed lmforge → ${_LMFORGE_INSTALL_DIR}/lmforge"

    mkdir -p "$HOME/.lmforge/models" "$HOME/.lmforge/engines" "$HOME/.lmforge/logs"
    _lmforge_ensure_path
}

_lmforge_ensure_path() {
    if command -v lmforge &>/dev/null; then
        return 0
    fi

    # Not yet in PATH — try to fix for current session
    export PATH="$_LMFORGE_INSTALL_DIR:$PATH"
    if command -v lmforge &>/dev/null; then
        warn "${_LMFORGE_INSTALL_DIR} added to PATH for this session."
    else
        warn "lmforge installed but not yet in PATH."
    fi

    # Shell-config hint
    echo ""
    echo "  Add to your shell config (~/.zshrc or ~/.bashrc):"
    echo ""
    echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
    echo ""
}
