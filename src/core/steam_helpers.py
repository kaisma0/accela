import logging
import os
import re
from pathlib import Path
import psutil
import subprocess
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_slssteam_so_path_cache = None
_library_inject_so_path_cache = None


def find_steam_install() -> Optional[str]:
    home_dir = Path.home()
    potential_paths = [
        home_dir / ".steam" / "steam",
        home_dir / ".local" / "share" / "Steam",
        home_dir / ".var" / "app" / "com.valvesoftware.Steam" / "data" / "Steam",
    ]

    for path in potential_paths:
        if (path / "steamapps").is_dir():
            real_path = str(path.resolve())
            logger.info(f"Found Steam installation at: {real_path} (from {path})")
            return real_path

    logger.error("Could not find Steam installation in common Linux directories.")
    return None


def _parse_vdf_libraries(vdf_path: str) -> Dict[int, str]:
    """Helper to safely parse libraryfolders.vdf and extract index-to-path mapping."""
    libraries = {}
    vdf_file = Path(vdf_path)
    if not vdf_file.exists():
        return libraries

    try:
        with vdf_file.open("r", encoding="utf-8") as f:
            content = f.read()

        lines = content.split("\n")
        current_index = None

        for line in lines:
            # Match numeric indices like "0", "1", "2"
            index_match = re.match(r'^\s*"(\d+)"\s*$', line)
            if index_match:
                current_index = int(index_match.group(1))
                continue

            # Match path line
            path_match = re.match(r'^\s*"path"\s*"([^"]+)"', line)
            if path_match and current_index is not None:
                path = path_match.group(1).replace("\\\\", "\\")
                libraries[current_index] = path

    except Exception as e:
        logger.error(f"Failed to parse libraryfolders.vdf: {e}")

    return libraries


def parse_library_folders(vdf_path: str) -> List[str]:
    library_paths = []
    libraries = _parse_vdf_libraries(vdf_path)
    for path in libraries.values():
        if (Path(path) / "steamapps").is_dir():
            library_paths.append(path)
    return library_paths


def get_steam_libraries() -> List[str]:
    steam_path = find_steam_install()
    if not steam_path:
        return []

    all_libraries = {str(Path(steam_path).resolve())}
    vdf_path = str(Path(steam_path) / "steamapps" / "libraryfolders.vdf")

    additional_libraries = parse_library_folders(vdf_path)
    for lib_path in additional_libraries:
        all_libraries.add(str(Path(lib_path).resolve()))

    return list(all_libraries)


def kill_steam_process() -> bool:
    global _slssteam_so_path_cache, _library_inject_so_path_cache
    _slssteam_so_path_cache = None
    _library_inject_so_path_cache = None

    process_name = "steam"
    steam_proc = next(
        (
            p
            for p in psutil.process_iter(["pid", "name"])
            if (p.info.get("name") or "").lower() == process_name
        ),
        None,
    )

    if not steam_proc:
        logger.warning(f"{process_name} process not found.")
        return False

    pid = steam_proc.pid
    maps_file = f"/proc/{pid}/maps"
    try:
        with open(maps_file, "r") as f:
            for line in f:
                if "SLSsteam.so" in line:
                    parts = line.split()
                    if len(parts) > 5 and Path(parts[-1]).exists():
                        _slssteam_so_path_cache = parts[-1]
                        logger.info(
                            f"Found and cached SLSsteam.so path: {_slssteam_so_path_cache}"
                        )
                elif "library-inject.so" in line or "libSLS-library-inject.so" in line:
                    parts = line.split()
                    if len(parts) > 5 and Path(parts[-1]).exists():
                        _library_inject_so_path_cache = parts[-1]
                        logger.info(
                            f"Found and cached library-inject.so path: {_library_inject_so_path_cache}"
                        )
    except Exception as e:
        logger.error(f"Error reading process maps for library paths: {e}")

    try:
        steam_proc.kill()
        steam_proc.wait(timeout=5)
        logger.info(f"Successfully terminated {process_name} (PID: {steam_proc.pid}).")
        return True
    except Exception as e:
        logger.error(f"Failed to terminate {process_name}: {e}")
        return False


