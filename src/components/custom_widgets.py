from PyQt6.QtCore import Qt, QRect
from PyQt6.QtGui import QFont, QFontMetrics
from PyQt6.QtWidgets import QLabel, QPushButton


def _calculate_fitting_font_size(text, base_font, width, height, max_size, min_size=8, word_wrap=False):
    """Helper function to find the largest font size that fits within the given dimensions."""
    new_size = max(min_size, min(max_size, int(height * 0.4)))
    test_font = QFont(base_font)

    target_width = int(width * 0.9)
    target_height = int(height * 0.9)

    while new_size > min_size:
        test_font.setPointSize(new_size)
        metrics = QFontMetrics(test_font)

        if word_wrap:
            # Measure bounding rect for wrapped multi-line text
            rect = metrics.boundingRect(
                QRect(0, 0, target_width, 10000),
                Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignCenter,
                text
            )
            if rect.width() <= target_width and rect.height() <= target_height:
                break
        else:
            # Measure single-line text
            text_width = metrics.horizontalAdvance(text)
            text_height = metrics.height()
            if text_width <= target_width and text_height <= target_height:
                break

        new_size -= 1

    return new_size


class ScaledLabel(QLabel):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setMinimumSize(1, 1)
        self._movie = None

    def setMovie(self, movie):
        if self._movie == movie:
            return

        if self._movie:
            try:
                self._movie.frameChanged.disconnect(self.on_frame_changed)
            except TypeError:
                pass # Ignore if it wasn't connected

        self._movie = movie
        if self._movie:
            self._movie.frameChanged.connect(self.on_frame_changed)
            self.on_frame_changed(0) # Trigger immediate update for current frame

    def on_frame_changed(self, frame_number=0):
        if self.size().width() > 0 and self.size().height() > 0 and self._movie:
            pixmap = self._movie.currentPixmap()
            if not pixmap.isNull():
                scaled_pixmap = pixmap.scaled(
                    self.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                super().setPixmap(scaled_pixmap)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._movie:
            self.on_frame_changed(0)


class ScaledFontLabel(QLabel):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setMinimumSize(1, 1)
        self.setWordWrap(True)  # Enable word wrap
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)  # Center text
        self.max_font_size = 72

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._scale_font()

    def setText(self, text):
        """Override setText to trigger font scaling immediately"""
        super().setText(text)
        self._scale_font()

    def _scale_font(self):
        text = self.text()
        if text and self.width() > 0 and self.height() > 0:
            font = self.font()
            new_size = _calculate_fitting_font_size(
                text, font, self.width(), self.height(),
                max_size=self.max_font_size, word_wrap=self.wordWrap()
            )
            font.setPointSize(new_size)
            self.setFont(font)


class ScaledButton(QPushButton):
    """QPushButton that automatically scales its font to fit the button size"""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setMinimumSize(1, 1)
        self.max_font_size = 14

    def set_max_font_size(self, size):
        """Set maximum font size for scaling"""
        self.max_font_size = max(8, size)
        self._scale_font()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._scale_font()

    def setText(self, text):
        """Override setText to trigger font scaling immediately"""
        super().setText(text)
        self._scale_font()

    def _scale_font(self):
        """Calculate and set appropriate font size for current text and button size"""
        text = self.text()
        if text and self.width() > 0 and self.height() > 0:
            font = self.font()
            new_size = _calculate_fitting_font_size(
                text, font, self.width(), self.height(),
                max_size=self.max_font_size, word_wrap=False
            )
            font.setPointSize(new_size)
            self.setFont(font)
