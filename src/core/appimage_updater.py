from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import requests

from PyQt6.QtCore import QThread, pyqtSignal, Qt, QCoreApplication
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from ui.custom_titlebar import CustomTitleBar

logger = logging.getLogger(__name__)

GITHUB_REPO = "kaisma0/accela"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
INSTALL_SCRIPT_URL = (
    f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/scripts/install-accela.sh"
)

_DOWNLOAD_CHUNK_SIZE = 128 * 1024

_GITHUB_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    download_url: str
    size: int
    content_type: str


@dataclass(frozen=True)
class UpdateInfo:
    current_version: str
    latest_version: str
    release_url: str
    appimage_asset: ReleaseAsset


class UpdaterError(Exception):
    pass


class NetworkError(UpdaterError):
    pass


class ParseError(UpdaterError):
    pass


class DownloadError(UpdaterError):
    pass


def is_update_available(latest_tag: str, current_tag: str) -> bool:
    try:
        return int(latest_tag.strip()) > int(current_tag.strip())
    except (ValueError, TypeError):
        return False


def _fetch_latest_release() -> dict:
    try:
        response = requests.get(GITHUB_API_URL, headers=_GITHUB_HEADERS, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.HTTPError as exc:
        raise NetworkError(
            f"GitHub API returned HTTP {exc.response.status_code}"
        ) from exc
    except requests.RequestException as exc:
        raise NetworkError(f"Network request failed: {exc}") from exc
    except ValueError as exc:
        raise ParseError(f"Invalid JSON from GitHub API: {exc}") from exc


def _pick_appimage_asset(release: dict) -> Optional[ReleaseAsset]:
    for asset in release.get("assets", []):
        if asset.get("name", "").endswith(".AppImage"):
            return ReleaseAsset(
                name=asset["name"],
                download_url=asset["browser_download_url"],
                size=asset.get("size", 0),
                content_type=asset.get("content_type", ""),
            )
    return None


def check_for_update(current_version: str) -> Optional[UpdateInfo]:
    current_version = current_version.strip()
    if not current_version or current_version.lower() == "unknown version":
        logger.info("Skipping update check: current version is unknown.")
        return None

    release = _fetch_latest_release()
    latest_tag: str = release.get("tag_name", "")

    if not is_update_available(latest_tag, current_version):
        logger.info(
            "No update available (current=%s, latest=%s).", current_version, latest_tag
        )
        return None

    asset = _pick_appimage_asset(release)
    if asset is None:
        raise ParseError(f"Release {latest_tag!r} has no .AppImage asset attached.")

    return UpdateInfo(
        current_version=current_version,
        latest_version=latest_tag.lstrip("vV"),
        release_url=release.get("html_url", ""),
        appimage_asset=asset,
    )


ProgressCallback = Callable[[int, int], None]


def download_asset(
    asset: ReleaseAsset,
    dest: Path,
    *,
    on_progress: Optional[ProgressCallback] = None,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    total = asset.size

    try:
        with requests.get(asset.download_url, stream=True, timeout=60) as response:
            response.raise_for_status()
            total = int(response.headers.get("Content-Length", total))
            with dest.open("wb") as fh:
                for chunk in response.iter_content(chunk_size=_DOWNLOAD_CHUNK_SIZE):
                    fh.write(chunk)
                    downloaded += len(chunk)
                    if on_progress is not None:
                        on_progress(downloaded, total)
    except requests.RequestException as exc:
        dest.unlink(missing_ok=True)
        raise DownloadError(f"Download failed: {exc}") from exc

    logger.info("Downloaded %d bytes → %s", downloaded, dest)
    return dest


def _download_install_script() -> Path:
    tmp = tempfile.NamedTemporaryFile(
        prefix="accela-install-", suffix=".sh", delete=False
    )
    script_path = Path(tmp.name)
    tmp.close()

    try:
        response = requests.get(INSTALL_SCRIPT_URL, timeout=10)
        response.raise_for_status()
        script_path.write_bytes(response.content)
    except (requests.RequestException, OSError) as exc:
        script_path.unlink(missing_ok=True)
        raise DownloadError(f"Failed to download install script: {exc}") from exc

    logger.info("Install script downloaded to %s", script_path)
    return script_path


def delegate_install_and_quit(downloaded_appimage: Path, script_path: Path) -> None:
    logger.info(
        "Launching installer: bash %s --relaunch -- %s (detached)",
        script_path,
        downloaded_appimage,
    )
    env = os.environ.copy()
    for var in (
        "APPDIR",
        "APPIMAGE",
        "LD_LIBRARY_PATH",
        "LD_PRELOAD",
        "ARGV0",
        "OWD",
        "APPIMAGE_EXTRACT_AND_RUN",
    ):
        env.pop(var, None)

    subprocess.Popen(
        ["bash", str(script_path), "--relaunch", "--", str(downloaded_appimage)],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )

    app = QCoreApplication.instance()
    if app is not None:
        app.quit()


class UpdateCheckWorker(QThread):
    update_available = pyqtSignal(object)  # UpdateInfo
    no_update = pyqtSignal()
    check_failed = pyqtSignal(str)

    def __init__(self, current_version: str, parent=None) -> None:
        super().__init__(parent)
        self._current_version = current_version

    def run(self) -> None:
        try:
            info = check_for_update(self._current_version)
        except UpdaterError as exc:
            logger.warning("Update check failed: %s", exc)
            self.check_failed.emit(str(exc))
            return
        self.no_update.emit() if info is None else self.update_available.emit(info)



    def run(self) -> None:
        tmp = tempfile.NamedTemporaryFile(
            prefix="accela-update-", suffix=".AppImage", delete=False
        )
        appimage_dest = Path(tmp.name)
        tmp.close()

        try:
            download_asset(self._asset, appimage_dest, on_progress=self._on_progress)

            self.downloading_script.emit()
            script_dest = _download_install_script()

            self.finished.emit(appimage_dest, script_dest)

        except (DownloadError, OSError) as exc:
            # If the AppImage OR the script fails, clean up the giant AppImage file
            appimage_dest.unlink(missing_ok=True)
            self.failed.emit(str(exc))

    def _on_progress(self, downloaded: int, total: int) -> None:
        self.progress.emit(downloaded, total)


class UpdateDialog(QDialog):
    def __init__(self, update_info: UpdateInfo, parent=None) -> None:
        super().__init__(parent)
        self._info = update_info
        self._worker: DownloadWorker | None = None

        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle(f"Update available — ACCELA {update_info.latest_version}")
        self.setMinimumWidth(500)

        CustomTitleBar.setup_dialog_layout(self, title=self.windowTitle())
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self._tb_content_widget)
        root.setSpacing(10)
        root.setContentsMargins(18, 18, 18, 14)

        header = QLabel(
            f"<b>ACCELA {self._info.latest_version}</b> is available.<br>"
            f"You are running <b>{self._info.current_version}</b>."
        )
        header.setWordWrap(True)
        root.addWidget(header)

        self._status_label = QLabel("Click <b>Update now</b> to download and install.")
        self._status_label.setWordWrap(True)
        root.addWidget(self._status_label)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(False)
        self._progress_bar.setTextVisible(True)
        root.addWidget(self._progress_bar)

        btn_row = QHBoxLayout()

        self._update_btn = QPushButton("Update now")
        self._update_btn.setDefault(True)
        self._update_btn.clicked.connect(self._start_download)

        self._later_btn = QPushButton("Later")
        self._later_btn.clicked.connect(self.reject)

        btn_row.addWidget(self._update_btn)
        btn_row.addWidget(self._later_btn)
        root.addLayout(btn_row)

    def _start_download(self) -> None:
        self._update_btn.setEnabled(False)
        self._later_btn.setEnabled(False)
        self._progress_bar.setVisible(True)
        self._set_status("Downloading update…")

        self._worker = DownloadWorker(self._info.appimage_asset, parent=self)
        self._worker.progress.connect(self._on_progress)
        self._worker.downloading_script.connect(self._on_downloading_script)
        self._worker.finished.connect(self._on_download_finished)
        self._worker.failed.connect(self._on_failure)
        self._worker.start()

    def _on_progress(self, downloaded: int, total: int) -> None:
        if total > 0:
            self._progress_bar.setValue(int(downloaded * 100 / total))
            self._set_status(
                f"Downloading update… {downloaded / 1_048_576:.1f} / {total / 1_048_576:.1f} MB"
            )
        else:
            self._progress_bar.setRange(0, 0)
            self._set_status(f"Downloading update… {downloaded / 1_048_576:.1f} MB")

    def _on_downloading_script(self) -> None:
        self._progress_bar.setRange(0, 0)
        self._set_status("Downloading installer script…")

    def _on_download_finished(
        self, downloaded_appimage: Path, script_path: Path
    ) -> None:
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(100)
        self._set_status("Launching installer…")

        if self._worker is not None:
            self._worker.wait(3000)

        try:
            delegate_install_and_quit(downloaded_appimage, script_path)
        except OSError as exc:
            self._on_failure(f"Failed to launch installer: {exc}")

    def _on_failure(self, message: str) -> None:
        logger.error("Update failed: %s", message)
        self._set_status(f"<span style='color:red;'>Error: {message}</span>")
        self._progress_bar.setVisible(False)
        self._update_btn.setEnabled(True)
        self._later_btn.setEnabled(True)
        QMessageBox.critical(
            self,
            "Update failed",
            f"The update could not be completed:\n\n{message}\n\n"
            "Please update manually from the GitHub releases page.",
        )

    def _set_status(self, text: str) -> None:
        self._status_label.setText(text)

    def closeEvent(self, event):
        if self._worker is not None and self._worker.isRunning():
            event.ignore()
            return
        super().closeEvent(event)