def _find_library(
    cached_path: Optional[str], default_paths: List[str], lib_name: str
) -> Optional[str]:
    """Helper to check cache or locate existing default library paths."""
    if cached_path and Path(cached_path).exists():
        return cached_path

    for path in default_paths:
        if Path(path).exists():
            logger.info(f"Found {lib_name} at: {path}")
            return path

    return None


def start_steam() -> str:
    """Attempt to start Steam with SLSsteam integration on Linux.
    Returns: "SUCCESS", "FAILED", or "NEEDS_USER_PATH"
    """
    global _slssteam_so_path_cache, _library_inject_so_path_cache
    logger.info("Attempting to start Steam...")

    try:
        slssteam_path = _find_library(
            _slssteam_so_path_cache,
            [
                "/usr/lib32/libSLSsteam.so",
                str(Path.home() / ".local/share/SLSsteam/SLSsteam.so"),
                str(
                    Path.home()
                    / ".var/app/com.valvesoftware.Steam/.local/share/SLSsteam/SLSsteam.so"
                ),
            ],
            "SLSsteam.so",
        )

        library_inject_path = _find_library(
            _library_inject_so_path_cache,
            [
                "/usr/lib32/libSLS-library-inject.so",
                str(Path.home() / ".local/share/SLSsteam/library-inject.so"),
                str(
                    Path.home()
                    / ".var/app/com.valvesoftware.Steam/.local/share/SLSsteam/library-inject.so"
                ),
            ],
            "library-inject.so",
        )

        if slssteam_path and library_inject_path:
            success = start_steam_with_slssteam(slssteam_path, library_inject_path)
            # Only clear caches if successful
            if success == "SUCCESS":
                _slssteam_so_path_cache = None
                _library_inject_so_path_cache = None
            return success
        else:
            missing = []
            if not slssteam_path:
                missing.append("SLSsteam.so")
            if not library_inject_path:
                missing.append("library-inject.so")
            logger.warning(f"Missing libraries: {', '.join(missing)}")
            return "NEEDS_USER_PATH"

    except Exception as e:
        logger.error(f"Failed to execute Steam: {e}", exc_info=True)
        return "FAILED"


def start_steam_with_slssteam(
    slssteam_path: str = None, library_inject_path: str = None
) -> str:
    """Start Steam on Linux with SLSsteam.so AND library-inject.so via LD_AUDIT
    Returns: "SUCCESS", "FAILED", or "NEEDS_USER_PATH"
    """
    if not slssteam_path or not Path(slssteam_path).exists():
        logger.error(f"SLSsteam.so path is invalid or does not exist: {slssteam_path}")
        return "NEEDS_USER_PATH"

    if not library_inject_path or not Path(library_inject_path).exists():
        logger.error(
            f"library-inject.so path is invalid or does not exist: {library_inject_path}"
        )
        return "NEEDS_USER_PATH"

    try:
        logger.info(
            f"Executing Steam with LD_AUDIT: {library_inject_path}:{slssteam_path}"
        )
        env = os.environ.copy()
        env["LD_AUDIT"] = f"{library_inject_path}:{slssteam_path}"
        subprocess.Popen(["steam"], env=env, start_new_session=True)
        return "SUCCESS"
    except Exception as e:
        logger.error(
            f"Failed to execute steam with provided libraries: {e}", exc_info=True
        )
        return "FAILED"


def get_library_index(library_path: str, steam_path: Optional[str] = None) -> int:
    """Get the library index from libraryfolders.vdf for a given library path.
    If `steam_path` is provided, it will be used instead of calling `find_steam_install()`.
    """
    if not steam_path:
        steam_path = find_steam_install()
    if not steam_path:
        return 0

    vdf_path = str(Path(steam_path) / "steamapps" / "libraryfolders.vdf")
    libraries = _parse_vdf_libraries(vdf_path)

    real_target_path = str(Path(library_path).resolve())
    for idx, path in libraries.items():
        if str(Path(path).resolve()) == real_target_path:
            return idx

    return 0


def slssteam_api_send(command: str) -> bool:
    """Send a command to SLSsteam API via named pipe."""
    pipe_path = "/tmp/SLSsteam.API"

    try:
        with open(pipe_path, "w") as f:
            f.write(command)
            f.flush()
        logger.info(f"SLSsteam API command sent: {command}")
        return True
    except Exception:
        return False
