#!/usr/bin/env bash
set -euo pipefail

if [ -t 1 ] && [ -t 0 ]; then
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    RED='\033[0;31m'
    NC='\033[0m'
else
    GREEN=''
    YELLOW=''
    RED=''
    NC=''
fi

INSTALL_DIR="$HOME/.local/share/ACCELA"
CONFIG_DIR="$HOME/.config/kaisma0"

log_info() { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

ask_confirm() {
    local prompt="$1"
    local default="${2:-n}"
    local response

    if [ "${FORCE_YES:-0}" = "1" ]; then
        return 0
    fi

    while true; do
        if [ "$default" = "y" ]; then
            read -r -p "$prompt [Y/n]: " response
        else
            read -r -p "$prompt [y/N]: " response
        fi

        case "${response:-$default}" in
            [Yy]*) return 0 ;;
            [Nn]*) return 1 ;;
            *) echo "Please answer yes or no." ;;
        esac
    done
}

remove_core_files() {
    rm -rf "$INSTALL_DIR/src" 2>/dev/null || true
    rm -f "$INSTALL_DIR/run.sh" 2>/dev/null || true
    rm -rf "$INSTALL_DIR/.venv" 2>/dev/null || true
    rm -f "$INSTALL_DIR/ACCELA" 2>/dev/null || true
    rm -f "$INSTALL_DIR/ACCELA.AppImage" 2>/dev/null || true
    rm -f "$INSTALL_DIR/.version" 2>/dev/null || true
    rm -f "$INSTALL_DIR/requirements.txt" 2>/dev/null || true
}

remove_data_folders() {
    local data_folders=(SLScheevo SLSsteam steamless gifs depots logs)
    local folder
    for folder in "${data_folders[@]}"; do
        rm -rf "$INSTALL_DIR/$folder" 2>/dev/null || true
    done
    rm -f "$INSTALL_DIR/steam_headers.db" 2>/dev/null || true
}

remove_manifest_cache() {
    rm -rf "$INSTALL_DIR/morrenus_manifests" 2>/dev/null || true
}

remove_desktop_entries() {
    rm -f "$HOME/.local/share/applications/accela.desktop" 2>/dev/null || true
    rm -f "$HOME/.local/bin/accela" 2>/dev/null || true
    local removed_icon=0
    if [ -f "$HOME/.local/share/icons/hicolor/256x256/apps/accela.png" ]; then
        rm -f "$HOME/.local/share/icons/hicolor/256x256/apps/accela.png" 2>/dev/null || true
        removed_icon=1
    fi

    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
    fi

    if [ "$removed_icon" = "1" ]; then
        if [ -z "${XDG_CURRENT_DESKTOP:-}" ] || [[ "$XDG_CURRENT_DESKTOP" != *"KDE"* ]]; then
            command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
            command -v gtk4-update-icon-cache >/dev/null 2>&1 && gtk4-update-icon-cache "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
        fi
    fi
}

cleanup_empty_install_dir() {
    if [ -d "$INSTALL_DIR" ] && [ -z "$(ls -A "$INSTALL_DIR" 2>/dev/null || true)" ]; then
        rmdir "$INSTALL_DIR" 2>/dev/null || true
    fi
}

main() {
    log_info "ACCELA uninstall starting"

    if [ ! -d "$INSTALL_DIR" ]; then
        log_warn "ACCELA installation not found at $INSTALL_DIR"
        if ! ask_confirm "Continue anyway?" "n"; then
            log_info "Uninstall cancelled"
            exit 0
        fi
    fi

    remove_core_files
    log_info "Removed ACCELA core files"

    if ask_confirm "Delete ACCELA data folders (depots/logs/cache/etc)?" "n"; then
        remove_data_folders
        log_info "Removed ACCELA data folders"
    else
        log_info "Keeping ACCELA data folders"
    fi

    if [ -d "$INSTALL_DIR/morrenus_manifests" ]; then
        if ask_confirm "Delete manifest cache in ~/.local/share/ACCELA/morrenus_manifests?" "n"; then
            remove_manifest_cache
            log_info "Removed manifest cache"
        else
            log_info "Keeping manifest cache"
        fi
    fi

    if ask_confirm "Delete ACCELA settings in ~/.config/kaisma0?" "n"; then
        rm -rf "$CONFIG_DIR" 2>/dev/null || true
        log_info "Removed settings"
    else
        log_info "Keeping settings"
    fi

    remove_desktop_entries
    cleanup_empty_install_dir
    log_info "ACCELA uninstall completed"
}

main "$@"
