#!/usr/bin/env bash
set -euo pipefail

if [ -t 1 ] && [ -t 0 ]; then
    GREEN='\033[0;32m'
    RED='\033[0;31m'
    YELLOW='\033[1;33m'
    NC='\033[0m'
else
    GREEN=''
    RED=''
    YELLOW=''
    NC=''
fi

INSTALL_DIR="$HOME/.local/share/ACCELA"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_DIR="$HOME/.config/kaisma0"
ICON_SOURCE_PATH="$PROJECT_ROOT/src/res/logo/accela.png"

log_info() { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

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

    log_error "Need curl or wget to download AppImage override"
    return 1
}

ensure_accela_conf_general() {
    local conf_path="$CONFIG_DIR/ACCELA.conf"
    local conf_dir
    local tmp_file

    conf_dir="$(dirname "$conf_path")"

    mkdir -p "$conf_dir" 2>/dev/null || return 0

    if [ ! -f "$conf_path" ]; then
        cat > "$conf_path" <<'EOF'
[General]
auto_skip_single_choice=true
library_mode=true
max_downloads=16
sls_config_management=true
slssteam_mode=true
use_steamless=true
EOF
        return 0
    fi

    tmp_file="$(mktemp)"

    if ! awk '
BEGIN {
    in_general = 0
    saw_general = 0
    saw_auto_skip_single_choice = 0
    saw_library_mode = 0
    saw_max_downloads = 0
    saw_sls_config_management = 0
    saw_slssteam_mode = 0
    saw_use_steamless = 0
}
function print_required_missing() {
    if (!saw_auto_skip_single_choice) print "auto_skip_single_choice=true"
    if (!saw_library_mode) print "library_mode=true"
    if (!saw_max_downloads) print "max_downloads=16"
    if (!saw_sls_config_management) print "sls_config_management=true"
    if (!saw_slssteam_mode) print "slssteam_mode=true"
    if (!saw_use_steamless) print "use_steamless=true"
}
/^\[[^]]+\]$/ {
    if (in_general) {
        print_required_missing()
        in_general = 0
    }
    if ($0 == "[General]") {
        saw_general = 1
        in_general = 1
        saw_auto_skip_single_choice = 0
        saw_library_mode = 0
        saw_max_downloads = 0
        saw_sls_config_management = 0
        saw_slssteam_mode = 0
        saw_use_steamless = 0
    }
    print $0
    next
}
{
    if (in_general) {
        if ($0 ~ /^auto_skip_single_choice[[:space:]]*=/) { if (!saw_auto_skip_single_choice) { print "auto_skip_single_choice=true"; saw_auto_skip_single_choice = 1 } ; next }
        if ($0 ~ /^library_mode[[:space:]]*=/) { if (!saw_library_mode) { print "library_mode=true"; saw_library_mode = 1 } ; next }
        if ($0 ~ /^max_downloads[[:space:]]*=/) { if (!saw_max_downloads) { print "max_downloads=16"; saw_max_downloads = 1 } ; next }
        if ($0 ~ /^sls_config_management[[:space:]]*=/) { if (!saw_sls_config_management) { print "sls_config_management=true"; saw_sls_config_management = 1 } ; next }
        if ($0 ~ /^slssteam_mode[[:space:]]*=/) { if (!saw_slssteam_mode) { print "slssteam_mode=true"; saw_slssteam_mode = 1 } ; next }
        if ($0 ~ /^use_steamless[[:space:]]*=/) { if (!saw_use_steamless) { print "use_steamless=true"; saw_use_steamless = 1 } ; next }
    }
    print $0
}
END {
    if (in_general) {
        print_required_missing()
    } else if (!saw_general) {
        print ""
        print "[General]"
        print "auto_skip_single_choice=true"
        print "library_mode=true"
        print "max_downloads=16"
        print "sls_config_management=true"
        print "slssteam_mode=true"
        print "use_steamless=true"
    }
}
' "$conf_path" > "$tmp_file"; then
        mv "$tmp_file" "$conf_path" 2>/dev/null || true
    else
        rm -f "$tmp_file" 2>/dev/null || true
    fi
}

find_newest_built_appimage() {
    local result

    if [ ! -d "$PROJECT_ROOT/build/dist" ]; then
        printf '%s' ""
        return 0
    fi

    result="$({
        find "$PROJECT_ROOT/build/dist" -maxdepth 1 -type f -name 'ACCELA-*.AppImage' 2>/dev/null
    } | while read -r f; do printf '%s\t%s\n' "$(stat -c %Y "$f" 2>/dev/null || echo 0)" "$f"; done | sort -rn | head -n1 | cut -f2- || true)"

    printf '%s' "$result"
}

