import io
import logging
import os
import shutil
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from utils.settings import get_settings

logger = logging.getLogger(__name__)

BACKUP_SUFFIX = ".bak"


# ──────────────────────────────────────────────────────────────────────────────
# Settings helpers
# ──────────────────────────────────────────────────────────────────────────────


def is_slssteam_mode_enabled() -> bool:
    """
    Check if SLSsteam mode is enabled in settings.

    Returns:
        True if SLSsteam mode is enabled, False otherwise.
    """
    settings = get_settings()
    return settings.value("slssteam_mode", False, type=bool)


def is_slssteam_config_management_enabled() -> bool:
    """
    Check if SLSsteam config management is enabled in settings.

    Returns:
        True if config management is enabled, False otherwise.
    """
    settings = get_settings()
    return settings.value("sls_config_management", True, type=bool)


def _check_guards(fn_name: str) -> bool:
    """Return True only when both SLSsteam mode and config management are enabled."""
    if not is_slssteam_mode_enabled():
        logger.debug(f"SLSsteam mode is disabled, skipping {fn_name}")
        return False
    if not is_slssteam_config_management_enabled():
        logger.debug(f"SLSsteam config management is disabled, skipping {fn_name}")
        return False
    return True


# ──────────────────────────────────────────────────────────────────────────────
# Backup & atomic write
# ──────────────────────────────────────────────────────────────────────────────


def _create_backup(config_path: Path, force: bool = False) -> bool:
    """
    Create a backup of the config file.

    Creates config.yaml.bak with the current config content.
    Only creates a backup if the source file exists.

    When *force* is False (the default), an existing backup is kept if the live
    file is smaller — this guards against overwriting a good backup with a
    partially-written or corrupted live file.

    When *force* is True the existing backup is always overwritten, which is the
    correct behaviour on startup (we want a clean pre-session snapshot).

    Args:
        config_path: Path to the config file to backup.
        force:       When True, always overwrite an existing backup.

    Returns:
        True if a backup exists or was created, False otherwise.
    """
    try:
        if not config_path.exists():
            return False

        backup_path = config_path.with_name(config_path.name + BACKUP_SUFFIX)

        if not force and backup_path.exists():
            new_size = config_path.stat().st_size
            backup_size = backup_path.stat().st_size
            if new_size < backup_size:
                logger.debug(
                    f"Skipping backup: current file ({new_size} B) is smaller than "
                    f"existing backup ({backup_size} B) — keeping backup"
                )
                return True  # Keep existing backup, still a success

        shutil.copy2(config_path, backup_path)
        logger.info(f"Created backup: {backup_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to create backup for {config_path}: {e}", exc_info=True)
        return False


def backup_config_on_startup(config_path: Path) -> bool:
    """
    Create a backup of config.yaml at application startup.

    Should be called once, before any modifications are made.
    Always overwrites any existing backup to produce a clean pre-session snapshot.

    Args:
        config_path: Path to the config.yaml file.

    Returns:
        True if a backup exists or was created, False if the config file is missing.
    """
    return _create_backup(config_path, force=True)


def _atomic_write(config_path: Path, content: str) -> bool:
    """
    Atomically write *content* to *config_path*.

    Writes to a .tmp sibling first, then renames via os.replace(), so the
    original file is never left in a partial state if a write is interrupted.

    Args:
        config_path: Destination path.
        content:     Text content to write (UTF-8).

    Returns:
        True on success, False on any error.
    """
    temp_path = config_path.with_name(config_path.name + ".tmp")
    try:
        temp_path.write_text(content, encoding="utf-8")
        temp_path.replace(config_path)
        return True
    except OSError as e:
        logger.error(f"Failed to atomically write {config_path}: {e}", exc_info=True)
        try:
            temp_path.unlink(missing_ok=True)
        except OSError as cleanup_err:
            logger.debug(f"Could not remove temp file {temp_path}: {cleanup_err}")
        return False


# ──────────────────────────────────────────────────────────────────────────────
# ruamel.yaml core helpers
# ──────────────────────────────────────────────────────────────────────────────


