import logging
import os
import re
import shutil
import stat
import tempfile
import time
import psutil
from pathlib import Path
from typing import Any, Dict, Optional

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QFileDialog, QMessageBox

from core import steam_helpers
from core.steam_manifest import write_appmanifest_acf
from core.tasks.application_shortcuts import ApplicationShortcutsTask
from core.tasks.download_depots_task import DownloadDepotsTask
from core.tasks.download_slssteam_task import DownloadSLSsteamTask
from core.tasks.generate_achievements_task import GenerateAchievementsTask
from core.tasks.monitor_speed_task import SpeedMonitorTask
from core.tasks.process_zip_task import ProcessZipTask
from core.tasks.steamless_task import SteamlessTask
from ui.dialogs.depotselection import DepotSelectionDialog
from ui.dialogs.steamlibrary import SteamLibraryDialog
from ui.dialogs.chmod_resume import ChmodResumeDialog
from ui.dialogs.steamless_resume import SteamlessResumeDialog
from utils.helpers import get_base_path
from utils.yaml_config_manager import (
    get_user_config_path,
    add_list_item,
    add_dlc_data,
)

from utils.paths import Paths
from utils.task_runner import TaskRunner

logger = logging.getLogger(__name__)


class PostDownloadStage:
    """Pipeline stages for deterministic post-download orchestration."""

    FINALIZE = "finalize"
    STEAMLESS = "steamless"
    SHORTCUTS = "shortcuts"
    ACHIEVEMENTS = "achievements"
    FINISH = "finish"


