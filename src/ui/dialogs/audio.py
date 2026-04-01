from ui.custom_titlebar import CustomTitleBar
import logging
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QVBoxLayout,
    QCheckBox,
    QLabel,
    QHBoxLayout,
    QSlider,
    QPushButton,
)
from PyQt6.QtCore import Qt

from utils.settings import get_settings

logger = logging.getLogger(__name__)

def make_volume_row(name: str, slider: QSlider, value_label: QLabel, reset_button: QPushButton, current_volume: int):
    layout = QHBoxLayout()

    slider.setRange(0, 100)
    slider.setTickPosition(QSlider.TickPosition.TicksBothSides)
    slider.setValue(current_volume)

    label = QLabel(f"{name}:")
    label.setAlignment(Qt.AlignmentFlag.AlignVCenter)
    label.setFixedWidth(105)

    value_label.setFixedWidth(30) # Percentage
    reset_button.setFixedHeight(25)

    layout.addWidget(label)
    layout.addWidget(slider, 1)
    layout.addWidget(value_label)
    layout.addWidget(reset_button)

    return layout


class AudioDialog(QDialog):
    # Pass audio_manager explicitly to remove tight coupling to the parent window
    def __init__(self, audio_manager=None, parent=None):
        super().__init__(parent)
        self.audio_manager = audio_manager
        
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle("Audio Settings")
        self.settings = get_settings()
        self.setMinimumSize(400, 300)
        
        CustomTitleBar.setup_dialog_layout(self, title=self.windowTitle())
        
        self.layout = QVBoxLayout(self._tb_content_widget)

        logger.debug("Opening AudioDialog.")

        # --- Audio Playback Settings ---
        audio_layout = QVBoxLayout()
        audio_label = QLabel("Audio Settings")
        audio_label.setStyleSheet("font-weight: bold;")
        audio_layout.addWidget(audio_label)

        # Keep track of generated checkboxes
        self.checkboxes = {}
        checkbox_configs = [
            ("Play \"Entering The Wired\" on start", "play_etw", True),
            ("Play \"Let's All Love Lain\" on exit", "play_lall", True),
            ("Play 50Hz hum noise loop", "play_50hz_hum", True),
        ]

        for text, key, default_val in checkbox_configs:
            cb = QCheckBox(text)
            cb.setChecked(self.settings.value(key, default_val, type=bool))
            audio_layout.addWidget(cb)
            self.checkboxes[key] = cb

        self.layout.addLayout(audio_layout)

        # --- Volume Settings ---
        volume_layout = QVBoxLayout()
        volume_label = QLabel("Volume Settings")
        volume_label.setStyleSheet("font-weight: bold;")
        volume_layout.addWidget(volume_label)

        # Keep track of generated sliders
        self.sliders = {}
        
        # Configuration for sliders: (Display Name, Setting Key, Default Value)
        # To disable a slider without deleting settings, just comment out its tuple here.
        volume_configs = [
            ("Master Volume", "master_volume", 80),
            # ("Music Volume", "music_volume", 80), 
            ("Effects Volume", "effects_volume", 50),
            ("Hum Volume", "hum_volume", 20),
        ]

        for label_text, key, default_val in volume_configs:
            current_val = self.settings.value(key, default_val, type=int)
            
            slider = QSlider(Qt.Orientation.Horizontal)
            
            # Apply specific styling if needed
            if key == "effects_volume":
                slider.setTickPosition(QSlider.TickPosition.TicksBelow)
                slider.setTickInterval(10)
                
            value_label = QLabel(f"{current_val}%")
            reset_button = QPushButton("Reset")
            
            # Note: We use lambda defaults (checked=False, k=key, etc.) to correctly 
            # capture the variables in the current loop iteration
            reset_button.clicked.connect(
                lambda checked=False, k=key, d=default_val, s=slider: self.reset_volume(k, d, s)
            )
            
            slider.valueChanged.connect(
                lambda value, k=key, lbl=value_label: self.on_volume_changed(k, value, lbl)
            )

            volume_layout.addLayout(make_volume_row(label_text, slider, value_label, reset_button, current_val))
            self.sliders[key] = slider

        self.layout.addLayout(volume_layout)

        # Test buttons for ETW and LALL sounds
        test_layout = QHBoxLayout()
        self.test_etw_button = QPushButton("Test ETW Sound")
        self.test_lall_button = QPushButton("Test LALL Sound")
        self.test_etw_button.clicked.connect(self.test_etw_sound)
        self.test_lall_button.clicked.connect(self.test_lall_sound)
        test_layout.addWidget(self.test_etw_button)
        test_layout.addWidget(self.test_lall_button)
        self.layout.addLayout(test_layout)

        # Dialog buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.layout.addWidget(buttons)

        # Sync audio preview values with current settings before any slider interaction
        if self.audio_manager:
            self.audio_manager.sync_preview_values_from_settings()

    def on_volume_changed(self, setting_key, value, value_label):
        """Handle volume changes in real-time (without saving to settings)"""
        value_label.setText(f"{value}%")
        self.apply_volume_preview(setting_key, value)

    def apply_volume_preview(self, setting_key, value):
        """Apply volume changes for preview only via dynamic method call"""
        if not self.audio_manager:
            return
        
        # Dynamically call the correct preview method (e.g., apply_master_volume_preview)
        method_name = f"apply_{setting_key}_preview"
        if hasattr(self.audio_manager, method_name):
            getattr(self.audio_manager, method_name)(value)
        else:
            logger.warning(f"Audio manager missing preview method: {method_name}")

    def reset_volume(self, setting_key, default_value, slider):
        """Reset volume to default value (preview only)"""
        # Setting the value automatically triggers the valueChanged signal, 
        # which will update the label and call apply_volume_preview for us.
        slider.setValue(default_value)

    def test_etw_sound(self):
        """Test play the ETW sound"""
        if self.audio_manager:
            self.audio_manager.test_etw_sound()

    def test_lall_sound(self):
        """Test play the LALL sound"""
        if self.audio_manager:
            self.audio_manager.test_lall_sound()

    def accept(self):
        # Save checkbox settings dynamically
        for key, checkbox in self.checkboxes.items():
            self.settings.setValue(key, checkbox.isChecked())

        # Save volume settings only for currently rendered sliders
        for key, slider in self.sliders.items():
            self.settings.setValue(key, slider.value())

        # Apply final audio settings
        if self.audio_manager:
            self.audio_manager.apply_audio_settings()

        logger.info("Audio settings saved.")
        super().accept()

    def reject(self):
        """Restores original volumes if cancelled"""
        if self.audio_manager:
            self.audio_manager.apply_audio_settings()
        super().reject()