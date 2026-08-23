# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
"""
Config validation helper for Upload Assistant.
Validates the user's config.py against expected structure and types.
"""

import math
from typing import Any, cast

from src.domain_models.image_host import (
    IMAGE_HOST_CONFIG_KEYS,
    IMAGE_HOST_NAMES,
    MAX_IMAGE_HOST_SLOTS,
)
from src.domain_models.tracker_catalog import USENET_TRACKERS

# Required top-level sections
REQUIRED_SECTIONS = ["DEFAULT", "TRACKERS"]

# Optional top-level sections
OPTIONAL_SECTIONS = ["IMAGES", "TORRENT_CLIENTS", "USENET"]

# Credentials are validated as an either/or pair in _validate_default_section.
REQUIRED_DEFAULT_KEYS: dict[str, type] = {}

# Expected types for common DEFAULT keys (for type validation, not required)
DEFAULT_KEY_TYPES: dict[str, tuple[type, ...]] = {
    "update_notification": (bool,),
    "verbose_notification": (bool,),
    "update_notification_cache_hours": (str, int, float),
    "tmdb_api": (str,),
    "tmdb_access_token": (str,),
    "tvdb_api": (str,),
    "tvdb_token": (str,),
    "btn_api": (str,),
    **{
        f"img_host_{index}": (str,)
        for index in range(1, MAX_IMAGE_HOST_SLOTS + 1)
    },
    "smart_image_host_selection": (bool,),
    "image_upload_concurrency": (str, int),
    "image_upload_delay": (str, float, int),
    "imgbb_api": (str,),
    "lostimg_api": (str,),
    "lensdump_api": (str,),
    "ptscreens_api": (str,),
    "onlyimage_api": (str,),
    "midnightscene_api_key": (str,),
    "add_logo": (bool,),
    "logo_size": (str, int),
    "episode_overview": (bool,),
    "screens": (str, int),
    "xxx_contact_sheet_rows": (str, int),
    "xxx_contact_sheet_columns": (str, int),
    "xxx_contact_sheet_max_videos": (str, int),
    "xxx_contact_sheet_animated_webp": (bool,),
    "xxx_contact_sheet_animation_seconds": (str, int, float),
    "cutoff_screens": (str, int),
    "max_menu_screens": (str, int),
    "thumbnail_size": (str, int),
    "frame_overlay": (bool,),
    "tone_map": (bool,),
    "auto_dvd_menus": (bool,),
    "scale_screenshots_for_par": (bool,),
    "use_libplacebo": (bool,),
    "ffmpeg_is_good": (bool,),
    "ffmpeg_warmup": (bool,),
    "ffmpeg_compression": (str, int),
    "ffmpeg_path": (str,),
    "ffprobe_path": (str,),
    "mediainfo_path": (str,),
    "dvd_mediainfo_path": (str,),
    "bdinfo_path": (str,),
    "mkbrr_path": (str,),
    "dovi_tool_path": (str,),
    "hdr10plus_tool_path": (str,),
    "process_limit": (str, int),
    "threads": (str, int),
    "ffmpeg_limit": (bool,),
    "multiScreens": (str, int),
    "pack_thumb_size": (str, int),
    "charLimit": (str, int),
    "fileLimit": (str, int),
    "processLimit": (str, int),
    "default_torrent_client": (str,),
    "skip_auto_torrent": (bool,),
    "sfx_on_prompt": (bool,),
    "console_show_time": (bool,),
    "console_show_level": (bool,),
    "console_show_path": (bool,),
    "console_markup": (bool,),
    "debug": (bool,),
    "console_debug_show_time": (bool,),
    "console_debug_show_level": (bool,),
    "console_debug_show_path": (bool,),
    "console_debug_markup": (bool,),
    "embed_links": (bool,),
    "embed_dupe_links": (bool,),
    "show_dupe_size_diff": (bool,),
    "dupe_size_difference_tolerance": (float, int),
    "tracker_pass_checks": (str, int),
    "use_largest_playlist": (bool,),
    "tracker_description_mode": (str,),
    "tracker_search_concurrency": (str, int),
    "tracker_comment_only": (bool,),
    "use_sonarr": (bool,),
    "use_radarr": (bool,),
    "mkbrr": (bool,),
    "mkbrr_threads": (str, int),
    "auto_zentag": (bool,),
    "ebook_meta_path": (str,),
    "add_dynamic_hdr_plot": (bool,),
    "dynamic_hdr_plot_header": (str,),
    "dynamic_hdr_plot_max_files": (str, int),
    "dynamic_hdr_plot_tool_timeout": (str, int),
    "user_overrides": (bool,),
    "ping_unit3d": (bool,),
    "get_bluray_info": (bool,),
    "add_bluray_link": (bool,),
    "use_bluray_images": (bool,),
    "bluray_image_size": (str, int),
    "bluray_score": (float, int),
    "bluray_single_score": (float, int),
    "keep_meta": (bool,),
    "show_upload_duration": (bool,),
    "print_tracker_messages": (bool,),
    "print_tracker_links": (bool,),
    "search_requests": (bool,),
    "check_predb": (bool,),
    "prefer_max_16_torrent": (bool,),
    "cross_seeding": (bool,),
    "cross_seed_check_everything": (bool,),
    "auto_mode": (bool, str),
    "google_books_api_key": (str,),
    "google_translate_api_key": (str,),
    "mam_api_key": (str,),
    "mam_id": (str,),
    "twitch_client_id": (str,),
    "twitch_client_secret": (str,),
    "upload_order": (str,),
    "music_enrichment_enabled": (bool,),
    "music_discogs_token": (str,),
    "metadata_cache_enabled": (bool,),
    "metadata_cache_dir": (str,),
    "metadata_cache_default_ttl_hours": (int,),
    "metadata_cache_negative_ttl_minutes": (int,),
    "metadata_cache_services": (dict,),
    "tracker_metadata_cache_enabled": (bool,),
    "tracker_metadata_cache_dir": (str,),
    "tracker_metadata_cache_ttl_hours": (int,),
    "tracker_metadata_cache_negative_ttl_minutes": (int,),
}