read_source_version() {
    local version_file="$PROJECT_ROOT/src/res/version"

    if [ ! -f "$version_file" ]; then
        printf '%s' ""
        return 0
    fi

    tr -d '\r\n' < "$version_file"
}

extract_appimage_version_from_name() {
    local appimage_path="$1"
    local appimage_name=""

    appimage_name="$(basename "$appimage_path")"

    if [[ "$appimage_name" =~ ^ACCELA-(.+)-[^-]+\.AppImage$ ]]; then
        printf '%s' "${BASH_REMATCH[1]}"
        return 0
    fi

    printf '%s' ""
}

version_is_newer() {
    local candidate_version="$1"
    local current_version="$2"

    if [ -z "$candidate_version" ] || [ -z "$current_version" ] || [ "$candidate_version" = "$current_version" ]; then
        return 1
    fi

    [ "$(printf '%s\n%s\n' "$candidate_version" "$current_version" | sort -V | tail -n1)" = "$candidate_version" ]
}

find_appimage_in_tree() {
    local search_dir="$1"
    local result

    result="$(find "$search_dir" -type f -name '*.AppImage' 2>/dev/null | head -n1)"
    printf '%s' "$result"
}

prepare_override_payload() {
    local override_source="$1"
    local payload_dir="$2"
    local temp_download=""
    local appimage_path=""
    local icon_source=""

    mkdir -p "$payload_dir"

    if [[ "$override_source" =~ ^https?:// ]]; then
        temp_download="$payload_dir/override-download"
        log_info "Downloading ACCELA override from $override_source"
        download_file "$override_source" "$temp_download"
    else
        temp_download="$override_source"
        if [ ! -f "$temp_download" ]; then
            log_error "Override source not found: $override_source"
            return 1
        fi
    fi

    if tar -tzf "$temp_download" >/dev/null 2>&1; then
        local extract_dir="$payload_dir/extracted"
        mkdir -p "$extract_dir"
        tar -xzf "$temp_download" -C "$extract_dir"
        appimage_path="$(find_appimage_in_tree "$extract_dir")"
        icon_source="$(find "$extract_dir" -type f -name 'accela.png' 2>/dev/null | head -n1)"
    else
        appimage_path="$temp_download"
    fi

    if [ -z "$appimage_path" ] || [ ! -f "$appimage_path" ]; then
        log_error "Could not find an AppImage in override source"
        return 1
    fi

    cp -f "$appimage_path" "$payload_dir/ACCELA.AppImage"
    chmod +x "$payload_dir/ACCELA.AppImage"

    if [ -n "$icon_source" ] && [ -f "$icon_source" ]; then
        cp -f "$icon_source" "$payload_dir/accela.png"
    elif [ -f "$ICON_SOURCE_PATH" ]; then
        cp -f "$ICON_SOURCE_PATH" "$payload_dir/accela.png"
    fi
}

finalize_install_from_payload() {
    local payload_dir="$1"

    cleanup_existing
    install_appimage "$payload_dir"

    mkdir -p "$INSTALL_DIR"
    if [ -f "$PROJECT_ROOT/src/res/version" ]; then
        cp -f "$PROJECT_ROOT/src/res/version" "$INSTALL_DIR/.version" 2>/dev/null || true
    fi
    ensure_accela_conf_general

    log_info "ACCELA installed successfully"
}

install_from_override_source() {
    local override_source="$1"
    local payload_dir

    payload_dir="$(mktemp -d)"
    prepare_override_payload "$override_source" "$payload_dir"

    finalize_install_from_payload "$payload_dir"
    rm -rf "$payload_dir"

    return 0
}

install_from_built_appimage() {
    local appimage_path=""
    local payload_dir=""
    local source_version=""
    local appimage_version=""

    appimage_path="$(find_newest_built_appimage)"

    if [ -n "$appimage_path" ]; then
        source_version="$(read_source_version)"
        appimage_version="$(extract_appimage_version_from_name "$appimage_path")"

        if [ -n "$source_version" ] && [ -n "$appimage_version" ] && version_is_newer "$source_version" "$appimage_version"; then
            if [ -x "$PROJECT_ROOT/build_appimage.sh" ]; then
                log_info "Source version $source_version is newer than built AppImage version $appimage_version; rebuilding AppImage"
                (cd "$PROJECT_ROOT" && ./build_appimage.sh)
                appimage_path="$(find_newest_built_appimage)"
            else
                log_warn "Source version $source_version is newer than built AppImage version $appimage_version, but build_appimage.sh is not executable"
            fi
        fi
    fi

    if [ -z "$appimage_path" ]; then
        if [ -x "$PROJECT_ROOT/build_appimage.sh" ]; then
            log_info "No built AppImage found in build/dist; building AppImage now"
            (cd "$PROJECT_ROOT" && ./build_appimage.sh)
            appimage_path="$(find_newest_built_appimage)"
        fi
    fi

    if [ -z "$appimage_path" ]; then
        return 1
    fi

    payload_dir="$(mktemp -d)"
    cp -f "$appimage_path" "$payload_dir/ACCELA.AppImage"
    chmod +x "$payload_dir/ACCELA.AppImage"

    if [ -f "$ICON_SOURCE_PATH" ]; then
        cp -f "$ICON_SOURCE_PATH" "$payload_dir/accela.png"
    fi

    finalize_install_from_payload "$payload_dir"
    rm -rf "$payload_dir"

    return 0
}

cleanup_existing() {
    rm -rf "$INSTALL_DIR/bin" 2>/dev/null || true
    rm -rf "$INSTALL_DIR/src" 2>/dev/null || true
    rm -f "$INSTALL_DIR/run.sh" 2>/dev/null || true
    rm -f "$INSTALL_DIR/requirements.txt" 2>/dev/null || true
    rm -rf "$INSTALL_DIR/.venv" 2>/dev/null || true
    rm -f "$INSTALL_DIR/ACCELA" 2>/dev/null || true
    rm -f "$INSTALL_DIR/ACCELA.AppImage" 2>/dev/null || true
}

install_desktop_entry() {
    local exec_path="$1"
    local source_dir="$2"

    mkdir -p "$HOME/.local/share/applications"
    cat > "$HOME/.local/share/applications/accela.desktop" <<EOF
[Desktop Entry]
Version=2.0
Name=ACCELA
Comment=ACCELA
Exec=$exec_path %u
Icon=accela
Terminal=false
Type=Application
Categories=Utility;Application;
MimeType=x-scheme-handler/accela;
EOF

    if [ -f "$source_dir/accela.png" ]; then
        mkdir -p "$HOME/.local/share/icons/hicolor/256x256/apps"
        cp -f "$source_dir/accela.png" "$HOME/.local/share/icons/hicolor/256x256/apps/accela.png"
        if [ -z "${XDG_CURRENT_DESKTOP:-}" ] || [[ "$XDG_CURRENT_DESKTOP" != *"KDE"* ]]; then
            command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
            command -v gtk4-update-icon-cache >/dev/null 2>&1 && gtk4-update-icon-cache "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
        fi
    fi

    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
    fi
    if command -v xdg-mime >/dev/null 2>&1; then
        xdg-mime default accela.desktop x-scheme-handler/accela || true
    fi
}

install_cli_wrapper() {
    local bin_dir="$HOME/.local/bin"
    mkdir -p "$bin_dir"
    cat > "$bin_dir/accela" <<'EOF'
#!/usr/bin/env bash
set -eu

INSTALL_DIR="$HOME/.local/share/ACCELA"
APPIMAGE="$INSTALL_DIR/ACCELA.AppImage"
RUNSH="$INSTALL_DIR/run.sh"
BIN="$INSTALL_DIR/ACCELA"

if [ -x "$APPIMAGE" ]; then
    exec "$APPIMAGE" "$@"
elif [ -x "$RUNSH" ]; then
    exec "$RUNSH" "$@"
elif [ -x "$BIN" ]; then
    exec "$BIN" "$@"
else
    echo "ACCELA not found in $INSTALL_DIR" >&2
    exit 1
fi
EOF
    chmod +x "$bin_dir/accela"
}

install_appimage() {
    local source_dir="$1"
    mkdir -p "$INSTALL_DIR"
    cp -f "$source_dir/ACCELA.AppImage" "$INSTALL_DIR/ACCELA.AppImage"
    chmod +x "$INSTALL_DIR/ACCELA.AppImage"
    install_desktop_entry "$INSTALL_DIR/ACCELA.AppImage" "$source_dir"
    install_cli_wrapper
}

install_accela() {
    if [ "$#" -gt 0 ]; then
        if [ "$1" != "--" ] || [ "$#" -ne 2 ]; then
            echo "Usage: $0 [-- <local-appimage|local-tar.gz|http(s)-url>]" >&2
            return 1
        fi

        install_from_override_source "$2"
        return 0
    fi

    if ! install_from_built_appimage; then
        echo -e "${RED}Error: no built ACCELA AppImage found.${NC}"
        echo "Expected a built AppImage in $PROJECT_ROOT/build/dist"
        echo "Or run: $0 -- <local-appimage|local-tar.gz|http(s)-url>"
        return 1
    fi
}

install_accela "$@"
