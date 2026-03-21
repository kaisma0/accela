"""
YAML Configuration Manager

Provides helper functions for modifying YAML config files while preserving
comments, formatting, and whitespace using ruamel.yaml.
"""

import io
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

from utils.settings import get_settings

logger = logging.getLogger(__name__)

BACKUP_SUFFIX = ".bak"


def _get_yaml_parser() -> YAML:
    """
    Configure and return a ruamel.yaml parser for this specific config format.

    Settings:
        - preserve_quotes: keeps original quoting style intact
        - version 1.1: ensures 'yes'/'no' are treated as native booleans
        - boolean_representation: serialises True/False as 'yes'/'no'
        - indent(mapping=2, sequence=4, offset=2): matches the 2-space
          alignment used throughout the SLSsteam config format

    Returns:
        A configured YAML parser instance.
    """
    yaml = YAML()
    yaml.preserve_quotes = True
    # version 1.1 ensures 'yes' and 'no' are natively evaluated as booleans
    yaml.version = (1, 1)
    yaml.boolean_representation = ['no', 'yes']
    # offset=2 and sequence=4 forces list items to perfectly match your 2-space alignment
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def _load_config(config_path: Path) -> CommentedMap | None:
    """
    Load a YAML config file, preserving all comments and formatting.

    Returns an empty CommentedMap if the file does not exist, so callers
    can treat a missing file the same as an empty one when creating new
    sections.  Returns None on a parse error so callers can abort rather
    than overwriting a corrupt file with empty data.

    Args:
        config_path: Path to the YAML config file.

    Returns:
        Parsed CommentedMap, an empty CommentedMap if the file is missing,
        or None if the file cannot be parsed.
    """
    if not config_path.exists():
        return CommentedMap()

    try:
        yaml = _get_yaml_parser()
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.load(f)
            return data if data is not None else CommentedMap()
    except Exception as e:
        logger.error(f"Failed to parse YAML file {config_path}: {e}")
        # CRITICAL: Return None on failure so we don't overwrite a broken file with an empty map
        return None


def _atomic_write(config_path: Path, content: str) -> bool:
    """
    Atomically write a raw string to a config file.

    Writes to a temporary file in the same directory first, then performs
    an atomic os.replace.  Used by functions that manipulate the file as a
    string rather than through the ruamel.yaml data model.

    Args:
        config_path: Destination path for the config file.
        content: Raw string content to write.

    Returns:
        True if the file was written successfully, False otherwise.
    """
    temp_path = None
    try:
        fd, temp_path_str = tempfile.mkstemp(
            dir=config_path.parent, prefix="config_", suffix=".tmp"
        )
        temp_path = Path(temp_path_str)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(temp_path, config_path)
        return True
    except Exception as e:
        logger.error(f"Failed to atomically write {config_path}: {e}", exc_info=True)
        if temp_path and temp_path.exists():
            try:
                temp_path.unlink()
            except Exception as cleanup_error:
                logger.debug(f"Failed to cleanup temp file {temp_path}: {cleanup_error}")
        return False


def _save_config(config_path: Path, data: CommentedMap) -> bool:
    """
    Atomically write a CommentedMap back to a YAML config file.

    Writes to a temporary file in the same directory first, then performs
    an atomic os.replace so the original file is never left in a partially
    written state if the process is interrupted.  The parent directory is
    created if it does not already exist.

    Args:
        config_path: Destination path for the config file.
        data: The CommentedMap to serialise.

    Returns:
        True if the file was written successfully, False otherwise.
    """
    config_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        # Create a genuinely atomic temporary file to prevent race conditions
        fd, temp_path_str = tempfile.mkstemp(
            dir=config_path.parent, prefix="config_", suffix=".tmp"
        )
        temp_path = Path(temp_path_str)

        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml = _get_yaml_parser()
            yaml.dump(data, f)

        os.replace(temp_path, config_path)
        return True
    except Exception as e:
        logger.error(f"Failed to atomically write {config_path}: {e}", exc_info=True)
        if temp_path and temp_path.exists():
            try:
                temp_path.unlink()
            except Exception as cleanup_error:
                logger.debug(f"Failed to cleanup temp file {temp_path}: {cleanup_error}")
        return False


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