class TaskManager:
    def __init__(self, main_window):
        self.main_window = main_window
        self.settings = main_window.settings

        # Task state
        self.speed_monitor_task = None
        self.speed_monitor_runner = None
        self.is_awaiting_speed_monitor_stop = False

        self.zip_task = None
        self.zip_task_runner = None
        self.is_awaiting_zip_task_stop = False

        self.download_task = None
        self.download_runner = None
        self.is_awaiting_download_stop = False
        self.achievement_task = None
        self.achievement_task_runner = None
        self.achievement_worker = None
        self.steamless_task = None
        self.application_shortcuts_task = None
        self.application_shortcuts_task_runner = None
        self.slssteam_download_task = None
        self.slssteam_download_runner = None
        self.post_finalize_runner = None

        # Processing state
        self.is_processing = False
        self.is_download_paused = False
        self.is_cancelling = False
        self.current_job: Optional[str] = None
        self.current_job_metadata: Optional[Dict[str, Any]] = None
        self.game_data: Optional[Dict[str, Any]] = None
        self.current_dest_path: Optional[str] = None
        self.slssteam_mode_was_active = False
        self._steamless_success = None
        self._steamless_manual_run = False

        # Progress tracking for manual Steamless runs (for resume dialog)
        self._steamless_progress_log = []
        self._steamless_game_name = ""

        # Persisted Steamless result for status dialog (preserved after steamless finishes)
        # True = DRM removed, False = no DRM found, None = didn't run
        self._last_steamless_success = None

        # Track if Steamless actually ran (vs didn't run at all)
        self._steamless_ran = False
        # Track if Steamless had an error
        self._steamless_error = False

        # Persisted SLScheevo (achievement generation) result for status dialog
        # True = succeeded, False = failed, None = didn't run
        self._last_slscheevo_success = None
        # Track if SLScheevo actually ran (vs didn't run at all)
        self._slscheevo_ran = False
        # Track if SLScheevo had an error
        self._slscheevo_error = False

        # Status tracking for last completed job (for StatusDialog)
        # Status values: "ok", "in_progress", "error", or "not_run"
        # Initial state: nothing has run yet
        self._last_ddm_status = "not_run"
        self._last_ddm_status_text = "N/A"
        self._last_slscheevo_status = "not_run"
        self._last_slscheevo_status_text = "N/A"
        self._last_steamless_status = "not_run"
        self._last_steamless_status_text = "N/A"
        self._last_installed_game = None

        # Download progress logging throttle state
        self._last_download_log_time = 0.0
        self._last_download_log_bucket = -1
        self._last_download_log_line = ""

        # Post-download pipeline state
        self._post_download_active = False
        self._post_download_current_stage = None
        self._post_download_completed_stages = set()
        self._post_download_abort_remaining = False

        # Cancel cleanup preference
        self._delete_files_on_cancel: Optional[bool] = None

        # Status colors (same as StatusDialog)
        self.STATUS_OK = "#00FF00"
        self.STATUS_IN_PROGRESS = "#FFA500"
        self.STATUS_ERROR = "#FF0000"

    def _get_install_folder_name(self) -> str:
        """Return the sanitised install folder name derived from game_data."""
        if not self.game_data:
            return ""
        safe_fallback = (
            re.sub(r"[^\w\s-]", "", self.game_data.get("game_name", ""))
            .strip()
            .replace(" ", "_")
        )
        name = self.game_data.get("installdir") or safe_fallback
        return name or f"App_{self.game_data['appid']}"

    def start_zip_processing(self, zip_path, metadata=None):
        """Start processing a ZIP file

        Args:
            zip_path: Path to the ZIP file
            metadata: Optional dict with job metadata (appid, library_path, install_path)
        """
        self.is_processing = True
        self.current_job = zip_path
        self.current_job_metadata = metadata or {}

        self.main_window.progress_bar.setVisible(True)
        self.main_window.progress_bar.setRange(0, 0)
        self.main_window.drop_text_label.setText(f"Processing: {Path(zip_path).name}")

        self.zip_task = ProcessZipTask()
        self.zip_task_runner = TaskRunner()
        self.is_awaiting_zip_task_stop = True
        self.zip_task_runner.cleanup_complete.connect(self._on_zip_task_stopped)

        worker = self.zip_task_runner.run(self.zip_task.run, zip_path)
        worker.finished.connect(self._on_zip_processed)
        worker.error.connect(self._handle_task_error)

    def _on_zip_processed(self, game_data):
        """Handle completed ZIP processing"""
        self.main_window.progress_bar.setRange(0, 100)
        self.main_window.progress_bar.setValue(100)
        self.game_data = game_data

        if self.game_data and self.game_data.get("depots"):
            self._show_depot_selection_dialog()
        else:
            QMessageBox.warning(
                self.main_window,
                "No Depots Found",
                "Zip file processed, but no downloadable depots were found.",
            )
            self.job_finished()

    def _show_depot_selection_dialog(self):
        """Show depot selection dialog"""
        game_data = self.game_data
        if not game_data:
            logger.warning("Cannot open depot selection dialog: Game data is missing.")
            self.job_finished()
            return

        auto_skip_single_choice = self.settings.value(
            "auto_skip_single_choice", False, type=bool
        )
        depots = game_data.get("depots") or {}
        if auto_skip_single_choice and len(depots) == 1:
            selected_depots = list(depots.keys())
            logger.info("Auto-selected single depot and skipped selection dialog.")

            if self.game_data:
                self.game_data["selected_depots_list"] = selected_depots

            dest_path = self._get_destination_path()
            if dest_path:
                self._start_download(selected_depots, dest_path)
            else:
                self.job_finished()
            return

        self.main_window.ui_state.depot_dialog = DepotSelectionDialog(
            game_data["appid"],
            game_data["game_name"],
            game_data["depots"],
            game_data.get("header_url"),
            self.main_window,
        )

        if self.main_window.ui_state.depot_dialog.exec():
            selected_depots = (
                self.main_window.ui_state.depot_dialog.get_selected_depots()
            )

            # Store selected depots for ACF generation
            if self.game_data:
                self.game_data["selected_depots_list"] = selected_depots

            if not selected_depots:
                self.job_finished()
                return

            dest_path = self._get_destination_path()
            if dest_path:
                self._start_download(selected_depots, dest_path)
            else:
                self.job_finished()
        else:
            self.job_finished()

    def _get_destination_path(self):
        """Get destination path based on current mode"""
        slssteam_mode = self.settings.value("slssteam_mode", False, type=bool)
        library_mode = self.settings.value("library_mode", False, type=bool)

        # Check if we have existing library_path from an update/validate action
        current_job_metadata = self.current_job_metadata or {}
        existing_library_path = current_job_metadata.get("library_path")

        if slssteam_mode:
            self._handle_slssteam_mode()
            # Use existing library path if available, otherwise ask
            if existing_library_path:
                logger.info(f"Using existing library path: {existing_library_path}")
                return existing_library_path
            return self._get_library_destination_path()
        elif library_mode:
            # Use existing library path if available, otherwise ask
            if existing_library_path:
                logger.info(f"Using existing library path: {existing_library_path}")
                return existing_library_path
            return self._get_library_destination_path()
        else:
            return QFileDialog.getExistingDirectory(
                self.main_window, "Select Destination Folder"
            )

    def _get_library_destination_path(self):
        libraries = steam_helpers.get_steam_libraries()
        if libraries:
            auto_skip_single_choice = self.settings.value(
                "auto_skip_single_choice", False, type=bool
            )
            if auto_skip_single_choice and len(libraries) == 1:
                logger.info("Auto-selected single Steam library and skipped dialog.")
                return libraries[0]
            dialog = SteamLibraryDialog(libraries, self.main_window)
            if dialog.exec():
                return dialog.get_selected_path()
            else:
                return None
        else:
            return QFileDialog.getExistingDirectory(
                self.main_window, "Select Destination Folder"
            )

    def _handle_slssteam_mode(self):
        """Handle SLSsteam mode specific setup"""
        game_data = self.game_data
        if not game_data:
            logger.warning("No game_data available for SLSsteam mode handling.")
            return

        # DLC selection
        if game_data.get("dlcs"):
            logger.info("SLSsteam mode active, skipping DLC selection.")

    def _start_download(self, selected_depots, dest_path):
        """Start the download process"""
        if not self.game_data:
            logger.error("Aborting download initiation: Missing requisite game data.")
            self.job_finished()
            return

        self.current_dest_path = dest_path
        self.slssteam_mode_was_active = self.settings.value(
            "slssteam_mode", False, type=bool
        )
        self.is_cancelling = False

        # Reset status tracking for new job
        self._last_steamless_success = None
        self._last_slscheevo_success = None
        self._steamless_ran = False
        self._steamless_error = False
        self._slscheevo_ran = False
        self._slscheevo_error = False
        self._last_ddm_status = "in_progress"
        self._last_ddm_status_text = "Downloading..."
        self._last_slscheevo_status = "not_run"
        self._last_slscheevo_status_text = "N/A"
        self._last_steamless_status = "not_run"
        self._last_steamless_status_text = "N/A"

        self.main_window.ui_state.switch_to_download_gif()

        # Update status button color to show in-progress
        self._update_status_button_color()
        self.main_window.drop_text_label.setText(
            f"Downloading: {self.game_data.get('game_name', '')}"
        )

        self.main_window.progress_bar.setVisible(True)
        self.main_window.progress_bar.setValue(0)
        self.main_window.speed_label.setVisible(True)

        # Reset download log throttling state for each new job.
        self._last_download_log_time = 0.0
        self._last_download_log_bucket = -1
        self._last_download_log_line = ""

        self.download_task = DownloadDepotsTask()
        self.download_task.progress.connect(self._handle_download_progress_log)
        self.download_task.progress_percentage.connect(
            self.main_window.progress_bar.setValue
        )
        self.download_task.completed.connect(self._on_download_complete)
        self.download_task.error.connect(self._handle_task_error)

        self.download_runner = TaskRunner()
        self.is_awaiting_download_stop = True
        self.download_runner.cleanup_complete.connect(self._on_download_task_stopped)
        worker = self.download_runner.run(
            self.download_task.run, self.game_data, selected_depots, dest_path
        )
        worker.error.connect(self._handle_task_error)

        self._start_speed_monitor()
        self.is_download_paused = False
        self.main_window.ui_state.pause_button.setText("Pause")
        self.main_window.ui_state.pause_button.setVisible(True)
        self.main_window.ui_state.cancel_button.setVisible(True)

        # Write app token to file if wrapper mode is disabled
        if not self.slssteam_mode_was_active:
            app_token = self.game_data.get("app_token")
            if app_token:
                install_folder_name = self._get_install_folder_name()
                game_dir = (
                    Path(dest_path) / "steamapps" / "common" / install_folder_name
                )
                token_file = game_dir / "apptoken.txt"
                try:
                    game_dir.mkdir(parents=True, exist_ok=True)
                    token_file.write_text(app_token)
                    logger.info(f"Wrote app token to {token_file}")
                except Exception as e:
                    logger.error(f"Failed to write app token to file: {e}")

    def _start_speed_monitor(self):
        """Start speed monitoring task"""
        self.speed_monitor_task = SpeedMonitorTask()
        self.speed_monitor_task.speed_update.connect(
            self.main_window.speed_label.setText
        )

        self.speed_monitor_runner = TaskRunner()
        self.speed_monitor_runner.cleanup_complete.connect(
            self._on_speed_monitor_stopped
        )
        self.speed_monitor_runner.run(self.speed_monitor_task.run)

    def _stop_speed_monitor(self):
        """Stop speed monitoring task"""
        if self.speed_monitor_task:
            logger.debug("Sending stop signal to SpeedMonitorTask.")
            self.speed_monitor_task.stop()
            self.speed_monitor_task = None
        elif self.is_awaiting_speed_monitor_stop:
            logger.debug("Speed monitor already stopped; clearing wait flag manually.")
            self.is_awaiting_speed_monitor_stop = False
            self.main_window.job_queue._check_if_safe_to_start_next_job()

    def _handle_download_progress_log(self, message):
        """Log downloader output with throttling so progress stays readable."""
        if not message:
            return

        text = message.strip()
        if not text:
            return

        lowered = text.lower()
        now = time.monotonic()

        is_error_prefix = text.startswith("ERROR:") or text.startswith("Error:")
        is_warning_prefix = text.startswith("Warning:") or text.startswith("WARNING:")

        # Process important info markers first as long as it's not explicitly prefixed as an error
        if not (is_error_prefix or is_warning_prefix):
            important_markers = (
                "starting download for depot",
                "starting verification for depot",
                "verification pass",
                "verification passed",
                "cleaning up temporary files",
                "removed temp",
                "skipped",
                "download destination set to",
                "checking .net 10 runtime",
            )
            if any(marker in lowered for marker in important_markers):
                logger.info(f"{text}")
                self._last_download_log_time = now
                self._last_download_log_line = text
                # Reset progress bucket so a new download/verification phase
                self._last_download_log_bucket = -1
                return

        # Check for actual errors, avoiding false positives from files with "error" in their name
        if is_error_prefix or " failed" in lowered or re.search(r"\berror\b", lowered):
            # Exclude lines that are clearly just progress indicators reporting a file path
            if not re.match(r"^\d{1,3}(?:\.\d{1,2})?% .+", text):
                logger.error(f"{text}")
                self._last_download_log_time = now
                self._last_download_log_line = text
                return

        # Check for actual warnings, avoiding false positives from files with "warning" in their name
        if is_warning_prefix or re.search(r"\bwarning\b", lowered):
            # Exclude lines that are clearly just progress indicators reporting a file path
            if not re.match(r"^\d{1,3}(?:\.\d{1,2})?% .+", text):
                logger.warning(f"{text}")
                self._last_download_log_time = now
                self._last_download_log_line = text
                return

        percent_match = re.search(r"(\d{1,3}(?:\.\d{1,2})?)%", text)
        if percent_match:
            try:
                percent = int(float(percent_match.group(1)))
            except ValueError:
                percent = None

            if percent is not None:
                percent = max(0, min(100, percent))

                # Emit when the percentage changes, with explicit completion safeguard.
                if percent > self._last_download_log_bucket or percent == 100:
                    logger.info(f"{text}")
                    self._last_download_log_bucket = percent
                    self._last_download_log_time = now
                    self._last_download_log_line = text
            return

        if (
            now - self._last_download_log_time >= 15
            and text != self._last_download_log_line
        ):
            logger.info(f"{text}")
            self._last_download_log_time = now
            self._last_download_log_line = text

    def _on_speed_monitor_stopped(self):
        """Handle speed monitor cleanup completion"""
        logger.debug("SpeedMonitorTask's worker has officially completed cleanup.")
        self.speed_monitor_runner = None
        self.is_awaiting_speed_monitor_stop = False
        self.main_window.job_queue._check_if_safe_to_start_next_job()

    def _on_zip_task_stopped(self):
        """Handle ZIP task cleanup completion"""
        logger.debug("ProcessZipTask's worker has officially completed cleanup.")
        self.zip_task_runner = None
        self.is_awaiting_zip_task_stop = False
        self.main_window.job_queue._check_if_safe_to_start_next_job()

    def _on_download_task_stopped(self):
        """Handle download task cleanup completion"""
        logger.debug("Download task's worker has officially completed cleanup.")
        self.download_runner = None
        self.is_awaiting_download_stop = False
        self.main_window.job_queue._check_if_safe_to_start_next_job()

    def _on_download_complete(self):
        """Handle download completion"""
        if self.is_cancelling:
            logger.info(
                "Download complete signal received, job was cancelled. Cleaning up..."
            )
            if self._delete_files_on_cancel:
                self._cleanup_cancelled_job_files()
            else:
                logger.info("Cancel confirmed; keeping existing files.")
            self.job_finished()
            return

        self.is_awaiting_speed_monitor_stop = True
        self._stop_speed_monitor()
        self.main_window.progress_bar.setValue(100)

        if not self.game_data:
            logger.warning(
                "_on_download_complete called, but game_data is None. Job was likely cancelled or errored."
            )
            if self.is_processing:
                self.job_finished()
            return

        self._start_post_download_pipeline()

    def _start_post_download_pipeline(self):
        """Start the staged post-download pipeline from a clean state."""
        self._post_download_active = True
        self._post_download_current_stage = None
        self._post_download_completed_stages = set()
        self._post_download_abort_remaining = False
        self._advance_post_download_pipeline()

    def _advance_post_download_pipeline(self):
        """Advance to the next eligible post-download stage exactly once."""
        if not self._post_download_active:
            return

        if self.is_cancelling:
            self._run_post_download_stage(PostDownloadStage.FINISH)
            return

        stage = self._determine_next_post_download_stage()
        if stage is None:
            self._post_download_active = False
            return

        self._run_post_download_stage(stage)

    def _determine_next_post_download_stage(self):
        """Resolve the next stage to execute according to settings and completion state."""
        if PostDownloadStage.FINALIZE not in self._post_download_completed_stages:
            return PostDownloadStage.FINALIZE

        if self._post_download_abort_remaining:
            if PostDownloadStage.FINISH not in self._post_download_completed_stages:
                return PostDownloadStage.FINISH
            return None

        steamless_enabled = self.settings.value("use_steamless", False, type=bool)
        if (
            steamless_enabled
            and not self.is_cancelling
            and PostDownloadStage.STEAMLESS not in self._post_download_completed_stages
        ):
            return PostDownloadStage.STEAMLESS

        shortcuts_enabled = self.settings.value(
            "create_application_shortcuts", False, type=bool
        )
        slssteam_mode = self.settings.value("slssteam_mode", False, type=bool)
        if shortcuts_enabled and not slssteam_mode:
            logger.info(
                "Application shortcuts creation is enabled but SLSsteam mode is disabled, skipping"
            )
        if (
            shortcuts_enabled
            and slssteam_mode
            and not self.is_cancelling
            and PostDownloadStage.SHORTCUTS not in self._post_download_completed_stages
        ):
            return PostDownloadStage.SHORTCUTS

        achievements_enabled = self.settings.value(
            "generate_achievements", False, type=bool
        )
        if (
            achievements_enabled
            and not self.is_cancelling
            and PostDownloadStage.ACHIEVEMENTS
            not in self._post_download_completed_stages
        ):
            return PostDownloadStage.ACHIEVEMENTS

        if PostDownloadStage.FINISH not in self._post_download_completed_stages:
            return PostDownloadStage.FINISH

        return None

    def _run_post_download_stage(self, stage):
        """Execute one stage from the post-download pipeline."""
        if stage in self._post_download_completed_stages:
            logger.debug("Skipping already-completed post-download stage: %s", stage)
            self._advance_post_download_pipeline()
            return

        if stage == self._post_download_current_stage:
            logger.debug("Post-download stage already running: %s", stage)
            return

        self._post_download_current_stage = stage

        if stage == PostDownloadStage.FINALIZE:
            self.main_window.drop_text_label.setText(
                f"Finalizing: {self.game_data.get('game_name', '')}"
            )
            self._start_post_download_finalization()
            return

        if stage == PostDownloadStage.STEAMLESS:
            logger.info("Feature enabled; preparing for DRM removal...")
            self.main_window.drop_text_label.setText(
                f"Running Steamless: {self.game_data.get('game_name', '')}"
            )
            self._start_steamless_processing()
            return

        if stage == PostDownloadStage.SHORTCUTS:
            logger.info("Generating application shortcuts...")
            self.main_window.drop_text_label.setText(
                f"Creating Application Shortcuts: {self.game_data.get('game_name', '')}"
            )
            self._start_application_shortcuts_processing()
            return

        if stage == PostDownloadStage.ACHIEVEMENTS:
            logger.info(
                "Achievement generation is enabled, starting after previous stage completion"
            )
            self.main_window.drop_text_label.setText(
                f"Generating Achievements: {self.game_data.get('game_name', '')}"
            )
            self._start_achievement_generation()
            return

        if stage == PostDownloadStage.FINISH:
            self._complete_post_download_stage(PostDownloadStage.FINISH)
            self._post_download_active = False
            self._continue_after_download()

    def _queue_post_download_advance(self):
        """Queue the next pipeline transition on the event loop."""
        QTimer.singleShot(0, self._advance_post_download_pipeline)

    def _complete_post_download_stage(self, stage):
        """Mark one stage complete and clear running marker if needed."""
        self._post_download_completed_stages.add(stage)
        if self._post_download_current_stage == stage:
            self._post_download_current_stage = None

    def _start_post_download_finalization(self):
        """Run disk finalization operations in a worker thread."""
        if not self.game_data or not self.current_dest_path:
            logger.warning(
                "Missing game data or destination path; skipping finalization stage."
            )
            self._complete_post_download_stage(PostDownloadStage.FINALIZE)
            self._queue_post_download_advance()
            return

        size_on_disk = 0
        if self.download_task:
            size_on_disk = self.download_task.total_download_size_for_this_job
            logger.info(f"Retrieved SizeOnDisk from download task: {size_on_disk}")
        else:
            logger.warning("Download task object is gone, SizeOnDisk will be 0.")

        game_data_snapshot = dict(self.game_data)
        current_dest_path = self.current_dest_path
        slssteam_mode_was_active = self.slssteam_mode_was_active
        auto_apply_goldberg = self.settings.value(
            "auto_apply_goldberg", False, type=bool
        )

        self.post_finalize_runner = TaskRunner()
        self.post_finalize_runner.cleanup_complete.connect(
            self._on_post_finalize_task_cleanup
        )
        worker = self.post_finalize_runner.run(
            self._run_post_download_finalization,
            game_data_snapshot,
            current_dest_path,
            size_on_disk,
            slssteam_mode_was_active,
            auto_apply_goldberg,
        )
        worker.finished.connect(self._on_post_download_finalization_complete)
        worker.error.connect(self._on_post_download_finalization_error)

    def _run_post_download_finalization(
        self,
        game_data_snapshot,
        current_dest_path,
        size_on_disk,
        slssteam_mode_was_active,
        auto_apply_goldberg,
    ):
        """Worker-thread finalization implementation."""
        self._create_acf_file(
            size_on_disk,
            game_data=game_data_snapshot,
            dest_path=current_dest_path,
            update_ui=False,
        )
        self._move_manifests_to_depotcache(
            game_data=game_data_snapshot, dest_path=current_dest_path
        )

        selected_depots = game_data_snapshot.get("selected_depots_list", [])
        all_manifests = game_data_snapshot.get("manifests", {})
        if selected_depots and all_manifests:
            self._save_main_depot_info(game_data_snapshot, selected_depots, all_manifests)

        self._set_linux_binary_permissions(
            game_data=game_data_snapshot, dest_path=current_dest_path
        )

        if slssteam_mode_was_active:
            self._add_appids_to_slssteam_config(game_data=game_data_snapshot)

        if auto_apply_goldberg and not self.is_cancelling:
            install_folder_name = self._get_install_folder_name_from_data(game_data_snapshot)
            game_directory = str(
                Path(current_dest_path) / "steamapps" / "common" / install_folder_name
            )
            logger.info("Auto-application triggered post-download")
            self.apply_goldberg_to_game(
                game_directory=game_directory,
                appid=str(game_data_snapshot.get("appid", "")),
                game_name=game_data_snapshot.get("game_name", ""),
                show_dialog=False,
            )

        return {"success": True}

    def _on_post_download_finalization_complete(self, _result):
        """Handle finalization completion and continue pipeline."""
        self._complete_post_download_stage(PostDownloadStage.FINALIZE)
        self._queue_post_download_advance()

    def _on_post_download_finalization_error(self, error_info):
        """Handle finalization errors and continue pipeline."""
        _, error_value, error_traceback = error_info
        logger.error(f"Post-download finalization failed: {error_value}")
        if error_traceback:
            logger.error("Traceback:\n%s", error_traceback)
        self._last_ddm_status = "error"
        self._last_ddm_status_text = "Finalization failed"
        self._post_download_abort_remaining = True
        self._complete_post_download_stage(PostDownloadStage.FINALIZE)
        self._queue_post_download_advance()

    def _on_post_finalize_task_cleanup(self):
        """Handle post-finalization worker cleanup completion."""
        logger.debug("Post-finalization worker cleanup complete.")
        self.post_finalize_runner = None
        self.main_window.job_queue._check_if_safe_to_start_next_job()

    def _save_main_depot_info(self, game_data, selected_depots, all_manifests):
        """
        Save main depot ID and manifest to persistent file.

        Args:
            game_data: Dictionary containing game metadata
            selected_depots: List of selected depot IDs
            all_manifests: Dictionary mapping depot_id → manifest_gid
        """
        try:
            # Get appid from game_data
            appid = game_data.get("appid")
            if not appid:
                logger.warning("Cannot save depot info: missing appid")
                return

            # Get the main depot (first in selected list)
            if not selected_depots:
                logger.warning(
                    f"Cannot save depot info for app {appid}: no selected depots"
                )
                return

            main_depot_id = str(selected_depots[0])  # Convert to string for consistency

            # Get manifest_id for the main depot
            manifest_id = all_manifests.get(main_depot_id)
            if not manifest_id:
                logger.warning(
                    f"Cannot save depot info for app {appid}: no manifest found for depot {main_depot_id}"
                )
                return

            # Construct file path
            depots_dir = Path(get_base_path()) / "depots"
            depots_dir.mkdir(parents=True, exist_ok=True)

            depot_file = depots_dir / f"{appid}.depot"

            # Get access_token from game_data
            access_token = game_data.get("app_token", "")

            # Write the depot info file
            with open(depot_file, "w") as f:
                if access_token:
                    f.write(f"{main_depot_id}: {manifest_id}: {access_token}\n")
                else:
                    f.write(f"{main_depot_id}: {manifest_id}\n")

            logger.info(f"Saved main depot info: {appid}:{manifest_id} → {depot_file}")

        except Exception as e:
            # Log error but don't fail the download
            logger.error(f"Failed to save depot info: {e}")

    def _create_acf_file(
        self,
        size_on_disk,
        game_data=None,
        dest_path=None,
        update_ui=True,
    ):
        """Create Steam ACF manifest file"""
        game_data = game_data or self.game_data
        dest_path = dest_path or self.current_dest_path

        if not game_data or not dest_path:
            logger.warning("Missing game data or destination path. Cannot create .acf.")
            return

        logger.info("Generating Steam .acf manifest file...")

        install_folder_name = self._get_install_folder_name_from_data(game_data)

        if update_ui:
            self.main_window.drop_text_label.setText(
                f"Generating .acf for {install_folder_name}"
            )

        steamapps_path = Path(dest_path) / "steamapps"

        buildid = game_data.get("buildid", "0")
        selected_depots = game_data.get("selected_depots_list", [])
        all_manifests = game_data.get("manifests", {})
        all_depots = game_data.get("depots", {})

        try:
            write_appmanifest_acf(
                steamapps_path=steamapps_path,
                appid=str(game_data["appid"]),
                game_name=game_data["game_name"],
                install_folder_name=install_folder_name,
                size_on_disk=int(size_on_disk),
                buildid=str(buildid),
                selected_depots=selected_depots,
                manifests=all_manifests,
                depots=all_depots,
            )
        except (IOError, OSError, ValueError, TypeError) as e:
            logger.error(f"Error creating .acf file: {e}")

    def _move_manifests_to_depotcache(self, game_data=None, dest_path=None):
        game_data = game_data or self.game_data
        dest_path = dest_path or self.current_dest_path

        if not game_data or not dest_path:
            logger.error(
                "Missing game data or destination path. Cannot move manifests."
            )
            return

        temp_manifest_dir = Path(tempfile.gettempdir()) / "mistwalker_manifests"

        if not temp_manifest_dir.exists():
            logger.warning(
                f"Temp manifest directory not found, nothing to move: {temp_manifest_dir}"
            )
            return

        target_depotcache_dir = Path(dest_path) / "depotcache"

        try:
            target_depotcache_dir.mkdir(parents=True, exist_ok=True)
            logger.info(
                f"Ensured depotcache directory exists at: {target_depotcache_dir}"
            )
            manifests_map = game_data.get("manifests", {})

            if not manifests_map:
                logger.info("No manifest information found in game data.")
                # Clean up the empty temp dir anyway
                shutil.rmtree(temp_manifest_dir)
                logger.info(
                    f"Removed temporary manifest directory (no manifests to move): {temp_manifest_dir}"
                )
                return

            moved_count = 0
            for depot_id, manifest_gid in manifests_map.items():
                manifest_filename = f"{depot_id}_{manifest_gid}.manifest"
                source_path = temp_manifest_dir / manifest_filename
                dest_path = target_depotcache_dir / manifest_filename
                if source_path.exists():
                    shutil.move(str(source_path), str(dest_path))
                    logger.info(f"Moved {manifest_filename} to {target_depotcache_dir}")
                    moved_count += 1
                else:
                    # This case can happen if a manifest wasn't in the zip but was in the LUA
                    logger.warning(
                        f"Manifest file not found in temp, skipping: {source_path}"
                    )
            logger.info(f"Moved {moved_count} manifest files to depotcache.")
            # Clean up the now (hopefully) empty temp manifest directory
            shutil.rmtree(temp_manifest_dir)
            logger.info(f"Removed temporary manifest directory: {temp_manifest_dir}")
        except Exception as e:
            logger.error(f"Failed to move manifests to depotcache: {e}", exc_info=True)
            logger.info(f"Error moving manifests: {e}")

    def _set_linux_binary_permissions(self, game_data=None, dest_path=None):
        """Set executable permissions for Linux binaries after download"""
        game_data = game_data or self.game_data
        dest_path = dest_path or self.current_dest_path

        if not game_data or not dest_path:
            logger.warning(
                "Missing game data or destination path. Cannot set binary permissions."
            )
            return

        install_folder_name = self._get_install_folder_name_from_data(game_data)
        game_directory = str(Path(dest_path) / "steamapps" / "common" / install_folder_name)

        if not Path(game_directory).exists():
            logger.warning(
                f"Game directory not found at {game_directory}, skipping permission setup"
            )
            return

        logger.info(
            f"Setting executable permissions for Linux binaries in: {game_directory}"
        )

        # Use the shared chmod method (no dialog for post-download)
        self._run_chmod_recursive(game_directory)

    def _start_steamless_processing(self):
        """Start Steamless DRM removal after download completion"""
        if not self.current_dest_path or not self.game_data:
            logger.warning(
                "No destination path or game data found, skipping Steamless processing"
            )
            if self._post_download_active:
                self._complete_post_download_stage(PostDownloadStage.STEAMLESS)
                self._queue_post_download_advance()
            return

        install_folder_name = self._get_install_folder_name()
        game_directory = str(
            Path(self.current_dest_path) / "steamapps" / "common" / install_folder_name
        )

        if not Path(game_directory).exists():
            logger.warning(
                f"Game directory not found at {game_directory}, skipping Steamless processing"
            )
            if self._post_download_active:
                self._complete_post_download_stage(PostDownloadStage.STEAMLESS)
                self._queue_post_download_advance()
            return

        logger.info("\n" + "=" * 40)
        logger.info("Starting Steamless DRM removal...")
        logger.info(f"Processing directory: {game_directory}")

        self.steamless_task = SteamlessTask()
        self.steamless_task.progress.connect(self._log_steamless_message)
        self.steamless_task.result.connect(self._on_steamless_complete)
        self.steamless_task.finished.connect(self._on_steamless_finished)
        self.steamless_task.error.connect(self._handle_steamless_task_error)
        self.steamless_task.set_game_directory(game_directory)
        self.steamless_task.start()

        # Mark that Steamless is running
        self._steamless_ran = True

        # Update status button color to show Steamless running
        self._update_status_button_color()

    def run_steamless_manually(self, exe_path: str, game_name: Optional[str] = None):
        """Manually run Steamless on a specific executable"""
        # Stop any existing steamless task first
        if self.steamless_task:
            self.steamless_task.stop()
            self.steamless_task = None

        # Set game name for resume dialog
        self._steamless_game_name = game_name or Path(exe_path).name

        # Clear progress log for new run
        self._steamless_progress_log = []

        logger.info(f"Commencing manual processing for executable: {exe_path}")
        self._steamless_manual_run = True

        self.steamless_task = SteamlessTask()
        self.steamless_task.set_target_exe(exe_path)
        self.steamless_task.progress.connect(self._on_steamless_progress)
        self.steamless_task.result.connect(self._on_steamless_complete)
        self.steamless_task.finished.connect(self._on_steamless_finished)
        self.steamless_task.error.connect(self._handle_steamless_task_error)
        self.steamless_task.start()

    def run_steamless_for_game(self, game_directory: str, game_name: str):
        """Run Steamless on all executables in a game directory (from Game Library)"""
        # Stop any existing steamless task first
        if self.steamless_task:
            self.steamless_task.stop()
            self.steamless_task = None

        # Set game name for resume dialog
        self._steamless_game_name = game_name

        # Clear progress log for new run
        self._steamless_progress_log = []

        logger.info(f"Starting manual Steamless processing for game: {game_name}")
        logger.info(f"Game directory: {game_directory}")
        self._steamless_manual_run = True

        self.steamless_task = SteamlessTask()
        self.steamless_task.set_game_directory(game_directory)
        self.steamless_task.progress.connect(self._on_steamless_progress)
        self.steamless_task.result.connect(self._on_steamless_complete)
        self.steamless_task.finished.connect(self._on_steamless_finished)
        self.steamless_task.error.connect(self._handle_steamless_task_error)
        self.steamless_task.start()

    def run_chmod_for_game(
        self, game_directory: str, game_name: str, show_dialog: bool = False
    ):
        """Make all executables in a game directory runnable (from Game Library)"""
        logger.info(f"Starting chmod for game: {game_name}")
        logger.info(f"Game directory: {game_directory}")

        # Run chmod synchronously
        file_count = self._run_chmod_recursive(game_directory)

        logger.info(f"Chmod completed: {file_count} files processed")

        if show_dialog:
            self._show_chmod_resume_dialog(game_name, file_count)

    def apply_goldberg_to_game(
        self, game_directory: str, appid: str, game_name: str, show_dialog: bool = True
    ) -> bool:
        """Rename steam_api DLLs to .valve and copy Goldberg files into directories where the DLLs were found.

        Returns True on success, False otherwise. Shows dialogs when show_dialog is True.
        """
        logger.info(
            f"Applying Goldberg for game: {game_name} (AppID: {appid}) in {game_directory}"
        )

        if not game_directory or not Path(game_directory).exists():
            logger.warning(f"Game directory not found: {game_directory}")
            if show_dialog:
                QMessageBox.warning(
                    self.main_window,
                    "Directory Not Found",
                    f"Game directory not found: {game_directory}",
                )
            return False

        # Find directories containing steam_api DLLs
        found_dirs = set()
        for root, _, files in os.walk(game_directory):
            for fname in files:
                if fname.lower() in ("steam_api.dll", "steam_api64.dll"):
                    found_dirs.add(root)

        if not found_dirs:
            logger.info("No steam_api DLLs found in game directory tree")
            if show_dialog:
                QMessageBox.information(
                    self.main_window,
                    "No DLLs Found",
                    "No steam_api.dll or steam_api64.dll files were found in the game folder tree.",
                )
            return False

        # Source Goldberg directory in bundled deps
        goldberg_src = Paths.deps("Goldberg")
        if not goldberg_src.exists():
            logger.error(f"Goldberg source not found: {goldberg_src}")
            if show_dialog:
                QMessageBox.critical(
                    self.main_window,
                    "Source Missing",
                    f"Goldberg folder not found: {goldberg_src}",
                )
            return False

        processed = 0
        try:
            for dest_dir in found_dirs:
                # Track which DLLs existed originally in this folder
                original_dlls_in_dir = set()
                for base in ("steam_api.dll", "steam_api64.dll"):
                    if Path(dest_dir, base).exists():
                        original_dlls_in_dir.add(base)

                # Rename DLLs if present
                for base in ("steam_api.dll", "steam_api64.dll"):
                    src_path = Path(dest_dir, base)
                    if src_path.exists():
                        try:
                            target_path = src_path.with_name(src_path.name + ".valve")
                            if not target_path.exists():
                                src_path.replace(target_path)
                                logger.info(f"Renamed {src_path} -> {target_path}")
                            else:
                                logger.info(
                                    f"Target already exists, skipping rename: {target_path}"
                                )
                        except Exception as e:
                            logger.warning(f"Failed to rename {src_path}: {e}")

                # Copy only the matching Goldberg DLL(s)
                for base in original_dlls_in_dir:
                    src_dll = goldberg_src / base
                    dest_dll = str(Path(dest_dir) / base)
                    try:
                        if src_dll.exists():
                            shutil.copy2(str(src_dll), dest_dll)
                            logger.info(f"Copied Goldberg DLL {src_dll} -> {dest_dll}")
                        else:
                            logger.warning(f"Goldberg DLL not found in deps: {src_dll}")
                    except Exception as e:
                        logger.warning(
                            f"Failed to copy Goldberg DLL {src_dll} to {dest_dll}: {e}"
                        )

                # Copy Goldberg contents into this directory
                for item in goldberg_src.iterdir():
                    # Avoid copying DLLs/appid here; handled explicitly above/below
                    if item.name.lower() in (
                        "steam_api.dll",
                        "steam_api64.dll",
                        "steam_appid.txt",
                    ):
                        continue
                    dest_path = Path(dest_dir) / item.name
                    try:
                        if item.is_dir():
                            shutil.copytree(str(item), dest_path, dirs_exist_ok=True)
                            logger.info(f"Copied dir {item} -> {dest_path}")
                        else:
                            shutil.copy2(str(item), dest_path)
                            logger.info(f"Copied file {item} -> {dest_path}")
                    except Exception as e:
                        logger.warning(f"Failed to copy {item} to {dest_path}: {e}")

                # Write steam_appid.txt with provided appid
                try:
                    appid_file = Path(dest_dir) / "steam_appid.txt"
                    with open(appid_file, "w", encoding="utf-8") as f:
                        f.write(str(appid))
                    logger.info(f"Wrote steam_appid.txt to {appid_file}")
                except Exception as e:
                    logger.warning(
                        f"Failed to write steam_appid.txt in {dest_dir}: {e}"
                    )

                processed += 1

            if show_dialog:
                QMessageBox.information(
                    self.main_window,
                    "Apply Goldberg",
                    f"Applied Goldberg files to {processed} folder(s).",
                )

            return True

        except Exception as e:
            logger.exception(f"Error applying Goldberg: {e}")
            if show_dialog:
                QMessageBox.critical(
                    self.main_window, "Error", f"Failed to apply Goldberg: {e}"
                )
            return False

    def remove_goldberg_from_game(
        self, game_directory: str, appid: str, game_name: str, show_dialog: bool = True
    ) -> bool:
        """Restore original steam_api DLLs from .valve backups and remove Goldberg files.

        Returns True on success, False otherwise. Shows dialogs when show_dialog is True.
        """
        logger.info(
            f"Removing Goldberg for game: {game_name} (AppID: {appid}) in {game_directory}"
        )

        if not game_directory or not Path(game_directory).exists():
            logger.warning(f"Game directory not found: {game_directory}")
            if show_dialog:
                QMessageBox.warning(
                    self.main_window,
                    "Directory Not Found",
                    f"Game directory not found: {game_directory}",
                )
            return False

        # Find directories containing .valve backups
        found_dirs = set()
        for root, _, files in os.walk(game_directory):
            for fname in files:
                if fname.lower() in ("steam_api.dll.valve", "steam_api64.dll.valve"):
                    found_dirs.add(root)

        if not found_dirs:
            logger.info("No .valve backups found in game directory tree")
            if show_dialog:
                QMessageBox.information(
                    self.main_window,
                    "No Backups Found",
                    "No .valve backup files were found in the game folder tree.",
                )
            return False

        # Goldberg source (used to know what to remove). If missing, we'll still attempt to restore backups.
        goldberg_src = Paths.deps("Goldberg")
        goldberg_items = []
        if goldberg_src.exists():
            try:
                goldberg_items = [p.name for p in goldberg_src.iterdir()]
            except Exception:
                goldberg_items = []
        else:
            logger.debug(
                f"Goldberg source not found (removal will only restore backups): {goldberg_src}"
            )

        processed = 0
        try:
            for dest_dir in found_dirs:
                # Restore .valve backups (always restore the backup over the current file)
                had_backup = {}
                for base in ("steam_api.dll", "steam_api64.dll"):
                    valve_path = Path(dest_dir) / (base + ".valve")
                    orig_path = Path(dest_dir) / base
                    had_backup[base] = valve_path.exists()
                    try:
                        if had_backup[base]:
                            try:
                                valve_path.replace(orig_path)
                                logger.info(f"Restored {valve_path} -> {orig_path}")
                            except Exception as e:
                                logger.warning(f"Failed to restore {valve_path}: {e}")
                    except Exception as e:
                        logger.warning(f"Error accessing {valve_path}: {e}")

                # Remove any extra Goldberg DLL that didn't exist originally
                # (i.e., no .valve backup exists for it)
                for base in ("steam_api.dll", "steam_api64.dll"):
                    if had_backup.get(base):
                        continue
                    extra_path = Path(dest_dir) / base
                    if extra_path.exists():
                        try:
                            extra_path.unlink()
                            logger.info(f"Removed extra Goldberg DLL: {extra_path}")
                        except Exception as e:
                            logger.warning(
                                f"Failed to remove extra DLL {extra_path}: {e}"
                            )

                # Remove Goldberg files that were copied (skip steam_api DLLs and steam_appid.txt)
                for name in goldberg_items:
                    lname = name.lower()
                    if lname in ("steam_api.dll", "steam_api64.dll", "steam_appid.txt"):
                        continue
                    dest_path = Path(dest_dir) / name
                    try:
                        if dest_path.is_dir():
                            shutil.rmtree(dest_path)
                            logger.info(f"Removed Goldberg dir: {dest_path}")
                        elif dest_path.exists():
                            dest_path.unlink()
                            logger.info(f"Removed Goldberg file: {dest_path}")
                    except Exception as e:
                        logger.warning(
                            f"Failed to remove Goldberg item {dest_path}: {e}"
                        )

                # Remove steam_appid.txt if it matches provided appid
                try:
                    appid_file = Path(dest_dir) / "steam_appid.txt"
                    if appid_file.exists():
                        try:
                            with open(appid_file, "r", encoding="utf-8") as f:
                                content = f.read().strip()
                        except Exception:
                            content = None
                        if content is None or content == str(appid):
                            try:
                                appid_file.unlink()
                                logger.info(f"Removed steam_appid.txt from {dest_dir}")
                            except Exception as e:
                                logger.warning(
                                    f"Failed to remove steam_appid.txt in {dest_dir}: {e}"
                                )
                except Exception as e:
                    logger.warning(f"Error handling steam_appid.txt in {dest_dir}: {e}")

                processed += 1

            if show_dialog:
                QMessageBox.information(
                    self.main_window,
                    "Remove Goldberg",
                    f"Restored originals and removed Goldberg files from {processed} folder(s).",
                )

            return True

        except Exception as e:
            logger.exception(f"Error removing Goldberg: {e}")
            if show_dialog:
                QMessageBox.critical(
                    self.main_window, "Error", f"Failed to remove Goldberg: {e}"
                )
            return False

    def _run_chmod_recursive(self, game_directory: str) -> int:
        """Recursively find and chmod executable files in game directory"""
        # Common Linux binary/script extensions used by games
        linux_binary_extensions = {
            ".sh",
            ".bash",
            ".x86",
            ".x86_64",
            ".bin",
            ".run",
            ".elf",
            ".pck",
        }

        # ELF magic bytes (4 bytes: 0x7F + "ELF")
        elf_magic = b"\x7fELF"
        # Shebang standard (2 bytes)
        shebang_magic = b"#!"

        chmod_count = 0

        for root, _, filenames in os.walk(game_directory):
            for filename in filenames:
                file_path = Path(root) / filename

                if file_path.is_symlink():
                    continue

                should_chmod = False
                filename_lower = filename.lower()

                if any(filename_lower.endswith(ext) for ext in linux_binary_extensions):
                    should_chmod = True

                elif "." not in filename:
                    try:
                        with open(file_path, "rb") as f:
                            header = f.read(4)
                            if header.startswith(elf_magic) or header.startswith(
                                shebang_magic
                            ):
                                should_chmod = True
                    except (IOError, OSError):
                        continue

                if should_chmod:
                    try:
                        file_stat = file_path.stat()
                        current_mode = file_stat.st_mode
                        if not (current_mode & stat.S_IXUSR):
                            new_mode = current_mode | 0o755
                            file_path.chmod(new_mode)
                            logger.debug(f"Set executable: {file_path}")
                            chmod_count += 1
                    except OSError as e:
                        logger.warning(
                            f"Could not set permissions for {file_path}: {e}"
                        )

        if chmod_count > 0:
            logger.info(
                f"Set executable permissions for {chmod_count} Linux binary files"
            )
        else:
            logger.info("No Linux binaries found that needed permission changes")

        return chmod_count

    def _show_chmod_resume_dialog(self, game_name: str, file_count: int):
        """Show the chmod resume dialog with a summary of the results"""
        dialog = ChmodResumeDialog(
            game_name=game_name,
            file_count=file_count,
            success=True,
            parent=self.main_window,
        )
        dialog.exec()

    def _on_steamless_progress(self, message):
        """Capture Steamless progress messages for resume dialog"""
        self._steamless_progress_log.append(message)
        self._log_steamless_message(message)

    def _log_steamless_message(self, message):
        logger.info(f"{message}")

    def _on_steamless_complete(self, success):
        """Handle Steamless processing completion"""
        logger.info("\n" + "=" * 40)
        if success:
            logger.info("Processing completed successfully")
        else:
            logger.info("Processing completed with warnings or no DRM found")

        # Store the result for _on_steamless_finished to use
        # This prevents duplicate achievement generation starts
        self._steamless_success = success
        # Also persist for status dialog (survives after steamless task is cleared)
        self._last_steamless_success = success

    def _on_steamless_finished(self):
        """Handle Steamless thread finished"""
        # The thread has finished (run() returned)
        # Defer cleanup to next event loop tick to ensure thread is fully done
        if self.steamless_task:
            logger.debug("Steamless thread finished, scheduling cleanup")
            QTimer.singleShot(0, self._clear_steamless_task)

        # For manual runs, show resume dialog
        if self._steamless_manual_run:
            logger.info("Manual processing completed")
            self._show_steamless_resume_dialog()
            self._steamless_manual_run = False
            self._steamless_success = None
            # Keep _last_steamless_success for status dialog
            return

        if self._post_download_active:
            if PostDownloadStage.STEAMLESS in self._post_download_completed_stages:
                return
            self._complete_post_download_stage(PostDownloadStage.STEAMLESS)
            self._steamless_success = None
            self._queue_post_download_advance()
            return

        # Fallback for non-pipeline paths
        if self._steamless_success is not None:
            self._steamless_success = None

    def _clear_steamless_task(self):
        """Clear steamless task reference on next event loop tick"""
        logger.debug("Clearing steamless task reference")
        self.steamless_task = None

    def _show_steamless_resume_dialog(self):
        """Show the Steamless resume dialog with a summary of the processing results"""
        # Parse progress log to extract summary info
        exe_count = 0
        processed_count = 0
        no_drm_count = 0
        had_error = self._steamless_error

        for message in self._steamless_progress_log:
            # Count executables found (from directory scan)
            if (
                "Found " in message
                and "executable(s)" in message
                and "to evaluate" in message
            ):
                try:
                    # Extract number from "Found X executable(s) to evaluate"
                    parts = message.split()
                    for i, part in enumerate(parts):
                        if part == "Found" and i + 1 < len(parts):
                            exe_count = int(parts[i + 1])
                            break
                except (ValueError, IndexError):
                    pass

            # Count successfully processed (from directory scan)
            if "Successfully processed:" in message:
                processed_count += 1

            # For single exe runs, check for successful unpack
            if "Successfully unpacked file!" in message:
                processed_count += 1
                exe_count = max(exe_count, 1)  # At least one exe was processed

            # Count executables with no DRM
            if "No Steam DRM detected" in message:
                no_drm_count += 1
                exe_count = max(exe_count, no_drm_count)  # Track at least this many

        # If no executables found but we had messages, assume single exe run
        if exe_count == 0 and processed_count == 0:
            # Check if Steamless reported success
            for message in self._steamless_progress_log:
                if "Successfully unpacked file!" in message:
                    exe_count = 1
                    processed_count = 1
                    break

        # Determine overall success status
        # success = True means DRM was removed from at least one executable
        # success = False could mean no DRM found OR an error occurred
        actual_success = processed_count > 0 and not had_error

        # Show the dialog
        dialog = SteamlessResumeDialog(
            game_name=self._steamless_game_name,
            exe_count=exe_count,
            processed_count=processed_count,
            success=actual_success,
            parent=self.main_window,
        )
        dialog.exec()

        # Clear the progress log
        self._steamless_progress_log = []
        self._steamless_game_name = ""

    def _handle_steamless_task_error(self, error_info):
        """Handle Steamless task runner errors"""
        _, error_value, error_traceback = error_info
        logger.info(f"Error: {error_value}")
        logger.error(f"Processing failed: {error_value}")
        if error_traceback:
            logger.error("Traceback:\n%s", error_traceback)

        # Mark that Steamless had an error
        self._steamless_error = True

        # The thread has already finished (run() returned)
        # Defer cleanup to next event loop tick
        if self.steamless_task:
            logger.debug("Steamless thread error, scheduling cleanup")
            QTimer.singleShot(0, self._clear_steamless_task)

        if self._steamless_manual_run:
            logger.info("Manual processing failed")
            self._show_steamless_resume_dialog()
            self._steamless_manual_run = False
            self._steamless_success = None
            return

        if self._post_download_active:
            self._complete_post_download_stage(PostDownloadStage.STEAMLESS)
            self._queue_post_download_advance()
            return

        logger.warning(
            "Steamless error callback received outside post-download pipeline; ignoring transition."
        )

    def _start_achievement_generation(self):
        """Start achievement generation task"""
        if not self.game_data:
            logger.warning("No game_data found, skipping achievement generation")
            if self._post_download_active:
                self._complete_post_download_stage(PostDownloadStage.ACHIEVEMENTS)
                self._queue_post_download_advance()
            else:
                self._continue_after_download()
            return

        app_id = self.game_data.get("appid")
        if not app_id:
            logger.warning("No AppID found, skipping achievement generation")
            if self._post_download_active:
                self._complete_post_download_stage(PostDownloadStage.ACHIEVEMENTS)
                self._queue_post_download_advance()
            else:
                self._continue_after_download()
            return

        logger.info("\n" + "=" * 40)
        logger.info("Starting Steam Achievement Generation...")
        logger.info("Auto-detecting account from SLScheevo...")

        self.achievement_task = GenerateAchievementsTask()
        self.achievement_task.progress.connect(lambda msg: logger.info(f"{msg}"))
        # Do NOT connect progress_percentage to progress bar - achievement generation
        # happens after download completion and should not interfere with the 100% progress
        # self.achievement_task.progress_percentage.connect(self.progress_bar.setValue)

        self.achievement_task_runner = TaskRunner()
        self.achievement_worker = self.achievement_task_runner.run(
            self.achievement_task.run, app_id
        )
        self.achievement_task_runner.cleanup_complete.connect(
            self._on_achievement_task_cleanup
        )

        # Update status button color to show achievements running
        self._update_status_button_color()

        # Mark that SLScheevo is running
        self._slscheevo_ran = True

        self.achievement_worker.finished.connect(
            self._on_achievement_generation_complete
        )
        self.achievement_worker.error.connect(self._handle_achievement_error)

    def _on_achievement_generation_complete(self, result):
        """Handle achievement generation completion"""
        # Defensive check in case result is None
        if result is None:
            success = False
            message = "Unknown error: result is None"
        else:
            success = result.get("success", False)
            message = result.get("message", "Unknown status")

        # Store for status dialog
        self._last_slscheevo_success = success

        logger.info("\n" + "=" * 40)
        if success:
            logger.info(f"Achievement generation completed: {message}")
        else:
            logger.info(f"Achievement generation failed: {message}")

        # Cleanup will happen via TaskRunner's cleanup_complete signal
        # Do NOT set to None here - wait for proper cleanup
        logger.debug(
            "Achievement generation complete, waiting for TaskRunner cleanup..."
        )
        if self._post_download_active:
            self._complete_post_download_stage(PostDownloadStage.ACHIEVEMENTS)
            self._queue_post_download_advance()
            return
        logger.warning(
            "Achievements completion callback received outside post-download pipeline; ignoring transition."
        )

    def _handle_achievement_error(self, error_info):
        """Handle achievement generation errors"""
        _, error_value, _ = error_info
        logger.info(f"Achievement generation error: {error_value}")
        logger.error(
            f"Achievement generation failed: {error_value}", exc_info=error_info
        )

        # Mark as failed for status dialog
        self._last_slscheevo_success = False
        self._slscheevo_error = True

        # Cleanup will happen via TaskRunner's cleanup_complete signal
        # Do NOT set to None here - wait for proper cleanup
        logger.debug("Achievement generation error, waiting for TaskRunner cleanup...")
        if self._post_download_active:
            self._complete_post_download_stage(PostDownloadStage.ACHIEVEMENTS)
            self._queue_post_download_advance()
            return
        logger.warning(
            "Achievements error callback received outside post-download pipeline; ignoring transition."
        )

    def _on_achievement_task_cleanup(self):
        """Handle achievement task cleanup completion"""
        logger.debug("AchievementTask's worker has officially completed cleanup.")
        self.achievement_task_runner = None
        self.achievement_task = None
        self.achievement_worker = None
        self.main_window.job_queue._check_if_safe_to_start_next_job()

    def _on_application_shortcuts_task_cleanup(self):
        """Handle application shortcuts task cleanup completion"""
        logger.debug(
            "ApplicationShortcutsTask's worker has officially completed cleanup."
        )
        self.application_shortcuts_task_runner = None
        self.application_shortcuts_task = None
        self.main_window.job_queue._check_if_safe_to_start_next_job()

    def _start_application_shortcuts_processing(self):
        """Start application shortcuts creation after download completion"""
        if not self.game_data:
            logger.warning(
                "No game_data found, skipping application shortcuts creation"
            )
            if self._post_download_active:
                self._complete_post_download_stage(PostDownloadStage.SHORTCUTS)
                self._queue_post_download_advance()
            else:
                self._continue_after_download()
            return

        app_id = self.game_data.get("appid")
        game_name = self.game_data.get("game_name")
        if not app_id:
            logger.warning("No AppID found, skipping application shortcuts creation")
            if self._post_download_active:
                self._complete_post_download_stage(PostDownloadStage.SHORTCUTS)
                self._queue_post_download_advance()
            else:
                self._continue_after_download()
            return

        sgdb_api_key = self.settings.value("sgdb_api_key", "", type=str)
        if not sgdb_api_key:
            logger.warning(
                "No Steam Grid DB API key configured, skipping application shortcuts creation"
            )
            if self._post_download_active:
                self._complete_post_download_stage(PostDownloadStage.SHORTCUTS)
                self._queue_post_download_advance()
            else:
                self._continue_after_download()
            return

        logger.info("\n" + "=" * 40)
        logger.info("Starting Application Shortcuts Creation...")
        logger.info("Using Steam Grid DB API for icons and desktop entries...")

        self.application_shortcuts_task = ApplicationShortcutsTask()
        self.application_shortcuts_task.set_api_key(sgdb_api_key)
        self.application_shortcuts_task.progress.connect(logger.info)
        # Do NOT connect progress_percentage to progress bar - application shortcuts
        # happens after download completion and should not interfere with the 100% progress

        self.application_shortcuts_task_runner = TaskRunner()
        self.application_shortcuts_task_runner.cleanup_complete.connect(
            self._on_application_shortcuts_task_cleanup
        )

        worker = self.application_shortcuts_task_runner.run(
            self.application_shortcuts_task.run, app_id, game_name
        )
        worker.finished.connect(self._on_application_shortcuts_complete)
        worker.error.connect(self._handle_application_shortcuts_error)

    def _on_application_shortcuts_complete(self, result):
        """Handle application shortcuts creation completion"""
        success = result  # ApplicationShortcutsTask directly returns a boolean
        logger.info("\n" + "=" * 40)
        if success:
            logger.info("Application shortcuts creation completed successfully")
        else:
            logger.info("Application shortcuts creation failed")

        # Cleanup will happen via TaskRunner's cleanup_complete signal
        # Do NOT set to None here - wait for proper cleanup
        logger.debug(
            "Application shortcuts complete, waiting for TaskRunner cleanup..."
        )

        if self._post_download_active:
            self._complete_post_download_stage(PostDownloadStage.SHORTCUTS)
            self._queue_post_download_advance()
            return
        logger.warning(
            "Application shortcuts completion callback received outside post-download pipeline; ignoring transition."
        )

    def _handle_application_shortcuts_error(self, error_info):
        """Handle application shortcuts creation errors"""
        _, error_value, _ = error_info
        logger.info(f"Application shortcuts creation error: {error_value}")
        logger.error(
            f"Application shortcuts creation failed: {error_value}", exc_info=error_info
        )

        # Cleanup will happen via TaskRunner's cleanup_complete signal
        # Do NOT set to None here - wait for proper cleanup
        logger.debug("Application shortcuts error, waiting for TaskRunner cleanup...")

        if self._post_download_active:
            self._complete_post_download_stage(PostDownloadStage.SHORTCUTS)
            self._queue_post_download_advance()
            return
        logger.warning(
            "Application shortcuts error callback received outside post-download pipeline; ignoring transition."
        )

    def _continue_after_download(self):
        """Continue with the normal download completion flow"""
        if self.slssteam_mode_was_active:
            self.main_window.job_queue.slssteam_prompt_pending = True

        self.main_window.job_queue.jobs_completed_count += 1

        # Auto-scan the game library after download completes
        if not self.is_cancelling:
            logger.info("Auto-scanning game library for updated games...")
            self.main_window.game_manager.scan_steam_libraries_async()

        self.job_finished()

    def _add_appids_to_slssteam_config(self, game_data=None):
        """Add downloaded AppIDs and DLCs to SLSsteam config.yaml on Linux."""
        game_data = game_data or self.game_data

        if not game_data:
            logger.warning("No game_data available, skipping SLSsteam config update")
            return

        try:
            config_path = get_user_config_path()
            if not config_path.exists():
                logger.debug(
                    f"SLSsteam config not found at {config_path}, skipping AppID addition"
                )
                return

            # Add main game AppID to AdditionalApps
            main_appid = game_data.get("appid")
            game_name = game_data.get("game_name", "")
            if main_appid:
                added = add_list_item(
                    config_path, "AdditionalApps", str(main_appid), game_name
                )
                if added:
                    logger.info(
                        f"Added main AppID '{main_appid}' to SLSsteam AdditionalApps"
                    )

            # Add selected DLCs to DlcData only if > 64 DLCs (Steam limit)
            selected_dlcs = game_data.get("selected_dlcs", [])
            dlcs = game_data.get("dlcs", {})

            if main_appid and selected_dlcs and len(selected_dlcs) > 64:
                logger.info(
                    f"Game has {len(selected_dlcs)} DLCs (>64), adding to DlcData"
                )
                for dlc_id in selected_dlcs:
                    dlc_name = dlcs.get(dlc_id, "")
                    added = add_dlc_data(
                        config_path, str(main_appid), str(dlc_id), dlc_name
                    )
                    if added:
                        logger.info(
                            f"Added DLC '{dlc_name}' ({dlc_id}) to SLSsteam DlcData"
                        )
            else:
                logger.debug(
                    f"Game has {len(selected_dlcs)} DLCs, skipping DlcData "
                    f"(only needed for >64 DLCs)"
                )

        except Exception as e:
            logger.warning(
                f"Failed to add AppIDs to SLSsteam config: {e}", exc_info=True
            )

    def _handle_task_error(self, error_info):
        """Handle general task errors"""
        if self.is_cancelling:
            logger.info(
                "Task error signal received, but job was cancelled. Suppressing error message."
            )
            return

        if not self.is_processing:
            logger.warning(
                f"Task error received, but no job is processing. Ignoring. Error: {error_info}"
            )
            return

        _, error_value, _ = error_info
        QMessageBox.critical(
            self.main_window, "Error", f"An error occurred: {error_value}"
        )

        self._last_ddm_status = "error"
        self._last_ddm_status_text = "Failed"

        self.job_finished()

    def job_finished(self):
        """Clean up after job completion"""
        if not self.is_processing:
            logger.warning("_job_finished called, but no job is processing. Ignoring.")
            return

        logger.info(
            f"Job '{Path(self.current_job or 'Unknown').name}' finished. Cycling to next job."
        )

        # Store last installed game name for status dialog
        if self.game_data:
            self._last_installed_game = self.game_data.get("game_name", "Unknown")

        # Update status for this job based on actual results
        # DDM: "ok" if not cancelled (download completed), "error" if cancelled
        ddm_ok = not self.is_cancelling
        # SLScheevo: "ok" if ran without error, "error" if error, None = didn't run
        if not self._slscheevo_ran:
            slscheevo_ok = None  # Didn't run
        elif self._slscheevo_error:
            slscheevo_ok = False  # Error occurred
        else:
            slscheevo_ok = True  # Ran successfully
        # Steamless: "ok" if ran without error (whether DRM found or not), "error" if error, None = didn't run
        # _last_steamless_success being False means "no DRM found" - that's still ok!
        # Only _steamless_error means actual failure
        if not self._steamless_ran:
            steamless_ok = None  # Didn't run
        elif self._steamless_error:
            steamless_ok = False  # Error occurred
        else:
            steamless_ok = True  # Ran successfully (DRM removed or not found)

        if self._last_ddm_status != "error":
            self._update_status_for_job(
                ddm_ok=ddm_ok,
                slscheevo_ok=slscheevo_ok,
                steamless_ok=steamless_ok,
            )
        else:
            # DDM already marked failed; only update the sub-task statuses.
            self._update_status_for_job(
                ddm_ok=False,
                slscheevo_ok=slscheevo_ok,
                steamless_ok=steamless_ok,
            )

        # Clear state BEFORE updating button color
        # This ensures get_component_status() returns the final status, not "in_progress"
        self.main_window.ui_state._show_main_gif()
        self.main_window.progress_bar.setVisible(False)
        self.main_window.speed_label.setVisible(False)
        self.game_data = None
        self.current_dest_path = None
        self.current_job_metadata = None
        self.slssteam_mode_was_active = False
        self.is_processing = False

        # Update status button color based on final status
        self._update_status_button_color()
        self.current_job = None

        self.is_download_paused = False
        self.main_window.ui_state.pause_button.setVisible(False)
        self.main_window.ui_state.cancel_button.setVisible(False)
        self.download_task = None
        self.is_cancelling = False
        self._delete_files_on_cancel = None
        self._post_download_active = False
        self._post_download_current_stage = None
        self._post_download_completed_stages = set()
        self._post_download_abort_remaining = False
        # Achievement and steamless clean up via their own signals/threads - don't clear here

        logger.info("\n" + "=" * 40 + "\n")

        if self.speed_monitor_task:
            # Speed monitor is still running; stop it and wait for cleanup.
            logger.debug("Job finished, telling speed monitor to stop.")
            self.is_awaiting_speed_monitor_stop = True
            self._stop_speed_monitor()
        elif self.speed_monitor_runner:
            logger.debug("Job finished, speed monitor stopping (runner still active).")
            self.is_awaiting_speed_monitor_stop = True
        else:
            self.is_awaiting_speed_monitor_stop = False
            logger.debug("Job finished, no speed monitor running.")

        if self.download_runner is None:
            self.is_awaiting_download_stop = False

        if self.zip_task_runner is None:
            self.is_awaiting_zip_task_stop = False

        self.main_window.job_queue._check_if_safe_to_start_next_job()

    def _update_status_button_color(self):
        """Update the status button color based on current component status"""
        status = self.get_component_status()

        # Get accent color from settings for initial "nothing ran" state
        settings = self.main_window.settings
        accent_color = settings.value("accent_color", "#C06C84")

        # Check status strings directly
        ddm_status = status["ddm_status"]
        slscheevo_status = status["slscheevo_status"]
        steamless_status = status["steamless_status"]

        # Priority:
        # 1. Any error → red
        # 2. Any in_progress → orange
        # 3. At least one component that ran is "ok" → green
        # 4. All are "not_run" (nothing ran yet) → accent_color
        if (
            ddm_status == "error"
            or slscheevo_status == "error"
            or steamless_status == "error"
        ):
            overall_color = self.STATUS_ERROR
        elif (
            ddm_status == "in_progress"
            or slscheevo_status == "in_progress"
            or steamless_status == "in_progress"
        ):
            overall_color = self.STATUS_IN_PROGRESS
        elif ddm_status == "ok" or slscheevo_status == "ok" or steamless_status == "ok":
            overall_color = self.STATUS_OK
        else:
            # All are "not_run" — nothing has run yet
            overall_color = accent_color

        # Update the button color
        self.main_window.bottom_titlebar._update_colored_circle_button(
            self.main_window.bottom_titlebar.status_button, overall_color
        )
        # Prevent accent color override on subsequent calls
        self.main_window.bottom_titlebar.no_previous_state = False

    def toggle_pause(self):
        """Toggle download pause/resume"""
        if not self.download_task:
            return

        self.is_download_paused = not self.is_download_paused

        try:
            self.download_task.toggle_pause(self.is_download_paused)
            if self.is_download_paused:
                self.main_window.ui_state.pause_button.setText("Resume")
                current_job_name = (
                    Path(self.current_job).name if self.current_job else "Unknown"
                )
                self.main_window.drop_text_label.setText(f"Paused: {current_job_name}")
                self._stop_speed_monitor()
            else:
                self.main_window.ui_state.pause_button.setText("Pause")
                current_job_name = (
                    Path(self.current_job).name if self.current_job else "Unknown"
                )
                self.main_window.drop_text_label.setText(
                    f"Downloading: {current_job_name}"
                )
                self._start_speed_monitor()
        except Exception as e:
            logger.error(f"Failed to toggle pause: {e}")
            QMessageBox.warning(
                self.main_window, "Error", f"Could not pause/resume download: {e}"
            )

    def cancel_current_job(self):
        """Cancel the current job"""
        if not self.download_task or not self.current_job:
            logger.warning(
                "Cancel button clicked, but no download task or job is active."
            )
            return

        reply = QMessageBox.question(
            self.main_window,
            "Cancel Job",
            f"Are you sure you want to cancel the download for '{Path(self.current_job).name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.No:
            return

        logger.info(f"--- Cancelling job: {Path(self.current_job).name} ---")
        self.is_cancelling = True
        if self.download_runner is not None:
            self.is_awaiting_download_stop = True

        existing_install = self._detect_existing_installation()
        self._delete_files_on_cancel = self._confirm_delete_on_cancel(existing_install)

        if self.download_task:
            self.download_task.stop()
        self._kill_download_process()

        if self.achievement_task:
            logger.info("Stopping achievement generation task...")
            self.achievement_task.stop()

        if self.steamless_task:
            logger.info("Stopping Steamless task...")
            self.steamless_task.stop()

        if self.application_shortcuts_task:
            logger.info("Stopping application shortcuts task...")
            self.application_shortcuts_task.stop()

    def _detect_existing_installation(self) -> bool:
        """Detect if there is an existing install for this job."""
        if not self.current_dest_path or not self.game_data:
            return False

        current_job_metadata = self.current_job_metadata or {}
        install_path = current_job_metadata.get("install_path")
        if install_path and Path(install_path).exists():
            return True

        install_folder_name = self._get_install_folder_name()

        steamapps_dir = Path(self.current_dest_path) / "steamapps"
        appmanifest_path = (
            steamapps_dir / f"appmanifest_{self.game_data.get('appid', '')}.acf"
        )
        if appmanifest_path.exists():
            return True

        game_dir = steamapps_dir / "common" / install_folder_name
        if game_dir.is_dir():
            try:
                with os.scandir(game_dir) as entries:
                    for _ in entries:
                        return True
            except OSError:
                return True

        return False

    def _confirm_delete_on_cancel(self, existing_install: bool) -> bool:
        """Ask whether to delete files after canceling."""
        if existing_install:
            message = (
                "An existing installation was detected.\n\n"
                "Do you want to delete the files for this canceled job?"
            )
            default_button = QMessageBox.StandardButton.No
        else:
            message = (
                "Do you want to delete the partially downloaded files for this job?"
            )
            default_button = QMessageBox.StandardButton.Yes

        reply = QMessageBox.question(
            self.main_window,
            "Cancel Download",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            default_button,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _kill_download_process(self):
        """Kill the download process"""
        if self.download_task and self.download_task.process:
            if psutil is None:
                logger.error(
                    "psutil is not available; cannot terminate download process safely."
                )
                return
            logger.info("Terminating active download process...")
            try:
                p = psutil.Process(self.download_task.process.pid)
                for child in p.children(recursive=True):
                    try:
                        child.kill()
                    except psutil.NoSuchProcess:
                        pass
                p.kill()
                logger.info("Download process terminated.")
            except psutil.NoSuchProcess:
                logger.warning(
                    f"Process {self.download_task.process.pid} already exited."
                )
            except Exception as e:
                logger.error(f"Failed to kill process: {e}")

            self.download_task.process = None
            self.download_task.process_pid = None

    def _cleanup_cancelled_job_files(self):
        """Clean up files from cancelled job"""
        if not self.game_data or not self.current_dest_path:
            logger.error(
                "Cancel cleanup failed: missing game_data or current_dest_path."
            )
            return

        try:
            install_folder_name = self._get_install_folder_name()

            steamapps_dir = Path(self.current_dest_path) / "steamapps"
            common_dir = steamapps_dir / "common"
            game_dir = common_dir / install_folder_name
            acf_path = steamapps_dir / f"appmanifest_{self.game_data['appid']}.acf"

            if game_dir.exists():
                shutil.rmtree(game_dir)
                logger.info(f"Removed cancelled job directory: {game_dir}")
            else:
                logger.info(
                    f"Download directory not found, nothing to clean: {game_dir}"
                )

            if acf_path.exists():
                acf_path.unlink()
                logger.info(f"Removed cancelled job manifest: {acf_path}")

            temp_manifest_dir = Path(tempfile.gettempdir()) / "mistwalker_manifests"
            if temp_manifest_dir.exists():
                try:
                    shutil.rmtree(temp_manifest_dir)
                    logger.info(
                        f"Removed temporary manifest directory: {temp_manifest_dir}"
                    )
                    logger.info(
                        f"Removed temp manifest dir on cancel: {temp_manifest_dir}"
                    )
                except Exception as e:
                    logger.error(f"Failed to remove temp manifest dir on cancel: {e}")

            if not self.slssteam_mode_was_active:
                logger.info(
                    "Normal mode: Attempting to clean up empty parent directories..."
                )
                try:
                    if common_dir.exists():
                        common_dir.rmdir()
                        logger.info(f"Removed empty common dir: {common_dir}")

                    if steamapps_dir.exists():
                        steamapps_dir.rmdir()
                        logger.info(f"Removed empty steamapps dir: {steamapps_dir}")

                except OSError as e:
                    logger.warning(
                        f"Could not remove parent directory (likely not empty): {e}"
                    )
                except Exception as e:
                    logger.error(
                        f"Error during parent directory cleanup: {e}", exc_info=True
                    )
            else:
                logger.info("Wrapper mode: Skipping parent directory cleanup.")

        except Exception as e:
            logger.error(f"Failed during cancel cleanup: {e}", exc_info=True)

    def download_slssteam(self):
        """Install/update SLSsteam using ACCELA's install-sls flow."""
        logger.info("Starting SLSsteam installation/update")

        # Check if already running
        if (
            self.slssteam_download_task is not None
            and self.slssteam_download_runner is not None
        ):
            QMessageBox.information(
                self.main_window,
                "Already Running",
                "SLSsteam installation is already in progress. Please wait for it to complete.",
            )
            return

        self.slssteam_download_task = DownloadSLSsteamTask()
        self.slssteam_download_task.progress.connect(self._handle_slssteam_progress)
        self.slssteam_download_task.progress_percentage.connect(
            self._handle_slssteam_progress_percentage
        )
        self.slssteam_download_task.completed.connect(
            self._on_slssteam_download_complete
        )
        self.slssteam_download_task.error.connect(self._handle_slssteam_download_error)

        self.slssteam_download_runner = TaskRunner()
        self.slssteam_download_runner.cleanup_complete.connect(
            self._on_slssteam_download_task_cleanup
        )
        worker = self.slssteam_download_runner.run(self.slssteam_download_task.run)
        worker.error.connect(self._handle_task_error)

    def _handle_slssteam_progress(self, message):
        """Handle SLSsteam install progress messages"""
        logger.info(f"SLSsteam: {message}")

    def _handle_slssteam_progress_percentage(self, percentage):
        """Handle SLSsteam download progress percentage"""
        # Silent progress updates to avoid log spam

    def _on_slssteam_download_complete(self, message):
        """Handle SLSsteam install completion"""
        logger.info(f"SLSsteam installation completed: {message}")
        QMessageBox.information(
            self.main_window, "SLSsteam Installation Complete", message
        )

    def _on_slssteam_download_task_cleanup(self):
        """Handle SLSsteam download task cleanup completion"""
        logger.debug("SLSsteamDownloadTask's worker has officially completed cleanup.")
        self.slssteam_download_runner = None
        self.slssteam_download_task = None

    def _handle_slssteam_download_error(self, *args):
        """Handle SLSsteam install errors"""
        logger.error("SLSsteam installation failed")
        QMessageBox.critical(
            self.main_window,
            "Error",
            "Failed to install/update SLSsteam. See the progress output above for details or check application logs.",
        )

    def cleanup(self):
        """Clean up all tasks during shutdown"""
        self._stop_speed_monitor()

        if self.download_task and self.download_task.process:
            self.download_task.stop()
            self._kill_download_process()

        if self.achievement_task:
            self.achievement_task.stop()

        if self.steamless_task:
            self.steamless_task.stop()

        if self.application_shortcuts_task:
            self.application_shortcuts_task.stop()
            self.application_shortcuts_task_runner = None
            self.application_shortcuts_task = None

        if self.slssteam_download_task:
            self.slssteam_download_task.stop()
            self.slssteam_download_runner = None
            self.slssteam_download_task = None

        if self.post_finalize_runner:
            self.post_finalize_runner.stop(wait_ms=0, terminate_on_timeout=True)
            self.post_finalize_runner = None

        TaskRunner.stop_all_active()

    def get_component_status(self):
        """Get status of DDM, SLScheevo, and Steamless for the last job.

        Returns:
            dict: Status information for each component with keys:
                - ddm_status: "ok", "in_progress", "error", or "not_run"
                - ddm_status_text: Human-readable status text
                - slscheevo_status: "ok", "in_progress", "error", or "not_run"
                - slscheevo_status_text: Human-readable status text
                - steamless_status: "ok", "in_progress", "error", or "not_run"
                - steamless_status_text: Human-readable status text
        """

        ddm_status = self._last_ddm_status
        ddm_status_text = self._last_ddm_status_text
        slscheevo_status = self._last_slscheevo_status
        slscheevo_status_text = self._last_slscheevo_status_text
        steamless_status = self._last_steamless_status
        steamless_status_text = self._last_steamless_status_text

        if self.is_processing:
            if self.download_task or self.zip_task:
                ddm_status = "in_progress"
                ddm_status_text = "Downloading..."
            elif (
                self._post_download_active
                and self._post_download_current_stage == PostDownloadStage.FINALIZE
            ):
                ddm_status = "in_progress"
                ddm_status_text = "Finalizing..."
            elif self.steamless_task:
                ddm_status = "ok"
                ddm_status_text = "Completed"
                steamless_status = "in_progress"
                steamless_status_text = "Running..."
            elif self.achievement_task:
                ddm_status = "ok"
                ddm_status_text = "Completed"
                slscheevo_status = "in_progress"
                slscheevo_status_text = "Generating achievements..."

        return {
            "ddm_status": ddm_status,
            "ddm_status_text": ddm_status_text,
            "slscheevo_status": slscheevo_status,
            "slscheevo_status_text": slscheevo_status_text,
            "steamless_status": steamless_status,
            "steamless_status_text": steamless_status_text,
        }

    def _get_steamless_status_text(self):
        """Get Steamless status text based on _last_steamless_success."""
        if self._last_steamless_success is None:
            return "Ready"
        elif self._last_steamless_success:
            return "DRM removed"
        else:
            return "Completed (no DRM found)"

    def _get_install_folder_name_from_data(self, game_data) -> str:
        """Return a sanitized install folder name from provided game data."""
        if not game_data:
            return ""
        safe_fallback = (
            re.sub(r"[^\w\s-]", "", game_data.get("game_name", ""))
            .strip()
            .replace(" ", "_")
        )
        name = game_data.get("installdir") or safe_fallback
        return name or f"App_{game_data['appid']}"

    def _update_status_for_job(self, ddm_ok=True, slscheevo_ok=None, steamless_ok=None):
        """Update status tracking after a job completes.

        Args:
            ddm_ok: Whether DDM completed successfully (True/False)
            slscheevo_ok: Whether SLScheevo completed successfully (True/False/None=didn't run)
            steamless_ok: Whether Steamless completed successfully (True/False/None=didn't run)
        """
        self._last_ddm_status = "ok" if ddm_ok else "error"
        self._last_ddm_status_text = "Completed" if ddm_ok else "Failed"

        # SLScheevo status
        if slscheevo_ok is None:
            self._last_slscheevo_status = "not_run"
            self._last_slscheevo_status_text = "N/A"
        else:
            self._last_slscheevo_status = "ok" if slscheevo_ok else "error"
            self._last_slscheevo_status_text = "Completed" if slscheevo_ok else "Failed"

        if steamless_ok is None:
            self._last_steamless_status = "not_run"
            self._last_steamless_status_text = "N/A"
        elif steamless_ok:
            self._last_steamless_status = "ok"
            self._last_steamless_status_text = self._get_steamless_status_text()
        else:
            self._last_steamless_status = "error"
            self._last_steamless_status_text = "Failed"
