import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import psutil

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from utils.paths import Paths
from utils.settings import get_settings

logger = logging.getLogger(__name__)


class StreamReader(QObject):
    new_line = pyqtSignal(str)

    def __init__(self, stream, task_instance):
        super().__init__()
        self.stream = stream
        self._is_running = True
        self.task_instance = task_instance
        self.downloaded_bytes = 0
        self.dl_regex = re.compile(r"Total downloaded:\s+(\d+)\s+bytes")

    def run(self):
        try:
            for line in iter(self.stream.readline, ""):
                if not self._is_running:
                    break
                if not self.task_instance._is_running:
                    break
                # Parse byte counts on the fly inside the thread to avoid storing log lines
                match = self.dl_regex.search(line)
                if match:
                    self.downloaded_bytes = int(match.group(1))

                self.new_line.emit(line)
        except ValueError:
            logger.debug(
                "StreamReader ValueError: Stream was likely closed forcefully."
            )
        except Exception as e:
            if self.task_instance._is_running:
                logger.error(f"StreamReader error: {e}", exc_info=True)
        finally:
            try:
                self.stream.close()
            except Exception as e:
                logger.debug(f"StreamReader failed to close stream cleanly: {e}")

    def stop(self):
        self._is_running = False


class DownloadDepotsTask(QObject):
    progress = pyqtSignal(str)
    progress_percentage = pyqtSignal(int)
    completed = pyqtSignal()
    error = pyqtSignal(tuple)  # Emits (error_type, error_value, traceback)

    MAX_VERIFICATION_PASSES = 5

    def __init__(self):
        super().__init__()
        self._is_running = True
        self.percentage_regex = re.compile(r"(\d{1,3}\.\d{2})%")
        self.last_percentage = -1
        self.process = None
        self.process_pid = None

        self.total_download_size_for_this_job = 0
        self.completed_so_far_for_this_job = 0
        self.current_depot_size = 0
        self._current_depot_started_at = None
        self._current_depot_id = None



        # Run metrics for end-of-job summary logging
        self._run_started_at = None
        self._attempted_depots = 0
        self._completed_depots = 0
        self._failed_depots = 0
        self._skipped_depots = 0
        self._warning_count = 0

    def _reset_run_metrics(self):
        self._run_started_at = time.monotonic()
        self._attempted_depots = 0
        self._completed_depots = 0
        self._failed_depots = 0
        self._skipped_depots = 0
        self._warning_count = 0

    def _log_run_summary(self, appid, status):
        elapsed = 0.0
        if self._run_started_at is not None:
            elapsed = max(0.0, time.monotonic() - self._run_started_at)

        logger.info(
            "Summary | app=%s | status=%s | attempted=%d | completed=%d | failed=%d | skipped=%d | warnings=%d | elapsed=%.1fs",
            appid,
            status,
            self._attempted_depots,
            self._completed_depots,
            self._failed_depots,
            self._skipped_depots,
            self._warning_count,
            elapsed,
        )

    def run(self, game_data, selected_depots, dest_path):
        appid = game_data.get("appid", "unknown")
        game_name = game_data.get("game_name", "unknown")
        self._reset_run_metrics()
        logger.info(
            f"Starting depot download task for app {appid} ({game_name}) with {len(selected_depots)} selected depots."
        )

        commands, skipped_depots, depot_sizes, dotnet_env = self._prepare_downloads(
            game_data, selected_depots, dest_path
        )
        if not commands:
            self.progress.emit("No valid download commands to execute. Task finished.")
            logger.warning(
                f"No valid depot download commands were generated for app {appid}; finishing without running downloader."
            )
            self._skipped_depots = len(skipped_depots)
            self._warning_count += self._skipped_depots
            self._log_run_summary(appid, "no-op")
            self.completed.emit()
            return

        total_depots = len(commands)

        self.total_download_size_for_this_job = sum(depot_sizes)
        self.completed_so_far_for_this_job = 0
        logger.info(
            f"Planned download size for app {appid}: {self.total_download_size_for_this_job} bytes across {total_depots} runnable depots."
        )

        try:
            free_space = shutil.disk_usage(dest_path).free
            # Adding a 10% safety margin or 500MB, whichever is larger
            margin = max(self.total_download_size_for_this_job * 0.1, 500 * 1024 * 1024)
            if self.total_download_size_for_this_job + margin > free_space:
                error_msg = f"Insufficient disk space! Required: {self.total_download_size_for_this_job / (1024**3):.2f} GB. Available: {free_space / (1024**3):.2f} GB."
                self.progress.emit(f"ERROR: {error_msg}")
                logger.error(f"{error_msg}")
                self._log_run_summary(appid, "failed")
                self.error.emit((RuntimeError, error_msg, None))
                return
        except Exception as e:
            self._warning_count += 1
            logger.warning(f"Failed to check disk space for {dest_path}: {e}")

        try:
            # --- Initial download pass ---
            cancelled, _ = self._run_depot_commands(
                commands, depot_sizes, total_depots, dotnet_env, appid, is_verification=False
            )
            if cancelled:
                return

            if skipped_depots:
                self.progress.emit(
                    f"Skipped {len(skipped_depots)} depots due to missing manifests: {', '.join(skipped_depots)}"
                )
                self._skipped_depots = len(skipped_depots)
                self._warning_count += len(skipped_depots)
                logger.warning(
                    f"Skipped {len(skipped_depots)} depots due to missing manifest IDs: {', '.join(skipped_depots)}"
                )

            # --- Post-download verification loop ---
            for verification_pass in range(1, self.MAX_VERIFICATION_PASSES + 1):
                if not self._is_running:
                    logger.info("Download task stopped before verification.")
                    self._log_run_summary(appid, "cancelled")
                    self.completed.emit()
                    return

                self.progress.emit(
                    f"--- Verification pass {verification_pass}/{self.MAX_VERIFICATION_PASSES}: re-running download to verify files ---"
                )
                logger.info(
                    f"Starting verification pass {verification_pass} for app {appid}."
                )

                self.completed_so_far_for_this_job = 0
                self.last_percentage = -1
                self.progress_percentage.emit(0)

                cancelled, verification_bytes = self._run_depot_commands(
                    commands, depot_sizes, total_depots, dotnet_env, appid, is_verification=True
                )
                if cancelled:
                    return

                if verification_bytes == 0:
                    self.progress.emit(
                        f"--- Verification passed: all files verified successfully (pass {verification_pass}) ---"
                    )
                    logger.info(
                        f"Verification passed for app {appid} on pass {verification_pass} "
                        f"(0 bytes downloaded, all files intact)."
                    )
                    break
                else:
                    self.progress.emit(
                        f"--- Verification pass {verification_pass}: {verification_bytes} bytes were re-downloaded (files were repaired) ---"
                    )
                    logger.warning(
                        f"Verification pass {verification_pass} for app {appid}: "
                        f"{verification_bytes} bytes re-downloaded. Will verify again."
                    )
            else:
                # Exhausted all verification passes without a clean run
                self.progress.emit(
                    f"--- Warning: verification did not fully pass after {self.MAX_VERIFICATION_PASSES} attempts ---"
                )
                self._warning_count += 1
                logger.warning(
                    f"Verification for app {appid} did not produce a clean pass after "
                    f"{self.MAX_VERIFICATION_PASSES} attempts."
                )

            if not self._is_running:
                logger.info("Download task stopped before cleanup.")
                self._log_run_summary(appid, "cancelled")
                self.completed.emit()
                return

            self.progress.emit("--- Cleaning up temporary files ---")
            temp_dir = tempfile.gettempdir()
            items_to_clean = {
                "mistwalker_keys.vdf": os.path.join(temp_dir, "mistwalker_keys.vdf"),
            }

            for name, path in items_to_clean.items():
                if os.path.exists(path):
                    try:
                        if os.path.isdir(path):
                            shutil.rmtree(path)
                            self.progress.emit(f"Removed temp directory '{name}'.")
                        else:
                            os.remove(path)
                            self.progress.emit(f"Removed temp file '{name}'.")
                    except OSError as e:
                        self.progress.emit(f"Error removing temp item '{name}': {e}")

            # NOTE: PlayNotOwnedGames is now handled by AdditionalApps
            # self._ensure_play_not_owned_games_enabled()

            self.completed.emit()
            logger.info(f"Depot download task finished for app {appid}.")
            self._log_run_summary(appid, "completed")

        except FileNotFoundError:
            exe_name = "DepotDownloader"
            self.progress.emit(
                f"ERROR: '{exe_name}' not found. Make sure the DepotDownloader binary is installed."
            )
            logger.critical(f"'{exe_name}' not found.")
            self._log_run_summary(appid, "failed")
            self.error.emit((FileNotFoundError, f"'{exe_name}' not found", None))
            raise
        except Exception as e:
            self.progress.emit(f"An unexpected error occurred during download: {e}")
            logger.error(f"Download subprocess failed: {e}", exc_info=True)
            self.process = None
            self.process_pid = None
            self._log_run_summary(appid, "failed")
            self.error.emit((type(e), str(e), None))
            raise

    def _run_depot_commands(self, commands, depot_sizes, total_depots, dotnet_env, appid, is_verification=False):
        """Run a set of DepotDownloader commands.

        Returns:
            tuple: (cancelled: bool, bytes_downloaded: int)
                - cancelled is True if the task was stopped
                - bytes_downloaded is the total compressed bytes downloaded
                  across all depots (parsed from DepotDownloader output)
        """
        label = "verification" if is_verification else "download"
        total_bytes_downloaded = 0

        for i, command in enumerate(commands):
            if not self._is_running:
                logger.info(f"Download task stopping before next depot ({label}).")
                self._log_run_summary(appid, "cancelled")
                self.completed.emit()
                return True, total_bytes_downloaded

            depot_id = command[4]
            self._current_depot_id = str(depot_id)
            self._current_depot_started_at = time.monotonic()
            self.current_depot_size = depot_sizes[i]
            self.progress.emit(
                f"--- Starting {label} for depot {depot_id} ({i + 1}/{total_depots}) [Size: {self.current_depot_size} bytes] ---"
            )
            logger.info(
                f"Launching DepotDownloader ({label}) for depot {depot_id} ({i + 1}/{total_depots}) with manifest {command[6]}."
            )
            self.last_percentage = -1
            self._attempted_depots += 1

            self.process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                env=dotnet_env,
            )

            self.process_pid = self.process.pid

            reader_thread = QThread()
            stream_reader = StreamReader(self.process.stdout, self)
            stream_reader.moveToThread(reader_thread)

            stream_reader.new_line.connect(self._handle_downloader_output)
            reader_thread.started.connect(stream_reader.run)

            reader_thread.start()
            self.process.wait()

            stream_reader.stop()
            reader_thread.quit()
            reader_thread.wait()

            # Read the final parsed integer directly from the reader object
            total_bytes_downloaded += stream_reader.downloaded_bytes

            # Properly clean up thread and reader objects
            stream_reader.deleteLater()
            reader_thread.deleteLater()

            if not self._is_running:
                logger.info(f"Download task stopping because stop() was called ({label}).")
                self._log_run_summary(appid, "cancelled")
                self.completed.emit()
                return True, total_bytes_downloaded

            return_code = self.process.returncode
            self.process = None
            self.process_pid = None

            if return_code != 0:
                self.progress.emit(
                    f"Warning: DepotDownloader exited with code {return_code} for depot {depot_id}."
                )
                self._failed_depots += 1
                self._warning_count += 1
                logger.warning(
                    f"DepotDownloader exited with code {return_code} for depot {depot_id}."
                )
            else:
                self.completed_so_far_for_this_job += self.current_depot_size
                self._completed_depots += 1
                elapsed = 0.0
                if self._current_depot_started_at is not None:
                    elapsed = time.monotonic() - self._current_depot_started_at
                logger.info(
                    f"Depot {depot_id} ({label}) completed successfully in {elapsed:.1f}s."
                )

            self._current_depot_started_at = None
            self._current_depot_id = None

        return False, total_bytes_downloaded

    def _handle_downloader_output(self, line):
        if not self._is_running:
            return
        line = line.strip()
        self.progress.emit(line)

        match = self.percentage_regex.search(line)
        if match:
            percentage = float(match.group(1))

            if self.total_download_size_for_this_job > 0:
                progress_of_current_depot = (
                    percentage / 100.0
                ) * self.current_depot_size

                total_progress_bytes = (
                    self.completed_so_far_for_this_job + progress_of_current_depot
                )

                total_percentage = int(
                    (total_progress_bytes / self.total_download_size_for_this_job) * 100
                )

                total_percentage = max(0, min(100, total_percentage))

                if total_percentage != self.last_percentage:
                    self.progress_percentage.emit(total_percentage)
                    self.last_percentage = total_percentage
            else:
                int_percentage = int(percentage)
                if int_percentage != self.last_percentage:
                    self.progress_percentage.emit(int_percentage)
                    self.last_percentage = int_percentage

    def _prepare_downloads(self, game_data, selected_depots, dest_path):
        temp_dir = tempfile.gettempdir()
        keys_path = os.path.join(temp_dir, "mistwalker_keys.vdf")
        manifest_dir = os.path.join(temp_dir, "mistwalker_manifests")

        self.progress.emit(f"Generating depot keys file at {keys_path}")
        logger.debug(f"Writing depot key file: {keys_path}")
        with open(keys_path, "w") as f:
            for depot_id in selected_depots:
                if depot_id in game_data["depots"]:
                    f.write(f"{depot_id};{game_data['depots'][depot_id]['key']}\n")

        safe_game_name_fallback = (
            re.sub(r"[^\w\s-]", "", game_data.get("game_name", ""))
            .strip()
            .replace(" ", "_")
        )
        install_folder_name = game_data.get("installdir", safe_game_name_fallback)
        if not install_folder_name:
            install_folder_name = f"App_{game_data['appid']}"

        download_dir = os.path.join(
            dest_path, "steamapps", "common", install_folder_name
        )
        os.makedirs(download_dir, exist_ok=True)
        self.progress.emit(f"Download destination set to: {download_dir}")
        logger.info(f"Resolved download destination: {download_dir}")

        # Use the self-contained native binary directly
        binary_path = Paths.deps("DepotDownloader").absolute()

        commands = []
        skipped_depots = []
        depot_sizes = []

        try:
            settings = get_settings()
            max_downloads_setting = settings.value("max_downloads", 255, type=int)
            try:
                max_downloads = int(max_downloads_setting)
            except Exception:
                max_downloads = 255
        except Exception:
            max_downloads = 255

        max_downloads = max(0, min(255, max_downloads))

        # Validate that manifests exist in game_data
        if not game_data.get("manifests"):
            self.progress.emit("ERROR: No manifest files found in the zip. The zip file may be incomplete or corrupted.")
            logger.error("No 'manifests' key found in game_data. Cannot proceed with download.")
            raise Exception("No manifest files were detected in the zip. Please ensure you're using a zip from a trusted source.")

        for depot_id in selected_depots:
            manifest_id = game_data.get("manifests", {}).get(depot_id)
            if not manifest_id:
                self.progress.emit(f"Warning: No manifest ID for depot {depot_id}. Skipping.")
                skipped_depots.append(str(depot_id))
                continue

            try:
                size_str = game_data["depots"][depot_id].get("size")
                if size_str:
                    depot_sizes.append(int(size_str))
                else:
                    depot_sizes.append(0)
                    self._warning_count += 1
                    self.progress.emit(f"Warning: No size data for depot {depot_id}. Total progress may be inaccurate.")
            except (ValueError, TypeError):
                depot_sizes.append(0)
                self._warning_count += 1
                self.progress.emit(f"Warning: Invalid size data for depot {depot_id}. Total progress may be inaccurate.")

            manifest_file_path = os.path.join(
                manifest_dir, f"{depot_id}_{manifest_id}.manifest"
            )

            commands.append(
                [
                    str(binary_path),
                    "-app",
                    game_data["appid"],
                    "-depot",
                    str(depot_id),
                    "-manifest",
                    manifest_id,
                    "-manifestfile",
                    manifest_file_path,
                    "-depotkeys",
                    keys_path,
                    "-max-downloads",
                    str(max_downloads),
                    "-dir",
                    download_dir,
                    "-validate",
                ]
            )

        # Use default environment for native binary
        env = os.environ.copy()

        return commands, skipped_depots, depot_sizes, env

    def stop(self):
        logger.debug("Stop signal received by download task.")
        self._is_running = False
        if self.process:
            try:
                # If paused, we need to resume to allow it to terminate
                try:
                    parent = psutil.Process(self.process_pid)
                    for proc in [parent] + parent.children(recursive=True):
                        try:
                            proc.resume()  # Resume in case it was suspended
                        except psutil.NoSuchProcess:
                            pass
                except psutil.NoSuchProcess:
                    pass
                
                self.process.terminate()
                self.process.kill()  # Ensure it dies
            except Exception as e:
                logger.error(f"Error terminating download process: {e}")

    def toggle_pause(self, pause):
        if not self.process_pid:
            logger.warning("Attempted to pause/resume, but no process is running.")
            return

        try:
            parent = psutil.Process(self.process_pid)
            children = parent.children(recursive=True)
            processes = [parent] + children

            for proc in processes:
                try:
                    if pause:
                        proc.suspend()
                    else:
                        proc.resume()
                except psutil.NoSuchProcess:
                    logger.warning(f"Process {proc.pid} no longer exists. Skipping.")

            status = "paused" if pause else "resumed"
            logger.info(f"Download process tree {status}.")

        except psutil.NoSuchProcess:
            logger.error(f"Main process {self.process_pid} not found. Cannot pause/resume.")
            self.process_pid = None
            self.process = None
        except Exception as e:
            status = "pause" if pause else "resume"
            logger.error(f"An error occurred while trying to {status} process: {e}")
            raise