def get_fake_appid_for_online() -> str:
    """
    Get the FakeAppId to use for playing games online.

    Returns:
        The appid from settings, or "480" (Spacewar) if not set.
    """
    settings = get_settings()
    fake_appid = settings.value("fake_appid_for_online", "", type=str).strip()
    return fake_appid if fake_appid else "480"


def _create_backup(config_path: Path) -> bool:
    """
    Create a backup of the config file.

    Creates config.yaml.bak with the current config content.
    Only creates a backup if the source file exists.
    Does not overwrite an existing backup if the new file is smaller,
    as a safeguard against accidentally backing up an incomplete or
    corrupted file.

    Args:
        config_path: Path to the config file to back up.

    Returns:
        True if the backup was created or a valid backup already exists,
        False if the source file is missing or the copy fails.
    """
    try:
        if not config_path.exists():
            return False

        backup_path = config_path.with_suffix(BACKUP_SUFFIX)
        if backup_path.exists():
            new_size = config_path.stat().st_size
            backup_size = backup_path.stat().st_size
            if new_size < backup_size:
                logger.debug("Skipping backup: new file is smaller than existing backup")
                return True

        shutil.copy2(config_path, backup_path)
        logger.info(f"Created backup: {backup_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to create backup: {e}")
        return False


def backup_config_on_startup(config_path: Path) -> bool:
    """
    Create a backup of the config file on application startup.

    Should be called once at startup before any modifications are made.
    Backs up to config.yaml.bak, overwriting any existing backup unless
    the current file is smaller (see _create_backup).

    Args:
        config_path: Path to the config.yaml file.

    Returns:
        True if a backup exists or was created, False if the config file
        is missing or the backup could not be written.
    """
    return _create_backup(config_path)


def ensure_slssteam_api_enabled(config_path: Path) -> bool:
    """
    Ensure the SLSsteam API is enabled in config.yaml.

    Sets ``API: yes`` if it is not already present or set to a different
    value.  Only performs the update if both SLSsteam mode and config
    management are enabled in settings.

    Args:
        config_path: Path to the SLSsteam config.yaml file.

    Returns:
        True if the API is enabled (either it already was, or it was just
        set), False on error or if SLSsteam mode / config management is
        disabled.
    """
    if not (is_slssteam_mode_enabled() and is_slssteam_config_management_enabled()):
        return False

    updated = update_yaml_scalar_value(config_path, "API", True)
    if updated:
        return True

    data = _load_config(config_path)
    return data is not None and bool(data.get("API", False))


def update_yaml_boolean_value(config_path: Path, key: str, value: bool) -> bool:
    """
    Update a top-level boolean value in the YAML config.

    Delegates to update_yaml_scalar_value; ruamel.yaml maps Python booleans
    to 'yes'/'no' automatically via the configured boolean_representation.
    All comments, whitespace, and formatting are preserved.

    Args:
        config_path: Path to the YAML config file.
        key: The top-level YAML key to update (e.g. 'API').
        value: Boolean value to set.

    Returns:
        True if the value was changed and saved, False if it was already
        correct, the key was not found, or an error occurred.
    """
    return update_yaml_scalar_value(config_path, key, value)


def update_yaml_scalar_value(config_path: Path, key: str, value) -> bool:
    """
    Update a top-level scalar value in the YAML config while preserving
    all comments, whitespace, and formatting.

    Supported value types:
        - bool   → serialised as yes/no
        - int / float → serialised as a numeric literal
        - str    → serialised as a double-quoted string

    Args:
        config_path: Path to the YAML config file.
        key: The top-level YAML key to update.
        value: New scalar value to set.

    Returns:
        True if the value was changed and saved, False if it was already
        correct, the key was not found, or an error occurred.
    """
    data = _load_config(config_path)
    if data is None:
        return False

    if key not in data:
        logger.warning(f"Key '{key}' not found in config file {config_path}")
        return False

    if isinstance(value, str):
        value = DoubleQuotedScalarString(value)

    if data.get(key) == value:
        logger.debug(f"Key '{key}' is already set to {value}")
        return False

    data[key] = value
    if _save_config(config_path, data):
        logger.info(f"Updated '{key}' to {value} in {config_path}")
        return True
    return False


