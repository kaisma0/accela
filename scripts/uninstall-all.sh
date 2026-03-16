#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
ACCELA_RAW_BASE="https://raw.githubusercontent.com/kaisma0/accela/main"
ACTIVE_SCRIPT_DIR="$SCRIPT_DIR"
REMOTE_ROOT=""

print_usage() {
    echo "Usage: $0 [--local]" >&2
}

handle_interrupt() {
    echo ""
    echo "Uninstall cancelled." >&2
    exit 130
}

trap handle_interrupt INT TERM

cleanup_remote_root() {
    if [ -n "$REMOTE_ROOT" ] && [ -d "$REMOTE_ROOT" ]; then
        rm -rf "$REMOTE_ROOT"
    fi
}

trap cleanup_remote_root EXIT

download_file() {
    local url="$1"
    local output_path="$2"

    if command -v curl >/dev/null 2>&1; then
        curl -L --fail --output "$output_path" "$url"
        return 0
    fi

    if command -v wget >/dev/null 2>&1; then
        wget -O "$output_path" "$url"
        return 0
    fi

    echo "Need curl or wget to download remote scripts" >&2
    return 1
}

download_repo_file() {
    local repo_path="$1"
    local output_path="$2"

    mkdir -p "$(dirname "$output_path")"
    download_file "$ACCELA_RAW_BASE/$repo_path" "$output_path"
}

prepare_script_source() {
    if [ "$#" -gt 1 ]; then
        print_usage
        return 1
    fi

    if [ "$#" -eq 1 ]; then
        if [ "$1" != "--local" ]; then
            print_usage
            return 1
        fi

        ACTIVE_SCRIPT_DIR="$SCRIPT_DIR"
        echo "Uninstall script source: local scripts"
        return 0
    fi

    echo "Uninstall script source: remote repo scripts from kaisma0/accela"
    REMOTE_ROOT="$(mktemp -d)"
    download_repo_file "scripts/uninstall-accela.sh" "$REMOTE_ROOT/scripts/uninstall-accela.sh"
    download_repo_file "scripts/uninstall-sls.sh" "$REMOTE_ROOT/scripts/uninstall-sls.sh"
    chmod +x "$REMOTE_ROOT/scripts/uninstall-accela.sh"
    chmod +x "$REMOTE_ROOT/scripts/uninstall-sls.sh"
    ACTIVE_SCRIPT_DIR="$REMOTE_ROOT/scripts"
}

run_step() {
    local label="$1"
    shift
    local exit_code=0

    echo "$label"
    if "$@"; then
        return 0
    fi

    exit_code=$?
    if [ "$exit_code" -eq 130 ]; then
        echo "Operation cancelled." >&2
        exit 130
    fi

    return "$exit_code"
}

prepare_script_source "$@"

if run_step "Running ACCELA uninstall..." "$ACTIVE_SCRIPT_DIR/uninstall-accela.sh"; then
    echo "ACCELA uninstall completed."
else
    echo "Warning: ACCELA uninstall reported errors; continuing."
fi

if run_step "Running SLSsteam uninstall..." "$ACTIVE_SCRIPT_DIR/uninstall-sls.sh"; then
    echo "SLSsteam uninstall completed."
else
    echo "Warning: SLSsteam uninstall reported errors; continuing."
fi

echo "All uninstall steps finished."