def _make_yaml() -> YAML:
    """
    Build a YAML instance configured for lossless round-trip editing.

    Key settings
    ────────────
    - typ='rt' (default)   Round-trip mode: comments, ordering, anchors/aliases
                           are all preserved across load → dump cycles.
    - preserve_quotes      Keeps the quoting style of existing string scalars.
    - width=4096           Suppresses line-wrapping on long values.
    - indent(...)          ``  - item`` style for block sequences
                           (dash at column 2, value at column 4).
    - bool representer     Python booleans serialise as ``yes``/``no``
                           (SLSsteam convention) rather than YAML 1.2 ``true``/``false``.
    """
    yaml = YAML()  # typ='rt' by default
    yaml.preserve_quotes = True
    yaml.width = 4096

    # sequence=4, offset=2 produces:
    #   key:
    #     - item       ← dash at col 2 from parent, value at col 4
    yaml.indent(mapping=2, sequence=4, offset=2)

    # Override the default bool representation to match SLSsteam config style.
    def _bool_representer(dumper, data: bool):
        return dumper.represent_scalar(
            "tag:yaml.org,2002:bool", "yes" if data else "no"
        )

    yaml.representer.add_representer(bool, _bool_representer)
    return yaml


def _load_yaml(config_path: Path) -> tuple[YAML, CommentedMap]:
    """
    Parse *config_path* with a round-trip YAML loader.

    Returns an empty ``CommentedMap`` when the file is absent or contains only
    comments / whitespace, so callers can operate on the result unconditionally
    (create-if-missing semantics).

    Args:
        config_path: Path to the YAML file (need not exist).

    Returns:
        Tuple of (configured YAML instance, parsed CommentedMap).
    """
    yaml = _make_yaml()
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.load(f)
        if data is None:
            data = CommentedMap()
    else:
        data = CommentedMap()
    return yaml, data


def _save_yaml(yaml: YAML, data: CommentedMap, config_path: Path) -> bool:
    """
    Serialise *data* back to *config_path* atomically.

    Args:
        yaml:        The YAML instance that originally loaded the data
                     (carries any comment / ordering state).
        data:        The (possibly mutated) document root.
        config_path: Destination path.

    Returns:
        True on success, False on any error.
    """
    buf = io.StringIO()
    yaml.dump(data, buf)
    return _atomic_write(config_path, buf.getvalue())


# ──────────────────────────────────────────────────────────────────────────────
# Path helper
# ──────────────────────────────────────────────────────────────────────────────


def get_user_config_path() -> Path:
    """
    Get the path to the user's SLSsteam config.yaml file.

    Respects ``$XDG_CONFIG_HOME`` when it is set and points to an absolute
    path; otherwise falls back to ``~/.config/SLSsteam/config.yaml``.

    Returns:
        Path to the config.yaml file.
    """
    xdg_str = os.environ.get("XDG_CONFIG_HOME", "")
    if xdg_str:
        xdg = Path(xdg_str).expanduser()
        if xdg.is_absolute():
            return xdg / "SLSsteam" / "config.yaml"
    return Path.home() / ".config" / "SLSsteam" / "config.yaml"


# ──────────────────────────────────────────────────────────────────────────────
# Generic scalar updaters
# ──────────────────────────────────────────────────────────────────────────────


def update_yaml_scalar_value(config_path: Path, key: str, value: Any) -> bool:
    """
    Set any top-level scalar key (bool, int, float, or str).

    Booleans are written as ``yes``/``no``; strings are written with their
    existing quoting style preserved when possible.

    Args:
        config_path: Path to the YAML config file.
        key:         Top-level key to update (e.g. ``'LogLevel'``).
        value:       New scalar value.

    Returns:
        True if the file was modified, False if already correct, not found,
        or an error occurred.
    """
    try:
        if not config_path.exists():
            logger.warning(f"Config file not found: {config_path}")
            return False

        yaml, data = _load_yaml(config_path)

        if key not in data:
            logger.warning(f"Key '{key}' not found in {config_path}")
            return False

        current = data[key]
        already_set = (
            bool(current) == value if isinstance(value, bool) else current == value
        )
        if already_set:
            logger.debug(f"'{key}' is already {value!r}")
            return False

        data[key] = value
        if not _save_yaml(yaml, data, config_path):
            return False

        logger.info(f"Updated '{key}' → {value!r} in {config_path}")
        return True

    except Exception as e:
        logger.error(f"Failed to update '{key}' in {config_path}: {e}", exc_info=True)
        return False


