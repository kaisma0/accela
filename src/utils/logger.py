import logging
import os
import sys
import subprocess
from pathlib import Path
from datetime import datetime
from PyQt6.QtCore import QObject, pyqtSignal
from utils.helpers import get_base_path


class QtLogHandler(QObject, logging.Handler):
    new_record = pyqtSignal(str)
    # Prevent logging.shutdown() from trying to flush this handler
    # when the Qt C++ object has already been deleted
    flushOnClose = False

    def __init__(self):
        QObject.__init__(self)
        logging.Handler.__init__(self)

        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        self.setFormatter(formatter)

    def emit(self, record):
        try:
            from utils.settings import get_settings

            settings = get_settings()
            debug_mode = settings.value("ui_debug_mode", False, type=bool)

            # Hide INFO and lower level logs in UI if debug mode is off
            if not debug_mode and record.levelno < logging.WARNING:
                return

            msg = self.format(record)
            self.new_record.emit(msg)
        except RuntimeError:
            # Qt object has been deleted
            pass

    def flush(self):
        # No-op to avoid issues with deleted Qt objects
        pass

    def close(self):
        # No-op to avoid issues with deleted Qt objects
        pass


qt_log_handler = QtLogHandler()
_current_log_name = None
_MAX_PREVIOUS_LOGS = 4
app_name_lower = "accela"
log_dir = get_base_path() / "logs"
logger = logging.getLogger(__name__)


def setup_logging():
    """Setup logging with timestamped log files"""

    from utils.settings import get_settings

    log_path = get_log_path()

    # Clean up old logs in the correctly resolved directory
    cleanup_old_logs()

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    handlers = []

    # File handler - Always create new timestamped log file
    try:
        file_handler = logging.FileHandler(
            log_path,
            mode="w",  # Create new file for each session
            encoding="utf-8",
            delay=False,
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)

        print(f"Log file created: {log_path}", file=sys.stderr)
    except (PermissionError, OSError) as e:
        print(f"Error: Could not create log file at {log_path}: {e}", file=sys.stderr)
        # Try TEMP directory as fallback
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            temp_dir = Path(os.environ.get("TEMP", str(Path.cwd())))
            fallback_path = temp_dir / f"{app_name_lower}_{timestamp}.log"
            file_handler = logging.FileHandler(
                fallback_path, mode="w", encoding="utf-8"
            )
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(formatter)
            handlers.append(file_handler)
            print(f"Using fallback log: {fallback_path}", file=sys.stderr)
        except Exception as e2:
            print(f"Could not create fallback log either: {e2}", file=sys.stderr)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    handlers.append(console_handler)

    # Qt handler
    # Keep handler permissive so emit() can enforce ui_debug_mode dynamically.
    qt_log_handler.setLevel(logging.DEBUG)
    qt_log_handler.setFormatter(formatter)
    handlers.append(qt_log_handler)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # Reduce noise from third-party libraries when offline
    logging.getLogger("CMServerList").setLevel(logging.CRITICAL)

    # Clear existing handlers to avoid duplicates
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Add new handlers
    for handler in handlers:
        root_logger.addHandler(handler)

    # Log configuration details
    settings = get_settings()
    debug_mode = settings.value("ui_debug_mode", False, type=bool)
    qt_ui_level = "INFO" if debug_mode else "WARNING"

    logger.info("Logging Initialized")
    logger.info("Python: %s", sys.version)
    logger.info("Log file: %s", log_path)
    logger.info("File level: DEBUG")
    logger.info("Console level: INFO")
    logger.info("Qt GUI level: %s", qt_ui_level)

    return logger


def open_log_directory():
    """Open the log directory in the system file manager"""
    global log_dir

    try:
        subprocess.run(["xdg-open", str(log_dir)], check=False)
        return True
    except Exception as e:
        logger.error("Failed to open log directory: %s", e)
        return False


def get_log_path():
    """Return path to a timestamped log file with counter if needed."""
    global _current_log_name, log_dir

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Fallback to temp directory
        temp_dir = (
            Path(os.environ.get("TEMP", str(Path.cwd()))) / "logs" / app_name_lower
        )
        temp_dir.mkdir(parents=True, exist_ok=True)
        log_dir = temp_dir

    # Base name with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"{app_name_lower}_{timestamp}"

    # Find next available filename
    counter = 1
    while True:
        if counter == 1:
            log_name = f"{base_name}.log"
        else:
            log_name = f"{base_name}_{counter}.log"

        log_path = log_dir / log_name
        if not log_path.exists():
            break
        counter += 1

    _current_log_name = log_name
    return log_path


def cleanup_old_logs():
    """Clean up old log files on startup."""
    global _MAX_PREVIOUS_LOGS, log_dir

    if not log_dir.exists():
        return

    # Get all accela*.log files
    log_files = [f for f in log_dir.glob(f"{app_name_lower}*.log") if f.is_file()]

    if not log_files:
        return

    # Sort by modification time (newest first)
    log_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

    # Keep only the N most recent files
    for old_log in log_files[_MAX_PREVIOUS_LOGS:]:
        try:
            old_log.unlink()
            print(f"Removed old log file: {old_log.name}", file=sys.stderr)
        except OSError as e:
            print(f"Could not remove {old_log.name}: {e}", file=sys.stderr)
