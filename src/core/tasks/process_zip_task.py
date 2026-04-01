import zipfile
import re
from pathlib import Path
import logging
import tempfile

from ui.assets import DEPOT_BLACKLIST
from core.steam_api import get_depot_info_from_api
from utils.yaml_config_manager import get_user_config_path, set_map_item
from utils.settings import get_settings

logger = logging.getLogger(__name__)


class ProcessZipTask:
    @staticmethod
    def _parse_lua(content, game_data):
        logger.debug("Starting LUA content parsing...")
        game_data.setdefault("manifest_sizes", {})

        try:
            all_app_matches = list(
                re.finditer(r"addappid\((.*?)\)(.*)", content, re.IGNORECASE)
            )
            if not all_app_matches:
                raise ValueError("LUA file is invalid; no 'addappid' entries found.")

            first_app_match = all_app_matches.pop(0)
            first_app_args = first_app_match.group(1).strip()
            game_data["appid"] = first_app_args.split(",")[0].strip()

            comment_part = first_app_match.group(2)
            game_name_match = re.search(r"--\s*(.*)", comment_part)
            game_data["game_name"] = (
                game_name_match.group(1).strip()
                if game_name_match
                else None
            )

            game_data["depots"] = {}
            game_data["dlcs"] = {}
            for match in all_app_matches:
                args_str = match.group(1).strip()
                args = [arg.strip() for arg in args_str.split(",")]
                app_id = args[0]

                comment_part = match.group(2)
                desc_match = re.search(r"--\s*(.*)", comment_part)
                desc = desc_match.group(1).strip() if desc_match else f"Depot {app_id}"

                if len(args) > 2 and args[2].strip('"'):
                    depot_key = args[2].strip('"')
                    game_data["depots"][app_id] = {"key": depot_key, "desc": desc}
                else:
                    game_data["dlcs"][app_id] = desc

            manifest_size_matches = list(
                re.finditer(
                    r'setManifestid\(\s*(\d+)\s*,\s*".*?"\s*,\s*(\d+)\s*\)',
                    content,
                    re.IGNORECASE,
                )
            )
            for match in manifest_size_matches:
                depot_id = match.group(1).strip()
                size_bytes = match.group(2).strip()
                game_data["manifest_sizes"][depot_id] = size_bytes
                logger.debug(
                    f"Found LUA manifest size for Depot {depot_id}: {size_bytes} bytes"
                )

        except Exception as e:
            logger.error(f"Critical error during LUA parsing: {e}", exc_info=True)
            raise

    @staticmethod
    def _extract_app_token(lua_content: str, app_id: str) -> str | None:
        if not app_id:
            logger.debug("No app_id provided, skipping token extraction")
            return None

        try:
            # Extract token from LUA content
            # Pattern: addtoken(<app_id>, "<token>") with optional whitespace
            token_pattern = r'addtoken\s*\(\s*\d+\s*,\s*"([^"]+)"\s*\)'
            match = re.search(token_pattern, lua_content, re.IGNORECASE)

            if not match:
                logger.debug(f"No addtoken pattern found for AppID {app_id}")
                return None

            app_token = match.group(1)
            logger.info(f"Found token for AppID {app_id}: {app_token[:10]}...")

            # Check if SLSsteam mode is enabled
            settings = get_settings()
            slssteam_mode = settings.value("slssteam_mode", False, type=bool)

            if slssteam_mode:
                config_path = get_user_config_path()

                if not config_path.exists():
                    logger.warning(f"SLSsteam config not found at {config_path}")
                success = set_map_item(config_path, "AppTokens", app_id, app_token)
                if success:
                    logger.info(f"Successfully added token for AppID {app_id} to SLSsteam config")

                return app_token

            # Wrapper mode is OFF - return token for file writing
            return app_token

        except Exception as e:
            logger.error(f"Failed to extract/configure app token: {e}", exc_info=True)
            return None

    def run(self, zip_path):
        logger.info(f"Starting zip processing task for: {zip_path}")

        game_data = {}
        try:
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                lua_files = [f for f in zip_ref.namelist() if f.endswith(".lua")]
                if not lua_files:
                    raise FileNotFoundError("No .lua file found in the zip archive.")

                manifest_files = {
                    Path(f).name: zip_ref.read(f)
                    for f in zip_ref.namelist()
                    if f.endswith(".manifest")
                }
                for depot_id_manifest in manifest_files:
                    parts = depot_id_manifest.replace(".manifest", "").split("_")
                    if len(parts) == 2:
                        game_data.setdefault("manifests", {})[parts[0]] = parts[1]

                lua_content = zip_ref.read(lua_files[0]).decode("utf-8")

                self._parse_lua(lua_content, game_data)

                app_id = game_data.get("appid")

                # Extract app token from LUA content and save to file if wrapper mode is disabled
                token = self._extract_app_token(lua_content, app_id)
                if token:
                    game_data["app_token"] = token

                unfiltered_depots = game_data.get("depots", {})
                if not unfiltered_depots:
                    logger.warning("LUA parsing did not identify any depots with keys.")
                else:
                    logger.info(
                        f"LUA parsing found {len(unfiltered_depots)} depots before filtering."
                    )

                    string_blacklist = {str(item) for item in DEPOT_BLACKLIST}
                    filtered_depots = {
                        depot_id: data
                        for depot_id, data in unfiltered_depots.items()
                        if str(depot_id) not in string_blacklist
                    }
                    if len(unfiltered_depots) > len(filtered_depots):
                        logger.info(
                            f"Removed {len(unfiltered_depots) - len(filtered_depots)} depots based on blacklist."
                        )

                    game_data["depots"] = filtered_depots

                    if not filtered_depots:
                        logger.warning(
                            "All depots were filtered out. No depots to download."
                        )
                    else:
                        api_data = (
                            get_depot_info_from_api(app_id, game_data.get("app_token"))
                            if app_id
                            else {}
                        )

                        for key in ("installdir", "buildid"):
                            if api_data.get(key):
                                game_data[key] = api_data[key]
                                logger.info(f"Found official {key}: {game_data[key]}")

                        if api_data.get("header_url"):
                            game_data["header_url"] = api_data["header_url"]

                        if not game_data.get("game_name") and api_data.get("name"):
                            game_data["game_name"] = api_data["name"]
                            logger.info(f"Resolved game name from Steam API: {game_data['game_name']}")

                        api_details = api_data.get("depots", {})
                        logger.debug(
                            f"Received API details for processing: {api_details}"
                        )

                        if not api_details:
                            logger.warning(
                                "Could not retrieve supplementary details from Steam API."
                            )

                        enriched_depots = {}
                        manifest_sizes = game_data.get("manifest_sizes", {})

                        for depot_id, lua_data in filtered_depots.items():
                            final_depot_data = {"key": lua_data["key"]}
                            details = api_details.get(str(depot_id)) or {}

                            # Use Steam API name if available, otherwise fall back to LUA description
                            base_description = details.get("name") or lua_data["desc"]

                            tags = []
                            if details.get("oslist"):
                                tags.append(f"[{details['oslist'].upper()}]")
                            if details.get("steamdeck"):
                                tags.append("[DECK]")
                            if details.get("language"):
                                base_description += f" ({details['language'].capitalize()})"

                            final_description = (
                                f"{' '.join(tags)} {base_description}".strip()
                                if tags
                                else base_description
                            )

                            if "oslist" in details:
                                final_depot_data["oslist"] = details["oslist"]
                            if "language" in details:
                                final_depot_data["language"] = details["language"]

                            lower_desc = final_description.lower()
                            if "soundtrack" in lower_desc or re.search(
                                r"\bost\b", lower_desc
                            ):
                                logger.info(
                                    f"Filtering out soundtrack depot {depot_id} ('{final_description}')."
                                )
                                continue

                            api_size = details.get("size")
                            lua_size = manifest_sizes.get(depot_id)

                            final_size = api_size or lua_size
                            if final_size:
                                final_depot_data["size"] = final_size
                                source = "API" if api_size else "LUA fallback"
                                logger.debug(f"Using {source} size for depot {depot_id}: {final_size}")
                            else:
                                logger.debug(f"No size found for depot {depot_id} in API or LUA.")

                            final_depot_data["desc"] = final_description
                            enriched_depots[depot_id] = final_depot_data

                        game_data["depots"] = enriched_depots

                if not game_data.get("game_name"):
                    game_data["game_name"] = f"App_{app_id}"
                    logger.warning(f"Could not determine game name from Lua or API. Fallback to {game_data['game_name']}")

                manifest_dir = Path(tempfile.gettempdir()) / "mistwalker_manifests"
                manifest_dir.mkdir(parents=True, exist_ok=True)
                for name, content in manifest_files.items():
                    with (manifest_dir / name).open("wb") as f:
                        f.write(content)

            logger.info("Zip processing task completed successfully.")
            return game_data
        except Exception as e:
            logger.error(f"Zip processing failed: {e}", exc_info=True)
            raise