def update_yaml_nested_scalar_value(
    config_path: Path, section: str, key: str, value: Any
) -> bool:
    """
    Set a scalar key nested one level inside a named YAML section.

    Target shape::

        IdleStatus:       # section
          AppId: 0        # key: value

    Args:
        config_path: Path to the YAML config file.
        section:     Parent section name (e.g. ``'IdleStatus'``).
        key:         Child key inside the section (e.g. ``'AppId'``).
        value:       New scalar value.

    Returns:
        True if the file was modified, False if already correct, not found,
        or an error occurred.
    """
    try:
        if not config_path.exists():
            logger.warning(f"Config file not found: {config_path}")
            return False

        yaml, data = _load_yaml(config_path)

        if section not in data or data[section] is None:
            logger.warning(f"Section '{section}' not found in {config_path}")
            return False

        section_data = data[section]

        if key not in section_data:
            logger.warning(
                f"Key '{key}' not found under section '{section}' in {config_path}"
            )
            return False

        current = section_data[key]
        already_set = (
            bool(current) == value if isinstance(value, bool) else current == value
        )
        if already_set:
            logger.debug(f"'{section}.{key}' is already {value!r}")
            return False

        section_data[key] = value
        if not _save_yaml(yaml, data, config_path):
            return False

        logger.info(f"Updated '{section}.{key}' → {value!r} in {config_path}")
        return True

    except Exception as e:
        logger.error(
            f"Failed to update '{section}.{key}' in {config_path}: {e}", exc_info=True
        )
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Shared list-section helpers
# ──────────────────────────────────────────────────────────────────────────────


def get_list_items(config_path: Path, section_name: str) -> set[str]:
    """
    Return every numeric item in a YAML block-sequence section as a set of strings.

    Args:
        config_path:  Path to the YAML config file.
        section_name: Top-level key whose value is a sequence (e.g. ``'AppIds'``).

    Returns:
        Set of app ID strings, empty if the file/section is missing or on error.
    """
    if not config_path.exists():
        return set()
    try:
        _, data = _load_yaml(config_path)
        section = data.get(section_name)
        if not section:
            return set()
        return {str(item) for item in section}
    except Exception as e:
        logger.error(
            f"Failed to read '{section_name}' from {config_path}: {e}", exc_info=True
        )
        return set()


def add_list_item(
    config_path: Path,
    section_name: str,
    item: str,
    comment: str = "",
) -> bool:
    """
    Append *item* to the named YAML block-sequence section.

    Creates the parent directories, the file, and/or the section itself if any
    are absent.
    Only acts when SLSsteam mode and config management are both enabled.

    Args:
        config_path:  Path to the YAML config file.
        section_name: Top-level sequence key (e.g. ``'AdditionalApps'``).
        item:         Numeric app ID to append (as a string).
        comment:      Optional inline comment to attach to the new entry.

    Returns:
        True if the entry was inserted, False if it already existed.
    """
    if not _check_guards(f"add_list_item ({section_name})"):
        return False

    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        yaml, data = _load_yaml(config_path)

        # Section may be absent from the mapping or explicitly set to null.
        if data.get(section_name) is None:
            seq: CommentedSeq = CommentedSeq()
            seq.fa.set_block_style()
            data[section_name] = seq

        section: CommentedSeq = data[section_name]

        if any(str(x) == item for x in section):
            logger.debug(f"Item '{item}' already present in '{section_name}'")
            return False

        idx = len(section)
        try:
            val = int(item)
        except ValueError:
            val = item
        section.append(val)
        if comment:
            section.yaml_add_eol_comment(comment, idx)

        if not _save_yaml(yaml, data, config_path):
            return False

        logger.info(f"Added item '{item}' to '{section_name}' in {config_path}")
        return True

    except Exception as e:
        logger.error(
            f"Failed to add item '{item}' to section '{section_name}' in {config_path}: {e}",
            exc_info=True,
        )
        return False


def remove_list_item(config_path: Path, section_name: str, item: str) -> bool:
    """
    Remove *item* from the named YAML block-sequence section.

    Only acts when SLSsteam mode and config management are both enabled.

    Args:
        config_path:  Path to the YAML config file.
        section_name: Top-level sequence key.
        item:         Numeric app ID to remove (as a string).

    Returns:
        True if the entry was removed, False if not found, file absent, or error.
    """
    if not _check_guards(f"remove_list_item ({section_name})"):
        return False

    if not config_path.exists():
        logger.debug(f"Config file does not exist: {config_path}")
        return False
    try:
        yaml, data = _load_yaml(config_path)
        section = data.get(section_name)

        if not section:
            logger.debug(f"'{section_name}' is empty; '{item}' not found")
            return False

        target_idx = next((i for i, x in enumerate(section) if str(x) == item), None)
        if target_idx is None:
            logger.debug(f"Item '{item}' not found in '{section_name}'")
            return False

        del section[target_idx]

        if not _save_yaml(yaml, data, config_path):
            return False

        logger.info(f"Removed item '{item}' from '{section_name}' in {config_path}")
        return True

    except Exception as e:
        logger.error(
            f"Failed to remove '{item}' from '{section_name}' in {config_path}: {e}",
            exc_info=True,
        )
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Shared map-section helpers
# ──────────────────────────────────────────────────────────────────────────────


