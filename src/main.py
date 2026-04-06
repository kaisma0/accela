import argparse
import multiprocessing
import sys
from pathlib import Path
from urllib.parse import unquote


from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, QTimer

QApplication.setHighDpiScaleFactorRoundingPolicy(
    Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
)
# Required for Qt WebEngine when imported after QApplication is created.
QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
from ui.main_window import MainWindow  # noqa: E402
from ui.theme import update_appearance  # noqa: E402

from utils.logger import setup_logging  # noqa: E402
from utils.settings import get_settings  # noqa: E402
from utils.yaml_config_manager import (  # noqa: E402
    backup_config_on_startup,
    ensure_slssteam_api_enabled,
    get_user_config_path,
)

project_root = str(Path(__file__).resolve().parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)


def parse_args(args, logger):
    """
    Parses command-line arguments and custom URI schemas (accela://...).
    Returns (command_line_appid, command_line_zips).
    """
    parser = argparse.ArgumentParser(description="ACCELA", add_help=False)
    parser.add_argument("--appid", type=str, help="AppID for the game")

    # Use parse_known_args to allow unrecognized arguments (like PyQt flags or URLs/ZIPs)
    parsed, unknown = parser.parse_known_args(args)

    command_line_appid = None
    command_line_zips = []

    if parsed.appid:
        if parsed.appid.isdigit():
            command_line_appid = int(parsed.appid)
        else:
            logger.error(f"Invalid AppID: {parsed.appid} (must be a number)")

    for arg in unknown:
        if arg.startswith("accela://"):
            # Handle custom URL scheme
            try:
                url_content = arg[9:]
                if "/" in url_content:
                    action, param = url_content.split("/", 1)
                    param = unquote(param)
                else:
                    action = url_content
                    param = None

                if action == "download" and param and param.isdigit():
                    command_line_appid = int(param)
                    logger.info(f"Found accela://download URL for AppID: {param}")
                elif action == "zip" and param:
                    if Path(param).exists():
                        command_line_zips.append(param)
                        logger.info(f"Found ZIP file from URL: {param}")
                    else:
                        logger.warning(f"ZIP file not found from URL: {param}")
                else:
                    logger.warning(f"Invalid accela:// URL format: {arg}")
            except Exception as e:
                logger.error(f"Failed to parse URL {arg}: {e}")

        elif arg.lower().endswith(".zip"):
            zip_path = Path(arg).resolve()
            if zip_path.exists():
                command_line_zips.append(str(zip_path))
                logger.info(f"Found ZIP file from command line: {zip_path}")
            else:
                logger.warning(f"ZIP file not found: {arg}")

    # AppID and ZIP files are mutually exclusive
    if command_line_appid and command_line_zips:
        logger.error("Cannot use --appid and .zip files together. Choose one.")
        return None, []

    return command_line_appid, command_line_zips


def setup_config(logger):
    """Backups and ensures SLSsteam config exists"""
    config_path = get_user_config_path()
    backup_created = backup_config_on_startup(config_path)
    if backup_created:
        logger.info("SLSsteam config backup created at startup")

    # Ensure SLSsteam API is enabled (only if config exists)
    if config_path.exists():
        if ensure_slssteam_api_enabled(config_path):
            logger.info("SLSsteam API enabled in config")


def apply_theme(app, logger):
    """Applies theme variables and custom fonts"""
    settings = get_settings()
    accent_color = settings.value("accent_color", "#C06C84")
    bg_color = settings.value("background_color", "#000000")

    # Load user's font settings
    font_family = settings.value("font", "TrixieCyrG-Plain")
    font_size = settings.value("font-size", 12, type=int)
    font_style = settings.value("font-style", "Normal")

    initial_font = QFont(font_family)
    initial_font.setPointSize(font_size)
    if font_style == "Italic":
        initial_font.setItalic(True)
    elif font_style == "Bold":
        initial_font.setBold(True)
    elif font_style == "Bold Italic":
        initial_font.setBold(True)
        initial_font.setItalic(True)

    # Apply palette + font
    font_file = settings.value("font-file", "", type=str) or None
    font_ok, font_info = update_appearance(
        app, accent_color, bg_color, font=initial_font, font_file=font_file
    )

    if font_ok:
        logger.info(f"Successfully loaded and applied custom font: '{str(font_info)}'")
    else:
        logger.warning(f"Failed to load custom font from: '{str(font_info)}'")


def launch_app(app, logger, app_version, command_line_appid, command_line_zips):
    """Launches the main window and processes standard jobs"""
    try:
        main_win = MainWindow()
        main_win.show()
        logger.info("Main window displayed successfully.")

        # Run update check after first paint so startup remains responsive.
        QTimer.singleShot(1200, lambda: main_win.check_for_startup_update(app_version))

        # Process command-line ZIP files after window is fully initialized
        if command_line_zips or command_line_appid:

            def process_command_line_args():
                """Add command-line ZIP files/AppID to queue after window initialization completes"""
                from core.morrenus_api import download_manifest

                if command_line_appid:
                    logger.info(
                        f"Downloading manifest for AppID {command_line_appid} from Morrenus API"
                    )
                    zip_path, error = download_manifest(command_line_appid)
                    if error:
                        logger.error(f"Failed to download manifest: {error}")
                        return
                    logger.info(f"Adding to queue: AppID {command_line_appid}")
                    main_win.job_queue.add_job(zip_path)
                else:
                    logger.info(
                        f"Adding {len(command_line_zips)} ZIP file(s) from command line to queue"
                    )
                    for zip_path in command_line_zips:
                        logger.info(f"Adding to queue: {Path(zip_path).name}")
                        main_win.job_queue.add_job(zip_path)

            # Use singleShot to defer until after window initialization
            QTimer.singleShot(0, process_command_line_args)

        sys.exit(app.exec())
    except Exception as e:
        logger.critical(
            f"A critical error occurred, and the application must close. Error: {e}",
            exc_info=True,
        )
        sys.exit(1)


def main():
    logger = setup_logging()
    from utils.version import app_version

    logger.info("========================================")
    logger.info(f"ACCELA {app_version} starting...")
    logger.info("========================================")

    # People only have substance within the memories of other people.

    app = QApplication(sys.argv)

    command_line_appid, command_line_zips = parse_args(sys.argv[1:], logger)

    setup_config(logger)
    apply_theme(app, logger)
    launch_app(app, logger, app_version, command_line_appid, command_line_zips)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
