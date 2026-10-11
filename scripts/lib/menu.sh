#!/bin/bash
# scripts/lib/menu.sh
# ==============================================================================
# Arrow-key menus shared by docintel.sh, logs.sh, test.sh and build.sh.
#
#   menu_select      <result-var> <title> <keys-array> <labels-array> [preselect] [--cancel]
#   menu_multiselect <result-array> <title> <keys-array> <labels-array>
#
# Keys: ↑/↓ or k/j move · Enter choose · Space toggle (multi) · a toggle all (multi)
#       q / Esc cancel (multi always; single only with --cancel) → return status 1.
# menu_select also sets MENU_INDEX to the chosen position.
#
# Written to survive the callers' `set -e` on bash 3.2 (macOS) and 5.x (Linux):
# no `((x++))` / `[[ ]] && cmd` as a final command, every `read` guarded. The
# menu renders relative to the cursor (no absolute rows), so it never overwrites
# what was printed above it, and restores the terminal on any exit path.
# Array names are passed by name (bash 3.2 has no namerefs).
# ==============================================================================

[ -n "${_MENU_LIB_LOADED:-}" ] && return 0
_MENU_LIB_LOADED=1

_MENU_SEL=$'\033[0;36m'     # cyan
_MENU_BOLD=$'\033[1m'
_MENU_DIM=$'\033[2m'
_MENU_OK=$'\033[0;32m'      # green
_MENU_NC=$'\033[0m'
_MENU_TTY=""

_menu_begin() {
    if [ ! -t 0 ]; then
        echo "menu: an interactive terminal is required" >&2
        return 2
    fi
    _MENU_TTY=$(stty -g 2>/dev/null || true)
    stty -echo -icanon min 1 time 0 2>/dev/null || true
    printf '\033[?25l'
    trap '_menu_end; exit 130' INT TERM
}

_menu_end() {
    if [ -n "$_MENU_TTY" ]; then
        stty "$_MENU_TTY" 2>/dev/null || true
    fi
    _MENU_TTY=""
    printf '\033[?25h'
    trap - INT TERM
}

# Sets _MENU_KEY to: up down enter space all quit escape other.
_menu_read_key() {
    local k="" s=""
    if ! IFS= read -r -s -n1 k; then
        _MENU_KEY=quit        # EOF on stdin
        return 0
    fi
    case "$k" in
        $'\x1b')
            # Arrow keys arrive as ESC + 2 bytes at once; the 1 s timeout only
            # matters for a bare Escape (integer -t: bash 3.2 compatible).
            IFS= read -r -s -n2 -t 1 s || true
            case "$s" in
                '[A'|'OA') _MENU_KEY=up ;;
                '[B'|'OB') _MENU_KEY=down ;;
                '')        _MENU_KEY=escape ;;
                *)         _MENU_KEY=other ;;
            esac
            ;;
        '')    _MENU_KEY=enter ;;
        ' ')   _MENU_KEY=space ;;
        k|K)   _MENU_KEY=up ;;
        j|J)   _MENU_KEY=down ;;
        a|A)   _MENU_KEY=all ;;
        q|Q)   _MENU_KEY=quit ;;
        *)     _MENU_KEY=other ;;
    esac
}

# _menu_draw <first|redraw> <title> <hint> <labels-array> <cursor> [<flags-array>]
_menu_draw() {
    local mode="$1" title="$2" hint="$3" lbls="$4" cur="$5" flags="${6:-}"
    local n i lbl flag mark
    eval "n=\${#${lbls}[@]}"
    if [ "$mode" = redraw ]; then
        printf '\033[%sA' $((n + 3))
    fi
    printf '\r\033[2K  %s%s%s\n' "$_MENU_BOLD" "$title" "$_MENU_NC"
    printf '\r\033[2K\n'
    i=0
    while [ "$i" -lt "$n" ]; do
        eval "lbl=\${${lbls}[$i]}"
        mark=""
        if [ -n "$flags" ]; then
            eval "flag=\${${flags}[$i]}"
            if [ "$flag" = 1 ]; then mark="${_MENU_OK}[x]${_MENU_NC} "; else mark="[ ] "; fi
        fi
        if [ "$i" -eq "$cur" ]; then
            printf '\r\033[2K  %s▸ %s%s%s%s\n' "$_MENU_SEL" "$mark" "$_MENU_BOLD" "$lbl" "$_MENU_NC"
        else
            printf '\r\033[2K    %s%s\n' "$mark" "$lbl"
        fi
        i=$((i + 1))
    done
    printf '\r\033[2K  %s%s%s\n' "$_MENU_DIM" "$hint" "$_MENU_NC"
}