def _find_map_key(section: CommentedMap, target_key: Any) -> Any | None:
    """
    Return the actual key object (int or str) in *section* that matches *target_key*.

    ruamel.yaml may deserialise numeric keys as either ``int`` or ``str``
    depending on quoting in the source file. This helper normalises the lookup
    so callers don't need to care about the stored type.

    Args:
        section:    The CommentedMap to search.
        target_key: The numeric key to find (can be int or str).

    Returns:
        The matching key object, or None if not found.
    """
    target = str(target_key)
    return next((k for k in section if str(k) == target), None)


def get_map_items(config_path: Path, section_name: str) -> dict[str, Any]:
    """
    Return all entries from a YAML mapping section as a flat dictionary.
    Keys are always returned as strings.
    """
    if not config_path.exists():
        return {}
    try:
        _, data = _load_yaml(config_path)
        section = data.get(section_name)
        if not section:
            return {}
        return {str(k): v for k, v in section.items()}
    except Exception as e:
        logger.error(
            f"Failed to read '{section_name}' from {config_path}: {e}", exc_info=True
        )
        return {}


def set_map_item(
    config_path: Path,
    section_name: str,
    key: str,
    value: Any,
    comment: str = "",
) -> bool:
    """
    Add or update *key* with *value* in the named YAML mapping section.

    Only acts when SLSsteam mode and config management are both enabled.

    Handles ruamel.yaml type matching (e.g. avoiding duping '123' and 123)
    automatically via `_find_map_key`. Creates section if missing.
    """
    if not _check_guards(f"set_map_item ({section_name})"):
        return False

    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        yaml, data = _load_yaml(config_path)

        if data.get(section_name) is None:
            data[section_name] = CommentedMap()

        section: CommentedMap = data[section_name]
        found_key = _find_map_key(section, key)

        if found_key is not None:
            if str(section[found_key]) == str(value):
                logger.debug(
                    f"Key '{key}' in '{section_name}' already set to matching value"
                )
                return False
            # Overwrite the existing matching key to preserve its loaded type
            section[found_key] = value
        else:
            # New keys are usually int for IDs if possible
            try:
                insert_key = int(key)
            except ValueError:
                insert_key = key

            section[insert_key] = value
            if comment:
                section.yaml_add_eol_comment(comment, insert_key)

        if not _save_yaml(yaml, data, config_path):
            return False

        logger.info(f"Set '{key}': {value!r} in '{section_name}' in {config_path}")
        return True

    except Exception as e:
        logger.error(
            f"Failed to set '{key}' in '{section_name}' in {config_path}: {e}",
            exc_info=True,
        )
        return False


def remove_map_item(
    config_path: Path, section_name: str, key: str, expected_value: str = ""
) -> bool:
    """
    Remove *key* from the named YAML mapping section.
    If expected_value is provided, only removes if the stringified value matches.

    Only acts when SLSsteam mode and config management are both enabled.
    """
    if not _check_guards(f"remove_map_item ({section_name})"):
        return False

    if not config_path.exists():
        logger.debug(f"Config file does not exist: {config_path}")
        return False

    try:
        yaml, data = _load_yaml(config_path)
        section = data.get(section_name)

        if not section:
            logger.debug(f"'{section_name}' is empty; '{key}' not found")
            return False

        found_key = _find_map_key(section, key)

        if found_key is None:
            logger.debug(f"Key '{key}' not found in '{section_name}'")
            return False

        if expected_value and str(section[found_key]) != expected_value:
            logger.debug(
                f"Key '{key}' in '{section_name}' has value '{section[found_key]}', "
                f"expected '{expected_value}', skipping removal"
            )
            return False

        del section[found_key]

        if not _save_yaml(yaml, data, config_path):
            return False

        logger.info(f"Removed '{key}' from '{section_name}' in {config_path}")
        return True

    except Exception as e:
        logger.error(
            f"Failed to remove '{key}' from '{section_name}' in {config_path}: {e}",
            exc_info=True,
        )
        return False


# ──────────────────────────────────────────────────────────────────────────────
# DlcData
# ──────────────────────────────────────────────────────────────────────────────


def get_dlc_data(config_path: Path) -> dict[str, dict[str, str]]:
    """
    Return all entries from DlcData section as ``{parent_app_id: {dlc_id: dlc_name}}``.
    """
    if not config_path.exists():
        return {}
    try:
        _, data = _load_yaml(config_path)
        section = data.get("DlcData")
        if not section:
            return {}
        result = {}
        for p_key, p_val in section.items():
            if isinstance(p_val, dict):
                result[str(p_key)] = {str(k): str(v) for k, v in p_val.items()}
        return result
    except Exception as e:
        logger.error(f"Failed to read DlcData from {config_path}: {e}", exc_info=True)
        return {}


