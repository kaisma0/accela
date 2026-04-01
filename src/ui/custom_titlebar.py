import logging

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QMovie, QPainter, QPixmap, QShortcut, QKeySequence
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget, QSizePolicy, QVBoxLayout

from utils.helpers import get_base_path
from utils.settings import get_settings
from utils.version import app_version

from .assets import (
    BOOK_SVG,
    GEAR_SVG,
    MAXIMIZE,
    MINIMIZE,
    POWER_SVG,
    SEARCH_SVG,
)

logger = logging.getLogger(__name__)


class ClickableLabel(QLabel):
    def __init__(self, text, parent=None, callback=None):
        super().__init__(text, parent)
        self.callback = callback
        self.setStyleSheet("cursor: pointer;")

    def mousePressEvent(self, ev):
        if self.callback:
            self.callback()
        super().mousePressEvent(ev)


class CustomTitleBar(QFrame):
    @classmethod
    def reposition_dialog_titlebar(cls, dialog, position=None):
        """Reposition an already-created dialog titlebar to top/bottom."""
        if not (hasattr(dialog, "_base_layout") and
                hasattr(dialog, "_titlebar") and
                hasattr(dialog, "_tb_content_widget")):
            return

        if position is None:
            settings = get_settings()
            position = settings.value("titlebar_position", "top", type=str)

        dialog._base_layout.removeWidget(dialog._titlebar)
        dialog._base_layout.removeWidget(dialog._tb_content_widget)

        if position == "bottom":
            dialog._base_layout.addWidget(dialog._tb_content_widget)
            dialog._base_layout.addWidget(dialog._titlebar)
        else:
            dialog._base_layout.addWidget(dialog._titlebar)
            dialog._base_layout.addWidget(dialog._tb_content_widget)

    @classmethod
    def setup_dialog_layout(cls, dialog, title=""):
        """Create the standard frameless dialog wrapper with position-aware titlebar."""
        dialog._base_layout = QVBoxLayout(dialog)
        dialog._base_layout.setContentsMargins(0, 0, 0, 0)
        dialog._base_layout.setSpacing(0)

        dialog._titlebar = cls(dialog, title=title or dialog.windowTitle())
        dialog._tb_content_widget = QWidget(dialog)
        cls.reposition_dialog_titlebar(dialog)

    def __init__(self, parent, title="", is_main_window=False):
        super().__init__(parent)
        self.parent = parent
        self.is_main_window = is_main_window
        self.setFixedHeight(36)

        self.no_previous_state = True
        self._svg_buttons = []  # Keeps track of SVG buttons for dynamic theme updates

        self.layout = QHBoxLayout()
        self.layout.setContentsMargins(5, 0, 5, 0)
        self.layout.setSpacing(5)

        self.left_widget = QWidget()
        self.left_layout = QHBoxLayout(self.left_widget)
        self.left_layout.setContentsMargins(0, 0, 0, 0)

        self.right_widget = QWidget()
        self.right_layout = QHBoxLayout(self.right_widget)
        self.right_layout.setContentsMargins(0, 0, 0, 0)
        self.right_layout.setSpacing(2)

        # Maintains normal size even when left side is big
        self.right_layout.addStretch()

        if self.is_main_window:
            self._setup_main_window_widgets(parent)

        # Window control buttons (minimize, maximize, close)
        self.minimize_button = self._create_svg_button(MINIMIZE, self._minimize_window, "Minimize")
        self.right_layout.addWidget(self.minimize_button)

        self.maximize_button = self._create_svg_button(MAXIMIZE, self._maximize_window, "Maximize")
        self.right_layout.addWidget(self.maximize_button)

        self.close_button = self._create_svg_button(POWER_SVG, parent.close, "Close")
        self.right_layout.addWidget(self.close_button)

        self.title_label = QLabel(title)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        self._update_title_label_style()

        self.layout.addWidget(self.left_widget, 0, Qt.AlignmentFlag.AlignLeft)
        self.layout.addWidget(self.title_label, 1)
        self.layout.addWidget(self.right_widget, 0, Qt.AlignmentFlag.AlignRight)

        self.setLayout(self.layout)

        # Set policies
        self.left_widget.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.right_widget.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self._update_sizing()

        self._apply_style()
        self._update_button_colors()
        self._update_button_styles()

        # Add scoped Ctrl+Q shortcut to close the window
        self.exit_shortcut = QShortcut(QKeySequence("Ctrl+Q"), self.parent)
        self.exit_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self.exit_shortcut.activated.connect(self.parent.close)

    def _setup_main_window_widgets(self, parent):
        self.navi_label = QLabel()
        self.navi_movie = QMovie(str(get_base_path() / "gifs/colorized/navi.gif"))
        if self.navi_movie.isValid():
            self.navi_movie.jumpToFrame(0)
            orig = self.navi_movie.currentImage().size()
            h, w = (20, int(20 * (orig.width() / orig.height())) if orig.height() > 0 else 57)
            self.navi_movie.setScaledSize(QSize(w, h))
            self.navi_label.setFixedSize(w, h)
            self.navi_label.setMovie(self.navi_movie)
            self.navi_movie.start()

        self.left_layout.insertWidget(0, self.navi_label, alignment=Qt.AlignmentFlag.AlignLeft)

        self.status_button = self._create_colored_circle_button("#FF0000", parent.open_status_dialog, "Download Status")
        self.right_layout.insertWidget(1, self.status_button)

        self.search_button = self._create_svg_button(SEARCH_SVG, parent.open_fetch_dialog, "Download Game")
        self.right_layout.insertWidget(2, self.search_button)

        self.game_library_button = self._create_svg_button(BOOK_SVG, parent.open_game_library, "Game Library")
        self.right_layout.insertWidget(3, self.game_library_button)

        self.settings_button = self._create_svg_button(GEAR_SVG, parent.open_settings, "Settings")
        self.right_layout.insertWidget(4, self.settings_button)

        version_label = ClickableLabel(app_version, parent, parent.open_credits_dialog)
        version_label.setStyleSheet("color: #888888;")
        version_label.setToolTip("View credits")
        self.left_layout.addWidget(version_label, alignment=Qt.AlignmentFlag.AlignLeft)

    def set_title(self, title):
        self.title_label.setText(title)

    def _update_sizing(self):
        self.left_widget.setMinimumSize(self.left_widget.sizeHint())
        self.right_widget.setMinimumSize(self.right_widget.sizeHint())

        max_side_width = max(self.left_widget.sizeHint().width(), self.right_widget.sizeHint().width())
        if max_side_width < 70:
            max_side_width = 70
        self.left_widget.setFixedWidth(max_side_width)
        self.right_widget.setFixedWidth(max_side_width)

    def _apply_style(self):
        settings = get_settings()
        bg_color = settings.value("background_color", "#000000")
        accent_color = settings.value("accent_color", "#C06C84")

        self.setStyleSheet(f"""
            QFrame {{
                background-color: {bg_color};
            }}
            QToolTip {{
                color: {accent_color};
                background-color: {bg_color};
                border: 1px solid {accent_color};
                padding: 2px;
            }}
        """)
        self._update_title_label_style()

    def _update_title_label_style(self):
        if hasattr(self, "title_label"):
            settings = get_settings()
            accent_color = settings.value("accent_color", "#C06C84")
            self.title_label.setStyleSheet(f"color: {accent_color}; font-size: 14pt;")

    def update_style(self):
        self._apply_style()
        self._update_button_colors()
        self._update_button_styles()

    def _update_button_styles(self):
        settings = get_settings()
        background_color = QColor(settings.value("background_color", "#000000"))
        background_color_hover = background_color
        hover_lightness = 150
        if background_color == QColor("#000000"):
            background_color_hover = QColor("#282828")
            hover_lightness = 120

        button_style = f"""
            QPushButton {{
                background-color: {background_color.name()};
                border: none;
                border-radius: 3px;
                padding: 1px;
            }}
            QPushButton:hover {{
                background-color: {background_color_hover.lighter(hover_lightness).name()};
            }}
        """

        # Apply to both left and right layouts cleanly
        for layout in (self.left_layout, self.right_layout):
            for idx in range(layout.count()):
                item = layout.itemAt(idx)
                if item and item.widget():
                    widget = item.widget()
                    if isinstance(widget, QPushButton) and not widget.property("is_circle"):
                        widget.setStyleSheet(button_style)

    def _update_button_colors(self):
        settings = get_settings()
        accent_color = settings.value("accent_color", "#C06C84")

        for button, svg_data in self._svg_buttons:
            if button:
                self._update_svg_button_color(button, svg_data, accent_color)

        if self.is_main_window and self.no_previous_state:
            self._update_colored_circle_button(self.status_button, accent_color)

    def _update_colored_circle_button(self, button, color):
        try:
            stylesheet = f"""
            QPushButton {{
                border-radius: 12px;
                background-color: {color};
                border: none;
            }}
            QPushButton:hover {{
                border: 2px solid {color};
                background-color: {color};
                opacity: 0.8;
            }}
            QPushButton:pressed {{
                opacity: 0.6;
            }}
            """
            button.setStyleSheet(stylesheet)
        except Exception as e:
            logger.error(f"Failed to update colored circle button: {e}", exc_info=True)

    def _get_colored_svg_icon(self, svg_data, color):
        """Helper to generate a QIcon from SVG data with a specific color."""
        renderer = QSvgRenderer(svg_data.encode("utf-8"))
        icon_size = QSize(18, 18)

        pixmap = QPixmap(icon_size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter)

        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(pixmap.rect(), QColor(color))
        painter.end()

        return QIcon(pixmap)

    def _update_svg_button_color(self, button, svg_data, color):
        try:
            icon = self._get_colored_svg_icon(svg_data, color)
            button.setIcon(icon)
        except Exception as e:
            logger.error(f"Failed to update SVG button color: {e}", exc_info=True)

    def _create_svg_button(self, svg_data, on_click, tooltip):
        try:
            button = QPushButton()
            button.setToolTip(tooltip)

            settings = get_settings()
            accent_color = settings.value("accent_color", "#C06C84")

            icon = self._get_colored_svg_icon(svg_data, accent_color)

            button.setIcon(icon)
            button.setIconSize(QSize(18, 18))
            button.setFixedSize(24, 24)

            button.clicked.connect(on_click)

            # Register button to easily update colors later
            self._svg_buttons.append((button, svg_data))

            return button
        except Exception as e:
            logger.error(f"Failed to create SVG button: {e}", exc_info=True)
            fallback_button = QPushButton("X")
            fallback_button.setFixedSize(24, 24)
            fallback_button.clicked.connect(on_click)
            return fallback_button

    def _create_colored_circle_button(self, color, callback, tooltip_text):
        button = QPushButton()
        button.setFixedSize(24, 24)
        button.setProperty("is_circle", True)
        self._update_colored_circle_button(button, color)

        if tooltip_text:
            button.setToolTip(tooltip_text)

        if callback:
            button.clicked.connect(callback)

        return button

    def _minimize_window(self):
        self.parent.showMinimized()

    def _maximize_window(self):
        if self.parent.isMaximized():
            self.parent.showNormal()
        else:
            self.parent.showMaximized()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.pos()
            is_edge = (
                pos.x() >= self.width() - 6
                or pos.x() <= 6
                or pos.y() <= 6
                or pos.y() >= self.height() - 6
            )
            if not is_edge:
                window = self.window().windowHandle()
                if window is not None:
                    window.startSystemMove()
            event.accept()


"""
The wired might actually be thought of as a highly advanced upper layer of the real world. In other words, physical reality is nothing but an illusion, a hologram of the information that flows to us through the wired.
This is because the body, physical motion, the activity of the human brain is merely a physical phenomenon, simply caused by synapses delivering electrical impulses.
The physical body exists at a less evolved plane only to verify one's existence in the universe.
"""
