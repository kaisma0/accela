#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ACCELA_RAW_BASE="https://raw.githubusercontent.com/kaisma0/accela/main"
ACCELA_INSTALL_MODE=""
ACTIVE_SCRIPT_DIR="$SCRIPT_DIR"
REMOTE_ROOT=""

print_usage() {
    echo "Usage: $0 [--local]" >&2
}

handle_interrupt() {
    echo ""
    echo "Installation cancelled." >&2
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

find_local_built_appimage() {
    if [ ! -d "$PROJECT_ROOT/build/dist" ]; then
        printf '%s' ""
        return 0
    fi

    find "$PROJECT_ROOT/build/dist" -maxdepth 1 -type f -name 'ACCELA-*.AppImage' 2>/dev/null | head -n1 || true
}

prepare_remote_install_bundle() {
    local repo_file

    REMOTE_ROOT="$(mktemp -d)"

    for repo_file in \
        "scripts/fix-deps.sh" \
        "scripts/install-accela.sh" \
        "scripts/install-sls.sh" \
        "src/res/logo/accela.png" \
        "src/res/version"
    do
        download_repo_file "$repo_file" "$REMOTE_ROOT/$repo_file"
    done

    chmod +x "$REMOTE_ROOT/scripts/fix-deps.sh"
    chmod +x "$REMOTE_ROOT/scripts/install-accela.sh"
    chmod +x "$REMOTE_ROOT/scripts/install-sls.sh"

    ACTIVE_SCRIPT_DIR="$REMOTE_ROOT/scripts"
}

preflight_accela_source() {
    if [ "$#" -gt 1 ]; then
        print_usage
        return 1
    fi

    if [ "$#" -eq 1 ]; then
        if [ "$1" != "--local" ]; then
            print_usage
            return 1
        fi

        local local_path=""
        local_path="$(find_local_built_appimage)"
        ACCELA_INSTALL_MODE="local"
        ACTIVE_SCRIPT_DIR="$SCRIPT_DIR"

        if [ -n "$local_path" ]; then
            echo "ACCELA source: local AppImage found at $local_path"
        else
            echo "ACCELA source: no local AppImage found in $PROJECT_ROOT/build/dist"
            echo "ACCELA source: local build will run during install"
        fi
        return 0
    fi

    ACCELA_INSTALL_MODE="remote"
    echo "ACCELA script source: remote repo scripts from kaisma0/accela"
    prepare_remote_install_bundle
}

run_accela_install() {
    case "$ACCELA_INSTALL_MODE" in
        local)
            "$ACTIVE_SCRIPT_DIR/install-accela.sh"
            return 0
            ;;
        remote)
            "$ACTIVE_SCRIPT_DIR/install-accela.sh" --latest
            return 0
            ;;
        *)
            echo "ACCELA install source was not prepared." >&2
            return 1
            ;;
    esac
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

preflight_accela_source "$@"

if run_step "Installing dependencies..." "$ACTIVE_SCRIPT_DIR/fix-deps.sh"; then
    echo "Dependencies installed."
else
    echo "Warning: Failed to install dependencies."
fi

if run_step "Running ACCELA install..." run_accela_install; then
    echo "ACCELA install completed."
else
    echo "Warning: ACCELA install reported errors; continuing."
fi

if run_step "Running SLSsteam install..." "$ACTIVE_SCRIPT_DIR/install-sls.sh"; then
    echo "SLSsteam install completed."
else
    echo "Warning: SLSsteam install reported errors; continuing."
fi

echo "All install steps finished."