def add_dlc_data(
    config_path: Path, parent_app_id: str, dlc_id: str, dlc_name: str
) -> bool:
    """
    Add a DLC entry under its parent game in the DlcData section.

    Target shape::

        DlcData:
          <parent_app_id>:
            <dlc_id>: "DLC Name"

    Creates the parent directories, the file, the DlcData section, and/or the
    parent node if any are absent.
    Only acts when SLSsteam mode and config management are both enabled.

    Args:
        config_path:   Path to the YAML config file.
        parent_app_id: Parent game AppID.
        dlc_id:        DLC AppID.
        dlc_name:      Human-readable DLC name.

    Returns:
        True if the DLC entry was added, False if it already existed, guards
        are off, or an error occurred.
    """
    if not _check_guards("add_dlc_data"):
        return False

    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        yaml, data = _load_yaml(config_path)

        if data.get("DlcData") is None:
            data["DlcData"] = CommentedMap()

        dlc_data: CommentedMap = data["DlcData"]

        found_parent_key = _find_map_key(dlc_data, parent_app_id)
        if found_parent_key is None:
            try:
                found_parent_key = int(parent_app_id)
            except ValueError:
                found_parent_key = parent_app_id
            dlc_data[found_parent_key] = CommentedMap()

        parent_map: CommentedMap = dlc_data[found_parent_key]

        found_dlc_key = _find_map_key(parent_map, dlc_id)

        if found_dlc_key is not None:
            if str(parent_map[found_dlc_key]) == dlc_name:
                logger.debug(
                    f"DLC '{dlc_id}' already exists under AppID '{parent_app_id}'"
                )
                return False
            parent_map[found_dlc_key] = dlc_name
        else:
            try:
                insert_dlc_key = int(dlc_id)
            except ValueError:
                insert_dlc_key = dlc_id
            parent_map[insert_dlc_key] = dlc_name

        if not _save_yaml(yaml, data, config_path):
            return False

        logger.info(
            f"Added DLC '{dlc_name}' ({dlc_id}) under AppID '{parent_app_id}' "
            f"in {config_path}"
        )
        return True

    except Exception as e:
        logger.error(
            f"Failed to add DLC '{dlc_id}' to {config_path}: {e}", exc_info=True
        )
        return False


def remove_dlc_data(config_path: Path, parent_app_id: str, dlc_id: str) -> bool:
    """
    Remove a DLC entry under its parent game in the DlcData section.

    Args:
        config_path:   Path to the YAML config file.
        parent_app_id: Parent game AppID.
        dlc_id:        DLC AppID to remove.

    Returns:
        True if removed, False if not found or an error occurred.
    """
    if not _check_guards("remove_dlc_data"):
        return False

    if not config_path.exists():
        return False

    try:
        yaml, data = _load_yaml(config_path)
        dlc_data = data.get("DlcData")

        if not dlc_data:
            return False

        found_parent_key = _find_map_key(dlc_data, parent_app_id)
        if found_parent_key is None:
            return False

        parent_map = dlc_data[found_parent_key]
        if not isinstance(parent_map, dict):
            return False

        found_dlc_key = _find_map_key(parent_map, dlc_id)
        if found_dlc_key is None:
            return False

        del parent_map[found_dlc_key]

        # Optionally remove parent if empty
        if not parent_map:
            del dlc_data[found_parent_key]

        if not _save_yaml(yaml, data, config_path):
            return False

        logger.info(
            f"Removed DLC '{dlc_id}' under AppID '{parent_app_id}' in {config_path}"
        )
        return True

    except Exception as e:
        logger.error(
            f"Failed to remove DLC '{dlc_id}' from {config_path}: {e}", exc_info=True
        )
        return False


# ──────────────────────────────────────────────────────────────────────────────
# SLSsteam-specific convenience functions
# ──────────────────────────────────────────────────────────────────────────────


def ensure_slssteam_api_enabled(config_path: Path) -> bool:
    """
    Ensure ``API: yes`` is present in config.yaml.

    Only acts when SLSsteam mode and config management are both enabled.

    Args:
        config_path: Path to the SLSsteam config.yaml file.

    Returns:
        True if the file was modified (API was off and is now on).
        False if already enabled, guards are off, or an error occurred.
    """
    if not _check_guards("ensure_slssteam_api_enabled"):
        return False
    return update_yaml_scalar_value(config_path, "API", True)