def update_yaml_nested_scalar_value(
    config_path: Path, section: str, key: str, value
) -> bool:
    """
    Update a scalar value for a key nested under a YAML section while
    preserving all comments, whitespace, and formatting.

    Example target:
        IdleStatus:
          AppId: 0

    Supported value types mirror update_yaml_scalar_value (bool, numeric,
    str).

    Args:
        config_path: Path to the YAML config file.
        section: Parent section name (e.g. 'IdleStatus').
        key: Child key name inside the section (e.g. 'AppId').
        value: New scalar value to set.

    Returns:
        True if the value was changed and saved, False if the section or
        key was not found, value was already correct, or an error occurred.
    """
    data = _load_config(config_path)
    if data is None:
        return False

    if section not in data or not isinstance(data[section], dict):
        logger.warning(f"Section '{section}' not found in config file {config_path}")
        return False

    if key not in data[section]:
        logger.warning(
            f"Key '{key}' under section '{section}' not found in config file {config_path}"
        )
        return False

    if isinstance(value, str):
        value = DoubleQuotedScalarString(value)

    if data[section].get(key) == value:
        logger.debug(f"Key '{section}.{key}' is already set to {value}")
        return False

    data[section][key] = value
    if _save_config(config_path, data):
        logger.info(f"Updated '{section}.{key}' to {value} in {config_path}")
        return True
    return False


def get_user_config_path() -> Path:
    """
    Get the path to the user's SLSsteam config.yaml file.

    Respects the XDG Base Directory Specification: uses XDG_CONFIG_HOME if
    it is set to an absolute path, otherwise falls back to ~/.config.

    Returns:
        Path to ~/.config/SLSsteam/config.yaml (or the XDG equivalent).
    """
    xdg_config_home_str = os.environ.get("XDG_CONFIG_HOME", "")
    xdg_config_home = (
        Path(xdg_config_home_str).expanduser() if xdg_config_home_str else Path()
    )

    if xdg_config_home_str and xdg_config_home.is_absolute():
        config_dir = xdg_config_home / "SLSsteam"
    else:
        config_dir = Path.home() / ".config" / "SLSsteam"

    return config_dir / "config.yaml"


def fix_slssteam_config_indentation(config_path: Path) -> bool:
    """
    Fix indentation and formatting of the SLSsteam config.yaml file by
    round-tripping it through ruamel.yaml.

    This replaces the old regex-based approach that targeted only
    AdditionalApps and AppTokens.  ruamel.yaml normalises all sections
    uniformly while preserving comments.

    The file is only written if the round-trip actually produces different
    content, preventing unnecessary disk writes on already-correct files.
    Only runs if both SLSsteam mode and config management are enabled.

    Args:
        config_path: Path to the YAML config file.

    Returns:
        True if the file was modified, False if it was already correctly
        formatted, the file is missing, the config could not be parsed, or
        the mode/management settings are disabled.
    """
    if not (is_slssteam_mode_enabled() and is_slssteam_config_management_enabled()):
        return False
    if not config_path.exists():
        return False

    with open(config_path, "r", encoding="utf-8") as f:
        original_content = f.read()

    data = _load_config(config_path)
    if data is None:
        return False

    yaml = _get_yaml_parser()
    buf = io.StringIO()
    yaml.dump(data, buf)
    new_content = buf.getvalue()

    # Prevent saving (and logging) if the indentation is already perfect
    if original_content != new_content:
        if _save_config(config_path, data):
            logger.info(f"Fixed formatting and indentation in {config_path}")
            return True
    return False


