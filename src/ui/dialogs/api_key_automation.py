import logging
import os
import shutil
from importlib import import_module

from PyQt6.QtCore import QTimer, QUrl
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)
from utils.helpers import get_base_path

logger = logging.getLogger(__name__)


def _load_webengine_classes():
    webengine_core = import_module("PyQt6.QtWebEngineCore")
    webengine_widgets = import_module("PyQt6.QtWebEngineWidgets")
    return (
        webengine_core.QWebEnginePage,
        webengine_core.QWebEngineProfile,
        webengine_widgets.QWebEngineView,
    )


class ApiKeyAutomationDialog(QDialog):
    BASE_URL = "https://manifest.morrenus.xyz"
    API_KEY_PAGE_URL = "https://manifest.morrenus.xyz/api-keys/user"
    MAX_RETRIES = 100
    RETRY_DELAY_MS = 1000
    MAX_KEY_POLL_RETRIES = 10

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Morrenus API Key Automation")
        self.resize(1000, 700)

        try:
            (
                self._webengine_page_cls,
                self._webengine_profile_cls,
                self._webengine_view_cls,
            ) = _load_webengine_classes()
        except Exception as exc:
            logger.error(f"PyQt6 WebEngine is unavailable: {exc}")
            raise RuntimeError(
                "PyQt6-WebEngine is required for Morrenus API automation."
            ) from exc

        self.generated_api_key = None
        self._closing = False
        self._profile = None
        self._page = None
        self._normal_geometry = self.geometry()
        self._discord_click_in_progress = False
        self._api_page_processing = False

        self._build_ui()
        self._initialize_webview()

    @staticmethod
    def prompt_for_api_key(parent=None):
        dialog = ApiKeyAutomationDialog(parent)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return dialog.generated_api_key
        return None

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.status_label = QLabel("Initializing...")
        self.status_label.setContentsMargins(15, 10, 15, 10)
        layout.addWidget(self.status_label)

        self.web_view = self._webengine_view_cls(self)
        layout.addWidget(self.web_view, 1)

        actions_layout = QHBoxLayout()
        actions_layout.setContentsMargins(15, 10, 15, 10)
        actions_layout.setSpacing(12)
        actions_layout.addStretch(1)

        self.clear_cache_button = QPushButton("Clear Cache and Restart")
        self.clear_cache_button.clicked.connect(self._clear_cache)
        actions_layout.addWidget(self.clear_cache_button)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        actions_layout.addWidget(self.cancel_button)

        layout.addLayout(actions_layout)

    def _initialize_webview(self):
        profile_root = os.path.join(str(get_base_path()), "WebView")
        self._profile_root = profile_root
        cache_path = os.path.join(profile_root, "Cache")
        os.makedirs(cache_path, exist_ok=True)

        self._profile = self._webengine_profile_cls("accela_morrenus", self)
        self._profile.setPersistentStoragePath(profile_root)
        self._profile.setCachePath(cache_path)

        self._page = self._webengine_page_cls(self._profile, self)
        self.web_view.setPage(self._page)
        self.web_view.loadFinished.connect(self._on_load_finished)

        self.web_view.setUrl(QUrl(self.BASE_URL))

    def closeEvent(self, event):
        self._closing = True
        super().closeEvent(event)

    def _is_discord_login_url(self, url):
        return "discord.com/login" in url or "discord.com/oauth2" in url

    def _on_load_finished(self, success):
        if self._closing:
            return

        if not success:
            self.status_label.setText("Navigation failed.")
            return

        current_url = self.web_view.url().toString()
        self.status_label.setText(f"Current URL: {current_url}")

        if "/api-keys/user" not in current_url:
            self._api_page_processing = False

        if self._is_discord_login_url(current_url):
            self._maximize_window()
            self.status_label.setText("Checking for 'Authorize' button...")
            if not self._discord_click_in_progress:
                self._discord_click_in_progress = True
                self._attempt_click_continue_with_discord(0)
            return

        self._restore_window()

        if current_url.rstrip("/") == self.BASE_URL.rstrip("/"):
            self.status_label.setText("Checking login status...")
            if not self._discord_click_in_progress:
                self._discord_click_in_progress = True
                self._attempt_click_continue_with_discord(0)
            return

        if "/api-keys/user" in current_url:
            self.status_label.setText("On API key page. Processing...")
            if not self._api_page_processing:
                self._api_page_processing = True
                self._process_api_key_page()

    def _maximize_window(self):
        if not self.isMaximized():
            self._normal_geometry = self.geometry()
            self.showMaximized()

    def _restore_window(self):
        if self.isMaximized():
            self.showNormal()
            if self._normal_geometry.isValid():
                self.setGeometry(self._normal_geometry)

    def _run_js(self, script, callback):
        if self._closing or self._page is None:
            callback(None)
            return

        try:
            self._page.runJavaScript(script, callback)
        except Exception as exc:
            logger.warning(f"JavaScript execution failed: {exc}")
            callback(None)

    def _attempt_click_continue_with_discord(self, attempt):
        if self._closing:
            return

        if attempt >= self.MAX_RETRIES:
            self._discord_click_in_progress = False
            current_url = self.web_view.url().toString()
            if current_url.rstrip("/") == self.BASE_URL.rstrip("/"):
                self.status_label.setText("Navigating to API key page...")
                self.web_view.setUrl(QUrl(self.API_KEY_PAGE_URL))
            return

        script = """
            (function() {
                const allLinks = document.querySelectorAll('a, button');
                for (const link of allLinks) {
                    const text = (link.innerText || '').toLowerCase();
                    if (text.includes('logout') || text.includes('api keys')) {
                        return 'already_logged_in';
                    }
                }

                const discordLoginBtn = document.querySelector('a.discord-login-btn');
                if (discordLoginBtn) {
                    discordLoginBtn.click();
                    return 'clicked_login';
                }

                const buttons = document.querySelectorAll('button');
                for (const btn of buttons) {
                    const text = (btn.innerText || '').toLowerCase();
                    if (text.includes('authorize')) {
                        if (btn.disabled) return 'found_but_disabled';
                        btn.click();
                        return 'clicked_authorize';
                    }
                }

                return 'not_found';
            })();
        """

        def on_result(result):
            if self._closing:
                return

            result_text = str(result or "")
            if "already_logged_in" in result_text:
                self._discord_click_in_progress = False
                self.status_label.setText("Logged in. Navigating to API keys...")
                self.web_view.setUrl(QUrl(self.API_KEY_PAGE_URL))
                return

            if "clicked_login" in result_text:
                self._discord_click_in_progress = False
                self.status_label.setText("Clicked 'Continue with Discord'...")
                return

            if "clicked_authorize" in result_text:
                self._discord_click_in_progress = False
                self.status_label.setText("Clicked 'Authorize'...")
                return

            if "found_but_disabled" in result_text:
                self.status_label.setText("Waiting for Authorize button...")

            QTimer.singleShot(
                self.RETRY_DELAY_MS,
                lambda: self._attempt_click_continue_with_discord(attempt + 1),
            )

        self._run_js(script, on_result)

    def _process_api_key_page(self):
        if self._closing:
            return

        self._extract_key(self._on_initial_key_extracted)

    def _on_initial_key_extracted(self, key):
        if self._closing:
            return

        if key:
            self._finish_with_key(key)
            return

        self.status_label.setText("Generating new key...")
        self._click_generate_button(self._on_generate_clicked)

    def _on_generate_clicked(self, clicked):
        if self._closing:
            return

        if not clicked:
            self.status_label.setText("Generate button not found. Please copy manually.")
            self._api_page_processing = False
            return

        self._poll_for_key(0)

    def _poll_for_key(self, attempt):
        if self._closing:
            return

        if attempt >= self.MAX_KEY_POLL_RETRIES:
            self.status_label.setText("Timeout waiting for key. Please copy manually.")
            self._api_page_processing = False
            return

        def check_once():
            if self._closing:
                return

            self._extract_key(lambda key: self._on_polled_key(key, attempt))

        QTimer.singleShot(self.RETRY_DELAY_MS, check_once)

    def _on_polled_key(self, key, attempt):
        if self._closing:
            return

        if key:
            self._finish_with_key(key)
            return

        self._poll_for_key(attempt + 1)

    def _extract_key(self, callback):
        script = """
            (function() {
                const newKeySpan = document.getElementById('newApiKey');
                if (newKeySpan && newKeySpan.innerText && newKeySpan.innerText.startsWith('smm')) {
                    return newKeySpan.innerText.trim();
                }

                const allElements = document.querySelectorAll('span, div, p, code');
                for (const el of allElements) {
                    const text = el.innerText ? el.innerText.trim() : '';
                    if (text.startsWith('smm') && text.length > 20 && !text.includes(' ')) {
                        return text;
                    }
                }

                return null;
            })();
        """

        def on_result(result):
            if self._closing:
                callback(None)
                return

            if isinstance(result, str):
                key = result.strip().strip('"')
                if key.startswith("smm") and len(key) > 20 and " " not in key:
                    callback(key)
                    return

            callback(None)

        self._run_js(script, on_result)

    def _click_generate_button(self, callback):
        script = """
            (function() {
                const generateBtn = document.getElementById('generateBtn');
                if (generateBtn) {
                    generateBtn.click();
                    return true;
                }
                return false;
            })();
        """

        def on_result(result):
            callback(bool(result))

        self._run_js(script, on_result)

    def _finish_with_key(self, key):
        if self._closing:
            return

        self._api_page_processing = False
        self.generated_api_key = key
        self.status_label.setText(f"Success! Key found: {key}")
        self.accept()

    def _clear_cache(self):
        if self._profile is None or self._closing:
            return

        try:
            self.status_label.setText("Clearing browsing data...")
            self.clear_cache_button.setEnabled(False)
            self._discord_click_in_progress = False
            self._api_page_processing = False

            # Tear down profile/page first, then remove profile storage on disk.
            try:
                self.web_view.loadFinished.disconnect(self._on_load_finished)
            except Exception:
                pass

            if self._page is not None:
                try:
                    self._page.deleteLater()
                except Exception:
                    pass
                self._page = None

            if self._profile is not None:
                try:
                    self._profile.deleteLater()
                except Exception:
                    pass
                self._profile = None

            QTimer.singleShot(50, self._recreate_clean_profile)
        except Exception as exc:
            logger.error(f"Error clearing web profile cache: {exc}")
            self.clear_cache_button.setEnabled(True)
            self.status_label.setText(f"Error clearing cache: {exc}")

    def _recreate_clean_profile(self):
        if self._closing:
            return

        try:
            if getattr(self, "_profile_root", None):
                shutil.rmtree(self._profile_root, ignore_errors=True)

            self._initialize_webview()
            self.status_label.setText("All browsing data cleared. Restarting...")
            self.web_view.setUrl(QUrl(self.BASE_URL))
        except Exception as exc:
            logger.error(f"Error recreating clean web profile: {exc}")
            self.status_label.setText(f"Error clearing cache: {exc}")
        finally:
            self.clear_cache_button.setEnabled(True)