menu_select() {
    local result_var="$1" title="$2" keys="$3" lbls="$4" cur="${5:-0}" cancel="${6:-}"
    local n hint chosen
    eval "n=\${#${keys}[@]}"
    if [ "$cur" -lt 0 ] || [ "$cur" -ge "$n" ]; then cur=0; fi
    hint="↑↓ navigate • enter select"
    if [ "$cancel" = "--cancel" ]; then hint="$hint • q/esc back"; fi

    _menu_begin || return 2
    _menu_draw first "$title" "$hint" "$lbls" "$cur"
    while :; do
        _menu_read_key
        case "$_MENU_KEY" in
            up)   if [ "$cur" -gt 0 ]; then cur=$((cur - 1)); fi ;;
            down) if [ "$cur" -lt $((n - 1)) ]; then cur=$((cur + 1)); fi ;;
            enter) break ;;
            quit|escape)
                if [ "$cancel" = "--cancel" ]; then
                    _menu_end
                    return 1
                fi
                ;;
        esac
        _menu_draw redraw "$title" "$hint" "$lbls" "$cur"
    done
    _menu_end

    eval "chosen=\${${keys}[$cur]}"
    MENU_INDEX=$cur
    printf -v "$result_var" '%s' "$chosen"
    return 0
}

menu_multiselect() {
    local result_var="$1" title="$2" keys="$3" lbls="$4"
    local n i cur=0 any_off count hint key
    local -a _menu_flags=()
    eval "n=\${#${keys}[@]}"
    i=0
    while [ "$i" -lt "$n" ]; do _menu_flags[$i]=0; i=$((i + 1)); done
    hint="↑↓ navigate • space select • a all • enter confirm • q/esc cancel"

    _menu_begin || return 2
    _menu_draw first "$title" "$hint" "$lbls" "$cur" _menu_flags
    while :; do
        _menu_read_key
        case "$_MENU_KEY" in
            up)   if [ "$cur" -gt 0 ]; then cur=$((cur - 1)); fi ;;
            down) if [ "$cur" -lt $((n - 1)) ]; then cur=$((cur + 1)); fi ;;
            space)
                if [ "${_menu_flags[$cur]}" = 1 ]; then _menu_flags[$cur]=0; else _menu_flags[$cur]=1; fi
                ;;
            all)
                any_off=0
                i=0
                while [ "$i" -lt "$n" ]; do
                    if [ "${_menu_flags[$i]}" = 0 ]; then any_off=1; fi
                    i=$((i + 1))
                done
                i=0
                while [ "$i" -lt "$n" ]; do _menu_flags[$i]=$any_off; i=$((i + 1)); done
                ;;
            enter) break ;;
            quit|escape)
                _menu_end
                return 1
                ;;
        esac
        _menu_draw redraw "$title" "$hint" "$lbls" "$cur" _menu_flags
    done
    _menu_end

    eval "$result_var=()"
    count=0
    i=0
    while [ "$i" -lt "$n" ]; do
        if [ "${_menu_flags[$i]}" = 1 ]; then
            eval "key=\${${keys}[$i]}"
            eval "$result_var[$count]=\"\$key\""
            count=$((count + 1))
        fi
        i=$((i + 1))
    done
    return 0
}
