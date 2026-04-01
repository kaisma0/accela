import multiprocessing
import os
import sys


from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, QTimer

QApplication.setHighDpiScaleFactorRoundingPolicy(
    Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
)
# Required for Qt WebEngine when imported after QApplication is created.
QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
from ui.main_window import MainWindow
from ui.theme import update_appearance

from managers.cli_manager import run_cli_mode, open_cli_terminal

from utils.logger import setup_logging
from utils.settings import get_settings
from utils.yaml_config_manager import (
    backup_config_on_startup,
    ensure_slssteam_api_enabled,
    get_user_config_path,
)

project_root = os.path.abspath(os.path.dirname(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

def handle_cli_mode(app, logger, cli_mode, command_line_appid, command_line_zips):
    """Handles CLI mode and returns True if CLI mode processed successfully"""
    # CLI mode: activated by -cli flag OR by --appid OR by accela://cli/ URL
    if cli_mode and (command_line_zips or command_line_appid):
        # Check if we should open in external terminal
        if command_line_appid:
            logger.info(f"Opening CLI mode in external terminal for AppID {command_line_appid}")
            if open_cli_terminal(appid=command_line_appid):
                logger.info("Terminal opened successfully")
                return True
        elif command_line_zips:
            logger.info(f"Opening CLI mode in external terminal for {len(command_line_zips)} ZIP(s)")
            if open_cli_terminal(zip_path=command_line_zips[0]):
                logger.info("Terminal opened successfully")
                return True

        # Fallback to internal CLI mode (when terminal couldn't be opened)
        if command_line_appid:
            logger.info(f"Will process AppID {command_line_appid} from Morrenus API in CLI mode")
            run_cli_mode(app, None, logger, appid=command_line_appid)
            return True
        else:
            logger.info(f"Will process {len(command_line_zips)} ZIP file(s) from command line in CLI mode")
            logger.info("Entering CLI mode - skipping main window")
            run_cli_mode(app, command_line_zips, logger)
            return True
    return False

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

    # Check for UI mode (e.g., Sonic) which may override colors/font
    ui_mode = settings.value("ui_mode", "default")
    font_file = None
    initial_font = None

    if ui_mode == "sonic":
        # Sonic palette: blue background, yellow accent
        accent_color = "#ffcc00"
        bg_color = "#002c83"
        font_file = settings.value("font-file", "sonic/sonic-1-hud-font.otf")
        initial_font = QFont()
    else:
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
    font_ok, font_info = update_appearance(app, accent_color, bg_color, font=initial_font, font_file=font_file)

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
                    logger.info(f"Downloading manifest for AppID {command_line_appid} from Morrenus API")
                    zip_path, error = download_manifest(command_line_appid)
                    if error:
                        logger.error(f"Failed to download manifest: {error}")
                        return
                    logger.info(f"Adding to queue: AppID {command_line_appid}")
                    main_win.job_queue.add_job(zip_path)
                else:
                    logger.info(f"Adding {len(command_line_zips)} ZIP file(s) from command line to queue")
                    for zip_path in command_line_zips:
                        logger.info(f"Adding to queue: {os.path.basename(zip_path)}")
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
    from managers.cli_manager import parse_cli_args

    logger.info("========================================")
    logger.info(f"ACCELA {app_version} starting...")
    logger.info("========================================")

    # People only have substance within the memories of other people.
    
    app = QApplication(sys.argv)
    
    cli_mode, command_line_appid, command_line_zips = parse_cli_args(sys.argv[1:], logger)

    if handle_cli_mode(app, logger, cli_mode, command_line_appid, command_line_zips):
        return
        
    setup_config(logger)
    apply_theme(app, logger)
    launch_app(app, logger, app_version, command_line_appid, command_line_zips)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
