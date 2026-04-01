import logging
import requests
import json
import os
import tempfile
import re
import time

from utils.image_fetcher import ImageFetcher
from managers.db_manager import DatabaseManager

logger = logging.getLogger(__name__)

try:
    from steam.client import SteamClient
except ImportError:
    SteamClient = None
    logger.warning(
        "`steam[client]` package not found. Skipping steam.client fetch method."
    )


def _parse_depot_entry(depot_id, depot_data):
    """Parse a single depot dict into a normalised depot_info entry."""
    if not isinstance(depot_data, dict):
        return None
    config = depot_data.get("config", {})
    manifests = depot_data.get("manifests", {})
    manifest_public = manifests.get("public", {})

    if isinstance(manifest_public, dict):
        manifest_id = manifest_public.get("gid")
        size_str = manifest_public.get("size")
    else:
        manifest_id = manifest_public
        size_str = None

    logger.debug(
        f"Depot {depot_id}: manifest_id={manifest_id}, raw size={size_str!r}"
    )
    return {
        "name": depot_data.get("name"),
        "oslist": config.get("oslist"),
        "language": config.get("language"),
        "steamdeck": config.get("steamdeck") == "1",
        "size": size_str,
        "manifest_id": manifest_id,
    }


def _parse_steam_client_app_data(int_app_id, app_data):
    """
    Convert a raw steam.client app dict into the normalised structure used
    throughout the application. Returns an empty dict if app_data is falsy.
    """
    if not app_data:
        return {}

    common_data = app_data.get("common", {})
    game_name = common_data.get("name")
    installdir = app_data.get("config", {}).get("installdir")

    header_url = None
    if common_data.get("header_image", {}).get("english"):
        header_url = ImageFetcher.get_header_image_url(int_app_id)
        logger.debug(f"Resolved header image URL for {int_app_id}: {header_url}")

    depots_raw = app_data.get("depots", {})

    buildid = None
    try:
        buildid = (
            depots_raw.get("branches", {})
            .get("public", {})
            .get("buildid")
        )
        if buildid:
            logger.info(f"Found public buildid: {buildid}")
        else:
            logger.warning("Could not find public buildid in steam.client response.")
    except Exception as e:
        logger.error(f"Error parsing buildid: {e}")

    depot_info = {}
    for depot_id, depot_data in depots_raw.items():
        parsed = _parse_depot_entry(depot_id, depot_data)
        if parsed is not None:
            depot_info[depot_id] = parsed

    return {
        "depots": depot_info,
        "installdir": installdir,
        "header_url": header_url,
        "buildid": buildid,
        "name": game_name,
    }


def _manifest_error(depot_id, message):
    return {
        "success": False,
        "manifest_id": None,
        "depot_id": depot_id,
        "error": message,
    }


def get_depot_info_from_api(app_id, access_token=None):
    db = DatabaseManager.get_instance()
    db_data = db.get_app_info(app_id)

    if db_data and db_data.get("depots"):
        name = db_data.get("name", "")
        is_generic = re.match(
            r"^App[ _]?" + str(app_id) + r"$", name, re.IGNORECASE
        )
        if name and not is_generic:
            logger.info(f"Loaded AppID {app_id} from database.")
            return db_data
        logger.info(
            f"Cached data for AppID {app_id} has generic/missing name. "
            "Forcing API refresh."
        )

    logger.info(f"Fetching app info for AppID {app_id} via steam.client...")
    steam_data = _fetch_with_steam_client(app_id, access_token)

    if steam_data and steam_data.get("depots"):
        final_data = steam_data
        if not final_data.get("header_url"):
            final_data["header_url"] = ImageFetcher.get_header_image_url(int(app_id))
    else:
        logger.warning(
            f"steam.client failed for AppID {app_id}. Falling back to Web API."
        )
        final_data = _fetch_with_web_api(app_id)

    if final_data:
        db.upsert_app_info(app_id, final_data)

    return final_data


