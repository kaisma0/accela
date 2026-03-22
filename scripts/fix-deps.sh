#!/usr/bin/env bash
set -euo pipefail

# Colors (disabled if not interactive terminal)
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

SUDO_CMD=()

handle_interrupt() {
    echo ""
    echo -e "${YELLOW}Dependency installation cancelled.${NC}" >&2
    exit 130
}

trap handle_interrupt INT TERM

setup_privilege_cmd() {
    if [ "${EUID:-$(id -u)}" -eq 0 ]; then
        SUDO_CMD=()
        return 0
    fi

    if ! command -v sudo >/dev/null 2>&1; then
        echo -e "${RED}sudo is required to install system dependencies.${NC}" >&2
        exit 1
    fi

    echo "Administrator privileges are required to install dependencies..."
    if ! sudo -v; then
        echo -e "${RED}sudo authentication failed or was cancelled.${NC}" >&2
        exit 130
    fi

    SUDO_CMD=(sudo -n)
}

run_privileged() {
    if [ "${#SUDO_CMD[@]}" -eq 0 ]; then
        "$@"
    else
        "${SUDO_CMD[@]}" "$@"
    fi
}

# Detect distribution family
detect_distro_family() {
    if [ -f /etc/os-release ]; then
        source /etc/os-release
    fi

    ID="${ID:-}"
    ID_LIKE="${ID_LIKE:-}"

    # SteamOS (HoloISO) - based on Arch Linux
    if [[ "$ID" == "steamos" ]] || [[ "$ID_LIKE" =~ "steamos" ]]; then
        echo "arch"
        return 0
    fi

    # Bazzite - based on Fedora Atomic
    if [[ "$ID" == "bazzite" ]]; then
        echo "fedora"
        return 0
    fi

    # Fedora/RHEL/CentOS
    if [[ "$ID" == "fedora" || "$ID" == "rhel" || "$ID" == "centos" || \
          "$ID_LIKE" =~ "fedora" || "$ID_LIKE" =~ "rhel" ]]; then
        echo "fedora"
        return 0
    fi

    # Debian/Ubuntu
    if [[ "$ID" == "debian" || "$ID" == "ubuntu" || \
          "$ID_LIKE" =~ "debian" || "$ID_LIKE" =~ "ubuntu" ]]; then
        echo "debian"
        return 0
    fi

    # Arch Linux
    if [[ "$ID" == "arch" || "$ID_LIKE" =~ "arch" ]] || \
       [ -f "/etc/arch-release" ]; then
        echo "arch"
        return 0
    fi

    # Void Linux
   if [[ "$ID" == "void" ]]; then
        echo "void"
        return 0
    fi 

    return 1
}

# Check if running on Bazzite
is_bazzite() {
    if [ -f /etc/os-release ]; then
        source /etc/os-release
        [[ "$ID" == "bazzite" ]] && return 0
    fi
    return 1
}

# Install packages one by one, skipping unavailable ones
install_packages() {
    local packages="$1"
    local family="$2"
    local skipped=0

    # Void Linux: xbps-install handles a list in one call; handle separately
    if [ "$family" = "void" ]; then
        # enable multilib if missing (needed for 32-bit packages)
        if ! xbps-query -l | grep -q "void-repo-multilib"; then
            echo "Enabling void-repo-multilib for 32-bit support..."
            if command -v sudo &>/dev/null; then
                run_privileged xbps-install -y void-repo-multilib || true
                run_privileged xbps-install -Sy >/dev/null 2>&1
            else
                su -c "xbps-install -y void-repo-multilib 2>/dev/null || true && xbps-install -Sy >/dev/null 2>&1"
            fi
        fi

        if command -v sudo &>/dev/null; then
            run_privileged xbps-install -y $packages
        else
            su -c "xbps-install -y $packages"
        fi
        return
    fi

    # Arch: sync package DB once before installing individual packages
    if [ "$family" = "arch" ]; then
        run_privileged pacman -Sy >/dev/null 2>&1 || true
    fi

    for pkg in $packages; do
        case "$family" in
            fedora)
                if run_privileged dnf install -y --setopt=install_weak_deps=False "$pkg" 2>/dev/null; then
                    echo -e "  ${GREEN}✓${NC} $pkg"
                else
                    echo -e "  ${YELLOW}⊘${NC} $pkg (not found)"
                    skipped=$((skipped + 1))
                fi
                ;;
            debian)
                if run_privileged apt-get install -y -m "$pkg" 2>/dev/null; then
                    echo -e "  ${GREEN}✓${NC} $pkg"
                else
                    echo -e "  ${YELLOW}⊘${NC} $pkg (not found)"
                    skipped=$((skipped + 1))
                fi
                ;;
            arch)
                if run_privileged pacman -S --noconfirm "$pkg" 2>/dev/null; then
                    echo -e "  ${GREEN}✓${NC} $pkg"
                else
                    echo -e "  ${YELLOW}⊘${NC} $pkg (not found)"
                    skipped=$((skipped + 1))
                fi
                ;;
        esac
    done

    if [ $skipped -gt 0 ]; then
        echo -e "  ${YELLOW}Skipped $skipped unavailable packages${NC}"
    fi
}