# Valid image hosts and required config settings come from the canonical
# image-host registry so validation cannot drift from the uploader/configurator.
VALID_IMAGE_HOSTS = [*IMAGE_HOST_NAMES, ""]
IMAGE_HOST_REQUIRED_CONFIG: dict[str, tuple[str, ...]] = {
    host: config_keys
    for host, config_keys in IMAGE_HOST_CONFIG_KEYS.items()
    if config_keys
}

# Valid torrent client types (must match example_config.py)
VALID_TORRENT_CLIENTS = ["qbit", "rtorrent", "deluge", "transmission", "watch"]

# Required keys in USENET section when Usenet uploading is active
USENET_REQUIRED_KEYS = ["host", "port", "username", "password", "newsgroups"]

# Expected types for known USENET keys (must match data/example_config.py's USENET section)
USENET_KEY_TYPES: dict[str, tuple[type, ...]] = {
    "enabled": (bool,),
    "host": (str,),
    "port": (str, int),
    "username": (str,),
    "password": (str,),
    "ssl": (bool,),
    "connections": (str, int),
    "newsgroups": (str,),
    "poster": (str,),
    "random_poster": (bool,),
    "skip_archive": (bool,),
    "rar_volume_size": (str,),
    "archive_password": (str,),
    "par2_percentage": (str, int),
    "obscure_subject": (bool,),
    "usenet_uploader": (str,),
    "pesto_check": (bool,),
    "pesto_check_delay": (str, int),
    "pesto_check_retries": (str, int),
    "pesto_check_connections": (str, int),
    "pesto_check_post_retries": (str, int),
    "nyuu_check": (bool,),
    "nyuu_check_delay": (str, int),
    "nyuu_check_retries": (str, int),
    "nyuu_check_connections": (str, int),
    "nyuu_path": (str,),
    "par2_path": (str,),
    "pesto_path": (str,),
    "7z_path": (str,),
    "nzb_output_dir": (str,),
    "usenet_tmp_dir": (str,),
}

# USENET keys expected to hold a plain integer or a numeric string
USENET_NUMERIC_STRING_KEYS = [
    "port",
    "connections",
    "par2_percentage",
    "pesto_check_delay",
    "pesto_check_retries",
    "pesto_check_connections",
    "pesto_check_post_retries",
    "nyuu_check_delay",
    "nyuu_check_retries",
    "nyuu_check_connections",
]

DEFAULT_NUMERIC_STRING_KEYS = [
    "screens",
    "xxx_contact_sheet_rows",
    "xxx_contact_sheet_columns",
    "xxx_contact_sheet_max_videos",
    "xxx_contact_sheet_animation_seconds",
    "cutoff_screens",
    "max_menu_screens",
    "thumbnail_size",
    "process_limit",
    "threads",
    "multiScreens",
    "pack_thumb_size",
    "charLimit",
    "fileLimit",
    "processLimit",
    "tracker_pass_checks",
    "mkbrr_threads",
    "ffmpeg_compression",
    "dupe_size_difference_tolerance",
]

