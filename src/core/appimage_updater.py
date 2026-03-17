import json
import logging
import os
import re
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

GITHUB_REPO = "kaisma0/accela"
GITHUB_LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
REMOTE_INSTALL_SCRIPT_URL = (
    "https://raw.githubusercontent.com/kaisma0/accela/main/scripts/install-accela.sh"
)


@dataclass
class AppImageUpdateInfo:
    latest_version: str
    current_version: str
    appimage_url: str
    release_url: str
    asset_name: str


def _normalize_version(value: str) -> str:
    if not value:
        return ""
    value = value.strip()
    if value.lower().startswith("v"):
        value = value[1:]
    return value


def _tokenize_version(value: str) -> list:
    parts = re.findall(r"\d+|[A-Za-z]+", value)
    tokens = []
    for part in parts:
        if part.isdigit():
            tokens.append((0, int(part)))
        else:
            tokens.append((1, part.lower()))
    return tokens


def is_newer_version(latest: str, current: str) -> bool:
    latest = _normalize_version(latest)
    current = _normalize_version(current)

    if not latest or not current or latest == current:
        return False

    if latest.isdigit() and current.isdigit():
        return int(latest) > int(current)

    return _tokenize_version(latest) > _tokenize_version(current)


def _pick_appimage_asset(release_data: dict) -> Optional[dict]:
    assets = release_data.get("assets", [])
    for asset in assets:
        name = asset.get("name", "")
        if name.endswith(".AppImage"):
            return asset
    return None


def check_for_appimage_update(current_version: str) -> Optional[AppImageUpdateInfo]:
    current = _normalize_version(current_version)
    if not current or current == "unknown version":
        logger.info("Skipping update check because current version is unknown")
        return None

    try:
        response = requests.get(
            GITHUB_LATEST_RELEASE_API,
            headers={"Accept": "application/vnd.github+json"},
            timeout=8,
        )
        response.raise_for_status()
        release_data = response.json()
    except requests.RequestException as e:
        logger.warning(f"Failed to fetch latest release: {e}")
        return None
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse release response: {e}")
        return None

    latest = _normalize_version(release_data.get("tag_name", ""))
    if not is_newer_version(latest, current):
        logger.info(f"No update available (current={current}, latest={latest})")
        return None

    asset = _pick_appimage_asset(release_data)
    if not asset:
        logger.warning("Latest release found, but no AppImage asset is attached")
        return None

    appimage_url = asset.get("browser_download_url", "")
    if not appimage_url:
        logger.warning("AppImage asset does not contain a browser_download_url")
        return None

    return AppImageUpdateInfo(
        latest_version=latest,
        current_version=current,
        appimage_url=appimage_url,
        release_url=release_data.get("html_url", ""),
        asset_name=asset.get("name", "ACCELA.AppImage"),
    )


def _build_updater_script(update_info: AppImageUpdateInfo) -> Path:
    fd, script_path_str = tempfile.mkstemp(prefix="accela-update-", suffix=".sh")
    os.close(fd)
    script_path = Path(script_path_str)

    script_content = f"""#!/usr/bin/env bash
set -euo pipefail

PARENT_PID=\"${{1:-}}\"
REMOTE_INSTALLER_URL='{REMOTE_INSTALL_SCRIPT_URL}'
APPIMAGE_URL='{update_info.appimage_url}'
INSTALLED_APPIMAGE=\"$HOME/.local/share/ACCELA/ACCELA.AppImage\"

if [ -n \"$PARENT_PID\" ]; then
    while kill -0 \"$PARENT_PID\" 2>/dev/null; do
        sleep 0.25
    done
fi

curl -fsSL \"$REMOTE_INSTALLER_URL\" | bash -s -- -- \"$APPIMAGE_URL\"

if [ -x \"$INSTALLED_APPIMAGE\" ]; then
    nohup \"$INSTALLED_APPIMAGE\" >/dev/null 2>&1 &
fi
"""

    script_path.write_text(script_content, encoding="utf-8")
    script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR)
    return script_path


_LINUX_TERMINALS = [
    ["wezterm", "start", "--always-new-process", "--"],
    ["konsole", "-e"],
    ["gnome-terminal", "--"],
    ["ptyxis", "--"],
    ["alacritty", "-e"],
    ["tilix", "-e"],
    ["xfce4-terminal", "-e"],
    ["terminator", "-x"],
    ["mate-terminal", "-e"],
    ["lxterminal", "-e"],
    ["xterm", "-e"],
    ["kitty", "-e"],
]


def _find_terminal() -> Optional[list]:
    for entry in _LINUX_TERMINALS:
        if shutil.which(entry[0]):
            return entry
    return None


def launch_appimage_update(update_info: AppImageUpdateInfo) -> bool:
    try:
        updater_script = _build_updater_script(update_info)
        script_cmd = ["bash", str(updater_script), str(os.getpid())]

        terminal = _find_terminal()
        if terminal:
            term_bin = shutil.which(terminal[0])
            cmd = [term_bin] + terminal[1:] + script_cmd
        else:
            # No terminal emulator found — run headlessly as last resort
            logger.warning("No terminal emulator found; running updater without visible window")
            cmd = script_cmd

        subprocess.Popen(cmd, start_new_session=True)
        logger.info(f"Started updater via: {cmd[0]}")
        return True
    except Exception as e:
        logger.error(f"Failed to start updater process: {e}")
        return False