# Main function
install_dependencies() {
    local FAMILY

    echo -e "${GREEN}Dependencies Installer${NC}"
    echo ""

    FAMILY=$(detect_distro_family)
    if [ -z "$FAMILY" ]; then
        echo -e "${RED}Could not detect distribution family.${NC}"
        echo "Defaulting to debian."
        FAMILY="debian"
    fi

    echo -e "Detected system: ${GREEN}$FAMILY${NC} family"

    setup_privilege_cmd

    # Bazzite check
    if is_bazzite; then
        echo -e "${YELLOW}Bazzite detected${NC}"
        echo "Bazzite uses rpm-ostree. Some packages may need to be installed via:"
        echo "  - rpm-ostree install <package> (requires reboot)"
        echo "  - distrobox"
        echo "  - homebrew"
        echo ""
    fi

    echo ""
    echo "Installing common dependencies..."

    # Enable i386 for Debian/Ubuntu
    if [ "$FAMILY" = "debian" ]; then
        if ! dpkg --print-foreign-architectures 2>/dev/null | grep -q i386; then
            echo "Enabling i386 architecture..."
            run_privileged dpkg --add-architecture i386
            run_privileged apt update || true
        fi
    fi

    # Install 7-zip (needed for both)
    case "$FAMILY" in
        fedora)
            install_packages "p7zip p7zip-plugins" "$FAMILY"
            ;;
        debian)
            install_packages "p7zip-full" "$FAMILY"
            ;;
        arch)
            install_packages "p7zip 7zip" "$FAMILY"
            ;;
        void)
            install_packages "p7zip 7zip" "$FAMILY"
            ;;
    esac

    echo ""
    echo "Installing ACCELA dependencies..."

    # ACCELA dependencies
    case "$FAMILY" in
        fedora)
            ACCELA_PACKAGES="python3 xcb-util-cursor libnotify git curl wget"
            install_packages "$ACCELA_PACKAGES" "$FAMILY"
            ;;
        debian)
            ACCELA_PACKAGES="python3 python3-venv libxcb-cursor0 libnotify-bin git curl wget"
            install_packages "$ACCELA_PACKAGES" "$FAMILY"
            ;;
        arch)
            ACCELA_PACKAGES="python xcb-util-cursor libnotify git curl wget"
            install_packages "$ACCELA_PACKAGES" "$FAMILY"
            ;;
        void)
            ACCELA_PACKAGES="python3 xcb-util-cursor libnotify git curl wget"
            install_packages "$ACCELA_PACKAGES" "$FAMILY"
            ;;
    esac

    echo ""
    echo "Installing SLSsteam dependencies..."

    # SLSsteam dependencies (32-bit)
    case "$FAMILY" in
        fedora)
            SLS_PACKAGES="libcurl-devel libcurl libcurl.i686 openssl-libs.i686 cryptopp cryptopp.i686"
            install_packages "$SLS_PACKAGES" "$FAMILY"
            ;;
        debian)
            SLS_PACKAGES="libc6:i386 libcurl4:i386 libcurl4t64:i386 libssl3:i386 libssl3t64:i386 libcrypto++8t64:i386 libcrypto++8:i386"
            install_packages "$SLS_PACKAGES" "$FAMILY"
            ;;
        arch)
            SLS_PACKAGES="lib32-glibc lib32-openssl lib32-curl curl"
            install_packages "$SLS_PACKAGES" "$FAMILY"
            ;;
        void)
            SLS_PACKAGES="libcurl-32bit openssl-32bit"
            install_packages "$SLS_PACKAGES" "$FAMILY"
            ;;
    esac

    echo ""
    echo "Ensuring latest .NET 10 runtime is installed..."

    # Ensure .NET 10 is installed and up-to-date in ~/.dotnet
    DOTNET_ROOT="$HOME/.dotnet"
    mkdir -p "$DOTNET_ROOT"
    
    echo "Downloading .NET installer script..."
    if command -v curl >/dev/null 2>&1; then
        curl -sSL -o "$DOTNET_ROOT/dotnet-install.sh" "https://dot.net/v1/dotnet-install.sh"
    else
        wget -q -O "$DOTNET_ROOT/dotnet-install.sh" "https://dot.net/v1/dotnet-install.sh"
    fi
    
    if [ -f "$DOTNET_ROOT/dotnet-install.sh" ]; then
        chmod +x "$DOTNET_ROOT/dotnet-install.sh"
        echo "Checking/Updating .NET 10 runtime..."
        DOTNET_ROOT="$DOTNET_ROOT" "$DOTNET_ROOT/dotnet-install.sh" --channel 10.0 --runtime dotnet || echo -e "  ${RED}Failed to install/update .NET 10${NC}"
        rm -f "$DOTNET_ROOT/dotnet-install.sh"
        echo -e "  ${GREEN}✓${NC} .NET 10 runtime is up to date"
    else
        echo -e "  ${RED}Failed to download .NET installer${NC}"
    fi

    echo ""
    echo -e "${GREEN}Dependencies installation completed!${NC}"
    echo ""
}

install_dependencies "$@"