TRACKER_BOOL_FIELDS = [
    "anon",
    "useAPI",
    "use_for_search",
    "modq",
    "draft",
    "draft_default",
    "img_rehost",
    "allow_ext_subtitles",
    "resolve_language",
]


class ConfigValidationError(Exception):
    """Raised when config validation fails with critical errors."""


class ConfigValidationWarning:
    """Represents a non-critical config warning."""

    def __init__(self, message: str, key: str = "", section: str = ""):
        """Create a warning with optional config location metadata."""
        self.message = message
        self.key = key
        self.section = section

    def __str__(self) -> str:
        """Render the warning with its section and key when available."""
        location = ""
        if self.section:
            location = f"[{self.section}]"
            if self.key:
                location += f"[{self.key}]"
        elif self.key:
            location = f"[{self.key}]"

        return f"{location} {self.message}" if location else self.message


def _as_dict(value: Any) -> dict[str, Any]:
    """Return value as a typed dictionary, or an empty dictionary."""
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def validate_config(
    config: Any,
    active_trackers: list[str] | None = None,
    active_imghost: str | None = None,
) -> tuple[bool, list[str], list[ConfigValidationWarning]]:
    """
    Validate the config dictionary structure and types.

    Args:
        config: The config object to validate
        active_trackers: List of tracker names that will be used (from meta.trackers)
                        If None, uses default_trackers from config
        active_imghost: The image host to use (from meta.imghost)
                       If None, uses img_host_1 from config

    Returns:
        Tuple of (is_valid, errors, warnings)
        - is_valid: True if config passes critical validation
        - errors: List of critical error messages
        - warnings: List of non-critical warnings
    """
    errors: list[str] = []
    warnings: list[ConfigValidationWarning] = []

    # Check if config is a dictionary
    if not isinstance(config, dict):
        errors.append(
            f"Config must be a dictionary, got {type(config).__name__}"
        )
        return False, errors, warnings

    config_dict = cast(dict[str, Any], config)

    # Check required sections
    for section in REQUIRED_SECTIONS:
        if section not in config_dict:
            errors.append(f"Missing required config section: '{section}'")
        elif not isinstance(config_dict[section], dict):
            errors.append(
                f"Config section '{section}' must be a dictionary, got {type(config_dict[section]).__name__}"
            )

    # If we have critical section errors, return early
    if errors:
        return False, errors, warnings

    # Validate DEFAULT section
    default_errors, default_warnings = _validate_default_section(
        _as_dict(config_dict.get("DEFAULT"))
    )
    errors.extend(default_errors)
    warnings.extend(default_warnings)

    # Validate TRACKERS section
    # Determine which trackers are active
    trackers_section = _as_dict(config_dict.get("TRACKERS"))
    if active_trackers is None:
        # Fall back to default_trackers from config
        default_trackers_val = trackers_section.get("default_trackers", "")
        if (
            isinstance(default_trackers_val, str)
            and default_trackers_val.strip()
        ):
            active_trackers = [
                t.strip().upper()
                for t in default_trackers_val.split(",")
                if t.strip()
            ]
        elif isinstance(default_trackers_val, list):
            active_trackers = [
                t.strip().upper()
                for t in default_trackers_val
                if isinstance(t, str) and t.strip()
            ]
        else:
            active_trackers = []

    tracker_errors, tracker_warnings = _validate_trackers_section(
        trackers_section, active_trackers
    )
    errors.extend(tracker_errors)
    warnings.extend(tracker_warnings)

    # Validate TORRENT_CLIENTS section if present
    if "TORRENT_CLIENTS" in config_dict:
        client_errors, client_warnings = _validate_torrent_clients_section(
            _as_dict(config_dict.get("TORRENT_CLIENTS"))
        )
        errors.extend(client_errors)
        warnings.extend(client_warnings)

    # Determine if Usenet is active (either because USENET is a target tracker, a Usenet tracker class is active, or enabled in config)
    trackers_upper = (
        [t.upper() for t in active_trackers] if active_trackers else []
    )
    is_usenet_tracker_active = any(
        tracker in USENET_TRACKERS for tracker in trackers_upper
    )

    if "USENET" in config_dict:
        usenet_cfg = _as_dict(config_dict.get("USENET"))
        is_usenet_active = (
            "USENET" in trackers_upper
            or is_usenet_tracker_active
            or usenet_cfg.get("enabled", False)
        )
        usenet_errors, usenet_warnings = _validate_usenet_section(
            usenet_cfg, is_usenet_active
        )
        errors.extend(usenet_errors)
        warnings.extend(usenet_warnings)
    else:
        if "USENET" in trackers_upper or is_usenet_tracker_active:
            errors.append(
                "Missing required config section: 'USENET' (required for Usenet uploads)"
            )

    # Cross-reference validation for torrent client configuration
    default_section = _as_dict(config_dict.get("DEFAULT"))
    torrent_clients = _as_dict(config_dict.get("TORRENT_CLIENTS"))
    defined_clients = list(torrent_clients.keys())

    if default_section:
        default_client = default_section.get("default_torrent_client", "")

        # Validate injecting_client_list
        injecting_list = default_section.get("injecting_client_list")
        injecting_clients: list[str] = []
        if injecting_list is not None:
            if isinstance(injecting_list, str):
                # String is valid - gets converted to single-item list at runtime
                if injecting_list.strip():
                    injecting_clients = [injecting_list.strip()]
            elif isinstance(injecting_list, list):
                # List is valid - validate each item
                for i, item in enumerate(injecting_list):
                    if item and isinstance(item, str) and item.strip():
                        injecting_clients.append(item.strip())
                    elif item and not isinstance(item, str):
                        warnings.append(
                            ConfigValidationWarning(
                                f"Item at index {i} should be a string, got {type(item).__name__}",
                                key="injecting_client_list",
                                section="DEFAULT",
                            )
                        )
            else:
                warnings.append(
                    ConfigValidationWarning(
                        f"Should be a list or string, got {type(injecting_list).__name__}. "
                        "Will fall back to default_torrent_client. "
                        "Example: ['Client1', 'Client2'] or 'Client1'",
                        key="injecting_client_list",
                        section="DEFAULT",
                    )
                )

        # Validate searching_client_list
        searching_list = default_section.get("searching_client_list")
        searching_clients: list[str] = []
        if searching_list is not None:
            if isinstance(searching_list, list):
                for i, item in enumerate(searching_list):
                    if item and isinstance(item, str) and item.strip():
                        searching_clients.append(item.strip())
                    elif item and not isinstance(item, str):
                        warnings.append(
                            ConfigValidationWarning(
                                f"Item at index {i} should be a string, got {type(item).__name__}",
                                key="searching_client_list",
                                section="DEFAULT",
                            )
                        )
            else:
                warnings.append(
                    ConfigValidationWarning(
                        f"Should be a list, got {type(searching_list).__name__}. Will fall back to default_torrent_client. Example: ['Client1', 'Client2']",
                        key="searching_client_list",
                        section="DEFAULT",
                    )
                )

        # Check that referenced client names exist in TORRENT_CLIENTS
        if torrent_clients:
            warnings.extend(
                [
                    ConfigValidationWarning(
                        f"References undefined client '{client_name}'",
                        key="injecting_client_list",
                        section="DEFAULT",
                    )
                    for client_name in injecting_clients
                    if client_name != "none"
                    and client_name not in torrent_clients
                ]
            )

            warnings.extend(
                [
                    ConfigValidationWarning(
                        f"References undefined client '{client_name}'",
                        key="searching_client_list",
                        section="DEFAULT",
                    )
                    for client_name in searching_clients
                    if client_name != "none"
                    and client_name not in torrent_clients
                ]
            )

        # Check default_torrent_client - only required if no client lists are populated
        if default_client:
            if default_client not in torrent_clients:
                if defined_clients:
                    warnings.append(
                        ConfigValidationWarning(
                            f"References undefined client '{default_client}'. Defined clients: {', '.join(defined_clients)}",
                            key="default_torrent_client",
                            section="DEFAULT",
                        )
                    )
                else:
                    warnings.append(
                        ConfigValidationWarning(
                            f"References '{default_client}' but no clients defined in TORRENT_CLIENTS",
                            key="default_torrent_client",
                            section="DEFAULT",
                        )
                    )
        elif (
            not injecting_clients and not searching_clients and defined_clients
        ):
            # Only warn if default_torrent_client is empty AND no client lists are configured
            warnings.append(
                ConfigValidationWarning(
                    "No default_torrent_client, injecting_client_list, or searching_client_list configured",
                    key="default_torrent_client",
                    section="DEFAULT",
                )
            )

    # Check for unknown top-level sections (warning only)
    known_sections = set(REQUIRED_SECTIONS + OPTIONAL_SECTIONS)
    warnings.extend(
        [
            ConfigValidationWarning(
                f"Unknown config section '{section}' - this may be intentional",
                section=section,
            )
            for section in config_dict
            if section not in known_sections
        ]
    )

    # Validate image host API keys
    default_section = _as_dict(config_dict.get("DEFAULT"))
    if default_section:
        # Determine which image hosts are active
        active_hosts: list[str] = []

        # If imghost specified from command line, use that
        if active_imghost and active_imghost.strip():
            active_hosts = [active_imghost.strip()]
        else:
            # Collect all configured img_host_* values
            for i in range(1, MAX_IMAGE_HOST_SLOTS + 1):
                host_key = f"img_host_{i}"
                host_value = default_section.get(host_key, "")
                if isinstance(host_value, str) and host_value.strip():
                    active_hosts.append(host_value.strip())

        # Check that each active host has its required API key
        for host in active_hosts:
            for config_key in IMAGE_HOST_REQUIRED_CONFIG.get(host, ()):
                config_value = default_section.get(config_key, "")
                if not config_value or (
                    isinstance(config_value, str) and not config_value.strip()
                ):
                    errors.append(
                        f"Image host '{host}' requires config setting '{config_key}' but it is not set"
                    )

    is_valid = len(errors) == 0
    return is_valid, errors, warnings


