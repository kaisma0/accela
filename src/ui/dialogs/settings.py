from ui.custom_titlebar import CustomTitleBar
import logging
import os
import shutil
import subprocess
import sys
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFontDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core import morrenus_api
from ui.dialogs.custom_gifs import CustomGifsDialog
from utils.helpers import (
    _get_slscheevo_path,
    _get_slscheevo_save_path,
    create_checkbox_setting,
    create_color_setting,
    create_font_setting,
    create_slider_setting,
    get_base_path,
    get_venv_activate,
    get_venv_python,
)
from utils.paths import Paths
from utils.settings import get_settings
from utils.yaml_config_manager import (
    get_user_config_path,
    update_yaml_boolean_value,
    update_yaml_nested_scalar_value,
    update_yaml_scalar_value,
)

logger = logging.getLogger(__name__)


class MorrenusStatsWidget(QWidget):
    """Widget displaying Morrenus API user statistics"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.settings = get_settings()
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 5, 0, 5)

        row1 = QHBoxLayout()
        row1.setSpacing(10)

        self.username_label = QLabel("User: --")
        self.username_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row1.addWidget(self.username_label)

        main_layout.addLayout(row1)

        self.daily_usage_bar = QProgressBar()
        self.daily_usage_bar.setRange(0, 100)
        self.daily_usage_bar.setValue(0)
        self.daily_usage_bar.setFormat("Daily: --")
        self.daily_usage_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)

        accent_color = self.settings.value("accent_color", "#C06C84")
        self.daily_usage_bar.setStyleSheet(f"""
            QProgressBar {{
                border: 1px solid #444;
                border-radius: 0px;
                text-align: center;
                color: #fff;
                background-color: #222;
                height: 20px;
            }}
            QProgressBar::chunk {{
                background-color: {accent_color};
            }}
        """)
        main_layout.addWidget(self.daily_usage_bar)

        row2 = QHBoxLayout()
        row2.setSpacing(10)

        self.expiration_label = QLabel("Expires: --")
        self.expiration_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row2.addWidget(self.expiration_label)

        self.total_calls_label = QLabel("Total: --")
        self.total_calls_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row2.addWidget(self.total_calls_label)

        self.status_label = QLabel("Status: --")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row2.addWidget(self.status_label)

        main_layout.addLayout(row2)

        # Refresh button
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.refresh_button.clicked.connect(self.refresh_stats)
        main_layout.addWidget(self.refresh_button)

    def refresh_stats(self):
        """Fetch and display latest stats from the API"""
        self.refresh_button.setEnabled(False)
        self.refresh_button.setText("Loading...")

        stats = morrenus_api.get_user_stats()

        self.refresh_button.setEnabled(True)
        self.refresh_button.setText("Refresh")

        if stats.get("error"):
            self.username_label.setText("User: Error")
            self.total_calls_label.setText("Total: --")
            self.daily_usage_bar.setFormat("Daily: Error")
            self.daily_usage_bar.setValue(0)
            self.expiration_label.setText("Expires: --")
            self.status_label.setText("Status: Error")
        else:
            self.username_label.setText(f"User: {stats.get('username', 'Unknown')}")
            self.total_calls_label.setText(
                f"Total: {stats.get('api_key_usage_count', 0)}"
            )

            try:
                daily_usage = int(stats.get("daily_usage", 0) or 0)
            except (TypeError, ValueError):
                daily_usage = 0

            try:
                daily_limit = int(stats.get("daily_limit", 100) or 100)
            except (TypeError, ValueError):
                daily_limit = 100
            if daily_limit == 0:
                daily_limit = 100

            self.daily_usage_bar.setRange(0, daily_limit)
            self.daily_usage_bar.setValue(daily_usage)
            self.daily_usage_bar.setFormat(f"Daily: {daily_usage}/{daily_limit}")

            expires_at = stats.get("api_key_expires_at", "")
            if expires_at:
                try:
                    from datetime import datetime

                    dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                    self.expiration_label.setText(f"Expires: {dt.strftime('%d/%m/%Y')}")
                except ValueError:
                    self.expiration_label.setText(f"Expires: {expires_at[:10]}")
            else:
                self.expiration_label.setText("Expires: Never")

            status = "Active" if stats.get("can_make_requests", False) else "Blocked"
            self.status_label.setText(f"Status: {status}")


class SettingsDialog(QDialog):
    slssteam_status_ready = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(610)
        self.setMinimumHeight(700)
        self.resize(610, 700)
        self.settings = get_settings()
        
        CustomTitleBar.setup_dialog_layout(self, title=self.windowTitle())
        
        self.main_layout = QVBoxLayout(self._tb_content_widget)
        self.main_window = parent
        self.accent_color = self.settings.value("accent_color", "#C06C84")

        # Save original API keys for restore on cancel
        self._original_morrenus_key = self.settings.value("morrenus_api_key", "", type=str)
        self._original_sgdb_key = self.settings.value("sgdb_api_key", "", type=str)
        self.slssteam_status_ready.connect(self._apply_slssteam_status)

        self._user_accent_color = self.settings.value(
            "user_accent_color", 
            self.settings.value("accent_color", "#C06C84"),
            type=str
        )
        self._user_background_color = self.settings.value(
            "user_background_color",
            self.settings.value("background_color", "#000000"),
            type=str
        )

        logger.debug("Opening SettingsDialog.")

        # Create tab widget
        self.tab_widget = QTabWidget()
        self.tab_widget.setStyleSheet(f"""
            QTabWidget::pane {{
                border: none;
            }}
            QTabBar::tab {{
                background: {self.settings.value("background_color", "#1E1E1E")};
                color: #888888;
                padding: 8px 16px;
                border: none;
            }}
            QTabBar::tab:selected {{
                color: {self.accent_color};
                border-bottom: 2px solid {self.accent_color};
            }}
            QTabBar::tab:!selected {{
                color: #888888;
            }}
        """)

        # Create tabs
        self._create_downloads_tab()
        self._create_morrenus_tab()
        self._create_steam_tab()
        self._create_tools_tab()
        self._create_slssteam_tab()
        self._create_style_tab()

        self.main_layout.addWidget(self.tab_widget)

        # On opening Settings, sync YAML to stored ACCELA values.
        self._sync_slssteam_config_from_stored_settings()

        # Sync audio preview values with current settings before any slider interaction
        if self.main_window and hasattr(self.main_window, "audio_manager"):
            self.main_window.audio_manager.sync_preview_values_from_settings()

        # Dialog buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.main_layout.addWidget(buttons)

    def _create_api_key_setting(
        self,
        label: str,
        placeholder: str,
        setting_key: str,
        help_url: Optional[str] = None,
        help_text: Optional[str] = None,
    ):
        """Create an API key input field with password toggle and help link."""
        layout = QVBoxLayout()
        layout.setSpacing(5)

        # Label
        label_widget = QLabel(label)
        layout.addWidget(label_widget)

        # Input with toggle button
        input_layout = QHBoxLayout()
        input_layout.setSpacing(5)

        api_key_input = QLineEdit()
        api_key_input.setPlaceholderText(placeholder)
        api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        current_key = self.settings.value(setting_key, "", type=str)
        api_key_input.setText(current_key)

        # Toggle button
        toggle_btn = QPushButton("Show")
        toggle_btn.clicked.connect(lambda: self._toggle_api_key_visibility(api_key_input, toggle_btn))

        input_layout.addWidget(api_key_input)
        input_layout.addWidget(toggle_btn)
        layout.addLayout(input_layout)

        # Help text/link
        accent_color = self.settings.value("accent_color", "#C06C84")
        if help_url:
            help_label = QLabel(f'<a href="{help_url}" style="color: {accent_color};">Get API key</a>')
            help_label.setOpenExternalLinks(True)
            layout.addWidget(help_label)
        elif help_text:
            help_label = QLabel(help_text)
            help_label.setStyleSheet("color: #888888; font-size: 11px;")
            layout.addWidget(help_label)

        return layout, api_key_input

    def _toggle_api_key_visibility(self, input_field, toggle_btn):
        """Toggle API key visibility between password and normal mode."""
        if input_field.echoMode() == QLineEdit.EchoMode.Password:
            input_field.setEchoMode(QLineEdit.EchoMode.Normal)
            toggle_btn.setText("Hide")
        else:
            input_field.setEchoMode(QLineEdit.EchoMode.Password)
            toggle_btn.setText("Show")

    def _create_scrollable_tab(self):
        """Create a tab root widget with a vertically scrollable content layout."""
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 0, 0, 0)

        scroll_area = QScrollArea(tab)
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(15, 15, 15, 15)

        scroll_area.setWidget(content)
        tab_layout.addWidget(scroll_area)
        return tab, content_layout

    def _create_downloads_tab(self):
        """Create the Downloads settings tab"""
        downloads_tab, downloads_layout = self._create_scrollable_tab()

        # --- Download Settings Section ---
        download_group = QGroupBox("Download Settings")
        download_layout = QVBoxLayout()

        self.library_mode_checkbox = create_checkbox_setting(
            "Limit Downloads to Steam Libraries",
            "library_mode",
            False,
            self,
            "Detect Steam libraries and let you choose where to install games.",
        )
        download_layout.addWidget(self.library_mode_checkbox)

        self.auto_skip_single_choice_checkbox = create_checkbox_setting(
            "Skip single-choice selection",
            "auto_skip_single_choice",
            False,
            self,
            "Automatically skip depot/library selection when only one option exists.",
        )
        download_layout.addWidget(self.auto_skip_single_choice_checkbox)

        max_dl_label = QLabel("Maximum concurrent downloads")
        max_dl_label.setToolTip("Set the maximum number of concurrent downloads (0-255)")

        self.max_downloads_spinbox = QSpinBox()
        self.max_downloads_spinbox.setRange(0, 255)
        current_max = self.settings.value("max_downloads", 255, type=int)
        try:
            current_max = int(current_max)
        except Exception:
            current_max = 255
        self.max_downloads_spinbox.setValue(current_max)

        max_dl_layout = QHBoxLayout()
        max_dl_layout.addWidget(max_dl_label)
        max_dl_layout.addWidget(self.max_downloads_spinbox)
        download_layout.addLayout(max_dl_layout)

        download_group.setLayout(download_layout)
        downloads_layout.addWidget(download_group)

        # --- Post-Processing Settings Section ---
        processing_group = QGroupBox("Post-Processing")
        processing_layout = QVBoxLayout()

        self.achievements_checkbox = create_checkbox_setting(
            "Generate Steam Achievements",
            "generate_achievements",
            False,
            self,
            "Generate achievement files for your games after downloads."
        )
        processing_layout.addWidget(self.achievements_checkbox)

        self.steamless_checkbox = create_checkbox_setting(
            "Remove Steam DRM with Steamless",
            "use_steamless",
            False,
            self,
            "Remove copy protection (DRM) from game executables after downloading."
        )
        processing_layout.addWidget(self.steamless_checkbox)

        self.auto_apply_goldberg_checkbox = create_checkbox_setting(
            "Apply Goldberg Automatically",
            "auto_apply_goldberg",
            False,
            self,
            "Automatically apply Goldberg after downloads."
        )
        processing_layout.addWidget(self.auto_apply_goldberg_checkbox)

        self.application_shortcuts_checkbox = create_checkbox_setting(
            "Create Application Shortcuts",
            "create_application_shortcuts",
            False,
            self,
            "Create desktop shortcuts and install game icons from SteamGridDB."
        )
        processing_layout.addWidget(self.application_shortcuts_checkbox)

        processing_group.setLayout(processing_layout)
        downloads_layout.addWidget(processing_group)

        downloads_layout.addStretch()
        self.tab_widget.addTab(downloads_tab, "Downloads")

    def _create_morrenus_tab(self):
        """Create the Morrenus API settings tab"""
        morrenus_tab, morrenus_layout = self._create_scrollable_tab()

        # --- API Keys Section ---
        api_key_group = QGroupBox("API Keys")
        api_key_layout = QVBoxLayout()
        api_key_layout.setSpacing(10)

        key_layout, self.api_key_input = self._create_api_key_setting(
            "Morrenus API Key:",
            "Paste your Morrenus API key",
            "morrenus_api_key",
            help_url="https://manifest.morrenus.xyz"
        )
        api_key_layout.addLayout(key_layout)

        # SteamGridDB API key
        sgdb_key_layout, self.sgdb_api_key_input = self._create_api_key_setting(
            "SteamGridDB API Key:",
            "Paste your SteamGridDB API key",
            "sgdb_api_key",
            help_url="https://www.steamgriddb.com/profile/account"
        )
        api_key_layout.addLayout(sgdb_key_layout)

        self.auto_refresh_morrenus_api_key_checkbox = create_checkbox_setting(
            "Auto Refresh Morrenus API Key",
            "auto_refresh_morrenus_api_key",
            True,
            self,
            "At startup, validate your Morrenus key and automatically open login/refresh flow if it is invalid.",
        )
        api_key_layout.addWidget(self.auto_refresh_morrenus_api_key_checkbox)

        api_key_group.setLayout(api_key_layout)
        morrenus_layout.addWidget(api_key_group)

        # --- Integration Stats Section ---
        stats_group = QGroupBox("Morrenus Stats")
        stats_group_layout = QVBoxLayout()
        stats_group_layout.setContentsMargins(5, 10, 5, 10)

        self.morrenus_stats_widget = MorrenusStatsWidget()
        stats_group_layout.addWidget(self.morrenus_stats_widget)

        stats_group.setLayout(stats_group_layout)
        morrenus_layout.addWidget(stats_group)

        morrenus_layout.addStretch()

        # Load stats when tab is shown
        from PyQt6.QtCore import QTimer

        self.morrenus_tab_initialized = False

        def on_tab_changed(index):
            if (
                self.tab_widget.tabText(index) == "Integrations"
                and not self.morrenus_tab_initialized
            ):
                self.morrenus_tab_initialized = True
                QTimer.singleShot(100, self.morrenus_stats_widget.refresh_stats)

        self.tab_widget.currentChanged.connect(on_tab_changed)

        self.tab_widget.addTab(morrenus_tab, "Integrations")

    def _create_steam_tab(self):
        """Create the Steam settings tab"""
        steam_tab, steam_layout = self._create_scrollable_tab()

        # --- Steam Integration Section ---
        steam_group = QGroupBox("Steam Integration")
        steam_inner_layout = QVBoxLayout()

        # SLSsteam integration
        wrapper_name = "SLSsteam"
        wrapper_full_name = "SLSsteam Wrapper Mode"
        wrapper_tooltip = (
            "Integrate downloaded games with Steam using SLSsteam.\n"
            "Games are registered in your Steam library automatically."
        )

        self.sls_mode_checkbox = create_checkbox_setting(
            wrapper_full_name,
            "slssteam_mode",
            False,
            self,
            wrapper_tooltip,
        )
        steam_inner_layout.addWidget(self.sls_mode_checkbox)

        self.sls_config_management_checkbox = create_checkbox_setting(
            f"{wrapper_name} Config Management",
            "sls_config_management",
            True,
            self,
            f"Allow ACCELA to manage {wrapper_name} configuration files.",
        )
        steam_inner_layout.addWidget(self.sls_config_management_checkbox)

        steam_group.setLayout(steam_inner_layout)
        steam_layout.addWidget(steam_group)

        # --- Steam Restart Section ---
        steam_settings_group = QGroupBox("Steam Settings ")
        steam_settings_layout = QVBoxLayout()

        self.prompt_steam_restart_checkbox = create_checkbox_setting(
            "Prompt Steam Restart",
            "prompt_steam_restart",
            True,
            self,
            "Show a prompt to restart Steam after downloads when wrapper mode is enabled.",
        )
        steam_settings_layout.addWidget(self.prompt_steam_restart_checkbox)

        self.block_steam_updates_checkbox = create_checkbox_setting(
            "Block Steam Updates",
            "block_steam_updates",
            self._is_steam_updates_blocked(),
            self,
            "Prevent Steam from automatically updating itself."
        )
        steam_settings_layout.addWidget(self.block_steam_updates_checkbox)

        steam_settings_group.setLayout(steam_settings_layout)
        steam_layout.addWidget(steam_settings_group)

        steam_layout.addStretch()
        self.tab_widget.addTab(steam_tab, "Steam")

    def _create_tools_tab(self):
        """Create the Tools settings tab"""
        tools_tab, tools_layout = self._create_scrollable_tab()

        # --- Tools Section ---
        tools_group = QGroupBox("Tools")
        tools_button_layout = QVBoxLayout()

        self.run_slscheevo_button = QPushButton("Configure Achievements")
        self.run_slscheevo_button.setToolTip(
            "Launch SLScheevo to setup your credentials to generate achievement files."
        )
        self.run_slscheevo_button.clicked.connect(self.run_slscheevo)
        tools_button_layout.addWidget(self.run_slscheevo_button)
        run_slscheevo_ex = QLabel(self.run_slscheevo_button.toolTip())
        run_slscheevo_ex.setStyleSheet("color: #888888; font-size: 11px;")
        run_slscheevo_ex.setWordWrap(True)
        tools_button_layout.addWidget(run_slscheevo_ex)

        self.run_steamless_button = QPushButton("Remove DRM")
        self.run_steamless_button.setToolTip(
            "Run Steamless manually on a game .exe to remove DRM protection."
        )
        self.run_steamless_button.clicked.connect(self.run_steamless_manually)
        tools_button_layout.addWidget(self.run_steamless_button)
        run_steamless_ex = QLabel(self.run_steamless_button.toolTip())
        run_steamless_ex.setStyleSheet("color: #888888; font-size: 11px;")
        run_steamless_ex.setWordWrap(True)
        tools_button_layout.addWidget(run_steamless_ex)

        self.download_slssteam_button = QPushButton("Install SLSsteam")
        self.download_slssteam_button.setToolTip(
            "Install or update SLSsteam using the install-sls flow."
        )
        self.download_slssteam_button.clicked.connect(self.download_slssteam)

        # Show when available
        tools_button_layout.addWidget(self.download_slssteam_button)
        download_slssteam_ex = QLabel(self.download_slssteam_button.toolTip())
        download_slssteam_ex.setStyleSheet("color: #888888; font-size: 11px;")
        download_slssteam_ex.setWordWrap(True)
        tools_button_layout.addWidget(download_slssteam_ex)
        # Update status indicator
        self.slssteam_status_label = QLabel()
        self.slssteam_status_label.setStyleSheet(
            f"color: {self.accent_color}; font-size: 12px;"
        )
        self._update_slssteam_status()
        tools_button_layout.addWidget(self.slssteam_status_label)

        # Steamclient.so hash warning label
        self.slssteam_hash_warning_label = QLabel()
        self.slssteam_hash_warning_label.setStyleSheet(
            f"color: #C06C84; font-size: 11px;"  # Pink warning color
        )
        self.slssteam_hash_warning_label.setWordWrap(True)
        self.slssteam_hash_warning_label.setMaximumWidth(300)
        tools_button_layout.addWidget(self.slssteam_hash_warning_label)

        tools_group.setLayout(tools_button_layout)
        tools_layout.addWidget(tools_group)

        tools_layout.addStretch()
        self.tab_widget.addTab(tools_tab, "Tools")

    def _create_slssteam_tab(self):
        """Create SLSsteam settings tab."""
        slssteam_tab, slssteam_layout = self._create_scrollable_tab()

        runtime_group = QGroupBox("Runtime")
        runtime_layout = QVBoxLayout()

        self.sls_safe_mode_checkbox = create_checkbox_setting(
            "Enable Safe Mode",
            "sls_safe_mode",
            False,
            self,
            "Disable SLSsteam automatically if steamclient.so hash is unknown.",
        )
        runtime_layout.addWidget(self.sls_safe_mode_checkbox)

        self.sls_warn_hash_missmatch_checkbox = create_checkbox_setting(
            "Warn On Hash Missmatch",
            "sls_warn_hash_missmatch",
            False,
            self,
            "Show a warning notification when steamclient.so hash is not recognized.",
        )
        runtime_layout.addWidget(self.sls_warn_hash_missmatch_checkbox)

        runtime_group.setLayout(runtime_layout)
        slssteam_layout.addWidget(runtime_group)

        notifications_group = QGroupBox("Notifications")
        notifications_layout = QVBoxLayout()

        self.sls_notifications_checkbox = create_checkbox_setting(
            "Enable Notifications",
            "sls_notifications",
            True,
            self,
            "Use notify-send messages from SLSsteam.",
        )
        notifications_layout.addWidget(self.sls_notifications_checkbox)

        self.sls_notify_init_checkbox = create_checkbox_setting(
            "Notify When Initialized",
            "sls_notify_init",
            True,
            self,
            "Send a notification when SLSsteam finishes startup.",
        )
        notifications_layout.addWidget(self.sls_notify_init_checkbox)

        notifications_group.setLayout(notifications_layout)
        slssteam_layout.addWidget(notifications_group)

        identity_group = QGroupBox("Client Overrides")
        identity_layout = QFormLayout()

        self.sls_fake_email_input = QLineEdit()
        self.sls_fake_email_input.setPlaceholderText("Leave empty to disable")
        self.sls_fake_email_input.setToolTip(
            "Override account e-mail on the client side only."
        )
        self.sls_fake_email_input.setText(
            self.settings.value("sls_fake_email", "", type=str)
        )
        identity_layout.addRow("Fake Email:", self.sls_fake_email_input)

        self.sls_fake_wallet_spinbox = QSpinBox()
        self.sls_fake_wallet_spinbox.setRange(0, 2_147_483_647)
        self.sls_fake_wallet_spinbox.setToolTip(
            "Client-side wallet balance override. Use 0 to disable."
        )
        self.sls_fake_wallet_spinbox.setValue(
            self.settings.value("sls_fake_wallet_balance", 0, type=int)
        )
        identity_layout.addRow("Fake Wallet Balance:", self.sls_fake_wallet_spinbox)

        identity_group.setLayout(identity_layout)
        slssteam_layout.addWidget(identity_group)

        status_group = QGroupBox("Custom In-Game Status")
        status_layout = QFormLayout()

        self.sls_idle_status_appid_spinbox = QSpinBox()
        self.sls_idle_status_appid_spinbox.setRange(0, 2_147_483_647)
        self.sls_idle_status_appid_spinbox.setToolTip(
            "Idle status AppId override. Use 0 to disable."
        )
        self.sls_idle_status_appid_spinbox.setValue(
            self.settings.value("sls_idle_status_appid", 0, type=int)
        )
        status_layout.addRow("Idle Status AppId:", self.sls_idle_status_appid_spinbox)

        self.sls_idle_status_title_input = QLineEdit()
        self.sls_idle_status_title_input.setPlaceholderText("Leave empty to disable")
        self.sls_idle_status_title_input.setToolTip(
            "Idle status title override."
        )
        self.sls_idle_status_title_input.setText(
            self.settings.value("sls_idle_status_title", "", type=str)
        )
        status_layout.addRow("Idle Status Title:", self.sls_idle_status_title_input)

        self.sls_unowned_status_appid_spinbox = QSpinBox()
        self.sls_unowned_status_appid_spinbox.setRange(0, 2_147_483_647)
        self.sls_unowned_status_appid_spinbox.setToolTip(
            "Unowned status AppId override. Use 0 to disable."
        )
        self.sls_unowned_status_appid_spinbox.setValue(
            self.settings.value("sls_unowned_status_appid", 0, type=int)
        )
        status_layout.addRow(
            "Unowned Status AppId:", self.sls_unowned_status_appid_spinbox
        )

        self.sls_unowned_status_title_input = QLineEdit()
        self.sls_unowned_status_title_input.setPlaceholderText("Leave empty to disable")
        self.sls_unowned_status_title_input.setToolTip(
            "Unowned status title override."
        )
        self.sls_unowned_status_title_input.setText(
            self.settings.value("sls_unowned_status_title", "", type=str)
        )
        status_layout.addRow(
            "Unowned Status Title:", self.sls_unowned_status_title_input
        )

        status_group.setLayout(status_layout)
        slssteam_layout.addWidget(status_group)

        slssteam_layout.addStretch()
        self.tab_widget.addTab(slssteam_tab, "SLSsteam")

    def _sync_slssteam_config_from_stored_settings(self):
        """Sync YAML from stored ACCELA settings when SLSsteam management is enabled."""
        try:
            sls_mode = self.settings.value("slssteam_mode", False, type=bool)
            sls_config_management = self.settings.value(
                "sls_config_management", True, type=bool
            )

            if not sls_mode or not sls_config_management:
                return

            config_path = get_user_config_path()
            if not config_path.exists():
                return

            safe_mode = self.settings.value("sls_safe_mode", False, type=bool)
            notifications = self.settings.value("sls_notifications", True, type=bool)
            warn_hash_missmatch = self.settings.value(
                "sls_warn_hash_missmatch", False, type=bool
            )
            notify_init = self.settings.value("sls_notify_init", True, type=bool)
            fake_email = self.settings.value("sls_fake_email", "", type=str).strip()

            try:
                fake_wallet_balance = int(
                    self.settings.value("sls_fake_wallet_balance", 0, type=int)
                )
            except Exception:
                fake_wallet_balance = 0

            try:
                idle_status_appid = int(
                    self.settings.value("sls_idle_status_appid", 0, type=int)
                )
            except Exception:
                idle_status_appid = 0

            idle_status_title = self.settings.value(
                "sls_idle_status_title", "", type=str
            ).strip()

            try:
                unowned_status_appid = int(
                    self.settings.value("sls_unowned_status_appid", 0, type=int)
                )
            except Exception:
                unowned_status_appid = 0

            unowned_status_title = self.settings.value(
                "sls_unowned_status_title", "", type=str
            ).strip()

            changed = 0
            changed += int(update_yaml_boolean_value(config_path, "SafeMode", safe_mode))
            changed += int(
                update_yaml_boolean_value(config_path, "Notifications", notifications)
            )
            changed += int(
                update_yaml_boolean_value(
                    config_path,
                    "WarnHashMissmatch",
                    warn_hash_missmatch,
                )
            )
            changed += int(update_yaml_boolean_value(config_path, "NotifyInit", notify_init))
            changed += int(update_yaml_scalar_value(config_path, "FakeEmail", fake_email))
            changed += int(
                update_yaml_scalar_value(
                    config_path,
                    "FakeWalletBalance",
                    fake_wallet_balance,
                )
            )
            changed += int(
                update_yaml_nested_scalar_value(
                    config_path,
                    "IdleStatus",
                    "AppId",
                    idle_status_appid,
                )
            )
            changed += int(
                update_yaml_nested_scalar_value(
                    config_path,
                    "IdleStatus",
                    "Title",
                    idle_status_title,
                )
            )
            changed += int(
                update_yaml_nested_scalar_value(
                    config_path,
                    "UnownedStatus",
                    "AppId",
                    unowned_status_appid,
                )
            )
            changed += int(
                update_yaml_nested_scalar_value(
                    config_path,
                    "UnownedStatus",
                    "Title",
                    unowned_status_title,
                )
            )

            if changed > 0:
                logger.info(f"Synced {changed} SLSsteam setting(s) to config.yaml")

        except Exception as e:
            logger.warning(
                f"Failed to sync stored SLSsteam settings to config.yaml: {e}",
                exc_info=True,
            )

    def _add_style_sections(self, style_layout):
        """Add style settings sections into an existing layout."""
        # --- Color Settings ---
        color_group = QGroupBox("Color Settings")
        color_layout = QVBoxLayout()

        accent_layout = QHBoxLayout()
        accent_label = QLabel("Accent Color:")
        self.accent_color_button = QPushButton()
        self.accent_color_button.setStyleSheet(f"background-color: {self._user_accent_color};")
        self.accent_reset_button = QPushButton("Reset")
        accent_layout.addWidget(accent_label)
        accent_layout.addWidget(self.accent_color_button)
        accent_layout.addWidget(self.accent_reset_button)
        accent_layout.addStretch()
        self.accent_color_button.clicked.connect(self.choose_accent_color)
        self.accent_reset_button.clicked.connect(self.reset_accent_color)
        color_layout.addLayout(accent_layout)

        bg_layout = QHBoxLayout()
        bg_label = QLabel("Background Color:")
        self.bg_color_button = QPushButton()
        self.bg_color_button.setStyleSheet(f"background-color: {self._user_background_color};")
        self.bg_reset_button = QPushButton("Reset")
        bg_layout.addWidget(bg_label)
        bg_layout.addWidget(self.bg_color_button)
        bg_layout.addWidget(self.bg_reset_button)
        bg_layout.addStretch()
        self.bg_color_button.clicked.connect(self.choose_bg_color)
        self.bg_reset_button.clicked.connect(self.reset_bg_color)
        color_layout.addLayout(bg_layout)

        color_group.setLayout(color_layout)
        style_layout.addWidget(color_group)

        # Font Settings
        font_group = QGroupBox("Font Settings")
        font_layout = QVBoxLayout()

        font_layout_children, self.font_button, self.font_reset_button = (
            create_font_setting(self)
        )
        self.font_button.clicked.connect(self.choose_font)
        self.font_reset_button.clicked.connect(self.reset_font)
        font_layout.addLayout(font_layout_children)

        font_group.setLayout(font_layout)
        style_layout.addWidget(font_group)

        # --- Display Settings ---
        display_group = QGroupBox("Display Settings")
        display_layout = QVBoxLayout()

        self.titlebar_position_checkbox = QCheckBox("Move Titlebar to Bottom")
        titlebar_top = (
            self.settings.value("titlebar_position", "top", type=str) == "top"
        )
        self.titlebar_position_checkbox.setChecked(not titlebar_top)
        self.titlebar_position_checkbox.setToolTip(
            "Move the titlebar to the bottom of the window."
        )
        self.titlebar_position_checkbox.stateChanged.connect(
            self.on_titlebar_position_changed
        )
        display_layout.addWidget(self.titlebar_position_checkbox)
        # Visible explanation label for titlebar position (indented to match other checkboxes)
        titlebar_explanation = QLabel("Move the titlebar to the bottom of the window.")
        titlebar_explanation.setStyleSheet("color: #888888; font-size: 11px;")
        titlebar_explanation.setWordWrap(True)
        titlebar_ex_layout = QHBoxLayout()
        titlebar_ex_layout.setContentsMargins(0, 0, 0, 0)
        titlebar_ex_layout.addSpacing(14)
        titlebar_ex_layout.addWidget(titlebar_explanation)
        display_layout.addLayout(titlebar_ex_layout)

        # Sonic Mode toggle
        self.sonic_mode_checkbox = QCheckBox("Enable Sonic Mode")
        sonic_on = self.settings.value("ui_mode", "default") == "sonic"
        self.sonic_mode_checkbox.setChecked(sonic_on)
        self.sonic_mode_checkbox.setToolTip(
            "Apply Sonic color palette, font and default media resources."
        )
        display_layout.addWidget(self.sonic_mode_checkbox)
        sonic_explanation = QLabel("Apply Sonic color palette, font and default media resources.")
        sonic_explanation.setStyleSheet("color: #888888; font-size: 11px;")
        sonic_explanation.setWordWrap(True)
        sonic_ex_layout = QHBoxLayout()
        sonic_ex_layout.setContentsMargins(0, 0, 0, 0)
        sonic_ex_layout.addSpacing(14)
        sonic_ex_layout.addWidget(sonic_explanation)
        display_layout.addLayout(sonic_ex_layout)

        self.gif_display_checkbox = create_checkbox_setting(
            "Show GIF Display",
            "gif_display_enabled",
            True,
            self,
            "Show animated GIF in the main window.",
        )
        self.gif_display_checkbox.stateChanged.connect(self.on_gif_display_changed)
        display_layout.addWidget(self.gif_display_checkbox)

        self.ignore_color_warnings_checkbox = create_checkbox_setting(
            "Ignore color warnings",
            "ignore_color_warnings",
            False,
            self,
            "Allow any color combination, even if hard to read.",
        )
        display_layout.addWidget(self.ignore_color_warnings_checkbox)

        display_group.setLayout(display_layout)
        style_layout.addWidget(display_group)

        # Custom GIFs button
        # Clear GIF Cache button
        gif_buttons_layout = QHBoxLayout()

        custom_gifs_button = QPushButton("Custom Gifs")
        custom_gifs_button.clicked.connect(self.open_custom_gifs_dialog)
        gif_buttons_layout.addWidget(custom_gifs_button)
        clear_cache_button = QPushButton("Clear GIF Cache")
        clear_cache_button.clicked.connect(self.clear_gif_cache)
        clear_cache_button.setToolTip(
            f"Delete {get_base_path() / 'gifs' / 'colorized'} and regenerate all GIFs"
        )
        gif_buttons_layout.addWidget(clear_cache_button)
        style_layout.addLayout(gif_buttons_layout)

    def _add_audio_sections(self, style_layout):
        """Add audio settings sections into an existing layout."""
        # --- Audio Playback Settings ---
        playback_group = QGroupBox("Audio Playback")
        playback_layout = QVBoxLayout()

        self.play_etw_checkbox = create_checkbox_setting(
            'Play "Entering The Wired" on start', "play_etw", True, self,
            "Play the 'Entering The Wired' intro audio when ACCELA starts."
        )
        playback_layout.addWidget(self.play_etw_checkbox)

        self.play_lall_checkbox = create_checkbox_setting(
            'Play "Let\'s All Love Lain" on exit', "play_lall", True, self,
            "Play the 'Let's All Love Lain' audio when ACCELA exits."
        )
        playback_layout.addWidget(self.play_lall_checkbox)

        self.play_50hz_hum_checkbox = create_checkbox_setting(
            "Play background hum sound", "play_50hz_hum", True, self,
            "Play a low 50Hz hum ambient sound in the background."
        )
        playback_layout.addWidget(self.play_50hz_hum_checkbox)

        playback_group.setLayout(playback_layout)
        style_layout.addWidget(playback_group)

        # --- Volume Settings ---
        volume_group = QGroupBox("Volume Settings")
        volume_layout = QVBoxLayout()

        # Master Volume
        (
            master_layout,
            self.master_volume_slider,
            self.master_volume_value_label,
            self.master_volume_reset,
        ) = create_slider_setting("Master Volume", "master_volume", 80, self)
        volume_layout.addLayout(master_layout)

        # Effects Volume
        (
            effects_layout,
            self.effects_volume_slider,
            self.effects_volume_value_label,
            self.effects_volume_reset,
        ) = create_slider_setting("Effects Volume", "effects_volume", 50, self)
        volume_layout.addLayout(effects_layout)

        # Hum Volume
        (
            hum_layout,
            self.hum_volume_slider,
            self.hum_volume_value_label,
            self.hum_volume_reset,
        ) = create_slider_setting("Hum Volume", "hum_volume", 20, self)
        volume_layout.addLayout(hum_layout)

        volume_group.setLayout(volume_layout)
        style_layout.addWidget(volume_group)

        # --- Test Section ---
        test_group = QGroupBox("Test Sounds")
        test_layout = QVBoxLayout()

        button_layout = QHBoxLayout()

        self.test_etw_button = QPushButton("Test ETW Sound")
        self.test_lall_button = QPushButton("Test LALL Sound")
        self.test_etw_button.clicked.connect(self.test_etw_sound)
        self.test_lall_button.clicked.connect(self.test_lall_sound)
        button_layout.addWidget(self.test_etw_button)
        button_layout.addWidget(self.test_lall_button)
        test_layout.addLayout(button_layout)

        test_group.setLayout(test_layout)
        style_layout.addWidget(test_group)

    def _create_style_tab(self):
        """Create the Style settings tab with appearance first, audio below."""
        style_tab, style_layout = self._create_scrollable_tab()
        self._add_style_sections(style_layout)
        self._add_audio_sections(style_layout)

        style_layout.addStretch()
        self.tab_widget.addTab(style_tab, "Style")

    # Audio-related methods
    def on_master_volume_changed(self, value):
        """Handle master volume changes in real-time (without saving to settings)"""
        if self.main_window and hasattr(self.main_window, "audio_manager"):
            self.main_window.audio_manager.apply_master_volume_preview(value)

    def on_effects_volume_changed(self, value):
        """Handle effects volume changes in real-time (without saving to settings)"""
        if self.main_window and hasattr(self.main_window, "audio_manager"):
            self.main_window.audio_manager.apply_effects_volume_preview(value)

    def on_hum_volume_changed(self, value):
        """Handle hum volume changes in real-time (without saving to settings)"""
        if self.main_window and hasattr(self.main_window, "audio_manager"):
            self.main_window.audio_manager.apply_hum_volume_preview(value)

    def test_etw_sound(self):
        """Test play the ETW sound"""
        if self.main_window and hasattr(self.main_window, "audio_manager"):
            self.main_window.audio_manager.test_etw_sound()

    def test_lall_sound(self):
        """Test play the LALL sound"""
        if self.main_window and hasattr(self.main_window, "audio_manager"):
            self.main_window.audio_manager.test_lall_sound()

    # Style-related methods
    def choose_accent_color(self):
        color = QColorDialog.getColor()
        if not color.isValid():
            return
        if not self.ignore_color_warnings_checkbox.isChecked():
            if self.is_too_dark(color):
                QMessageBox.warning(
                    self,
                    "Invalid Color",
                    "This color is too dark and will make the interface unusable.",
                )
                return
        hex_color = color.name()
        self.accent_color_button.setStyleSheet(f"background-color: {hex_color};")

    def reset_accent_color(self):
        default = "#C06C84"
        self.settings.setValue("accent_color", default)
        self.accent_color_button.setStyleSheet(f"background-color: {default};")

    def choose_bg_color(self):
        color = QColorDialog.getColor()
        if not color.isValid():
            return
        hex_color = color.name()
        self.bg_color_button.setStyleSheet(f"background-color: {hex_color};")

    def reset_bg_color(self):
        default = "#000000"
        self.settings.setValue("background_color", default)
        self.bg_color_button.setStyleSheet(f"background-color: {default};")

    def update_font_button_text(self):
        """Update the font button text to show current font details"""
        if hasattr(self, "font_button") and hasattr(self, "current_font"):
            font_text = (
                f"{self.current_font.family()} {self.current_font.pointSize()}pt"
            )
            if self.current_font.bold() and self.current_font.italic():
                font_text += " Bold Italic"
            elif self.current_font.bold():
                font_text += " Bold"
            elif self.current_font.italic():
                font_text += " Italic"
            self.font_button.setText(font_text)
            self.font_button.setFont(self.current_font)

    def choose_font(self):
        font, ok = QFontDialog.getFont(self.current_font, self)
        if ok:
            self.current_font = font
            self.update_font_button_text()

    def reset_font(self):
        default_font = QFont()
        default_font.setFamily("TrixieCyrG-Plain")
        default_font.setPointSize(12)
        default_font.setBold(False)
        default_font.setItalic(False)
        self.current_font = default_font
        self.update_font_button_text()

    def on_titlebar_position_changed(self, state):
        """Handle immediate titlebar position change"""
        position = "bottom" if state == 2 else "top"
        self.settings.setValue("titlebar_position", position)
        CustomTitleBar.reposition_dialog_titlebar(self, position)
        if self.main_window and hasattr(self.main_window, "reposition_titlebar"):
            self.main_window.reposition_titlebar(position)

    def on_gif_display_changed(self, state):
        """Handle GIF display setting change"""
        gif_display_enabled = state == 2
        if self.main_window and hasattr(self.main_window, "update_gif_display"):
            self.main_window.update_gif_display(gif_display_enabled)

    def is_too_dark(self, color: QColor) -> bool:
        # Calculate perceived brightness (0–255 range)
        brightness = color.red() * 0.299 + color.green() * 0.587 + color.blue() * 0.114
        return brightness < 15  # Darker than ~15%, tweak if needed

    def is_too_close_to_accent_color(
        self, accent_color: QColor, background_color: QColor, threshold: int = 100
    ) -> bool:
        """Return True if background color is too close to accent color"""
        # Calculate color distance using Euclidean distance in RGB space
        r_diff = background_color.red() - accent_color.red()
        g_diff = background_color.green() - accent_color.green()
        b_diff = background_color.blue() - accent_color.blue()

        distance = (r_diff**2 + g_diff**2 + b_diff**2) ** 0.5

        return distance < threshold

    def accept(self):
        # --- General Settings ---
        # API Keys
        api_key = self.api_key_input.text().strip()
        self.settings.setValue("morrenus_api_key", api_key)
        if api_key:
            logger.info("Morrenus API key saved.")
        else:
            logger.info("Morrenus API key cleared.")

        if self.sgdb_api_key_input:
            sgdb_api_key = self.sgdb_api_key_input.text().strip()
            self.settings.setValue("sgdb_api_key", sgdb_api_key)
            if sgdb_api_key:
                logger.info("Steam Grid DB API key saved.")
            else:
                logger.info("Steam Grid DB API key cleared.")

        auto_refresh_morrenus_api_key = (
            self.auto_refresh_morrenus_api_key_checkbox.isChecked()
        )
        self.settings.setValue(
            "auto_refresh_morrenus_api_key", auto_refresh_morrenus_api_key
        )
        logger.info(
            "Auto Refresh Morrenus API Key set to: "
            f"{auto_refresh_morrenus_api_key}"
        )

        # Download Settings
        is_sls_mode = self.sls_mode_checkbox.isChecked()
        self.settings.setValue("slssteam_mode", is_sls_mode)
        if is_sls_mode:
            logger.info("SLSsteam mode enabled - games will sync to config.yaml")
        else:
            logger.info("SLSsteam mode disabled")

        # SLSsteam Config Management
        sls_config_management = self.sls_config_management_checkbox.isChecked()
        self.settings.setValue("sls_config_management", sls_config_management)
        logger.info(f"SLSsteam Config Management set to: {sls_config_management}")

        # SLSsteam Config Options
        sls_safe_mode = self.sls_safe_mode_checkbox.isChecked()
        self.settings.setValue("sls_safe_mode", sls_safe_mode)

        sls_notifications = self.sls_notifications_checkbox.isChecked()
        self.settings.setValue("sls_notifications", sls_notifications)

        sls_warn_hash_missmatch = self.sls_warn_hash_missmatch_checkbox.isChecked()
        self.settings.setValue("sls_warn_hash_missmatch", sls_warn_hash_missmatch)

        sls_notify_init = self.sls_notify_init_checkbox.isChecked()
        self.settings.setValue("sls_notify_init", sls_notify_init)

        sls_fake_email = self.sls_fake_email_input.text().strip()
        self.settings.setValue("sls_fake_email", sls_fake_email)

        sls_fake_wallet_balance = int(self.sls_fake_wallet_spinbox.value())
        self.settings.setValue("sls_fake_wallet_balance", sls_fake_wallet_balance)

        sls_idle_status_appid = int(self.sls_idle_status_appid_spinbox.value())
        self.settings.setValue("sls_idle_status_appid", sls_idle_status_appid)

        sls_idle_status_title = self.sls_idle_status_title_input.text().strip()
        self.settings.setValue("sls_idle_status_title", sls_idle_status_title)

        sls_unowned_status_appid = int(self.sls_unowned_status_appid_spinbox.value())
        self.settings.setValue("sls_unowned_status_appid", sls_unowned_status_appid)

        sls_unowned_status_title = self.sls_unowned_status_title_input.text().strip()
        self.settings.setValue("sls_unowned_status_title", sls_unowned_status_title)

        library_mode_enabled = self.library_mode_checkbox.isChecked()
        self.settings.setValue("library_mode", library_mode_enabled)
        logger.info(f"Library mode setting changed to: {library_mode_enabled}")

        auto_skip_single_choice = self.auto_skip_single_choice_checkbox.isChecked()
        self.settings.setValue("auto_skip_single_choice", auto_skip_single_choice)
        logger.info(
            f"Auto-skip single-choice selection set to: {auto_skip_single_choice}"
        )

        prompt_steam_restart = self.prompt_steam_restart_checkbox.isChecked()
        self.settings.setValue("prompt_steam_restart", prompt_steam_restart)
        logger.info(f"Prompt Steam Restart set to: {prompt_steam_restart}")

        # Post-Processing Settings
        achievements_enabled = self.achievements_checkbox.isChecked()
        self.settings.setValue("generate_achievements", achievements_enabled)
        logger.info(f"Generate Achievements is set to: {achievements_enabled}")

        steamless_enabled = self.steamless_checkbox.isChecked()
        self.settings.setValue("use_steamless", steamless_enabled)
        logger.info(f"Use Steamless is set to: {steamless_enabled}")

        auto_apply_goldberg = self.auto_apply_goldberg_checkbox.isChecked()
        self.settings.setValue("auto_apply_goldberg", auto_apply_goldberg)
        logger.info(f"Auto-apply Goldberg is set to: {auto_apply_goldberg}")

        # Application Shortcuts
        if (
            hasattr(self, "application_shortcuts_checkbox")
            and self.application_shortcuts_checkbox
        ):
            shortcuts_enabled = self.application_shortcuts_checkbox.isChecked()
            self.settings.setValue("create_application_shortcuts", shortcuts_enabled)
            logger.info(f"Create Application Shortcuts is set to: {shortcuts_enabled}")

        # System Settings
        block_steam_updates = self.block_steam_updates_checkbox.isChecked()
        self.settings.setValue("block_steam_updates", block_steam_updates)
        logger.info(f"Block Steam Updates set to: {block_steam_updates}")
        self._apply_steam_updates_block(block_steam_updates)

        # --- Audio Settings ---
        # Playback settings
        self.settings.setValue("play_etw", self.play_etw_checkbox.isChecked())
        self.settings.setValue("play_lall", self.play_lall_checkbox.isChecked())
        self.settings.setValue("play_50hz_hum", self.play_50hz_hum_checkbox.isChecked())

        # Volume settings
        self.settings.setValue("master_volume", self.master_volume_slider.value())
        self.settings.setValue("effects_volume", self.effects_volume_slider.value())
        self.settings.setValue("hum_volume", self.hum_volume_slider.value())

        # Apply final audio settings
        if self.main_window and hasattr(self.main_window, "audio_manager"):
            self.main_window.audio_manager.apply_audio_settings()

        # --- Style Settings ---
        user_accent_color = (
            self.accent_color_button.styleSheet()
            .split("background-color: ")[1]
            .split(";")[0]
        )
        user_bg_color = (
            self.bg_color_button.styleSheet()
            .split("background-color: ")[1]
            .split(";")[0]
        )

        self.settings.setValue("user_accent_color", user_accent_color)
        self.settings.setValue("user_background_color", user_bg_color)

        previous_ui_mode = self.settings.value("ui_mode", "default")
        sonic_enabled = hasattr(self, "sonic_mode_checkbox") and self.sonic_mode_checkbox.isChecked()
        new_ui_mode = "sonic" if sonic_enabled else "default"
        self.settings.setValue("ui_mode", new_ui_mode)

        if sonic_enabled:
            # Use Sonic palette: blue background, yellow accent
            applied_accent = "#ffcc00"
            applied_bg = "#002c83"
            self.settings.setValue("font-file", "sonic/sonic-1-hud-font.otf")
        else:
            applied_accent = user_accent_color
            applied_bg = user_bg_color
            self.settings.setValue("font-file", "")

        # Reload audio assets if UI mode changed (Sonic mode affects sound paths)
        if (
            previous_ui_mode != new_ui_mode
            and self.main_window
            and hasattr(self.main_window, "audio_manager")
        ):
            self.main_window.audio_manager.reload_sounds_for_ui_mode()

        ignore_color_warnings = self.ignore_color_warnings_checkbox.isChecked()
        self.settings.setValue("ignore_color_warnings", ignore_color_warnings)

        if not ignore_color_warnings and not sonic_enabled:
            if self.is_too_close_to_accent_color(
                QColor(user_accent_color), QColor(user_bg_color)
            ):
                QMessageBox.warning(
                    self,
                    "Invalid Color",
                    "The background color is too similar to the accent color and will reduce contrast.",
                )
                return

        # Save the applied colors (either Sonic or user colors)
        self.settings.setValue("accent_color", applied_accent)
        self.settings.setValue("background_color", applied_bg)

        # Font settings
        self.settings.setValue("font", self.current_font.family())
        self.settings.setValue("font-size", self.current_font.pointSize())

        if self.current_font.bold() and self.current_font.italic():
            font_style = "Bold Italic"
        elif self.current_font.bold():
            font_style = "Bold"
        elif self.current_font.italic():
            font_style = "Italic"
        else:
            font_style = "Normal"
        self.settings.setValue("font-style", font_style)

        # Display settings
        move_to_bottom = self.titlebar_position_checkbox.isChecked()
        titlebar_position = "bottom" if move_to_bottom else "top"
        self.settings.setValue("titlebar_position", titlebar_position)

        gif_display_enabled = self.gif_display_checkbox.isChecked()
        self.settings.setValue("gif_display_enabled", gif_display_enabled)

        # Apply style settings
        if self.main_window and hasattr(self.main_window, "ui_state"):
            self.main_window.ui_state.apply_style_settings()

        if hasattr(self, "max_downloads_spinbox"):
            try:
                val = int(self.max_downloads_spinbox.value())
            except Exception:
                val = 255
            val = max(0, min(255, val))
            self.settings.setValue("max_downloads", val)

        # Sync SLSsteam config only after all validations pass.
        self._sync_slssteam_config_from_stored_settings()

        logger.info("All settings saved.")
        super().accept()

    def reject(self):
        """Restores original settings if cancelled"""
        # Restore API keys
        self.settings.setValue("morrenus_api_key", self._original_morrenus_key)
        if self.sgdb_api_key_input is not None:
            self.settings.setValue("sgdb_api_key", self._original_sgdb_key)

        # Restore audio settings
        if self.main_window and hasattr(self.main_window, "audio_manager"):
            self.main_window.audio_manager.apply_audio_settings()
        super().reject()

    def _is_steam_updates_blocked(self):
        """Check if steam.cfg exists in Steam directory"""
        try:
            from core.steam_helpers import find_steam_install

            steam_path = find_steam_install()
            if not steam_path:
                return False

            steam_cfg_path = os.path.join(steam_path, "steam.cfg")
            return os.path.exists(steam_cfg_path)
        except Exception:
            return False

    def _apply_steam_updates_block(self, block_enabled):
        """Apply steam.cfg configuration to Steam installation directory"""
        try:
            from core.steam_helpers import find_steam_install

            steam_path = find_steam_install()
            if not steam_path:
                logger.warning(
                    "Could not find Steam installation. Skipping steam.cfg configuration."
                )
                return

            steam_cfg_path = os.path.join(steam_path, "steam.cfg")
            source_cfg_path = Paths.deps("steam.cfg")

            if block_enabled:
                # Copy steam.cfg to Steam directory
                if not source_cfg_path.exists():
                    logger.error(
                        f"Source steam.cfg not found at: {str(source_cfg_path)}"
                    )
                    return

                try:
                    shutil.copy2(str(source_cfg_path), steam_cfg_path)
                    logger.info(f"Successfully copied steam.cfg to: {steam_cfg_path}")
                except Exception as e:
                    logger.error(f"Failed to copy steam.cfg to {steam_cfg_path}: {e}")
            else:
                # Remove steam.cfg from Steam directory
                if os.path.exists(steam_cfg_path):
                    try:
                        os.remove(steam_cfg_path)
                        logger.info(
                            f"Successfully removed steam.cfg from: {steam_cfg_path}"
                        )
                    except Exception as e:
                        logger.error(
                            f"Failed to remove steam.cfg from {steam_cfg_path}: {e}"
                        )
                else:
                    logger.info(
                        "steam.cfg not found in Steam directory (already removed or never created)"
                    )

        except Exception as e:
            logger.error(f"Failed to apply steam.cfg configuration: {e}", exc_info=True)

    def _update_slssteam_status(self):
        """Check and display SLSsteam installation status"""
        from pathlib import Path
        from core.tasks.download_slssteam_task import DownloadSLSsteamTask

        # Check if SLSsteam is installed in either native or Flatpak path
        xdg_data_home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        native_so = Path(xdg_data_home) / "SLSsteam" / "SLSsteam.so"
        flatpak_so = Path.home() / ".var/app/com.valvesoftware.Steam/.local/share/SLSsteam/SLSsteam.so"
        sls_installed = native_so.exists() or flatpak_so.exists()

        if not sls_installed:
            if hasattr(self, "slssteam_status_label"):
                self.slssteam_status_label.setVisible(False)
            if hasattr(self, "slssteam_hash_warning_label"):
                self.slssteam_hash_warning_label.setVisible(False)
            return

        # Show labels if version file exists
        if hasattr(self, "slssteam_status_label"):
            self.slssteam_status_label.setVisible(True)
        if hasattr(self, "slssteam_hash_warning_label"):
            self.slssteam_hash_warning_label.setVisible(True)

        try:
            # Run in a thread to avoid blocking UI
            import threading

            def check_status():
                status = DownloadSLSsteamTask.check_update_available()

                # Marshal updates back to the Qt main thread.
                self.slssteam_status_ready.emit(status)

            thread = threading.Thread(target=check_status, daemon=True)
            thread.start()
        except Exception as e:
            logger.error(f"Failed to check SLSsteam status: {e}")
            if hasattr(self, "slssteam_status_label"):
                self.slssteam_status_label.setText("Error checking status")

    def _apply_slssteam_status(self, status):
        """Apply async SLSsteam status update on the Qt main thread."""
        if hasattr(self, "slssteam_status_label"):
            self.slssteam_status_label.setText(self._format_status_text(status))
        if hasattr(self, "slssteam_hash_warning_label"):
            self._update_slssteam_hash_warning(status)

    def _update_slssteam_hash_warning(self, status):
        """Update the steamclient.so hash warning label"""
        if not hasattr(self, "slssteam_hash_warning_label"):
            return

        mismatch = status.get("steamclient_mismatch")
        found = status.get("steamclient_found")
        error = status.get("steamclient_error")
        warning_style = "color: #C06C84; font-size: 11px;"  # Pink warning color

        if mismatch is True:
            self.slssteam_hash_warning_label.setText(
                "Your Steam client is not compatible."
            )
            self.slssteam_hash_warning_label.setStyleSheet(warning_style)
            self.slssteam_hash_warning_label.setVisible(True)
        elif error and found:
            # Found steamclient.so but couldn't check remote hashes
            self.slssteam_hash_warning_label.setText("Could not verify compatibility.")
            self.slssteam_hash_warning_label.setStyleSheet(warning_style)
            self.slssteam_hash_warning_label.setVisible(True)
        elif not found:
            self.slssteam_hash_warning_label.setText("Steam client not found.")
            self.slssteam_hash_warning_label.setStyleSheet(warning_style)
            self.slssteam_hash_warning_label.setVisible(True)
        elif mismatch is False:
            # Hash matches - show success message
            self.slssteam_hash_warning_label.setText("Your Steam client is compatible.")
            self.slssteam_hash_warning_label.setStyleSheet(
                "color: #7FC97F; font-size: 11px;"  # Green success color
            )
            self.slssteam_hash_warning_label.setVisible(True)

    def _format_status_text(self, status):
        """Format the status text for display"""
        if status.get("error"):
            return "Status unknown (error checking)"

        installed = status.get("installed", False)
        latest_version = status.get("latest_version", "Unknown")
        update_available = status.get("update_available", False)

        if not installed:
            return f"Not installed • Latest: {latest_version}"
        else:
            if update_available:
                return f"Update available • Latest: {latest_version}"
            else:
                installed_version = status.get("installed_version", "Unknown")
                if installed_version == "Unknown":
                    return "Installed • Version: Unknown"
                return f"Up to date • Version: {installed_version}"

    def download_slssteam(self):
        """Install or update SLSsteam using the install-sls flow."""
        try:
            if self.main_window and hasattr(self.main_window, "task_manager"):
                self.main_window.task_manager.download_slssteam()
                # Dialog can close now - download runs independently
                self.accept()
            else:
                QMessageBox.critical(
                    self, "Error", "Could not access task manager. Please try again."
                )
        except Exception as e:
            error_msg = f"Failed to start SLSsteam installation: {e}. Check application logs for details."
            logger.error(error_msg, exc_info=True)
            QMessageBox.critical(self, "Error", error_msg)

    def run_slscheevo(self):
        """Launch SLScheevo in the terminal"""
        try:
            slscheevo_path = _get_slscheevo_path()

            if not os.path.exists(slscheevo_path):
                QMessageBox.critical(
                    self, "Error", f"SLScheevo not found at:\n{slscheevo_path}"
                )
                return

            logger.info(f"Launching SLScheevo from: {slscheevo_path}")
            save_dir = _get_slscheevo_save_path()

            command = []

            if str(slscheevo_path).endswith(".py"):
                venv_python = get_venv_python()
                if venv_python:
                    command.extend([venv_python])
                else:
                    command.extend(["python3"])

            command.extend(
                [
                    str(slscheevo_path),
                    "--save-dir",
                    str(save_dir),
                    "--noclear",
                    "--max-tries",
                    "101",
                ]
            )

            working_dir = os.path.dirname(slscheevo_path)

            launched = False

            # Try available terminals
            linux_terminals = [
                ["wezterm", "start", "--always-new-process", "--"] + command,
                ["konsole", "-e"] + command,
                ["gnome-terminal", "--"] + command,
                ["ptyxis", "--"] + command,
                ["alacritty", "-e"] + command,
                ["tilix", "-e"] + command,
                ["xfce4-terminal", "-e"] + command,
                ["terminator", "-x"] + command,
                ["mate-terminal", "-e"] + command,
                ["lxterminal", "-e"] + command,
                ["xterm", "-e"] + command,
                ["kitty", "-e"] + command,
            ]
            for cmd in linux_terminals:
                try:
                    logger.info(f"Trying: {cmd}")
                    subprocess.Popen(cmd, cwd=working_dir)
                    launched = True
                    break
                except FileNotFoundError:
                    continue

            if not launched:
                venv_activate = get_venv_activate()
                if venv_activate is not None:
                    command_text = f'bash -c \'cd "{working_dir}" && source "{venv_activate}" && {" ".join(command)}\''
                else:
                    command_text = " ".join(command)

                msg_box = QMessageBox(self)
                msg_box.setWindowTitle("Terminal Not Found")
                msg_box.setText(
                    "Could not automatically launch a terminal.\n"
                    "Please open a terminal and run:\n"
                )
                msg_box.setInformativeText(command_text)
                msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
                msg_box.setTextInteractionFlags(
                    Qt.TextInteractionFlag.TextSelectableByMouse
                )
                msg_box.exec()

        except Exception as e:
            error_msg = f"Failed to launch SLScheevo: {e}"
            logger.error(error_msg, exc_info=True)
            QMessageBox.critical(self, "Error", error_msg)

    def run_steamless_manually(self):
        """Open a file dialog to select an .exe and run Steamless on it"""
        exe_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Game Executable",
            os.path.expanduser("~"),
            "Executable files (*.exe);;All files (*)",
        )
        if exe_path and self.main_window and hasattr(self.main_window, "task_manager"):
            self.main_window.task_manager.run_steamless_manually(exe_path)

    def open_custom_gifs_dialog(self):
        """Open the Custom GIFs dialog"""
        try:
            dialog = CustomGifsDialog(self.main_window)
            dialog.exec()

        except Exception as e:
            logger.error(f"Failed to open Custom GIFs dialog: {e}")
            QMessageBox.critical(
                self,
                "Error",
                "Failed to open Custom GIFs dialog. Please check the logs for details.",
            )

    def clear_gif_cache(self):
        """Delete the GIF colorized cache and regenerate all GIFs"""
        reply = QMessageBox.question(
            self,
            "Clear GIF Cache?",
            f"This will regenerate all GIFs.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.No:
            return

        if self.main_window and hasattr(self.main_window, "ui_state"):
            logger.info("Regenerating all GIFs...")
            self.main_window.gif_manager.regenerate_anyway = True
            self.main_window.ui_state._update_gifs()