def add_additional_app(config_path: Path, app_id: str, comment: str = "") -> bool:
    """
    Add an AppID to the AdditionalApps list in SLSsteam config.yaml.

    Creates the AdditionalApps section if it does not exist.  Does nothing
    if the AppID is already present.  An optional inline comment can be
    attached to the new list entry.
    Only runs if both SLSsteam mode and config management are enabled.

    Args:
        config_path: Path to the YAML config file.
        app_id: The AppID to add.
        comment: Optional comment to add after the AppID entry.

    Returns:
        True if the AppID was added, False if it already exists, the config
        could not be loaded/saved, or the mode/management settings are
        disabled.
    """
    if not (is_slssteam_mode_enabled() and is_slssteam_config_management_enabled()):
        return False

    # Use ruamel.yaml only for structure validation and duplicate detection.
    # Writing is done via raw string insertion to avoid a ruamel.yaml bug where
    # between-section comments get displaced inside the sequence on append.
    data = _load_config(config_path)
    if data is None:
        return False

    apps = data.get("AdditionalApps")
    if apps is not None:
        if not isinstance(apps, list):
            logger.error("'AdditionalApps' is not a list in the config.")
            return False
        if any(str(x) == str(app_id) for x in apps):
            return False

    new_line = f"  - {app_id}   # {comment}\n" if comment else f"  - {app_id}\n"

    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        if _atomic_write(config_path, f"AdditionalApps:\n{new_line}"):
            logger.info(f"Created config with AppID '{app_id}' in {config_path}")
            return True
        return False

    with open(config_path, "r", encoding="utf-8") as f:
        content = f.read()

    section_re = re.compile(r"^AdditionalApps:\s*$", re.MULTILINE)
    match = section_re.search(content)

    if match:
        # Walk lines after the header to find the insertion point: right after
        # the last list item, before any comment or key at a lower indent level.
        start_pos = match.end()
        lines = content[start_pos:].split("\n")
        last_item_end = start_pos

        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("-"):
                last_item_end = start_pos + sum(len(lines[j]) + 1 for j in range(i + 1))
            elif not stripped or stripped.startswith("#"):
                continue
            else:
                break
        else:
            last_item_end = len(content)

        new_content = content[:last_item_end] + new_line + content[last_item_end:]
    else:
        new_content = content + f"\nAdditionalApps:\n{new_line}"

    if _atomic_write(config_path, new_content):
        logger.info(f"Added AppID '{app_id}' to AdditionalApps in {config_path}")
        return True
    return False