def _required_default_errors(default: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key, expected_type in REQUIRED_DEFAULT_KEYS.items():
        if key not in default:
            errors.append(f"Missing required key in DEFAULT section: '{key}'")
            continue
        if not isinstance(default[key], expected_type):
            errors.append(
                f"DEFAULT['{key}'] must be {expected_type.__name__}, got {type(default[key]).__name__}"
            )
    return errors


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _tmdb_access_errors(default: dict[str, Any]) -> list[str]:
    if _nonempty_string(default.get("tmdb_api", "")):
        return []
    if _nonempty_string(default.get("tmdb_access_token", "")):
        return []
    return [
        "Configure DEFAULT['tmdb_api'] or DEFAULT['tmdb_access_token'] for TMDb access"
    ]


def _type_warning(
    section: str,
    key: str,
    value: Any,
    expected_types: tuple[type, ...],
) -> ConfigValidationWarning:
    expected = " or ".join(item.__name__ for item in expected_types)
    return ConfigValidationWarning(
        f"Expected type {expected}, got {type(value).__name__}",
        key=key,
        section=section,
    )


def _known_type_warnings(
    values: dict[str, Any],
    expected_types: dict[str, tuple[type, ...]],
    section: str,
) -> list[ConfigValidationWarning]:
    warnings: list[ConfigValidationWarning] = []
    for key, types in expected_types.items():
        if key not in values or values[key] is None:
            continue
        value = values[key]
        if not isinstance(value, types):
            warnings.append(_type_warning(section, key, value, types))
    return warnings


def _upload_order_warnings(
    default: dict[str, Any],
) -> list[ConfigValidationWarning]:
    value = default.get("upload_order")
    if not isinstance(value, str):
        return []
    if value.strip().lower() in {"concurrent", "usenet", "tracker"}:
        return []
    return [
        ConfigValidationWarning(
            f"Invalid value '{value}' for upload_order. Must be one of: 'concurrent', 'usenet', 'tracker'",
            key="upload_order",
            section="DEFAULT",
        )
    ]


def _deprecated_default_warnings(
    default: dict[str, Any],
) -> list[ConfigValidationWarning]:
    if "embed_dupe_links" not in default:
        return []
    return [
        ConfigValidationWarning(
            "Deprecated; rename to 'embed_links'.",
            key="embed_dupe_links",
            section="DEFAULT",
        )
    ]


def _configured_image_host(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value if value else None


def _image_host_warning(
    key: str, value: Any
) -> ConfigValidationWarning | None:
    host = _configured_image_host(value)
    if host is None or host in VALID_IMAGE_HOSTS:
        return None
    valid_hosts = ", ".join(host for host in VALID_IMAGE_HOSTS if host)
    return ConfigValidationWarning(
        f"Unknown image host '{host}'. Valid hosts: {valid_hosts}",
        key=key,
        section="DEFAULT",
    )


def _default_image_host_warnings(
    default: dict[str, Any],
) -> list[ConfigValidationWarning]:
    warnings: list[ConfigValidationWarning] = []
    for index in range(1, MAX_IMAGE_HOST_SLOTS + 1):
        key = f"img_host_{index}"
        if key not in default:
            continue
        warning = _image_host_warning(key, default[key])
        if warning is not None:
            warnings.append(warning)
    return warnings


def _numeric_parser_for_default_key(key: str) -> type[int] | type[float]:
    if key == "xxx_contact_sheet_animation_seconds":
        return float
    return int


def _numeric_expected_label(key: str) -> str:
    if key == "xxx_contact_sheet_animation_seconds":
        return "number"
    return "integer"


def _numeric_default_warning(
    key: str, value: Any
) -> ConfigValidationWarning | None:
    if not isinstance(value, str):
        return None
    try:
        _numeric_parser_for_default_key(key)(value)
    except ValueError:
        return ConfigValidationWarning(
            f"Cannot parse '{value}' as {_numeric_expected_label(key)}",
            key=key,
            section="DEFAULT",
        )
    return None


def _default_numeric_warnings(
    default: dict[str, Any],
) -> list[ConfigValidationWarning]:
    warnings: list[ConfigValidationWarning] = []
    for key in DEFAULT_NUMERIC_STRING_KEYS:
        if key not in default:
            continue
        warning = _numeric_default_warning(key, default[key])
        if warning is not None:
            warnings.append(warning)
    return warnings


def _image_upload_concurrency_warnings(
    default: dict[str, Any],
) -> list[ConfigValidationWarning]:
    value = default.get("image_upload_concurrency")
    if value is None:
        return []
    try:
        parsed = int(value)
    except OverflowError, TypeError, ValueError:
        return [
            ConfigValidationWarning(
                f"Cannot parse '{value}' as integer",
                key="image_upload_concurrency",
                section="DEFAULT",
            )
        ]
    if parsed >= 0:
        return []
    return [
        ConfigValidationWarning(
            "Value must be >= 0",
            key="image_upload_concurrency",
            section="DEFAULT",
        )
    ]


def _image_upload_delay_warnings(
    default: dict[str, Any],
) -> list[ConfigValidationWarning]:
    value = default.get("image_upload_delay")
    if value is None:
        return []
    try:
        parsed = float(value)
    except TypeError, ValueError:
        return [
            ConfigValidationWarning(
                f"Cannot parse '{value}' as number",
                key="image_upload_delay",
                section="DEFAULT",
            )
        ]
    if math.isfinite(parsed) and parsed >= 0:
        return []
    return [
        ConfigValidationWarning(
            "Value must be finite and >= 0",
            key="image_upload_delay",
            section="DEFAULT",
        )
    ]


def _validate_default_section(
    default: dict[str, Any],
) -> tuple[list[str], list[ConfigValidationWarning]]:
    """Validate the DEFAULT config section."""
    errors = _required_default_errors(default)
    errors.extend(_tmdb_access_errors(default))
    warnings = _known_type_warnings(default, DEFAULT_KEY_TYPES, "DEFAULT")
    warnings.extend(_upload_order_warnings(default))
    warnings.extend(_deprecated_default_warnings(default))
    warnings.extend(_default_image_host_warnings(default))
    warnings.extend(_default_numeric_warnings(default))
    warnings.extend(_image_upload_concurrency_warnings(default))
    warnings.extend(_image_upload_delay_warnings(default))
    return errors, warnings


def _missing_default_trackers_warning(
    trackers: dict[str, Any],
) -> list[ConfigValidationWarning]:
    if "default_trackers" in trackers:
        return []
    return [
        ConfigValidationWarning(
            "No 'default_trackers' defined - you'll need to specify trackers via command line",
            key="default_trackers",
            section="TRACKERS",
        )
    ]


def _drunkenslug_errors(
    trackers: dict[str, Any], active_set: set[str]
) -> list[str]:
    configured = {name.upper() for name in trackers}
    if "DRUNKENSLUG" not in active_set or "DRUNKENSLUG" in configured:
        return []
    return [
        "Missing config section for active tracker: 'DRUNKENSLUG' under 'TRACKERS'"
    ]


def _tracker_config_type_warning(
    tracker_name: str, config: Any
) -> ConfigValidationWarning:
    return ConfigValidationWarning(
        f"Tracker config must be a dictionary, got {type(config).__name__}",
        key=tracker_name,
        section="TRACKERS",
    )


def _tracker_api_key_warnings(
    tracker_name: str, config: dict[str, Any]
) -> list[ConfigValidationWarning]:
    value = config.get("api_key")
    if not isinstance(value, str) or not value or value.strip():
        return []
    return [
        ConfigValidationWarning(
            "api_key is whitespace-only",
            key=tracker_name,
            section="TRACKERS",
        )
    ]


def _placeholder_announce_url(config: dict[str, Any]) -> str | None:
    value = config.get("announce_url")
    if not isinstance(value, str):
        return None
    if not value or "<" not in value or ">" not in value:
        return None
    return value


def _announce_placeholder_errors(
    tracker_name: str,
    config: dict[str, Any],
    active_set: set[str],
) -> list[str]:
    if tracker_name.upper() not in active_set:
        return []
    if _placeholder_announce_url(config) is None:
        return []
    return [
        f"[TRACKERS][{tracker_name}] announce_url contains placeholder (e.g., <PASSKEY>) - replace with actual value"
    ]


def _tracker_bool_warning(
    tracker_name: str, field: str, value: Any
) -> ConfigValidationWarning | None:
    if isinstance(value, bool):
        return None
    return ConfigValidationWarning(
        f"'{field}' must be a boolean type (True/False), got {type(value).__name__}: {value!r}",
        key=tracker_name,
        section="TRACKERS",
    )


def _tracker_bool_warnings(
    tracker_name: str, config: dict[str, Any]
) -> list[ConfigValidationWarning]:
    warnings: list[ConfigValidationWarning] = []
    for field in TRACKER_BOOL_FIELDS:
        if field not in config:
            continue
        warning = _tracker_bool_warning(tracker_name, field, config[field])
        if warning is not None:
            warnings.append(warning)
    return warnings


def _validate_tracker_config(
    tracker_name: str,
    config: Any,
    active_set: set[str],
) -> tuple[list[str], list[ConfigValidationWarning]]:
    if not isinstance(config, dict):
        return [], [_tracker_config_type_warning(tracker_name, config)]
    typed = cast(dict[str, Any], config)
    errors = _announce_placeholder_errors(tracker_name, typed, active_set)
    warnings = _tracker_api_key_warnings(tracker_name, typed)
    warnings.extend(_tracker_bool_warnings(tracker_name, typed))
    return errors, warnings


def _validate_trackers_section(
    trackers: dict[str, Any], active_trackers: list[str]
) -> tuple[list[str], list[ConfigValidationWarning]]:
    """Validate the TRACKERS config section."""
    active_set = {tracker.upper() for tracker in active_trackers}
    errors = _drunkenslug_errors(trackers, active_set)
    warnings = _missing_default_trackers_warning(trackers)
    for tracker_name, tracker_config in trackers.items():
        if tracker_name == "default_trackers":
            continue
        item_errors, item_warnings = _validate_tracker_config(
            tracker_name, tracker_config, active_set
        )
        errors.extend(item_errors)
        warnings.extend(item_warnings)
    return errors, warnings


def _client_config_type_warning(
    client_name: str, config: Any
) -> ConfigValidationWarning:
    return ConfigValidationWarning(
        f"Client config must be a dictionary, got {type(config).__name__}",
        key=client_name,
        section="TORRENT_CLIENTS",
    )


def _client_type_warnings(
    client_name: str, config: dict[str, Any]
) -> list[ConfigValidationWarning]:
    client_type = config.get("torrent_client", "")
    if not client_type or client_type in VALID_TORRENT_CLIENTS:
        return []
    return [
        ConfigValidationWarning(
            f"Unknown torrent_client type '{client_type}'",
            key=client_name,
            section="TORRENT_CLIENTS",
        )
    ]


def _client_linking_warnings(
    client_name: str, config: dict[str, Any]
) -> list[ConfigValidationWarning]:
    linking = config.get("linking", "")
    if not linking or linking in ("symlink", "hardlink", ""):
        return []
    return [
        ConfigValidationWarning(
            f"Invalid linking option '{linking}'. Use 'symlink', 'hardlink', or empty string",
            key=client_name,
            section="TORRENT_CLIENTS",
        )
    ]


def _client_path_lists(
    config: dict[str, Any],
) -> tuple[list[Any], list[Any]] | None:
    local = config.get("local_path", [])
    remote = config.get("remote_path", [])
    if not isinstance(local, list) or not isinstance(remote, list):
        return None
    return cast(list[Any], local), cast(list[Any], remote)


def _client_path_warnings(
    client_name: str, config: dict[str, Any]
) -> list[ConfigValidationWarning]:
    paths = _client_path_lists(config)
    if paths is None:
        return []
    local, remote = paths
    if not local or not remote or len(local) == len(remote):
        return []
    return [
        ConfigValidationWarning(
            f"local_path ({len(local)} items) and remote_path ({len(remote)} items) should have matching lengths",
            key=client_name,
            section="TORRENT_CLIENTS",
        )
    ]


def _validate_torrent_client(
    client_name: str, config: Any
) -> list[ConfigValidationWarning]:
    if not isinstance(config, dict):
        return [_client_config_type_warning(client_name, config)]
    typed = cast(dict[str, Any], config)
    warnings = _client_type_warnings(client_name, typed)
    warnings.extend(_client_linking_warnings(client_name, typed))
    warnings.extend(_client_path_warnings(client_name, typed))
    return warnings


def _validate_torrent_clients_section(
    clients: dict[str, Any],
) -> tuple[list[str], list[ConfigValidationWarning]]:
    """Validate the TORRENT_CLIENTS config section."""
    warnings: list[ConfigValidationWarning] = []
    for client_name, client_config in clients.items():
        warnings.extend(_validate_torrent_client(client_name, client_config))
    return [], warnings


def _required_usenet_errors(usenet: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in USENET_REQUIRED_KEYS:
        value = usenet.get(key)
        if not value or (isinstance(value, str) and not value.strip()):
            errors.append(
                f"[USENET] is active but '{key}' is empty or not configured"
            )
    return errors


def _usenet_numeric_warning(
    key: str, value: Any
) -> ConfigValidationWarning | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        int(value)
    except ValueError:
        return ConfigValidationWarning(
            f"Cannot parse '{value}' as integer",
            key=key,
            section="USENET",
        )
    return None


def _usenet_numeric_warnings(
    usenet: dict[str, Any],
) -> list[ConfigValidationWarning]:
    warnings: list[ConfigValidationWarning] = []
    for key in USENET_NUMERIC_STRING_KEYS:
        if key not in usenet:
            continue
        warning = _usenet_numeric_warning(key, usenet[key])
        if warning is not None:
            warnings.append(warning)
    return warnings


def _validate_usenet_section(
    usenet: dict[str, Any], is_usenet_active: bool = False
) -> tuple[list[str], list[ConfigValidationWarning]]:
    """Validate the USENET config section."""
    errors = _required_usenet_errors(usenet) if is_usenet_active else []
    warnings = _known_type_warnings(usenet, USENET_KEY_TYPES, "USENET")
    warnings.extend(_usenet_numeric_warnings(usenet))
    return errors, warnings


def _warning_location(section: str, keys_str: str) -> str:
    if section and keys_str:
        return f"[{section}][{keys_str}]"
    if section:
        return f"[{section}]"
    if keys_str:
        return f"[{keys_str}]"
    return ""


def _grouped_warning_text(section: str, message: str, keys: list[str]) -> str:
    keys_str = ", ".join(key for key in keys if key)
    location = _warning_location(section, keys_str)
    return f"{location} {message}" if location else message


def group_warnings(warnings: list[ConfigValidationWarning]) -> list[str]:
    """Group warnings with the same section and message, combining keys."""
    from collections import defaultdict

    grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
    for warning in warnings:
        grouped[(warning.section, warning.message)].append(warning.key or "")
    return [
        _grouped_warning_text(section, message, keys)
        for (section, message), keys in grouped.items()
    ]


def _append_error_lines(lines: list[str], errors: list[str]) -> None:
    if not errors:
        return
    lines.append("Config Validation Errors:")
    lines.extend(f"  ✗ {error}" for error in errors)


def _append_warning_lines(
    lines: list[str],
    warnings: list[ConfigValidationWarning],
    show_warnings: bool,
) -> None:
    if not show_warnings or not warnings:
        return
    if lines:
        lines.append("")
    lines.append("Config Validation Warnings:")
    lines.extend(f"  ⚠ {warning}" for warning in group_warnings(warnings))


def _append_validation_summary(
    lines: list[str], is_valid: bool, warnings: list[ConfigValidationWarning]
) -> None:
    if not is_valid:
        return
    if not warnings:
        lines.append("Config validation passed.")
        return
    lines.append(
        f"\nConfig validation passed with {len(warnings)} warning(s)."
    )


def format_validation_results(
    is_valid: bool,
    errors: list[str],
    warnings: list[ConfigValidationWarning],
    show_warnings: bool = True,
) -> str:
    """Format validation results for display."""
    lines: list[str] = []
    _append_error_lines(lines, errors)
    _append_warning_lines(lines, warnings, show_warnings)
    _append_validation_summary(lines, is_valid, warnings)
    return "\n".join(lines)