def _fetch_with_steam_client(app_id, access_token=None):
    if not SteamClient:
        return {}

    client = SteamClient()
    try:
        logger.debug("Attempting anonymous Steam login.")
        client.anonymous_login()
        if not client.logged_on:
            logger.error("Failed to log in anonymously to Steam.")
            return {}

        try:
            int_app_id = int(app_id)
        except (ValueError, TypeError):
            logger.error(f"Invalid AppID format: {app_id!r}")
            return {}

        if access_token:
            try:
                request_list = [{"appid": int_app_id, "access_token": int(access_token)}]
            except (ValueError, TypeError):
                request_list = [{"appid": int_app_id, "access_token": access_token}]
            logger.debug(f"Using access token for AppID {app_id}.")
        else:
            request_list = [int_app_id]

        result = client.get_product_info(apps=request_list, timeout=30)

        if logger.isEnabledFor(logging.DEBUG):
            dump_path = os.path.join(
                tempfile.gettempdir(),
                f"mistwalker_steamclient_response_{int_app_id}.json",
            )
            try:
                with open(dump_path, "w", encoding="utf-8") as f:
                    json.dump(result, f, indent=4, default=str)
                logger.debug(f"Raw steam.client response dumped to {dump_path}")
            except Exception as e:
                logger.error(f"Failed to dump raw response: {e}", exc_info=True)

        try:
            cleaned = json.loads(json.dumps(result, default=str))
        except Exception as e:
            logger.error(f"Failed to serialise steam.client response: {e}")
            return {}

        app_data = cleaned.get("apps", {}).get(str(int_app_id), {})
        parsed = _parse_steam_client_app_data(int_app_id, app_data)

        if parsed and any(parsed.get(k) for k in ("depots", "buildid", "name")):
            logger.info("steam.client fetch successful.")
            return parsed

        logger.warning("steam.client fetch returned no meaningful data.")
        return {}

    except Exception as e:
        logger.error(
            f"Unexpected error in _fetch_with_steam_client: {e}", exc_info=True
        )
        return {}
    finally:
        if client and client.logged_on:
            logger.debug("Logging out in finally block.")
            client.logout()


def _fetch_with_web_api(app_id):
    url = "https://store.steampowered.com/api/appdetails"
    try:
        response = requests.get(url, params={"appids": app_id}, timeout=15)
        response.raise_for_status()
        return _parse_web_api_response(app_id, response.json())
    except requests.exceptions.RequestException as e:
        logger.error(f"Web API request failed for AppID {app_id}: {e}")
    return {}


def _parse_web_api_response(app_id, data):
    app_data_wrapper = data.get(str(app_id))
    if not (app_data_wrapper and app_data_wrapper.get("success")):
        return {
            "depots": {},
            "installdir": None,
            "header_url": None,
            "name": None
         }

    app_data = app_data_wrapper.get("data", {})
    depot_info = {}
    for depot_id, depot_data in app_data.get("depots", {}).items():
        if not isinstance(depot_data, dict):
            continue
        size_str = depot_data.get("max_size")
        logger.debug(f"Depot {depot_id} (Web API): raw size={size_str!r}")
        depot_info[depot_id] = {
            "name": depot_data.get("name"),
            "oslist": None,
            "language": None,
            "steamdeck": False,
            "size": size_str,
        }

    return {
        "depots": depot_info,
        "installdir": app_data.get("install_dir"),
        "header_url": app_data.get("header_image"),
        "name": app_data.get("name"),
    }


