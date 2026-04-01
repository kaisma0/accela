import logging
import os
import hashlib
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

import requests
import yaml
from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)

REMOTE_UPDATES_URL = "https://raw.githubusercontent.com/AceSLS/SLSsteam/refs/heads/main/res/updates.yaml"
INSTALL_SLS_RAW_URL = "https://raw.githubusercontent.com/kaisma0/accela/main/scripts/install-sls.sh"


class DownloadSLSsteamTask(QObject):
    progress = pyqtSignal(str)
    progress_percentage = pyqtSignal(int)
    completed = pyqtSignal(str)
    error = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._is_running = True
        self._process = None

    def run(self):
        logger.info("Starting SLSsteam install task")
        temp_dir = None

        try:
            script_path, temp_dir = self._download_install_script()
            self.progress.emit(f"Using installer: {script_path}")
            self._run_install_script(script_path)

            if not self._is_running:
                self.progress.emit("SLSsteam installation cancelled")
                self.error.emit()
                return

            self.progress.emit("SLSsteam installation completed successfully")
            self.completed.emit(
                "SLSsteam has been successfully installed/updated."
            )

        except Exception as e:
            logger.error(f"SLSsteam installation task failed: {e}", exc_info=True)
            self.progress.emit(f"Error: {e}")
            self.error.emit()
            raise
        finally:
            if temp_dir:
                self._cleanup_temp_dir(temp_dir)

    def _download_install_script(self):
        """Download install-sls.sh from the official ACCELA repo into a temp file."""
        temp_dir = tempfile.mkdtemp(prefix="accela-install-sls-")
        script_path = Path(temp_dir) / "install-sls.sh"

        response = requests.get(INSTALL_SLS_RAW_URL, timeout=30)
        response.raise_for_status()

        with open(script_path, "w", encoding="utf-8") as f:
            f.write(response.text)

        script_path = Path(script_path)
        script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)
        return script_path, temp_dir

    def _run_install_script(self, script_path):
        """Execute install-sls.sh and stream output to progress signal."""
        self.progress.emit("Running SLSsteam installer...")

        self._process = subprocess.Popen(
            ["bash", script_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        for line in iter(self._process.stdout.readline, ""):
            if line:
                self.progress.emit(f"install-sls.sh: {line.strip()}")

        self._process.wait()
        return_code = self._process.returncode
        self._process = None

        # Handle cancellation gracefully without raising an exception that bypasses run()'s logic
        if not self._is_running:
            return

        if return_code != 0:
            raise Exception(f"install-sls.sh failed with exit code {return_code}")

    @staticmethod
    def find_steamclient_so():
        """Find steamclient.so in user's Steam installation"""
        from core.steam_helpers import find_steam_install

        steam_path = find_steam_install()
        if not steam_path:
            logger.debug("Steam installation not found")
            return None

        steam_path = Path(steam_path) / "ubuntu12_32" / "steamclient.so"
        if steam_path.exists():
            logger.debug(f"Found steamclient.so at {steam_path}")
            return str(steam_path)

        logger.debug("steamclient.so not found in Steam installation")
        return None

    @staticmethod
    def compute_file_hash(filepath):
        """Compute SHA-256 hash of a file"""
        sha256 = hashlib.sha256()
        try:
            with open(filepath, "rb") as f:
                while chunk := f.read(8192):
                    sha256.update(chunk)
            return sha256.hexdigest()
        except Exception as e:
            logger.error(f"Failed to compute hash for {filepath}: {e}")
            return None

    @staticmethod
    def fetch_remote_hashes():
        """Fetch remote updates.yaml and extract all hashes"""
        try:
            response = requests.get(REMOTE_UPDATES_URL, timeout=10)
            response.raise_for_status()
            data = yaml.safe_load(response.text)

            all_hashes = []
            safe_mode_hashes = data.get("SafeModeHashes", {})
            for timestamp, entries in safe_mode_hashes.items():
                if isinstance(entries, list):
                    for entry in entries:
                        parts = entry.strip().split()
                        if parts:
                            all_hashes.append(parts[0])

            logger.debug(f"Fetched {len(all_hashes)} hashes from remote updates.yaml")
            return all_hashes
        except Exception as e:
            logger.error(f"Failed to fetch remote hashes: {e}")
            return None

    @staticmethod
    def check_steamclient_hash():
        """Check if user's steamclient.so hash matches remote"""
        local_path = DownloadSLSsteamTask.find_steamclient_so()

        if not local_path:
            return {"found": False, "hash": None, "mismatch": None}

        local_hash = DownloadSLSsteamTask.compute_file_hash(local_path)
        if not local_hash:
            return {"found": True, "hash": None, "mismatch": None, "error": True}

        remote_hashes = DownloadSLSsteamTask.fetch_remote_hashes()
        if remote_hashes is None:
            return {"found": True, "hash": local_hash, "mismatch": None, "error": True}

        mismatch = local_hash not in remote_hashes
        return {
            "found": True,
            "hash": local_hash,
            "mismatch": mismatch,
        }

    @staticmethod
    def check_update_available():
        """Check if an update is available for SLSsteam"""
        result = {
            "update_available": False,
            "latest_version": "Unknown",
            "latest_date": "",
            "installed": False,
            "installed_version": None,
            "steamclient_found": False,
            "steamclient_hash": "",
            "steamclient_mismatch": None,
            "steamclient_error": False,
            "error": None,
        }

        try:
            response = requests.get(
                "https://api.github.com/repos/AceSLS/SLSsteam/releases/latest",
                timeout=10,
            )
            response.raise_for_status()
            release_data = response.json()

            result["latest_version"] = release_data.get("tag_name", "Unknown")
            result["latest_date"] = release_data.get("published_at", "")

            # Combine Native and Flatpak paths into a searchable list
            xdg_data_home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
            possible_install_dirs = [
                Path(xdg_data_home) / "SLSsteam",
                Path.home() / ".var/app/com.valvesoftware.Steam/.local/share/SLSsteam"
            ]

            # Find the first directory that actually contains the .so file
            installed_dir = next(
                (d for d in possible_install_dirs if (d / "SLSsteam.so").exists()),
                None
            )

            if not installed_dir:
                return result  # Returns early with installed=False and the fetched API data

            result["installed"] = True

            # Check VERSION file ONLY in the directory where SLSsteam.so was actually found
            version_file = installed_dir / "VERSION"
            if version_file.exists():
                with open(version_file, "r") as f:
                    result["installed_version"] = f.read().strip()


                result["update_available"] = int(result["latest_version"]) > int(result["installed_version"])
            else:
                result["installed_version"] = "Unknown"

            # Check steamclient.so hash compatibility
            hash_check = DownloadSLSsteamTask.check_steamclient_hash()

            # Update the base dict with the hash check results
            result.update({
                "steamclient_found": hash_check.get("found", False),
                "steamclient_hash": hash_check.get("hash", "") or "",
                "steamclient_mismatch": hash_check.get("mismatch"),
                "steamclient_error": hash_check.get("error", False),
            })

            return result

        except Exception as e:
            logger.error(f"Failed to check for SLSsteam updates: {e}")
            result["error"] = str(e)
            result["steamclient_error"] = True
            return result

    def _cleanup_temp_dir(self, temp_dir):
        """Clean up temporary directory"""
        try:
            shutil.rmtree(temp_dir)
        except Exception as e:
            logger.warning(f"Failed to clean up temp dir {temp_dir}: {e}")

    def stop(self):
        """Stop the task"""
        logger.info("SLSsteam installation task received stop signal")
        self._is_running = False
        if self._process and self._process.poll() is None:
            self._process.terminate()
