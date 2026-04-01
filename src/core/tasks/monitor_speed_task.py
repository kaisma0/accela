import logging
import time
import psutil
from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)


class SpeedMonitorTask(QObject):
    speed_update = pyqtSignal(str)

    def __init__(self, interval: float = 1.0):
        super().__init__()
        # Prevent ZeroDivisionError and CPU thrashing if interval is 0 or negative
        self.interval = max(0.1, interval) 
        self._is_running = True

    def run(self):
        logger.info("Speed monitor task starting.")
        try:
            last_bytes = psutil.net_io_counters().bytes_recv
        except Exception as e:
            logger.error(f"Could not initialize psutil for speed monitoring: {e}")
            return

        while self._is_running:
            time.sleep(self.interval)
            if not self._is_running:
                break
            try:
                current_bytes = psutil.net_io_counters().bytes_recv
                speed = (current_bytes - last_bytes) / self.interval
                last_bytes = current_bytes
                self.speed_update.emit(f"Download Speed: {self._format_speed(speed)}")
            except Exception as e:
                logger.warning(f"Error during speed update loop: {e}")
                self.stop()

        logger.info("Speed monitor task finished.")

    @staticmethod
    def _format_speed(speed_bps):
        if speed_bps < 1024:
            return f"{speed_bps:.2f} B/s"
        if speed_bps < 1024**2:
            return f"{(speed_bps / 1024):.2f} KB/s"
        if speed_bps < 1024**3:
            return f"{(speed_bps / 1024**2):.2f} MB/s"
        return f"{(speed_bps / 1024**3):.2f} GB/s"

    def stop(self):
        logger.debug("Stop signal received by speed monitor.")
        self._is_running = False