def batched_get_product_info(
    appid_list,
    access_tokens=None,
    batch_size=20,
    rate_limit_delay=0.3,
    is_cancelled=None,
    request_timeout=10,
):
    if not SteamClient:
        logger.warning("SteamClient not available, cannot perform batched fetch.")
        return {}
    if not appid_list:
        logger.warning("Empty appid_list provided to batched_get_product_info.")
        return {}
    if access_tokens is None:
        access_tokens = {}

    batches = [
        appid_list[i : i + batch_size]
        for i in range(0, len(appid_list), batch_size)
    ]
    logger.info(
        f"Batched fetch: {len(appid_list)} appids → {len(batches)} batches "
        f"(size={batch_size})"
    )

    all_results = {}
    failed_appids = []
    client = None  # single client for all batches

    try:
        client = SteamClient()
        client.anonymous_login()

        if not client.logged_on:
            logger.error("Failed to log in to Steam for batched fetch.")
            return {}

        for batch_idx, batch_appids in enumerate(batches):
            if is_cancelled and is_cancelled():
                logger.info("Batched fetch cancelled.")
                break

            try:
                int_appids = []
                for appid in batch_appids:
                    try:
                        int_appids.append(int(appid))
                    except (ValueError, TypeError):
                        logger.error(f"Invalid AppID skipped: {appid!r}")
                        failed_appids.append(appid)

                if not int_appids:
                    continue

                request_list = []
                for appid in int_appids:
                    token = access_tokens.get(str(appid))
                    if token:
                        try:
                            request_list.append({"appid": appid, "access_token": int(token)})
                        except (ValueError, TypeError):
                            request_list.append(appid)
                    else:
                        request_list.append(appid)

                result = client.get_product_info(
                    apps=request_list, timeout=request_timeout
                )

                if result and isinstance(result, dict):
                    cleaned = json.loads(json.dumps(result, default=str))
                    apps_data = cleaned.get("apps", {})
                    for int_appid in int_appids:
                        appid_str = str(int_appid)
                        app_data = apps_data.get(appid_str, {})
                        parsed = _parse_steam_client_app_data(int_appid, app_data)
                        if not parsed:
                            parsed = {
                                "depots": {},
                                "installdir": None,
                                "header_url": None,
                                "buildid": None,
                                "name": None,
                            }
                        all_results[appid_str] = parsed
                else:
                    failed_appids.extend(batch_appids)

            except Exception as e:
                logger.error(
                    f"Batch {batch_idx + 1}: unexpected error: {e}", exc_info=True
                )
                failed_appids.extend(batch_appids)

            if batch_idx < len(batches) - 1 and rate_limit_delay > 0:
                time.sleep(rate_limit_delay)

    except Exception as e:
        logger.error(f"Fatal error during batched fetch setup: {e}", exc_info=True)
    finally:
        if client and client.logged_on:
            try:
                client.logout()
            except Exception as exc:
                logger.debug("Steam client logout failed: %s", exc)

    logger.info(
        f"Batched fetch complete: {len(all_results)} succeeded, "
        f"{len(failed_appids)} failed."
    )
    if failed_appids:
        logger.debug(f"Failed appids: {failed_appids}")

    return all_results


def get_manifest_id(appid, depot_id=None, use_cache=True):
    try:
        if not use_cache:
            DatabaseManager.get_instance().clear_app_info(appid)

        app_data = get_depot_info_from_api(appid)
        if not app_data:
            return _manifest_error(depot_id, "Failed to fetch app data")

        depots = app_data.get("depots", {})
        if not depots:
            return _manifest_error(depot_id, "No depots found for this app")

        if depot_id:
            if str(depot_id) not in depots:
                return _manifest_error(depot_id, f"Depot {depot_id} not found")
            target_depot_id = str(depot_id)
        else:
            target_depot_id = next(iter(depots))

        manifest_id = depots[target_depot_id].get("manifest_id")

        if not manifest_id:
            if use_cache:
                logger.debug(
                    f"Manifest ID missing for {appid} in cache; retrying with refresh."
                )
                return get_manifest_id(appid, depot_id, use_cache=False)
            return _manifest_error(target_depot_id, "No manifest ID found")

        return {
            "success": True,
            "manifest_id": manifest_id,
            "depot_id": target_depot_id,
            "error": None,
        }

    except Exception as e:
        logger.error(f"Error fetching manifest for {appid}: {e}")
        return _manifest_error(depot_id, f"Unexpected error: {e}")
