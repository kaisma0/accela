import logging
import threading
from pathlib import Path
from PyQt6.QtWidgets import QMessageBox, QFileDialog
from PyQt6.QtCore import QObject, pyqtSignal, QThread

from core import steam_helpers

logger = logging.getLogger(__name__)


class JobQueueManager(QObject):
    _queue_add_requested = pyqtSignal(str, object)
    _steam_restart_finished = pyqtSignal(str)

    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        self.job_queue = []  # List of dicts: {"path": file_path, "metadata": {...}}
        self.jobs_completed_count = 0
        self.slssteam_prompt_pending = False
        self.is_showing_completion_dialog = False
        self._queue_add_requested.connect(self.add_job)
        self._steam_restart_finished.connect(self._handle_steam_restart_result)

    def add_job(self, file_path, metadata=None):
        """Add a job to the queue

        Args:
            file_path: Path to the manifest file
            metadata: Optional dict with job metadata (appid, library_path, install_path)
        """
        if QThread.currentThread() != self.thread():
            self._queue_add_requested.emit(str(file_path), metadata)
            return

        if not Path(file_path).exists():
            logger.error(f"Failed to add job: file {file_path} does not exist.")
            QMessageBox.critical(
                self.main_window,
                "Error",
                f"Could not add job: File not found at {file_path}",
            )
            return

        job = {"path": file_path, "metadata": metadata or {}}
        self.job_queue.append(job)
        logger.info(f"Added new job to queue: {Path(file_path).name}")

        self._update_ui_state()

        if not self.main_window.task_manager.is_processing:
            logger.info("Not processing, starting new job from queue.")
            self.main_window.log_output.clear()
            self._start_next_job()
        else:
            logger.info("App is busy, job added to queue.")

    def move_item_up(self):
        """Move selected queue item up"""
        current_row = self.main_window.ui_state.queue_list_widget.currentRow()
        if current_row > 0:
            item = self.job_queue.pop(current_row)
            self.job_queue.insert(current_row - 1, item)
            self._update_queue_display()
            self.main_window.ui_state.queue_list_widget.setCurrentRow(current_row - 1)

    def move_item_down(self):
        """Move selected queue item down"""
        current_row = self.main_window.ui_state.queue_list_widget.currentRow()
        if current_row != -1 and current_row < len(self.job_queue) - 1:
            item = self.job_queue.pop(current_row)
            self.job_queue.insert(current_row + 1, item)
            self._update_queue_display()
            self.main_window.ui_state.queue_list_widget.setCurrentRow(current_row + 1)

    def remove_item(self):
        """Remove selected queue item"""
        current_row = self.main_window.ui_state.queue_list_widget.currentRow()
        if current_row == -1:
            logger.debug("Remove item clicked, but no item is selected.")
            return

        try:
            removed_job = self.job_queue.pop(current_row)
            logger.info(f"Removed job from queue: {Path(removed_job['path']).name}")
            self._update_queue_display()

            if current_row < self.main_window.ui_state.queue_list_widget.count():
                self.main_window.ui_state.queue_list_widget.setCurrentRow(current_row)
            elif self.main_window.ui_state.queue_list_widget.count() > 0:
                self.main_window.ui_state.queue_list_widget.setCurrentRow(
                    current_row - 1
                )

        except Exception as e:
            logger.error(f"Error removing queue item: {e}", exc_info=True)

    def _start_next_job(self):
        """Start the next job in queue"""
        self._update_ui_state()

        if not self.job_queue:
            self._handle_queue_completion()
            return

        next_job = self.job_queue[0]
        file_path = next_job["path"]
        metadata = next_job.get("metadata", {})
        self.main_window.task_manager.start_zip_processing(file_path, metadata)
        self.job_queue.pop(0)
        self._update_ui_state()

    def _handle_queue_completion(self):
        """Handle when queue is empty"""
        if self.is_showing_completion_dialog:
            return

        self.is_showing_completion_dialog = True
        try:
            was_pending = self.slssteam_prompt_pending
            self.slssteam_prompt_pending = False

            if was_pending:
                from utils.settings import get_settings

                settings = get_settings()
                prompt_steam_restart = settings.value(
                    "prompt_steam_restart", True, type=bool
                )

                if prompt_steam_restart:
                    self._prompt_for_steam_restart()
                else:
                    logger.info(
                        "Steam restart prompt disabled by settings. Skipping prompt."
                    )
            elif self.jobs_completed_count > 0:
                QMessageBox.information(
                    self.main_window,
                    "Queue Finished",
                    f"All {self.jobs_completed_count} job(s) have finished successfully!",
                )

            self.jobs_completed_count = 0
        finally:
            self.is_showing_completion_dialog = False

    def _update_ui_state(self):
        """Update UI based on queue state"""
        has_jobs = len(self.job_queue) > 0
        is_processing = self.main_window.task_manager.is_processing

        if not self.main_window.isVisible():
            return

        self.main_window.ui_state.update_queue_visibility(is_processing, has_jobs)
        self._update_queue_display()

    def _update_queue_display(self):
        """Update the queue list widget"""
        if not self.main_window.isVisible():
            return

        self.main_window.ui_state.queue_list_widget.clear()
        self.main_window.ui_state.queue_list_widget.addItems(
            [Path(job["path"]).name for job in self.job_queue]
        )

    def _check_if_safe_to_start_next_job(self):
        """Check if it's safe to start the next job"""
        tm = self.main_window.task_manager

        is_busy = (
            tm.is_processing
            or tm.is_awaiting_zip_task_stop
            or tm.is_awaiting_speed_monitor_stop
            or tm.is_awaiting_download_stop
            or tm.achievement_task_runner is not None
        )

        if not is_busy:
            logger.debug("All thread cleanup flags are clear. Safe to start next job.")
            self._start_next_job()
        else:
            logger.debug(
                f"Not starting next job yet. State: "
                f"is_processing={tm.is_processing}, "
                f"awaiting_zip={tm.is_awaiting_zip_task_stop}, "
                f"awaiting_speed={tm.is_awaiting_speed_monitor_stop}, "
                f"awaiting_download={tm.is_awaiting_download_stop}, "
                f"achievement_runner={tm.achievement_task_runner is not None}"
            )

    def _get_library_path(self, title, filter_str):
        """Helper to DRY up file dialogs for missing libraries"""
        file_path, _ = QFileDialog.getOpenFileName(
            self.main_window, title, str(Path.home()), filter_str
        )
        return file_path

    def _prompt_for_steam_restart(self):
        """Prompt user to restart Steam with complete restart logic"""
        title = "SLSsteam Integration"
        reply = QMessageBox.question(
            self.main_window,
            title,
            "Wrapper files have been created. Would you like to restart Steam now to apply the changes?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            logger.info("User agreed to restart Steam.")

            self.main_window.setEnabled(False)
            threading.Thread(target=self._restart_steam_worker, daemon=True).start()

    def _restart_steam_worker(self):
        """Kill and restart Steam in a worker thread, then report result to the UI thread."""
        try:
            logger.info("Attempting to kill Steam process...")
            steam_helpers.kill_steam_process()
            result = steam_helpers.start_steam()
        except Exception as e:
            logger.error(f"Steam restart worker failed: {e}", exc_info=True)
            result = "ERROR"
        self._steam_restart_finished.emit(result)

    def _handle_steam_restart_result(self, result: str):
        """Handle steam restart result on the UI thread."""
        self.main_window.setEnabled(True)

        if result == "NEEDS_USER_PATH":
            logger.warning("SLSsteam libraries not found. Please locate them manually.")
            self.handle_linux_steam_path_selection()
        elif result == "SUCCESS":
            logger.info("Steam started successfully with cached libraries.")
        else:
            logger.warning("Failed to start Steam.")
            QMessageBox.warning(
                self.main_window, "Execution Failed", "Could not start Steam."
            )

    def handle_linux_steam_path_selection(self):
        """Prompt for Linux SLSsteam library paths and retry Steam startup."""
        file_path_1 = self._get_library_path(
            "Select SLSsteam.so", "SLSsteam.so (SLSsteam.so libSLSsteam.so)"
        )
        if not file_path_1:
            logger.info("User cancelled file selection for SLSsteam.so")
            return

        file_path_2 = self._get_library_path(
            "Select library-inject.so",
            "library-inject.so (library-inject.so libSLS-library-inject.so)",
        )
        if not file_path_2:
            logger.info("User cancelled file selection for library-inject.so")
            return

        result = steam_helpers.start_steam_with_slssteam(file_path_1, file_path_2)
        if result == "SUCCESS":
            logger.info("Started Steam with SLSsteam.so and library-inject.so")
        elif result == "NEEDS_USER_PATH":
            QMessageBox.warning(
                self.main_window,
                "Execution Failed",
                "One or both of the selected library files are invalid or don't exist.",
            )
        else:
            QMessageBox.warning(
                self.main_window,
                "Execution Failed",
                "Could not start Steam with the selected libraries.",
            )

    def clear(self):
        """Clear the job queue"""
        self.job_queue.clear()
        self._update_ui_state()
