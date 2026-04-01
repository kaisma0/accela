import logging
import traceback
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from utils.helpers import get_base_path

logger = logging.getLogger(__name__)

from core.steam_api import batched_get_product_info



class ManifestCheckTask(QObject):
    """
    Asynchronous task to check game updates by comparing .depot files
    with current Steam API manifest data without updating the database.
    """

    # Signals
    game_update_checked = pyqtSignal(str, str)  # (appid, update_status)
    progress = pyqtSignal(int, int)  # (current, total)
    batch_progress = pyqtSignal(int, int)  # (current_batch, total_batches)
    completed = pyqtSignal()
    error = pyqtSignal(tuple)  # (Exception, message, traceback)

    def __init__(self, games_list):
        """
        Args:
            games_list: List of game dictionaries to check
        """
        super().__init__()
        self.games_list = games_list
        self._is_running = False

    def run(self):
        """Run the update checks asynchronously using batched API calls"""
        logger.info(f"Starting async update check for {len(self.games_list)} games")
        self._is_running = True

        try:
            total_games = len(self.games_list)
            checked_games = 0

            # Collect all valid appids
            valid_games = []
            for game in self.games_list:
                # Check if task was stopped
                if not self._is_running:
                    logger.debug("Update check task was stopped, exiting")
                    return

                appid = game.get("appid")

                # Skip invalid appids
                if not appid or appid in ("0", "N/A", "unknown"):
                    logger.debug(f"Skipping update check for invalid appid: {appid}")
                    checked_games += 1
                    self.progress.emit(checked_games, total_games)
                    continue

                valid_games.append(game)

            if not valid_games:
                logger.warning("No valid games to check")
                return

            logger.info(f"Valid games to check: {len(valid_games)}")

            # Read tokens from depot files for token-gated apps
            access_tokens = {}
            for game in valid_games:
                appid = game.get("appid")
                _, _, access_token = self._parse_depot_file(appid)
                if access_token:
                    access_tokens[appid] = access_token

            # Use batched API call for all valid games
            appid_list = [game["appid"] for game in valid_games]
            batch_size = 20  
            rate_limit_delay = 0.3  

            # Calculate number of batches for progress reporting
            num_batches = (len(appid_list) + batch_size - 1) // batch_size
            logger.info(f"Will process {len(appid_list)} appids in {num_batches} batches")

            # Fetch all data in batched calls
            if batched_get_product_info is None:
                logger.warning("batched_get_product_info is not available; skipping API fetch and assuming no data.")
                batched_results = {}
            else:
                batched_results = batched_get_product_info(
                    appid_list,
                    access_tokens=access_tokens,
                    batch_size=batch_size,
                    rate_limit_delay=rate_limit_delay,
                    is_cancelled=lambda: not self._is_running,
                    request_timeout=10,
                )

            if not self._is_running:
                logger.debug("Update check task was stopped after batched fetch")
                return

            # Process each game with the batched results
            for game in valid_games:
                # Check if task was stopped
                if not self._is_running:
                    break

                appid = game.get("appid")

                try:
                    # Use batched results to determine update status
                    update_status = self._check_game_update_with_batched_data(game, batched_results)
                    # Emit signal with results
                    self.game_update_checked.emit(appid, update_status)

                except Exception as e:
                    logger.error(f"Error checking update for game {appid}: {e}")
                    self.error.emit((type(e), str(e), traceback.format_exc()))
                    self.game_update_checked.emit(appid, "cannot_determine")

                checked_games += 1
                self.progress.emit(checked_games, total_games)

            logger.info("Async update check complete")

        finally:
            self.completed.emit()

    def _parse_depot_file(self, appid):
        """
        Helper method to parse a depot file and extract its components safely.
        
        Returns:
            tuple: (depot_id, manifest_id, access_token). Values are None if missing or invalid.
        """
        depot_file = Path(get_base_path()) / "depots" / f"{appid}.depot"
        
        if not depot_file.exists():
            return None, None, None

        try:
            content = depot_file.read_text(encoding="utf-8").strip()
            if ":" not in content:
                logger.warning(f"Invalid depot file format for {appid}")
                return None, None, None

            parts = [p.strip() for p in content.split(":", 2)]
            
            depot_id = parts[0] if len(parts) > 0 else None
            manifest_id = parts[1] if len(parts) > 1 else None
            access_token = parts[2] if len(parts) > 2 and parts[2] else None

            if not depot_id or not manifest_id:
                logger.warning(f"Incomplete depot file data for {appid}")
                return None, None, None

            return depot_id, manifest_id, access_token

        except Exception as e:
            logger.debug(f"Failed to parse depot file '{depot_file}' for appid {appid}: {e}")
            return None, None, None

    def _check_game_update_with_batched_data(self, game_data, batched_results):
            """
            Check if a game has an update available using pre-fetched batched data.

            This method uses the results from a batched API call to determine if a game
            has an update, comparing the saved manifest ID with the current public manifest ID.

            Args:
                game_data: Dictionary containing game information
                batched_results: Dict mapping appid -> product_info from batched_get_product_info()

            Returns:
                str: Status constant ('update_available', 'up_to_date', 'cannot_determine')
            """
            appid = game_data.get("appid")

            # Skip if no valid appid
            if not appid or appid in ("0", "N/A", "unknown"):
                return "cannot_determine"

            # Read saved manifest ID
            saved_main_depot_id, saved_manifest_id, _ = self._parse_depot_file(appid)
            
            if not saved_main_depot_id or not saved_manifest_id:
                logger.debug(f"Cannot determine version: Valid depot data not found for app {appid}")
                return "cannot_determine"

            # Get current manifest from batched results
            try:
                steam_client_data = batched_results.get(appid)
                if not steam_client_data:
                    logger.debug(f"App {appid} not found in batched results")
                    return "cannot_determine"

                # Safely grab depots, defaulting to an empty dict if the API returned None
                depots = steam_client_data.get("depots") or {}
                
                # Safely extract the manifest ID
                current_manifest_id = depots.get(saved_main_depot_id, {}).get("manifest_id")

                if current_manifest_id:
                    if saved_manifest_id != current_manifest_id:
                        logger.info(
                            f"Update available for app {appid}: saved={saved_manifest_id}, current={current_manifest_id}"
                        )
                        return "update_available"
                    return "up_to_date"
                
                return "cannot_determine"

            except Exception as e:
                logger.error(f"Error checking for updates for app {appid}: {e}")
                return "cannot_determine"

    def stop(self):
        """Stop the task"""
        logger.debug("Stopping manifest check task")
        self._is_running = False
