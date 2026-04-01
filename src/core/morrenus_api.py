import logging
import requests
from pathlib import Path
from utils.settings import get_settings
from utils.helpers import get_base_path

logger = logging.getLogger(__name__)

BASE_URL = "https://manifest.morrenus.xyz/api/v1"

# Create a session with default secure SSL verification.
_session = requests.Session()

# Error messages for specific HTTP status codes
API_ERROR_MESSAGES = {
    401: "Invalid or missing API key. Please check your credentials in Settings.",
    403: "Access denied. Your account may be blocked or the App ID is not accessible.",
    404: "Game not found in library. The App ID may be incorrect or not available.",
    429: "Daily API limit exceeded. Please try again later.",
    500: "Server error. The manifest may be corrupted or temporarily unavailable.",
}


def _handle_api_error(response):
    """
    Handles API errors for specific status codes and returns an error message.
    Returns None if error is handled, or the original exception for generic handling.
    """
    status_code = response.status_code

    if status_code in API_ERROR_MESSAGES:
        error_msg = API_ERROR_MESSAGES[status_code]
        logger.error(f"API error ({status_code}): {error_msg}")
        return error_msg

    return None


def _get_headers():
    """
    Retrieves the Morrenus API key from settings and constructs auth headers.
    """
    settings = get_settings()
    api_key = settings.value("morrenus_api_key", "", type=str)
    if not api_key:
        logger.warning("Morrenus API key is not set in settings.")
        return None
    return {"Authorization": f"Bearer {api_key}"}


def _handle_request_exception(e, action="API request"):
    """
    Centralized exception handling for requests.
    """
    if isinstance(e, requests.exceptions.HTTPError):
        response = e.response
        response_text = response.text if response is not None else ""
        status_code = response.status_code if response is not None else "N/A"
        logger.error(f"{action} HTTP error: {e} - {response_text}")

        if response is None:
            return f"API Error ({status_code}): {e}"

        try:
            error_detail = response.json().get("detail", response_text)
            return f"API Error ({status_code}): {error_detail}"
        except ValueError:  # Handles JSONDecodeError safely across requests versions
            return f"API Error ({status_code}): {response_text}"

    elif isinstance(e, requests.exceptions.RequestException):
        logger.error(f"{action} failed: {e}")
        error_str = str(e).lower()
        if "ssl" in error_str or "wrong_version_number" in error_str:
            return "SSL connection failed. This may be caused by a proxy, firewall, or network configuration blocking HTTPS connections."
        return f"Request Failed: {e}"

    else:
        logger.error(f"An unexpected error occurred during {action.lower()}: {e}", exc_info=True)
        return f"An unexpected error occurred: {e}"


def search_games(query):
    """
    Searches for games on the Morrenus API.
    """
    headers = _get_headers()
    if headers is None:
        return {"error": "API Key is not set. Please set it in Settings."}

    params = {"q": query, "limit": 50}
    url = f"{BASE_URL}/search"
    logger.info(f"Searching Morrenus API: {url} with query: {query}")

    try:
        response = _session.get(url, headers=headers, params=params, timeout=10)
        error_msg = _handle_api_error(response)
        if error_msg:
            return {"error": error_msg}
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {"error": _handle_request_exception(e, "API search")}


def download_manifest(app_id):
    """
    Downloads a manifest zip for a given app_id to a persistent folder.
    Returns (filepath, None) on success, or (None, error_message) on failure.
    """
    headers = _get_headers()
    if headers is None:
        return (None, "API Key is not set. Please set it in Settings.")

    url = f"{BASE_URL}/manifest/{app_id}"
    manifests_dir = Path(get_base_path()) / "morrenus_manifests"
    manifests_dir.mkdir(parents=True, exist_ok=True)
    save_path = manifests_dir / f"accela_fetch_{app_id}.zip"

    logger.info(f"Attempting to download manifest for AppID {app_id} to {save_path}")

    try:
        with _session.get(url, headers=headers, stream=True, timeout=60) as r:
            error_msg = _handle_api_error(r)
            if error_msg:
                return (None, error_msg)

            r.raise_for_status()
            with open(save_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)

        logger.info(f"Manifest for {app_id} downloaded successfully to {save_path}")
        return (str(save_path), None)
    except Exception as e:
        if save_path.exists():
            save_path.unlink()
        return (None, _handle_request_exception(e, "API download"))


def get_user_stats():
    """
    Retrieves user statistics from the Morrenus API.
    Returns dict with user info or {"error": message} on failure.
    """
    settings = get_settings()
    api_key = settings.value("morrenus_api_key", "", type=str)
    if not api_key:
        return {"error": "API key is not set. Please set it in Settings."}

    url = f"{BASE_URL}/user/stats"
    params = {"api_key": api_key}
    logger.info("Fetching user stats from Morrenus API")

    try:
        response = _session.get(url, params=params, timeout=10)
        error_msg = _handle_api_error(response)
        if error_msg:
            return {"error": error_msg}
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {"error": _handle_request_exception(e, "API stats request")}


def validate_api_key(api_key=None):
    """
    Validates a Morrenus API key against the user stats endpoint.
    Returns (True, None) when valid, otherwise (False, error_message).
    """
    if api_key is None:
        settings = get_settings()
        api_key = settings.value("morrenus_api_key", "", type=str)

    key = (api_key or "").strip()
    if not key:
        return (False, "API key is empty.")

    url = f"{BASE_URL}/user/stats"
    params = {"api_key": key}

    try:
        response = _session.get(url, params=params, timeout=10)
        error_msg = _handle_api_error(response)
        if error_msg:
            return (False, error_msg)

        response.raise_for_status()
        return (True, None)
    except Exception as e:
        return (False, _handle_request_exception(e, "API key validation"))


def check_health():
    """
    Checks if the Morrenus API is healthy.
    Returns dict with health info or {"error": message} on failure.
    """
    url = f"{BASE_URL}/health"
    logger.info("Checking Morrenus API health")

    try:
        response = _session.get(url, timeout=5)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        error_msg = _handle_request_exception(e, "API health check")
        status = "unhealthy" if isinstance(e, requests.exceptions.RequestException) else "unknown"
        return {"status": status, "error": error_msg}
