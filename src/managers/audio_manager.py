import logging
import shutil
import subprocess

from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import QApplication
from PyQt6.QtMultimedia import (
    QMediaDevices,
    QSoundEffect,
)

from utils.paths import Paths
from utils.settings import get_settings

logger = logging.getLogger(__name__)

# Sound file constants
SOUND_OPEN_FILE = "etw.wav"
SOUND_CLOSE_FILE = "lall.wav"
SOUND_LOOP_FILE = "50hz.wav"


class AudioManager:
    def __init__(self, main_window):
        self.main_window = main_window
        self.settings = get_settings()
        self.exit_sound_played = False
        self._quit_handler_connected = False

        # Store current preview values (percent from sliders/settings).
        self.preview_master_volume_pct = self.settings.value(
            "master_volume", 80, type=int
        )
        self.preview_effects_volume_pct = self.settings.value(
            "effects_volume", 50, type=int
        )
        self.preview_hum_volume_pct = self.settings.value(
            "hum_volume", 20, type=int
        )

        # QtMultimedia players
        self.open_sound = None
        self.close_sound = None
        self.loop_sound = None

        self.setup_sounds()

    def check_audio_devices(self):
        """Check available audio output devices using PyQt6"""
        return len(QMediaDevices.audioOutputs()) > 0

    def apply_volume(self, volumeSliderValue):
        """Convert slider value to linear volume"""
        return volumeSliderValue / 100.0

    def setup_sounds(self, play_open_sound: bool = True):
        """Setup all audio effects with volume control using QtMultimedia"""
        logger.debug("Setting up audio sounds...")

        # Check for audio devices first
        if not self.check_audio_devices():
            logger.warning("Audio setup aborted - no audio devices available")
            self.open_sound = self.close_sound = None
            self.loop_sound = None
            return

        try:
            # Open sound ("Entering The Wired")
            logger.debug("Setting up open sound...")
            open_sound_path = self._resolve_sound_path(SOUND_OPEN_FILE)
            if open_sound_path.exists():
                logger.debug(f"Loading open sound: {str(open_sound_path)}")
                self.open_sound = QSoundEffect(self.main_window)
                self.open_sound.setSource(QUrl.fromLocalFile(str(open_sound_path)))
                if (
                    play_open_sound
                    and self.settings.value("play_etw", True, type=bool)
                    and not self.open_sound.isPlaying()
                ):
                    logger.debug("Playing open sound (ETW)")
                    self.open_sound.play()
            else:
                logger.warning(f"Could not find open sound: {str(open_sound_path)}")
                self.open_sound = None

            # Close sound ("Let's All Love Lain")
            logger.debug("Setting up close sound...")
            close_sound_path = self._resolve_sound_path(SOUND_CLOSE_FILE)
            if close_sound_path.exists():
                logger.debug(f"Loading close sound: {str(close_sound_path)}")
                app = QApplication.instance()
                close_parent = app if app is not None else self.main_window
                self.close_sound = QSoundEffect(close_parent)
                self.close_sound.setSource(QUrl.fromLocalFile(str(close_sound_path)))
                if app is not None and not self._quit_handler_connected:
                    app.aboutToQuit.connect(self.on_app_about_to_quit)
                    self._quit_handler_connected = True
            else:
                logger.warning(f"Could not find close sound: {str(close_sound_path)}")
                self.close_sound = None

            # Loop sound (50Hz hum)
            logger.debug("Setting up loop sound...")
            loop_sound_path = self._resolve_sound_path(SOUND_LOOP_FILE)
            if loop_sound_path.exists():
                logger.debug(f"Loading loop sound: {str(loop_sound_path)}")
                self.loop_sound = QSoundEffect(self.main_window)
                self.loop_sound.setSource(QUrl.fromLocalFile(str(loop_sound_path)))
                self.loop_sound.setLoopCount(QSoundEffect.Infinite)

                # Only play if enabled in settings
                if self.settings.value("play_50hz_hum", True, type=bool):
                    logger.debug("Starting loop sound playback")
                    self.loop_sound.setVolume(0.0)
                    self.loop_sound.play()
            else:
                logger.warning(f"Could not find loop sound: {str(loop_sound_path)}")
                self.loop_sound = None

            # Apply initial audio settings
            logger.debug("Applying initial audio settings...")
            self.apply_audio_settings()
            logger.debug("Audio setup completed successfully")

        except Exception as e:
            logger.error(f"Error during audio setup: {e}")
            self.open_sound = self.close_sound = None
            self.loop_sound = None

    def _resolve_sound_path(self, filename: str):
        """Resolve sound path from the resources folder."""
        return Paths.sound_path(filename)

    def apply_audio_settings(self):
        """Applies all audio settings including volumes using saved settings"""
        if not any([self.open_sound, self.close_sound, self.loop_sound]):
            logger.debug("Audio not available, skipping settings application")
            return

        logger.debug("Applying audio settings...")

        # Get slider values from settings
        master_volume = self.apply_volume(
            self.settings.value("master_volume", 80, type=int)
        )
        effects_volume = self.apply_volume(
            self.settings.value("effects_volume", 50, type=int)
        )
        hum_volume = self.apply_volume(
            self.settings.value("hum_volume", 20, type=int)
        )

        logger.debug(
            f"Volume levels - Master: {master_volume:.3f}, Effects: {effects_volume:.3f}, Hum: {hum_volume:.3f}"
        )

        # Apply volumes
        effects_final = master_volume * effects_volume
        hum_final = master_volume * hum_volume

        if self.open_sound:
            self.open_sound.setVolume(effects_final)
        if self.close_sound:
            self.close_sound.setVolume(effects_final)
        logger.debug(f"Effects volume: {effects_final:.3f}")

        if self.loop_sound:
            self.loop_sound.setVolume(hum_final)
        logger.debug(f"Hum volume: {hum_final:.3f}")

        # Handle loop sound playback state
        play_loop = self.settings.value("play_50hz_hum", True, type=bool)
        if play_loop and self.loop_sound and not self.loop_sound.isPlaying():
            logger.debug("Starting loop sound playback")
            self.loop_sound.play()
        elif not play_loop and self.loop_sound and self.loop_sound.isPlaying():
            logger.debug("Stopping loop sound playback")
            self.loop_sound.stop()

    def sync_preview_values_from_settings(self):
        """Sync preview values with current settings - call before starting preview interactions"""
        self.preview_master_volume_pct = self.settings.value(
            "master_volume", 80, type=int
        )
        self.preview_effects_volume_pct = self.settings.value(
            "effects_volume", 50, type=int
        )
        self.preview_hum_volume_pct = self.settings.value(
            "hum_volume", 20, type=int
        )
        logger.debug(
            f"Synced preview values from settings - Master: {self.preview_master_volume_pct}, Effects: {self.preview_effects_volume_pct}, Hum: {self.preview_hum_volume_pct}"
        )

    def apply_preview_volumes(self, master=None, effects=None, hum=None):
        """Apply preview volumes using current slider values"""
        # Update preview values if provided
        if master is not None:
            self.preview_master_volume_pct = master
        if effects is not None:
            self.preview_effects_volume_pct = effects
        if hum is not None:
            self.preview_hum_volume_pct = hum

        # Calculate volumes using current preview values
        master_volume = self.apply_volume(self.preview_master_volume_pct)
        effects_volume = self.apply_volume(self.preview_effects_volume_pct)
        hum_volume = self.apply_volume(self.preview_hum_volume_pct)

        logger.debug(
            f"Preview volumes - Master: {self.preview_master_volume_pct}, Effects: {self.preview_effects_volume_pct}, Hum: {self.preview_hum_volume_pct}"
        )

        effects_final = master_volume * effects_volume
        hum_final = master_volume * hum_volume

        if self.open_sound:
            self.open_sound.setVolume(effects_final)
        if self.close_sound:
            self.close_sound.setVolume(effects_final)
        if self.loop_sound:
            self.loop_sound.setVolume(hum_final)

    def apply_master_volume_preview(self, value):
        """Apply master volume changes for preview only"""
        logger.debug(f"Preview master volume: {value}")
        self.apply_preview_volumes(master=value)

    def apply_effects_volume_preview(self, value):
        """Apply effects volume changes for preview only"""
        logger.debug(f"Preview effects volume: {value}")
        self.apply_preview_volumes(effects=value)

    def apply_hum_volume_preview(self, value):
        """Apply hum volume changes for preview only"""
        logger.debug(f"Preview hum volume: {value}")
        self.apply_preview_volumes(hum=value)

    def on_app_about_to_quit(self):
        """Handle application about to quit event - wait for sound to finish"""
        try:
            # Prevent double execution
            if self.exit_sound_played:
                logger.debug("Exit sound already played, skipping")
                return

            self.exit_sound_played = True
            logger.debug("Playing exit sound")

            # Stop the loop sound immediately
            try:
                if self.loop_sound and self.loop_sound.isPlaying():
                    logger.debug("Stopping loop sound")
                    self.loop_sound.stop()
            except RuntimeError:
                # Under teardown, Qt may already have deleted multimedia objects.
                pass

            # Play close sound without blocking shutdown.
            self.play_close_sound_on_shutdown()
        except Exception:
            # Never allow exceptions to escape Qt signal handlers.
            logger.exception("Audio shutdown handler failed")

    def play_close_sound_on_shutdown(self):
        """Play the close sound via detached external player only."""
        try:
            if self.settings.value("play_lall", True, type=bool):
                logger.debug("Starting close sound playback")
                close_sound_path = self._resolve_sound_path(SOUND_CLOSE_FILE)
                if not close_sound_path.exists():
                    logger.debug("Close sound file not found: %s", close_sound_path)
                    return
                self._play_close_sound_detached(close_sound_path)
            else:
                logger.debug("Close sound disabled by settings")
        except RuntimeError:
            # Qt object may already be deleted during app teardown.
            logger.debug("Close sound object no longer available during shutdown")

    def _play_close_sound_detached(self, close_sound_path) -> bool:
        """Spawn a detached system player so shutdown sound survives process exit."""
        sound_path = str(close_sound_path)
        candidates = [
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", sound_path],
            ["mpv", "--no-video", "--really-quiet", "--force-window=no", sound_path],
            ["paplay", sound_path],
            ["pw-play", sound_path],
            ["aplay", sound_path],
        ]

        for cmd in candidates:
            if shutil.which(cmd[0]) is None:
                continue
            try:
                subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                logger.debug("Started detached close sound player: %s", cmd[0])
                return True
            except Exception as exc:
                logger.debug("Detached close sound player failed (%s): %s", cmd[0], exc)

        logger.debug("No detached audio player found for close sound")
        return False

    def test_etw_sound(self):
        """Test play the ETW sound"""
        logger.debug("Testing ETW sound")
        if self.open_sound:
            # If already playing, stop and restart from beginning
            if self.open_sound.isPlaying():
                logger.debug("ETW sound already playing, restarting")
                self.open_sound.stop()
            self.open_sound.play()
        else:
            logger.warning("ETW sound not available for testing")

    def test_lall_sound(self):
        """Test play the LALL sound"""
        logger.debug("Testing LALL sound")
        if self.close_sound:
            # If already playing, stop and restart from beginning
            if self.close_sound.isPlaying():
                logger.debug("LALL sound already playing, restarting")
                self.close_sound.stop()
            self.close_sound.play()
        else:
            logger.warning("LALL sound not available for testing")

    def audio_diagnostics(self):
        """Run audio system diagnostics"""
        logger.debug("=== Audio Diagnostics ===")

        logger.debug(
            f"Audio outputs available: {len(QMediaDevices.audioOutputs()) > 0}"
        )
        loop_state = "Playing" if self.loop_sound and self.loop_sound.isPlaying() else "Stopped"
        loop_volume = self.loop_sound.volume() if self.loop_sound else 0.0
        logger.debug(f"Loop player state: {loop_state}")
        logger.debug(f"Loop output volume: {loop_volume:.3f}")

        sounds = [
            ("Open", self.open_sound),
            ("Close", self.close_sound),
            ("Loop", self.loop_sound),
        ]

        for name, sound in sounds:
            logger.debug(f"{name} Sound - Loaded: {'Yes' if sound else 'No'}")
