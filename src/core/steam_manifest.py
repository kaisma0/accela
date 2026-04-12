import logging
from pathlib import Path
from typing import Any, Mapping, Sequence

logger = logging.getLogger(__name__)


def _build_platform_config(
    selected_depots: Sequence[Any], depots: Mapping[str, Mapping[str, Any]]
) -> str:
    """Build optional platform override section for appmanifest ACF."""
    empty_platform_config = '\t"UserConfig"\n\t{\n\t}\n\t"MountedConfig"\n\t{\n\t}'

    downloading_proton_depots = False
    downloading_linux_depots = False
    depot_source_platform = "linux"

    logger.info(
        "Checking depot platforms for %d selected depots...", len(selected_depots)
    )

    for depot_id in selected_depots:
        depot_id_str = str(depot_id)
        depot_info = depots.get(depot_id_str, {})
        try:
            platform = (depot_info.get("oslist") or "").lower() or "unknown"
        except Exception:
            platform = "unknown"

        logger.info(
            "Depot %s: platform='%s', config=%s",
            depot_id_str,
            platform,
            depot_info.get("config", {}),
        )

        if platform == "linux":
            downloading_linux_depots = True
            logger.info("-> Identified as Linux depot")
        elif platform and platform != "unknown":
            downloading_proton_depots = True
            depot_source_platform = platform
            logger.info("-> Identified as non-Linux depot: %s", platform)

    logger.info(
        "Platform detection summary - Proton source: %s, Linux: %s",
        downloading_proton_depots,
        downloading_linux_depots,
    )

    if downloading_proton_depots:
        logger.info(
            "Non-Linux depots detected - adding compatibility configuration (source: %s)",
            depot_source_platform,
        )
        return (
            '\t"UserConfig"\n'
            "\t{\n"
            '\t\t"platform_override_dest"\t\t"linux"\n'
            f'\t\t"platform_override_source"\t\t"{depot_source_platform}"\n'
            "\t}\n"
            '\t"MountedConfig"\n'
            "\t{\n"
            '\t\t"platform_override_dest"\t\t"linux"\n'
            f'\t\t"platform_override_source"\t\t"{depot_source_platform}"\n'
            "\t}"
        )

    if downloading_linux_depots:
        logger.info("Linux depots on Linux - adding empty platform config")
    else:
        logger.info("No platform-specific depots detected - adding empty platform config")

    return empty_platform_config


def _build_installed_depots_block(
    selected_depots: Sequence[Any],
    manifests: Mapping[str, Any],
    depots: Mapping[str, Mapping[str, Any]],
) -> str:
    """Build InstalledDepots ACF block from selected depots and manifest ids."""
    depots_content = ""
    for depot_id in selected_depots:
        depot_id_str = str(depot_id)
        manifest_gid = manifests.get(depot_id_str)
        depot_info = depots.get(depot_id_str, {})
        depot_size = depot_info.get("size", "0")

        if manifest_gid:
            depots_content += (
                f'\t\t"{depot_id_str}"\n'
                "\t\t{\n"
                f'\t\t\t"manifest"\t\t"{manifest_gid}"\n'
                f'\t\t\t"size"\t\t"{depot_size}"\n'
                "\t\t}\n"
            )
        else:
            logger.warning(
                "Could not find manifest GID for selected depot %s", depot_id_str
            )

    if depots_content:
        return f'\t"InstalledDepots"\n\t{{\n{depots_content}\t}}'

    return '\t"InstalledDepots"\n\t{\n\t}'


def write_appmanifest_acf(
    steamapps_path: Path,
    appid: str,
    game_name: str,
    install_folder_name: str,
    size_on_disk: int,
    buildid: str,
    selected_depots: Sequence[Any],
    manifests: Mapping[str, Any],
    depots: Mapping[str, Mapping[str, Any]],
) -> Path:
    """Write steamapps/appmanifest_<appid>.acf and return output path."""
    acf_path = steamapps_path / f"appmanifest_{appid}.acf"

    installed_depots_str = _build_installed_depots_block(
        selected_depots=selected_depots,
        manifests=manifests,
        depots=depots,
    )
    platform_config = _build_platform_config(selected_depots=selected_depots, depots=depots)

    acf_content = (
        '"AppState"\n'
        "{\n"
        f'\t"appid"\t\t"{appid}"\n'
        '\t"Universe"\t\t"1"\n'
        f'\t"name"\t\t"{game_name}"\n'
        '\t"StateFlags"\t\t"4"\n'
        f'\t"installdir"\t\t"{install_folder_name}"\n'
        f'\t"SizeOnDisk"\t\t"{size_on_disk}"\n'
        f'\t"buildid"\t\t"{buildid}"\n'
        f"{installed_depots_str}"
    )

    if platform_config:
        acf_content += f"\n{platform_config}"

    acf_content += "\n}"

    with acf_path.open("w", encoding="utf-8") as f:
        f.write(acf_content)

    logger.info("Created .acf file at %s", acf_path)
    return acf_path
