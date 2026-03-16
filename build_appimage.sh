#!/usr/bin/env bash
set -euo pipefail

# Build ACCELA AppImage from source-only tree.
# Output: ./build/dist/ACCELA-<version>-<arch>.AppImage

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_NAME="ACCELA"
BUILD_DIR="$SCRIPT_DIR/build"
APPDIR="$BUILD_DIR/${APP_NAME}.AppDir"
DIST_DIR="$BUILD_DIR/dist"

VERSION="$(cat "$SCRIPT_DIR/src/res/version" 2>/dev/null || date +%Y%m%d%H%M%S)"
ARCH="$(uname -m)"
OUTPUT_APPIMAGE="$DIST_DIR/${APP_NAME}-${VERSION}-${ARCH}.AppImage"

require_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "[ERROR] Missing required command: $1" >&2
        exit 1
    fi
}

log() {
    echo "[INFO] $*"
}

error() {
    echo "[ERROR] $*" >&2
}

prepare_appimagetool() {
    if command -v appimagetool >/dev/null 2>&1; then
        APPIMAGETOOL_BIN="$(command -v appimagetool)"
        return
    fi

    mkdir -p "$BUILD_DIR/tools"
    APPIMAGETOOL_BIN="$BUILD_DIR/tools/appimagetool.AppImage"

    if [ ! -x "$APPIMAGETOOL_BIN" ]; then
        log "appimagetool not found; downloading local copy"
        require_cmd curl
        curl -fL --retry 3 --retry-delay 2 \
            -o "$APPIMAGETOOL_BIN" \
            "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-${ARCH}.AppImage"
        chmod +x "$APPIMAGETOOL_BIN"
        "$APPIMAGETOOL_BIN" --version >/dev/null 2>&1 || {
            error "Downloaded appimagetool is not executable or invalid: $APPIMAGETOOL_BIN"
            exit 1
        }
    fi
}

copy_icon() {
    # Preferred: local icon next to this script.
    if [ -f "$SCRIPT_DIR/src/res/logo/accela.png" ]; then
        cp -f "$SCRIPT_DIR/src/res/logo/accela.png" "$APPDIR/accela.png"
        return
    fi

    log "Icon not found; generating fallback accela.png"
    require_cmd base64
    cat <<'EOF' | base64 -d > "$APPDIR/accela.png"
iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9oN2yW4AAAAASUVORK5CYII=
EOF
}

build_python_env() {
    require_cmd python3

    log "Creating bundled virtual environment"
    python3 -m venv --copies "$APPDIR/bin/.venv"

    if [ -L "$APPDIR/bin/.venv/bin/python3" ]; then
        error "Bundled python3 is a symlink; this AppImage would not be portable"
        exit 1
    fi

    # shellcheck disable=SC1091
    source "$APPDIR/bin/.venv/bin/activate"
    python -m pip install --upgrade pip wheel setuptools
    python -m pip install -r "$SCRIPT_DIR/requirements.txt"
    deactivate
}

build_appdir() {
    log "Preparing AppDir at $APPDIR"
    rm -rf "$APPDIR"
    mkdir -p "$APPDIR/bin"

    cp -a "$SCRIPT_DIR/src" "$APPDIR/bin/src"
    cp -a "$SCRIPT_DIR/run.sh" "$APPDIR/bin/run.sh"
    cp -a "$SCRIPT_DIR/requirements.txt" "$APPDIR/bin/requirements.txt"
    chmod +x "$APPDIR/bin/run.sh"

    copy_icon

    cat > "$APPDIR/ACCELA.desktop" <<'EOF'
[Desktop Entry]
Name=ACCELA
Comment=ACCELA
Exec=run.sh
Icon=accela
Terminal=false
Type=Application
Categories=Utility;Game;
MimeType=x-scheme-handler/accela;
EOF

    cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
cd "$HERE/bin"
export APPIMAGE=1
exec "./run.sh" "$@"
EOF
    chmod +x "$APPDIR/AppRun"

    ln -sf accela.png "$APPDIR/.DirIcon"
}

package_appimage() {
    mkdir -p "$DIST_DIR"
    rm -f "$OUTPUT_APPIMAGE"

    log "Packaging AppImage"
    # Improve compatibility on hosts without FUSE by forcing extract-and-run.
    APPIMAGE_EXTRACT_AND_RUN=1 ARCH="$ARCH" "$APPIMAGETOOL_BIN" "$APPDIR" "$OUTPUT_APPIMAGE"

    log "Build complete: $OUTPUT_APPIMAGE"
}

main() {
    require_cmd chmod
    [ -f "$SCRIPT_DIR/run.sh" ] || { echo "[ERROR] Missing run.sh in $SCRIPT_DIR" >&2; exit 1; }
    [ -f "$SCRIPT_DIR/requirements.txt" ] || { echo "[ERROR] Missing requirements.txt in $SCRIPT_DIR" >&2; exit 1; }
    [ -d "$SCRIPT_DIR/src" ] || { echo "[ERROR] Missing src directory in $SCRIPT_DIR" >&2; exit 1; }
    prepare_appimagetool
    build_appdir
    build_python_env
    package_appimage
}

main "$@"