def remove_additional_app(config_path: Path, app_id: str) -> bool:
    """
    Remove an AppID from the AdditionalApps list in SLSsteam config.yaml.

    Removes all entries matching app_id (handles accidental duplicates).
    All other content, comments, and formatting are preserved.
    Only runs if both SLSsteam mode and config management are enabled.

    Args:
        config_path: Path to the YAML config file.
        app_id: The AppID to remove.

    Returns:
        True if at least one entry was removed, False if the AppID was not
        found, the config could not be loaded/saved, or the mode/management
        settings are disabled.
    """
    if not (is_slssteam_mode_enabled() and is_slssteam_config_management_enabled()):
        return False

    if not config_path.exists():
        return False

    with open(config_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Match the list item line (with optional inline comment), including its newline.
    line_re = re.compile(
        rf"^[ \t]*-[ \t]+{re.escape(app_id)}[ \t]*(?:#[^\n]*)?\n",
        re.MULTILINE,
    )
    match = line_re.search(content)
    if not match:
        logger.debug(f"AppID '{app_id}' not found in AdditionalApps")
        return False

    new_content = content[: match.start()] + content[match.end() :]
    if _atomic_write(config_path, new_content):
        logger.info(f"Removed AppID '{app_id}' from AdditionalApps in {config_path}")
        return True
    return False


def add_depot_data(
    config_path: Path, parent_app_id: str, depot_id: str, comment: str = ""
) -> bool:
    """
    Add a depot entry to the DepotData section in SLSsteam config.yaml.

    Expected format:
        DepotData:
          ParentAppId:
            - DepotId      # optional comment
            - DepotId2

    Creates the DepotData section and/or the parent AppID block if they do
    not already exist.  Does nothing if the depot is already listed under
    the given parent.
    Only runs if both SLSsteam mode and config management are enabled.

    Args:
        config_path: Path to the YAML config file.
        parent_app_id: The parent game AppID under which to add the depot.
        depot_id: The depot ID to add.
        comment: Optional comment/description for the depot entry.

    Returns:
        True if the depot was added, False if it already exists, the config
        could not be loaded/saved, or the mode/management settings are
        disabled.
    """
    if not (is_slssteam_mode_enabled() and is_slssteam_config_management_enabled()):
        return False

    # Use ruamel.yaml only for duplicate detection; write via raw string insertion
    # to avoid the ruamel.yaml comment-displacement bug on sequence/map append.
    data = _load_config(config_path)
    if data is None:
        return False

    depot_section = data.get("DepotData")
    if depot_section is not None:
        if not isinstance(depot_section, dict):
            logger.error("'DepotData' is not a mapping in the config.")
            return False
        parent_key = next((k for k in depot_section if str(k) == str(parent_app_id)), None)
        if parent_key is not None and depot_section[parent_key]:
            if any(str(x) == str(depot_id) for x in depot_section[parent_key]):
                return False

    depot_line = f"    - {depot_id}    # {comment}\n" if comment else f"    - {depot_id}\n"

    if not config_path.exists():
        logger.warning(f"Config file not found at {config_path}")
        return False

    with open(config_path, "r", encoding="utf-8") as f:
        content = f.read()

    section_re = re.compile(r"^DepotData:\s*$", re.MULTILINE)
    depot_match = section_re.search(content)

    if not depot_match:
        # DepotData section does not exist — create it from scratch.
        new_block = f"\nDepotData:\n  {parent_app_id}:\n{depot_line}\n"
        if _atomic_write(config_path, content + new_block):
            logger.info(f"Added depot '{depot_id}' under '{parent_app_id}' in {config_path}")
            return True
        return False

    depot_section_start = depot_match.end()

    # Check whether parent_app_id already has a block under DepotData.
    parent_re = re.compile(
        rf"^  {re.escape(parent_app_id)}:\s*(?:#.*)?$", re.MULTILINE
    )
    parent_match = parent_re.search(content, depot_section_start)

    # Reject a match that belongs to a different top-level section.
    if parent_match:
        between = content[depot_section_start:parent_match.start()]
        if re.search(r"^[A-Za-z]", between, re.MULTILINE):
            parent_match = None

    if parent_match:
        # Parent block exists — insert new depot after its last "    - " item.
        parent_end = parent_match.end()
        lines = content[parent_end:].split("\n")
        last_depot_end = parent_end

        for i, line in enumerate(lines):
            stripped = line.strip()
            if line.startswith("    -"):
                last_depot_end = parent_end + sum(len(lines[j]) + 1 for j in range(i + 1))
            elif not stripped or stripped.startswith("#"):
                continue
            else:
                break
        else:
            last_depot_end = len(content)

        new_content = content[:last_depot_end] + depot_line + content[last_depot_end:]

    else:
        # Parent block does not exist — append a new one inside DepotData.
        # Walk from the header to find the end of existing DepotData content
        # (only indented lines belong to it; column-0 lines end the section).
        remaining_lines = content[depot_section_start:].split("\n")
        last_content_end = depot_section_start

        # Skip the newline that immediately follows "DepotData:"
        start_idx = 0
        if remaining_lines and remaining_lines[0] == "":
            start_idx = 1
            last_content_end = depot_section_start + 1

        for i in range(start_idx, len(remaining_lines)):
            line = remaining_lines[i]
            if not line.strip():
                continue
            if line[0] in (" ", "\t"):
                last_content_end = depot_section_start + sum(
                    len(remaining_lines[j]) + 1 for j in range(i + 1)
                )
            else:
                break
        else:
            last_content_end = len(content)

        is_first_entry = last_content_end <= depot_section_start + 1
        separator = "" if is_first_entry else "\n"
        parent_block = f"{separator}  {parent_app_id}:\n{depot_line}"
        new_content = content[:last_content_end] + parent_block + content[last_content_end:]

    if _atomic_write(config_path, new_content):
        logger.info(f"Added depot '{depot_id}' under '{parent_app_id}' in {config_path}")
        return True
    return False


def remove_depot_data(config_path: Path, app_id: str) -> bool:
    """
    Remove an AppID and all its depot entries from the DepotData section
    in SLSsteam config.yaml.

    Removes the entire parent block, e.g.:
        AppId:
          - depot1
          - depot2

    Only runs if both SLSsteam mode and config management are enabled.

    Args:
        config_path: Path to the YAML config file.
        app_id: The AppID whose depot block should be removed.

    Returns:
        True if the block was removed, False if the AppID was not found,
        the config could not be loaded/saved, or the mode/management
        settings are disabled.
    """
    if not (is_slssteam_mode_enabled() and is_slssteam_config_management_enabled()):
        return False

    if not config_path.exists():
        logger.debug(f"Config file does not exist at {config_path}")
        return False

    with open(config_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Find DepotData section header.
    section_re = re.compile(r"^DepotData:\s*$", re.MULTILINE)
    section_match = section_re.search(content)
    if not section_match:
        logger.debug("No DepotData section found")
        return False

    # Find the parent AppID header line (2-space indent) inside DepotData.
    parent_re = re.compile(
        rf"^  {re.escape(app_id)}:[ \t]*(?:#[^\n]*)?\n",
        re.MULTILINE,
    )
    parent_match = parent_re.search(content, section_match.end())
    if not parent_match:
        logger.debug(f"AppID '{app_id}' not found in DepotData")
        return False

    # Confirm it's still within the DepotData section (no top-level key between them).
    between = content[section_match.end() : parent_match.start()]
    if re.search(r"^[A-Za-z]", between, re.MULTILINE):
        logger.debug(f"AppID '{app_id}' found but not within DepotData section")
        return False

    # Remove the parent line plus all immediately following lines that are
    # indented deeper than 2 spaces (the depot list items).
    # Also consume the blank separator line that add_depot_data inserts before
    # each new parent block, so repeated add/remove cycles don't accumulate
    # empty lines inside the DepotData section.
    block_start = parent_match.start()
    if block_start > 0 and content[block_start - 1] == "\n":
        look = block_start - 1
        if look > 0 and content[look - 1] == "\n":
            block_start = look  # step back over the blank line (the \n that ends it)
    pos = parent_match.end()
    child_re = re.compile(r"^[ \t]{3,}[^\n]*\n", re.MULTILINE)
    while pos < len(content):
        m = child_re.match(content, pos)
        if m:
            pos = m.end()
        else:
            break

    new_content = content[:block_start] + content[pos:]
    if _atomic_write(config_path, new_content):
        logger.info(
            f"Removed AppID '{app_id}' and its depots from DepotData in {config_path}"
        )
        return True
    return False


def add_app_token(config_path: Path, app_id: str, token: str) -> bool:
    """
    Add or update an AppToken in the AppTokens section of SLSsteam config.yaml.

    Expected format:
        AppTokens:
          app_id: token_value

    Creates the AppTokens section if it does not exist.  If the app_id is
    already present with a different token, the token is updated in place.
    Only runs if both SLSsteam mode and config management are enabled.

    Args:
        config_path: Path to the YAML config file.
        app_id: The AppID to add or update.
        token: The application token value.

    Returns:
        True if the token was added or updated, False if it already exists
        with the same value, the config could not be loaded/saved, or the
        mode/management settings are disabled.
    """
    if not (is_slssteam_mode_enabled() and is_slssteam_config_management_enabled()):
        return False

    data = _load_config(config_path)
    if data is None:
        return False

    if "AppTokens" not in data or data["AppTokens"] is None:
        data["AppTokens"] = CommentedMap()
    elif not isinstance(data["AppTokens"], dict):
        logger.error("'AppTokens' is not a mapping in the config.")
        return False

    target_key = next(
        (k for k in data["AppTokens"] if str(k) == str(app_id)), None
    )
    if target_key is None:
        target_key = int(app_id) if str(app_id).isdigit() else app_id

    if data["AppTokens"].get(target_key) == token:
        return False

    data["AppTokens"][target_key] = token
    if _save_config(config_path, data):
        logger.info(f"Added/Updated AppToken for '{app_id}' in {config_path}")
        return True
    return False


def get_app_tokens(config_path: Path) -> dict[str, str]:
    """
    Get all AppTokens from SLSsteam config.yaml.

    Args:
        config_path: Path to the YAML config file.

    Returns:
        Dict mapping app_id (str) → token (str).  Returns an empty dict if
        the AppTokens section is absent, empty, or the file cannot be
        parsed.
    """
    data = _load_config(config_path)
    if data is None or not data.get("AppTokens"):
        return {}
    if not isinstance(data["AppTokens"], dict):
        return {}

    return {str(k): str(v) for k, v in data["AppTokens"].items()}


def get_fake_app_ids(config_path: Path, fake_appid: str = "") -> set[str]:
    """
    Get all AppIDs mapped to a given fake AppID in the FakeAppIds section.

    Args:
        config_path: Path to the YAML config file.
        fake_appid: The fake AppID to filter by.  Defaults to the value
            from settings, or "480" (Spacewar) if not configured.

    Returns:
        Set of app_id strings whose value matches fake_appid.  Returns an
        empty set if the section is absent or the file cannot be parsed.
    """
    fake_appid = fake_appid or get_fake_appid_for_online()
    data = _load_config(config_path)
    if data is None or not data.get("FakeAppIds"):
        return set()
    if not isinstance(data["FakeAppIds"], dict):
        return set()

    return {
        str(k)
        for k, v in data["FakeAppIds"].items()
        if str(v) == str(fake_appid)
    }


def add_fake_app_id(
    config_path: Path,
    app_id: str,
    game_name: str = "",
    fake_appid: str = "",
) -> bool:
    """
    Add an AppID to the FakeAppIds section in SLSsteam config.yaml.

    Expected format:
        FakeAppIds:
          appid: <fake_appid>   # Game Name -> Spacewar

    Creates the FakeAppIds section if it does not exist.  If the app_id is
    already present with a different fake_appid, the value is updated in
    place.  An optional game name is written as an inline comment with a
    suffix of "Spacewar" for fake_appid "480", or "SLSonline" otherwise.
    Only runs if both SLSsteam mode and config management are enabled.

    Args:
        config_path: Path to the YAML config file.
        app_id: The AppID to add.
        game_name: Optional game name used to build the inline comment.
        fake_appid: The fake AppID to assign.  Defaults to the value from
            settings, or "480" (Spacewar) if not configured.

    Returns:
        True if the entry was added or updated, False if it already exists
        with the same value, the config could not be loaded/saved, or the
        mode/management settings are disabled.
    """
    if not (is_slssteam_mode_enabled() and is_slssteam_config_management_enabled()):
        return False

    fake_appid = fake_appid or get_fake_appid_for_online()
    data = _load_config(config_path)
    if data is None:
        return False

    if "FakeAppIds" not in data or data["FakeAppIds"] is None:
        data["FakeAppIds"] = CommentedMap()
    elif not isinstance(data["FakeAppIds"], dict):
        logger.error("'FakeAppIds' is not a mapping in the config.")
        return False

    target_key = next(
        (k for k in data["FakeAppIds"] if str(k) == str(app_id)), None
    )
    if target_key is None:
        target_key = int(app_id) if str(app_id).isdigit() else app_id

    if str(data["FakeAppIds"].get(target_key, "")) == str(fake_appid):
        return False

    data["FakeAppIds"][target_key] = (
        int(fake_appid) if fake_appid.isdigit() else fake_appid
    )

    if game_name:
        suffix = "Spacewar" if fake_appid == "480" else "SLSonline"
        data["FakeAppIds"].yaml_add_eol_comment(
            f"{game_name} -> {suffix}", target_key
        )

    if _save_config(config_path, data):
        logger.info(f"Added FakeAppId '{app_id}' -> '{fake_appid}' in {config_path}")
        return True
    return False


def remove_fake_app_id(
    config_path: Path, app_id: str, fake_appid: str = ""
) -> bool:
    """
    Remove an AppID from the FakeAppIds section in SLSsteam config.yaml.

    Only removes the entry if both the app_id and its current fake_appid
    value match, preventing accidental removal of entries that were
    reassigned to a different fake AppID.
    Only runs if both SLSsteam mode and config management are enabled.

    Args:
        config_path: Path to the YAML config file.
        app_id: The AppID to remove.
        fake_appid: The fake AppID the entry must currently be mapped to.
            Defaults to the value from settings, or "480" (Spacewar).

    Returns:
        True if the entry was removed, False if it was not found, the
        fake_appid did not match, the config could not be loaded/saved, or
        the mode/management settings are disabled.
    """
    if not (is_slssteam_mode_enabled() and is_slssteam_config_management_enabled()):
        return False

    fake_appid = fake_appid or get_fake_appid_for_online()
    data = _load_config(config_path)
    if data is None or not data.get("FakeAppIds"):
        return False
    if not isinstance(data["FakeAppIds"], dict):
        return False

    target_key = next(
        (k for k in data["FakeAppIds"] if str(k) == str(app_id)), None
    )

    if target_key is not None and str(data["FakeAppIds"][target_key]) == str(fake_appid):
        del data["FakeAppIds"][target_key]
        if _save_config(config_path, data):
            logger.info(f"Removed FakeAppId '{app_id}' from {config_path}")
            return True

    return False