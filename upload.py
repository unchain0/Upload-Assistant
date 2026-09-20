#!/usr/bin/env python3
# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import ast
import asyncio
import contextlib
import gc
import inspect
import json
import logging
import os
import platform
import re
import shlex
import shutil
import signal
import sys
import time
import traceback
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from urllib.parse import urljoin, urlparse

import aiofiles
import cli_ui  # pyright: ignore[reportMissingImports]
import requests
from rich.markup import escape
from torf import (
    Torrent as _Torrent,  # pyright: ignore[reportMissingImports,reportUnknownVariableType]
)

from src.bootstrap import build_configuration_service
from src.delivery.cli.arguments import (
    Args,
    partition_existing_paths,
    read_paths_from_stdin,
)
from src.domain_models import application_version
from src.domain_models.book_language import (
    is_valid_book_language,
    resolve_book_language,
)
from src.domain_models.errors import (
    AmbiguousMetadataError,
    ConfigurationError,
    NoWorkAvailableError,
    OperationAbortedError,
)
from src.domain_models.processing import ItemProcessingError
from src.domain_models.tracker_image_policy import (
    configured_screenshot_minimum,
    screenshot_requirement_error,
)
from src.integrations.filesystem.cleanup import cleanup_manager
from src.integrations.filesystem.paths import CODE_DIR, STATE_DIR
from src.integrations.filesystem.temp_paths import (
    artwork_dir,
    ensure_temp_root,
    music_release_snapshot_path,
    screenshots_dir,
)
from src.integrations.image_hosts.fallback import configured_image_hosts
from src.integrations.image_hosts.rehosting import check_tracker_image_hosts
from src.integrations.image_hosts.uploader import UploadScreensManager
from src.integrations.media.artwork import (
    is_public_http_url,
    is_valid_cover_image,
)
from src.integrations.media.audio_spectrogram import process_audio_spectrograms
from src.integrations.media.disc_menus import process_disc_menus
from src.integrations.media.dynamic_hdr_plot import (
    dynamic_hdr_plot_enabled,
    process_dynamic_hdr_plots,
)
from src.integrations.media.media_info import ensure_mediainfo_binary
from src.integrations.media.screenshot_capture import (
    TakeScreensManager,
    download_artwork_from_meta,
)
from src.integrations.observability.console import (
    configure_console,
    current_release_log_path,
    logger,
)  # pyright: ignore[reportUnknownVariableType]
from src.integrations.observability.console import (
    rich_handler as _rich_handler,
)
from src.integrations.runtime_tools.configured_binaries import (
    configure_binary_paths,
    configured_binary,
)
from src.integrations.runtime_tools.ffmpeg import FfmpegBinaryManager
from src.integrations.runtime_tools.mkbrr import MkbrrBinaryManager
from src.integrations.security.redaction import PathAwareEncoder, Redaction
from src.integrations.torrent.torrent_creator import TorrentCreator
from src.integrations.torrent_clients.bandwidth import Wait
from src.integrations.torrent_clients.client_manager import Clients
from src.integrations.trackers.alpharatio import AlphaRatio
from src.integrations.trackers.common import Common
from src.integrations.trackers.description_builder import gen_desc
from src.integrations.trackers.passthepopcorn import PassThePopcorn
from src.integrations.trackers.registry import (
    TrackerSetup,
    api_trackers,
    http_trackers,
    other_api_trackers,
    tracker_class_map,
)
from src.services.book_input_service import detect_newspaper
from src.services.book_preparation import missing_book_fields
from src.services.comparison_service import ComparisonManager
from src.services.duplicate_check_service import DupeChecker
from src.services.early_artifact_service import (
    cancel_and_drain_early_artifact_tasks,
    get_early_artifact_tasks,
    start_early_artifact_tasks,
)
from src.services.early_artifact_service import (
    is_usenet_only as _is_usenet_only,
)
from src.services.episode_service import sync_single_episode_from_filename
from src.services.queue_service import QueueManager
from src.services.release_naming_service import NameManager
from src.services.tracker_metadata_service import TrackerDataManager
from src.services.tracker_status_service import TrackerStatusManager
from src.services.tracker_upload_service import process_trackers
from src.services.upload_decision_service import UploadHelper

# Runtime artifacts are user-owned; CODE_DIR remains the read-only checkout.
base_dir = str(STATE_DIR)
CLI_UI: Any = cli_ui
TORF_Torrent: Any = cast(Any, _Torrent)
RICH_HANDLER: Any = cast(Any, _rich_handler)
TORRENT_CREATOR: Any = cast(Any, TorrentCreator)
CLI_UI.setup(color="always", title="Upload Assistant")


def _parse_version_tuple(value: str) -> tuple[int, ...]:
    """Parse a dotted version string into a tuple for comparison."""
    cleaned = value.strip().lstrip("vV")
    parts: list[int] = []
    for part in cleaned.split("."):
        if not part.isdigit():
            break
        parts.append(int(part))
    return tuple(parts)


# Global state for graceful CLI shutdown. Tests may reset this between in-process runs.
_shutdown_requested = False


def _reset_shutdown_state() -> None:
    """Reset shutdown state for a clean in-process CLI run."""
    global _shutdown_requested
    _shutdown_requested = False


def _handle_shutdown_signal(signum: int, _frame: Any) -> None:
    """Cancel the active CLI run on SIGTERM or SIGINT."""
    global _shutdown_requested
    signal_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
    if _shutdown_requested:
        logger.info("[red]Forced exit[/red]")
        raise SystemExit(1)
    _shutdown_requested = True
    logger.info(
        f"\n[yellow]Received {signal_name}, shutting down gracefully...[/yellow]"
    )
    raise KeyboardInterrupt


# ── Restore built-in data/ files when a Docker volume mount hides them ──
# The Dockerfile copies the original data/ tree to defaults/data/ so that
# volume mounts over /Upload-Assistant/data/ don't lose critical files
# (__init__.py, example_config.py, templates/).
_data_dir = Path(base_dir) / "data"
_defaults_data_dir = CODE_DIR / "data"

# Directories that should never be copied into user-facing data/
_SKIP_DIRS = {"__pycache__", ".mypy_cache", ".ruff_cache"}

if Path(_defaults_data_dir).is_dir():
    Path(_data_dir).mkdir(parents=True, exist_ok=True)
    _restored_count = 0
    _restore_errors: list[str] = []
    # Walk the defaults tree and copy anything missing in the live data dir.
    # Never overwrite user files (config.py, cookies/, tags.json, etc.).
    for dirpath, dirnames, filenames in os.walk(_defaults_data_dir):
        # Prune unwanted directories in-place so os.walk skips them entirely
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]

        rel_dir = os.path.relpath(dirpath, _defaults_data_dir)
        target_dir = Path(_data_dir) / rel_dir if rel_dir != "." else _data_dir
        try:
            Path(target_dir).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            _restore_errors.append(f"mkdir {rel_dir}: {exc}")
            continue  # skip this subtree if we can't create the directory
        for fname in filenames:
            # Skip bytecode and cache files
            if fname.endswith((".pyc", ".pyo")):
                continue
            target_file = Path(target_dir) / fname
            src_file = Path(dirpath) / fname
            if not Path(target_file).exists():
                try:
                    shutil.copy2(src_file, target_file)
                    _restored_count += 1
                except OSError as exc:
                    _restore_errors.append(f"{Path(rel_dir) / fname}: {exc}")
    if _restored_count:
        logger.info(
            f"Restored {_restored_count} built-in file(s) into data/ from defaults.",
            extra={"markup": False},
        )
    if _restore_errors:
        logger.warning(
            f"[red]Warning: failed to restore {len(_restore_errors)} file(s) into data/:[/red]"
        )
        for _err in _restore_errors[:5]:
            logger.info(f"[red]  {_err}[/red]")
        if len(_restore_errors) > 5:
            logger.info(
                f"[red]  ... and {len(_restore_errors) - 5} more[/red]"
            )
        logger.info(
            "[yellow]Hint: ensure the mounted data/ directory is writable by the container user.[/yellow]"
        )
        logger.info(
            "[yellow]  e.g. on the host: chown -R 1000:1000 /path/to/data[/yellow]"
        )

from src.domain_models.release import Meta
from src.services.book_input_service import (
    sanitize_book_author,
    sanitize_book_language,
)
from src.services.preparation_service import Prep

# Enable ANSI colors on Windows
_use_colors = True
if sys.platform == "win32":
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        # Enable VIRTUAL_TERMINAL_PROCESSING
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        _use_colors = False

# Color codes (empty strings if colors not supported)
_RED = "\033[91m" if _use_colors else ""
_YELLOW = "\033[93m" if _use_colors else ""
_GREEN = "\033[92m" if _use_colors else ""
_RESET = "\033[0m" if _use_colors else ""


def _print_config_error(
    error_type: str,
    message: str,
    lineno: int | None = None,
    text: str | None = None,
    offset: int | None = None,
    suggestion: str | None = None,
) -> None:
    """Print a formatted config error message."""
    logger.info(
        f"{_RED}{error_type} in config.py:{_RESET}", extra={"markup": False}
    )
    if lineno:
        logger.info(
            f"{_RED}  Line {lineno}: {message}{_RESET}",
            extra={"markup": False},
        )
        if text:
            logger.info(
                f"{_YELLOW}    {text.rstrip()}{_RESET}",
                extra={"markup": False},
            )
            if offset:
                logger.info(
                    f"{_YELLOW}    {' ' * (offset - 1)}^{_RESET}",
                    extra={"markup": False},
                )
    else:
        logger.info(f"{_RED}  {message}{_RESET}", extra={"markup": False})
    if suggestion:
        logger.info(
            f"{_GREEN}  Suggestion: {suggestion}{_RESET}",
            extra={"markup": False},
        )
    logger.info(
        f"\n{_RED}Reference: https://github.com/wastaken7/Upload-Assistant/blob/master/data/example_config.py{_RESET}",
        extra={"markup": False},
    )


config: dict[str, Any]

_CONFIGURATION_SERVICE = build_configuration_service()

try:
    _configuration_snapshot = _CONFIGURATION_SERVICE.load()
    _config_path = Path(_configuration_snapshot.source.path)
    config = cast(dict[str, Any], _configuration_snapshot.mutable_copy())
    default_runtime_config = cast(dict[str, Any], config.get("DEFAULT", {}))
    configure_console(default_runtime_config)
    configure_binary_paths(default_runtime_config)
    parser: Any = Args(config)
    client = Clients(config)
    name_manager = NameManager(config)
    tracker_data_manager = TrackerDataManager(config)
    takescreens_manager = TakeScreensManager(config)
    uploadscreens_manager = UploadScreensManager(config)
except ConfigurationError as error:
    _print_config_error("Configuration error", str(error))
    sys.exit(1)
except Exception as error:
    _print_config_error("Error", str(error))
    sys.exit(1)


def _meta_overwrite_fields() -> frozenset[str]:
    return frozenset(
        {
            "anon",
            "asin",
            "audiobook_bitrate",
            "audiobook_duration_formatted",
            "audiobook_duration",
            "author",
            "book_asin",
            "book_author",
            "book_isbn",
            "book_language_iso",
            "book_language",
            "book_publisher",
            "book_title",
            "category",
            "client",
            "comic",
            "debug",
            "desc",
            "description_file",
            "description_link",
            "draft",
            "dual_audio",
            "dupe",
            "freeleech",
            "game_region",
            "game_subcategory",
            "game_system",
            "game_version",
            "hardcoded_subs",
            "igdb_manual",
            "imdb",
            "imghost",
            "isbn",
            "keywords",
            "magazine",
            "mal",
            "manga",
            "manual_edition",
            "manual_episode",
            "manual_platform",
            "manual_season",
            "manual_source",
            "manual_type",
            "manual_year",
            "manual",
            "modq",
            "narrator",
            "newspaper",
            "no_aka",
            "no_dub",
            "no_season",
            "no_seed",
            "no_tag",
            "no_year",
            "nohash",
            "openlibrary",
            "personalrelease",
            "platform",
            "qbit_cat",
            "qbit_tag",
            "region",
            "screens",
            "skip_imghost_upload",
            "steam_manual",
            "title",
            "tmdb_manual",
            "torrent_creation",
            "trackers",
            "tvmaze_manual",
            "type",
            "unattended",
            "webdv",
            "year",
        }
    )


def _clean_saved_meta_key(key: Any) -> str:
    return str(key).strip().strip("'").strip('"')


def _current_meta_override(meta: Meta, key: str) -> Any:
    if key not in meta:
        return None
    return getattr(meta, key, None)


def _saved_meta_value(
    meta: Meta,
    key: str,
    value: Any,
    overwrite_fields: frozenset[str],
) -> Any:
    if key == "tracker_ids":
        return meta.tracker_ids or value
    if key not in overwrite_fields:
        return value
    current = _current_meta_override(meta, key)
    if current is None:
        return value
    logger.debug(f"Overriding {key} with meta value: {current}")
    return current


def _sanitized_saved_meta(
    meta: Meta, saved_meta: dict[str, Any]
) -> dict[str, Any]:
    overwrite_fields = _meta_overwrite_fields()
    return {
        clean_key: _saved_meta_value(meta, clean_key, value, overwrite_fields)
        for key, value in saved_meta.items()
        if (clean_key := _clean_saved_meta_key(key))
    }


def _apply_saved_tracker_ids(meta: Meta, sanitized: dict[str, Any]) -> None:
    tracker_ids = sanitized.pop("tracker_ids", None)
    meta.update(sanitized)
    if not isinstance(tracker_ids, dict):
        return
    meta.set_tracker_ids(cast(dict[str, Any], tracker_ids))
    sanitized["tracker_ids"] = dict(meta.tracker_ids)


async def merge_meta(meta: Meta, saved_meta: dict[str, Any]) -> dict[str, Any]:
    """Merges saved metadata with the current meta, respecting overwrite rules."""
    sanitized = _sanitized_saved_meta(meta, saved_meta)
    _apply_saved_tracker_ids(meta, sanitized)
    sanitize_book_language(meta)
    sanitize_book_author(meta)
    return sanitized


async def print_progress(message: str, interval: int = 10) -> None:
    """Prints a progress message every `interval` seconds until cancelled."""
    try:
        while True:
            await asyncio.sleep(interval)
            logger.info(message)
    except asyncio.CancelledError:
        pass


def update_oeimg_to_onlyimage() -> None:
    """Update all img_host_* values from 'oeimg' to 'onlyimage' in the config file."""
    config_path = f"{base_dir}/data/config.py"
    with Path(config_path).open(encoding="utf-8") as f:
        content = f.read()

    new_content = re.sub(
        r"(['\"]img_host_\d+['\"]\s*:\s*)['\"]oeimg['\"]",
        r"\1'onlyimage'",
        content,
    )
    new_content = re.sub(
        r"(['\"])(oeimg_api)(['\"]\s*:)", r"\1onlyimage_api\3", new_content
    )

    if new_content != content:
        with Path(config_path).open("w", encoding="utf-8") as f:
            f.write(new_content)
        logger.info(
            "[green]Updated 'oeimg' to 'onlyimage' and 'oeimg_api' to 'onlyimage_api' in config.py[/green]"
        )
    else:
        logger.info(
            "[yellow]No 'oeimg' or 'oeimg_api' found to update in config.py[/yellow]"
        )


def _tracker_status_map(meta: Meta) -> dict[str, dict[str, Any]]:
    raw = meta.tracker_status
    if isinstance(raw, dict):
        return cast(dict[str, dict[str, Any]], raw)
    status: dict[str, dict[str, Any]] = {}
    meta.tracker_status = status
    return status


def _tracker_name_values(trackers: list[str] | str | None) -> list[str]:
    if not trackers:
        return []
    return [trackers] if isinstance(trackers, str) else list(trackers)


def _http_login_trackers(values: list[str]) -> list[str]:
    return [
        tracker
        for tracker in values
        if tracker in tracker_class_map and tracker in http_trackers
    ]


def _special_login_trackers(
    values: list[str], selected: list[str]
) -> list[str]:
    return [
        tracker
        for tracker in ("RETROFLIX", "PASSTHEPOPCORN")
        if tracker in values and tracker not in selected
    ]


def _trackers_for_login_validation(
    trackers: list[str] | str | None,
) -> list[str]:
    values = _tracker_name_values(trackers)
    selected = _http_login_trackers(values)
    return [*selected, *_special_login_trackers(values, selected)]


async def _tracker_login_result(tracker_name: str, meta: Meta) -> bool:
    tracker_class = tracker_class_map[tracker_name](config=config)
    logger.debug(f"[cyan]Validating {tracker_name} credentials...[/cyan]")
    if tracker_name == "RETROFLIX":
        return bool(await tracker_class.api_test(meta))
    if tracker_name == "PASSTHEPOPCORN":
        return bool(await tracker_class.get_anti_csrf_token(meta))
    return bool(await tracker_class.validate_credentials(meta))


async def _validate_single_tracker_login(
    tracker_name: str, meta: Meta
) -> tuple[str, bool]:
    status_dict = _tracker_status_map(meta)
    status_dict.setdefault(tracker_name, {})
    try:
        login = await _tracker_login_result(tracker_name, meta)
    except Exception as error:
        logger.error(f"[red]Error validating {tracker_name}: {error}[/red]")
        status_dict[tracker_name]["skipped"] = True
        return tracker_name, False
    if not login:
        status_dict[tracker_name]["skipped"] = True
    return tracker_name, login


async def validate_tracker_logins(
    meta: Meta, trackers: list[str] | str | None = None
) -> None:
    _tracker_status_map(meta)
    valid_trackers = _trackers_for_login_validation(trackers)
    if not valid_trackers:
        return
    await asyncio.gather(
        *(
            _validate_single_tracker_login(tracker, meta)
            for tracker in valid_trackers
        )
    )


def _book_prompt_missing_fields(meta: Meta) -> list[str]:
    missing = list(missing_book_fields(meta))
    has_artwork = bool(
        is_valid_cover_image(meta.artwork_path)
        or _is_http_url(meta.artwork_url)
    )
    if not has_artwork:
        missing.append("artwork")
    return missing


def _book_prompt_label(field: str) -> str:
    labels = {
        "book_language": "language",
        "artwork": "cover artwork (path to image file or URL)",
    }
    return labels.get(field, field)


def _prompt_book_language(meta: Meta) -> bool:
    while True:
        value = str(
            CLI_UI.ask_string("Enter language (leave blank to skip): ") or ""
        ).strip()
        if not value:
            return False
        full, iso = resolve_book_language(value)
        if is_valid_book_language(full, iso):
            meta.book_language = full
            meta.book_language_iso = iso
            return True
        logger.info("[red]Invalid language. Please try again.[/red]")


def _prompt_book_identifier(meta: Meta) -> bool:
    value = str(
        CLI_UI.ask_string("Enter ISBN or ASIN (leave blank to skip): ") or ""
    ).strip()
    if not value:
        return False
    from src.integrations.media.book_extractors import validate_isbn_checksum

    validated_isbn = validate_isbn_checksum(value)
    if validated_isbn:
        meta.isbn = validated_isbn
        return False
    if re.fullmatch(r"[A-Z0-9]{10}", value.upper()):
        meta.asin = value.upper()
        return False
    logger.info("[red]Invalid ISBN or ASIN. Skipping identifier.[/red]")
    return False


def _book_year_input_result(meta: Meta, value: str) -> tuple[bool, bool]:
    if not value:
        return False, True
    if _valid_music_year(value):
        meta.year = int(value)
        meta.search_year = value
        return True, True
    logger.info(
        "[red]Invalid year (must be a 4-digit number between 1000 and 3000). Please try again.[/red]"
    )
    return False, False


def _prompt_book_year(meta: Meta) -> bool:
    while True:
        value = str(
            CLI_UI.ask_string("Enter year (leave blank to skip): ") or ""
        ).strip()
        changed, done = _book_year_input_result(meta, value)
        if done:
            return changed


def _book_artwork_input_result(meta: Meta, value: str) -> bool:
    if _is_http_url(value):
        meta.artwork_url = value
        return True
    path_obj = Path(value).expanduser()
    if path_obj.is_file():
        meta.artwork_path = str(path_obj.resolve())
        return True
    return False


def _prompt_book_artwork(meta: Meta) -> bool:
    prompt = (
        "Enter path to cover artwork image (or public image URL) for BOOK: "
    )
    while True:
        value = str(CLI_UI.ask_string(prompt) or "").strip()
        if not value:
            logger.info(
                "[red]Artwork is required for BOOK uploads. Please enter a valid file path or image URL.[/red]"
            )
            continue
        if _book_artwork_input_result(meta, value):
            return False
        logger.info(
            "[red]Invalid artwork path or URL. The file does not exist or URL is invalid. Please try again.[/red]"
        )


def _prompt_book_text(meta: Meta, field: str) -> bool:
    label = _book_prompt_label(field)
    value = str(
        CLI_UI.ask_string(f"Enter {label} (leave blank to skip): ") or ""
    ).strip()
    if not value:
        return False
    meta[field] = value
    return True


def _prompt_book_field(meta: Meta, field: str) -> bool:
    handlers = {
        "book_language": _prompt_book_language,
        "isbn_or_asin": _prompt_book_identifier,
        "year": _prompt_book_year,
        "artwork": _prompt_book_artwork,
    }
    handler = handlers.get(field)
    return (
        handler(meta)
        if handler is not None
        else _prompt_book_text(meta, field)
    )


def _run_book_prompts(meta: Meta, missing: list[str]) -> bool:
    name_needs_rebuild = False
    try:
        for field in missing:
            name_needs_rebuild = (
                _prompt_book_field(meta, field) or name_needs_rebuild
            )
    except EOFError:
        logger.info(
            "[yellow]Input cancelled — continuing with missing book fields.[/yellow]"
        )
        return False
    return name_needs_rebuild


async def _refresh_book_name(meta: Meta) -> None:
    detect_newspaper(meta)
    (
        meta.name_notag,
        meta.name,
        meta.clean_name,
        meta.potential_missing,
    ) = await name_manager.get_name(meta)


async def _prompt_book_meta(meta: Meta) -> None:
    """Prompt attended BOOK uploads for required metadata fields."""
    book_missing = _book_prompt_missing_fields(meta)
    if not book_missing:
        return
    if meta.unattended:
        logger.info(
            f"[yellow]BOOK upload: the following required fields are missing: "
            f"{', '.join(book_missing)}. "
            f"Re-run with -btitle / -author / -year / -blang / --narrator / --publisher / --isbn / --asin / --book-cover to supply them, "
            f"or trackers that require them will be skipped.[/yellow]"
        )
        return
    logger.info(
        "\n[bold yellow]The following fields are required:[/bold yellow]"
    )
    name_needs_rebuild = _run_book_prompts(meta, book_missing)
    sanitize_book_language(meta)
    sanitize_book_author(meta)
    if name_needs_rebuild:
        await _refresh_book_name(meta)


def _game_missing_fields(meta: Meta) -> list[str]:
    from src.services.game_preparation import missing_game_fields

    return list(missing_game_fields(meta))


def _log_missing_game_fields(meta: Meta, game_missing: list[str]) -> None:
    logger.info(
        f"[yellow]{'SOFTWARE' if meta.software else 'GAME'} upload: the following required fields are missing: "
        f"{', '.join(game_missing)}. "
        f"Re-run with appropriate CLI arguments, "
        f"or trackers that require them will be skipped.[/yellow]"
    )


def _game_platform_choices() -> list[str]:
    return [
        "pc",
        "mac",
        "linux",
        "ps5",
        "ps4",
        "ps3",
        "ps2",
        "xbox",
        "x360",
        "xone",
        "xsx",
        "switch",
        "3ds",
        "nds",
        "wiiu",
        "wii",
    ]


def _prompt_game_platform(meta: Meta) -> bool:
    try:
        value = CLI_UI.ask_choice(
            "Select target platform: (can be manually set with -plat / --platform)",
            choices=_game_platform_choices(),
            sort=False,
        )
    except EOFError:
        return False
    if not value:
        return False
    meta.platform = value
    return True


def _prompt_game_version(meta: Meta) -> bool:
    value = str(
        CLI_UI.ask_string(
            "Enter game version (e.g., 1.15) (leave blank to skip): "
        )
        or ""
    ).strip()
    if not value:
        return False
    from src.services.game_preparation import normalize_version

    meta.game_version = normalize_version(value)
    return True


def _game_subcategory_choices() -> tuple[list[str], dict[str, str]]:
    choices = [
        "full_game (Full Game)",
        "full_game_dlc (Full Game + DLC)",
        "dlc (DLC only)",
        "update (Update only)",
    ]
    values = {
        "full_game (Full Game)": "full_game",
        "full_game_dlc (Full Game + DLC)": "full_game_dlc",
        "dlc (DLC only)": "dlc",
        "update (Update only)": "update",
    }
    return choices, values


def _prompt_game_subcategory(meta: Meta) -> bool:
    choices, values = _game_subcategory_choices()
    choice = CLI_UI.ask_choice(
        "Select game subcategory (can be manually set with -gsc / --game-subcategory):",
        choices=choices,
        sort=False,
    )
    meta.game_subcategory = values.get(choice, "full_game")
    return True


def _prompt_game_text(meta: Meta, field: str) -> bool:
    value = str(
        CLI_UI.ask_string(f"Enter {field} (leave blank to skip): ") or ""
    ).strip()
    if not value:
        return False
    meta[field] = value
    return True


def _prompt_game_field(meta: Meta, field: str) -> bool:
    handlers = {
        "year": _prompt_book_year,
        "platform": _prompt_game_platform,
        "game_version": _prompt_game_version,
        "game_subcategory": _prompt_game_subcategory,
    }
    handler = handlers.get(field)
    return (
        handler(meta)
        if handler is not None
        else _prompt_game_text(meta, field)
    )


def _run_game_prompts(meta: Meta, game_missing: list[str]) -> bool:
    name_needs_rebuild = False
    try:
        for field in game_missing:
            name_needs_rebuild = (
                _prompt_game_field(meta, field) or name_needs_rebuild
            )
    except EOFError:
        logger.info(
            "[yellow]Input cancelled — continuing with missing game fields.[/yellow]"
        )
        return False
    return name_needs_rebuild


async def _refresh_game_name(meta: Meta) -> None:
    (
        meta.name_notag,
        meta.name,
        meta.clean_name,
        meta.potential_missing,
    ) = await name_manager.get_name(meta)


def _meta_tracker_names(meta: Meta) -> set[str]:
    raw = meta.trackers
    values = (
        [raw] if isinstance(raw, str) else raw if isinstance(raw, list) else []
    )
    return {str(value).upper() for value in values}


def _bjshare_game_prompt_enabled(meta: Meta) -> bool:
    return "BJSHARE" in _meta_tracker_names(meta) and not meta.unattended


def _bjshare_console_platform(meta: Meta) -> str:
    return str(meta.platform or "").upper().strip()


def _bjshare_console_required(platform: str) -> bool:
    return platform not in {"PC", "MAC", "LINUX", "EMULATOR", ""}


def _bjshare_needs_game_system(platform: str) -> bool:
    return platform in {"PS1", "PS2", "PSP", "WII", "WIIU", "X360"}


def _bjshare_needs_game_region(platform: str) -> bool:
    return platform in {"3DS", "NDS", "PSVITA", "PS1", "PS2", "PS3"}


def _bjshare_needs_container(platform: str) -> bool:
    return platform in {"SWITCH", "X360"}


def _bjshare_system_choices(platform: str) -> list[str]:
    if platform == "PSP":
        return ["FREE", "NTSC", "PAL", "Skip"]
    return ["PAL", "NTSC-U", "NTSC-J", "Skip"]


def _bjshare_container_choices(platform: str) -> list[str]:
    if platform == "X360":
        return ["LT", "JTAG/RGH", "Skip"]
    return ["NSP", "XCI", "NSZ", "XCZ", "Skip"]


def _safe_game_choice(prompt: str, choices: list[str]) -> str:
    try:
        return str(CLI_UI.ask_choice(prompt, choices=choices) or "")
    except EOFError:
        return ""


def _prompt_bjshare_game_system(meta: Meta, platform: str) -> None:
    if not _bjshare_needs_game_system(platform) or meta.game_system:
        return
    choice = _safe_game_choice(
        "BJSHARE: Select game system (TV standard):",
        _bjshare_system_choices(platform),
    )
    if choice and choice != "Skip":
        meta.game_system = choice


def _prompt_bjshare_game_region(meta: Meta, platform: str) -> None:
    if not _bjshare_needs_game_region(platform) or meta.game_region:
        return
    choice = _safe_game_choice(
        "BJSHARE: Select game region:", ["USA", "EUR", "JPN", "Skip"]
    )
    if choice and choice != "Skip":
        meta.game_region = choice


def _accepted_game_choice(choice: str) -> str:
    return choice if choice and choice != "Skip" else ""


def _prompt_bjshare_game_container(meta: Meta, platform: str) -> None:
    if not _bjshare_needs_container(platform):
        return
    choices = _bjshare_container_choices(platform)
    if str(meta.container or "").upper() in choices:
        return
    choice = _accepted_game_choice(
        _safe_game_choice(
            "BJSHARE: Select container format ('Destravamento'):", choices
        )
    )
    if choice:
        meta.container = choice


def _prompt_bjshare_game_meta(meta: Meta) -> None:
    if not _bjshare_game_prompt_enabled(meta):
        return
    platform = _bjshare_console_platform(meta)
    if not _bjshare_console_required(platform):
        return
    _prompt_bjshare_game_system(meta, platform)
    _prompt_bjshare_game_region(meta, platform)
    _prompt_bjshare_game_container(meta, platform)


async def _prompt_missing_game_meta(
    meta: Meta, game_missing: list[str]
) -> bool:
    if not game_missing:
        return False
    if meta.unattended or meta.software:
        _log_missing_game_fields(meta, game_missing)
        return True
    logger.info(
        "\n[bold yellow]The following fields are required:[/bold yellow]"
    )
    if _run_game_prompts(meta, game_missing):
        await _refresh_game_name(meta)
    return False


async def _prompt_game_meta(meta: Meta) -> None:
    """Prompt attended GAME uploads for required metadata and BJSHARE console fields."""
    if await _prompt_missing_game_meta(meta, _game_missing_fields(meta)):
        return
    _prompt_bjshare_game_meta(meta)


MUSIC_REQUIRED_FIELDS = ("artist", "album", "year", "media", "release_type")
MUSIC_MEDIA_CHOICES = (
    "CD",
    "WEB",
    "Vinyl",
    "DVD",
    "BD",
    "Soundboard",
    "SACD",
    "DAT",
    "Cassette",
)
MUSIC_RELEASE_TYPE_CHOICES = (
    "Album",
    "Soundtrack",
    "EP",
    "Anthology",
    "Compilation",
    "Sampler",
    "Single",
    "Demo",
    "Live album",
    "Split",
    "Remix",
    "Bootleg",
    "Interview",
    "Mixtape",
    "Concert recording",
    "DJ Mix",
    "Unknown",
)


def _music_release_mapping(
    meta: Meta, *, create: bool = False
) -> dict[str, Any]:
    raw = meta.music_release
    if isinstance(raw, dict):
        return cast(dict[str, Any], raw)
    if not create:
        return {}
    release: dict[str, Any] = {"fields": {}}
    meta.music_release = release
    return release


def _music_fields_mapping(
    meta: Meta, *, create: bool = False
) -> dict[str, Any]:
    release = _music_release_mapping(meta, create=create)
    raw = release.get("fields", {})
    if isinstance(raw, dict):
        return cast(dict[str, Any], raw)
    if not create:
        return {}
    fields: dict[str, Any] = {}
    release["fields"] = fields
    return fields


def _music_field_entry(meta: Meta, field: str) -> dict[str, Any]:
    raw = _music_fields_mapping(meta).get(field, {})
    return cast(dict[str, Any], raw) if isinstance(raw, dict) else {}


def _music_meta_fallback(meta: Meta, field: str) -> Any:
    attributes: dict[str, str] = {
        "artist": "artist",
        "album": "title",
        "year": "year",
        "media": "source",
        "cover_url": "artwork_url",
    }
    attribute = attributes.get(field)
    return getattr(meta, attribute, "") if attribute else ""


def _music_field(meta: Meta, field: str) -> Any:
    """Read a normalized release field, falling back to the shared Meta view."""
    value = _music_field_entry(meta, field).get("value")
    return (
        value if value not in (None, "") else _music_meta_fallback(meta, field)
    )


def _music_field_source(meta: Meta, field: str) -> str:
    return str(_music_field_entry(meta, field).get("source", ""))


def _music_field_payload(value: Any, source: str) -> dict[str, Any]:
    return {"value": value, "source": source, "confidence": 1.0}


def _music_artist_values(value: Any) -> list[str]:
    rendered = str(value)
    artists = [
        part.strip() for part in re.split(r"\s+&\s+", rendered) if part.strip()
    ]
    return artists or [rendered]


def _apply_music_year(meta: Meta, value: str | int) -> None:
    meta.year = int(value)
    meta.search_year = str(value)


def _apply_music_meta_field(meta: Meta, field: str, value: str | int) -> None:
    if field == "year":
        _apply_music_year(meta, value)
        return
    attribute = {
        "artist": "artist",
        "album": "title",
        "media": "source",
        "cover_url": "artwork_url",
    }.get(field)
    if attribute:
        setattr(meta, attribute, str(value))


def _set_music_field(
    meta: Meta, field: str, value: str | int, *, source: str = "user"
) -> None:
    """Keep prompted values and their provenance available to tracker adapters."""
    fields = _music_fields_mapping(meta, create=True)
    fields[field] = _music_field_payload(value, source)
    if field == "artist":
        fields["artists"] = _music_field_payload(
            _music_artist_values(value), source
        )
    _apply_music_meta_field(meta, field, value)


def _is_http_url(value: Any) -> bool:
    parsed = urlparse(str(value or "").strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


MUSIC_COVER_MAX_BYTES = 10 * 1024 * 1024
MUSIC_COVER_MAX_REDIRECTS = 3


def _is_public_music_cover_url(value: Any) -> bool:
    """Allow artwork downloads only from public HTTP(S) hosts."""
    return is_public_http_url(str(value or ""))


def _music_cover_redirect_url(
    current_url: str, response: requests.Response
) -> str | None:
    location = response.headers.get("Location", "")
    return urljoin(current_url, location) if location else None


def _music_cover_content_type(response: requests.Response) -> str:
    return (
        response.headers.get("Content-Type", "")
        .split(";", 1)[0]
        .strip()
        .lower()
    )


def _music_cover_content_type_allowed(response: requests.Response) -> bool:
    content_type = _music_cover_content_type(response)
    if content_type.startswith("image/"):
        return True
    logger.warning(
        f"[yellow]MUSIC: artwork URL returned unsupported content type {content_type or 'unknown'}.[/yellow]"
    )
    return False


def _music_cover_content_length_allowed(response: requests.Response) -> bool:
    content_length = response.headers.get("Content-Length")
    if not content_length:
        return True
    valid = (
        content_length.isdigit()
        and int(content_length) <= MUSIC_COVER_MAX_BYTES
    )
    if not valid:
        logger.warning(
            "[yellow]MUSIC: artwork download exceeds the 10 MiB limit.[/yellow]"
        )
    return valid


def _bounded_music_cover_bytes(response: requests.Response) -> bytes | None:
    content = bytearray()
    for chunk in response.iter_content(chunk_size=64 * 1024):
        content.extend(chunk)
        if len(content) > MUSIC_COVER_MAX_BYTES:
            logger.warning(
                "[yellow]MUSIC: artwork download exceeds the 10 MiB limit.[/yellow]"
            )
            return None
    return bytes(content)


def _music_cover_response_result(
    current_url: str, response: requests.Response
) -> tuple[str | None, bytes | None] | None:
    if response.is_redirect:
        redirect = _music_cover_redirect_url(current_url, response)
        return (redirect, None) if redirect else None
    response.raise_for_status()
    if not _music_cover_content_type_allowed(response):
        return None
    if not _music_cover_content_length_allowed(response):
        return None
    return None, _bounded_music_cover_bytes(response)


def _music_cover_request(
    current_url: str,
) -> tuple[str | None, bytes | None] | None:
    try:
        response = requests.get(
            current_url, timeout=30, allow_redirects=False, stream=True
        )
    except requests.RequestException as error:
        logger.warning(
            f"[yellow]MUSIC: could not download artwork for image hosting: {error}[/yellow]"
        )
        return None
    try:
        return _music_cover_response_result(current_url, response)
    except requests.RequestException as error:
        logger.warning(
            f"[yellow]MUSIC: could not download artwork for image hosting: {error}[/yellow]"
        )
        return None
    finally:
        response.close()


def _download_music_cover(url: str) -> bytes | None:
    """Download a bounded image while validating every redirect destination."""
    current_url = url
    for _ in range(MUSIC_COVER_MAX_REDIRECTS + 1):
        if not _is_public_music_cover_url(current_url):
            logger.warning(
                "[yellow]MUSIC: refused artwork download from a non-public URL.[/yellow]"
            )
            return None
        result = _music_cover_request(current_url)
        if result is None:
            return None
        redirect, content = result
        if redirect is None:
            return content
        current_url = redirect
    logger.warning(
        "[yellow]MUSIC: artwork URL exceeded the redirect limit.[/yellow]"
    )
    return None


async def _write_music_snapshot(meta: Meta) -> None:
    path = music_release_snapshot_path(meta.base_dir, str(meta.uuid))
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(path, "w", encoding="utf-8") as file:
        await file.write(
            json.dumps(meta.music_release, indent=2, cls=PathAwareEncoder)
        )


def _tracker_music_cover_hosts(tracker_name: Any) -> set[str] | None:
    tracker_class = tracker_class_map.get(str(tracker_name).upper())
    if tracker_class is None:
        return None
    raw = getattr(
        tracker_class,
        "music_cover_approved_image_hosts",
        getattr(tracker_class, "approved_image_hosts", None),
    )
    if not raw:
        return None
    return {str(host) for host in raw}


def _intersect_music_cover_hosts(
    current: set[str] | None, tracker_hosts: set[str] | None
) -> set[str] | None:
    if tracker_hosts is None:
        return current
    return tracker_hosts if current is None else current & tracker_hosts


def _music_cover_allowed_hosts(trackers: Iterable[Any]) -> list[str] | None:
    """Return the hosts accepted by every selected tracker with a host policy."""
    approved_hosts: set[str] | None = None
    for tracker_name in trackers:
        approved_hosts = _intersect_music_cover_hosts(
            approved_hosts, _tracker_music_cover_hosts(tracker_name)
        )
    return sorted(approved_hosts) if approved_hosts is not None else None


def _music_cover_cache_path(meta: Meta) -> Path:
    return Path(meta.base_dir) / "tmp" / str(meta.uuid) / "covers.json"


def _music_cover_records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [
        cast(dict[str, Any], item)
        for item in cast(list[Any], value)
        if isinstance(item, dict)
    ]


def _first_music_cover_url(records: list[dict[str, Any]]) -> str:
    if not records:
        return ""
    return str(records[0].get("raw_url") or "")


async def _reuse_cached_music_cover(meta: Meta, cache_path: Path) -> bool:
    if not cache_path.is_file():
        return False
    try:
        cached = json.loads(
            await asyncio.to_thread(cache_path.read_text, encoding="utf-8")
        )
    except (OSError, ValueError, TypeError) as error:
        logger.debug(
            f"[yellow]MUSIC: ignored unusable artwork cache: {error}[/yellow]"
        )
        return False
    records = _music_cover_records(cached)
    cached_url = _first_music_cover_url(records)
    if not _is_http_url(cached_url):
        return False
    meta.artwork_url = cached_url
    meta.hosted_artwork = records
    _set_music_field(meta, "cover_url", cached_url, source="external")
    return True


async def _write_downloaded_music_cover(
    meta: Meta, artwork_path: Path, content: bytes
) -> bool:
    try:
        artwork_path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(artwork_path.write_bytes, content)
    except OSError as error:
        logger.warning(
            f"[yellow]MUSIC: could not save downloaded artwork for image hosting: {error}[/yellow]"
        )
        return False
    meta.artwork_path = str(artwork_path)
    return True


def _existing_music_cover_path(meta: Meta) -> Path | None:
    path = Path(str(meta.artwork_path or ""))
    return path if path.is_file() else None


async def _downloaded_music_cover_path(meta: Meta) -> Path | None:
    if not _is_http_url(meta.artwork_url):
        return Path(str(meta.artwork_path or ""))
    destination = (
        artwork_dir(meta.base_dir, str(meta.uuid)) / "music_cover.jpg"
    )
    content = await asyncio.to_thread(_download_music_cover, meta.artwork_url)
    if content is None:
        return None
    if not await _write_downloaded_music_cover(meta, destination, content):
        return None
    return destination


async def _materialized_music_cover(meta: Meta) -> Path | None:
    existing = _existing_music_cover_path(meta)
    return (
        existing
        if existing is not None
        else await _downloaded_music_cover_path(meta)
    )


async def _upload_music_cover_file(
    meta: Meta,
    uploadscreens_manager: UploadScreensManager,
    artwork_path: Path,
    allowed_hosts: list[str] | None,
) -> list[dict[str, Any]]:
    try:
        uploaded, _ = await uploadscreens_manager.upload_screens(
            meta,
            1,
            1,
            0,
            1,
            [str(artwork_path)],
            {},
            allowed_hosts=allowed_hosts,
        )
    except Exception as error:
        logger.warning(
            f"[yellow]MUSIC: artwork host upload failed: {error}[/yellow]"
        )
        return []
    return _music_cover_records(uploaded)


async def _persist_hosted_music_cover(
    meta: Meta,
    cache_path: Path,
    uploaded: list[dict[str, Any]],
) -> bool:
    raw_url = _first_music_cover_url(uploaded)
    if not _is_http_url(raw_url):
        logger.warning(
            "[yellow]MUSIC: image host did not return a usable artwork URL.[/yellow]"
        )
        return False
    meta.artwork_url = raw_url
    meta.hosted_artwork = uploaded
    _set_music_field(meta, "cover_url", raw_url, source="external")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(cache_path, "w", encoding="utf-8") as file:
        await file.write(json.dumps(uploaded, indent=2))
    await _write_music_snapshot(meta)
    return True


def _music_cover_hosting_allowed(meta: Meta) -> bool:
    if meta.debug:
        logger.info("[yellow]MUSIC debug: image-host upload skipped.[/yellow]")
        return False
    if meta.skip_imghost_upload:
        logger.info(
            "[yellow]MUSIC: image-host upload is disabled; provide a hosted artwork URL.[/yellow]"
        )
        return False
    return True


async def _host_uncached_music_cover(
    meta: Meta,
    uploadscreens_manager: UploadScreensManager,
    cache_path: Path,
    allowed_hosts: list[str] | None,
) -> None:
    artwork_path = await _materialized_music_cover(meta)
    if artwork_path is None:
        return
    if not is_valid_cover_image(artwork_path):
        logger.warning(
            "[yellow]MUSIC: local artwork is not a valid supported image.[/yellow]"
        )
        return
    uploaded = await _upload_music_cover_file(
        meta, uploadscreens_manager, artwork_path, allowed_hosts
    )
    if uploaded:
        await _persist_hosted_music_cover(meta, cache_path, uploaded)


async def _host_music_cover(
    meta: Meta,
    uploadscreens_manager: UploadScreensManager,
    allowed_hosts: list[str] | None = None,
) -> None:
    """Host MUSIC artwork and publish it through the shared artwork API."""
    if not _music_cover_hosting_allowed(meta):
        return
    cache_path = _music_cover_cache_path(meta)
    if await _reuse_cached_music_cover(meta, cache_path):
        return
    await _host_uncached_music_cover(
        meta, uploadscreens_manager, cache_path, allowed_hosts
    )


async def _ensure_valid_book_artwork(meta: Meta) -> bool:
    """Ensure every BOOK upload has a local, decodable image before tracker checks."""
    if is_valid_cover_image(meta.artwork_path):
        return True

    if not _is_http_url(meta.artwork_url):
        return False

    destination = artwork_dir(meta.base_dir, meta.uuid) / "manual_cover.jpg"
    if await download_artwork_from_meta(meta, str(destination), force=True):
        return is_valid_cover_image(meta.artwork_path)
    return False


async def _prepare_book_artwork(meta: Meta) -> None:
    while not await _ensure_valid_book_artwork(meta):
        if meta.unattended:
            logger.info(
                "[yellow]BOOK upload: no valid cover could be obtained. Trackers requiring a cover will be skipped.[/yellow]"
            )
            return
        meta.artwork_path = ""
        meta.artwork_url = ""
        await _prompt_book_meta(meta)


def _music_field_missing(meta: Meta, field: str) -> bool:
    value = _music_field(meta, field)
    if field == "cover_url":
        return not _is_http_url(value)
    return not str(value or "").strip()


def _music_has_artwork(meta: Meta) -> bool:
    return bool(
        is_valid_cover_image(meta.artwork_path)
        or _is_http_url(meta.artwork_url)
        or _is_http_url(_music_field(meta, "cover_url"))
    )


def _music_missing_prompt_fields(meta: Meta) -> list[str]:
    missing = [
        field
        for field in MUSIC_REQUIRED_FIELDS
        if _music_field_missing(meta, field)
    ]
    if not _music_has_artwork(meta):
        missing.append("artwork")
    return missing


def _music_conflicts(meta: Meta) -> dict[str, Any]:
    raw = _music_release_mapping(meta).get("conflicts", {})
    return cast(dict[str, Any], raw) if isinstance(raw, dict) else {}


def _music_edition_year_needs_confirmation(
    meta: Meta, conflicts: dict[str, Any], missing: list[str]
) -> bool:
    if conflicts.get("edition_year") and "edition_year" not in missing:
        return True
    if not _music_field(meta, "edition"):
        return False
    return bool(
        not _music_field(meta, "edition_year")
        or _music_field_source(meta, "edition_year") == "file_tag"
    )


def _music_contextual_prompt_fields(
    meta: Meta, missing: list[str]
) -> list[str]:
    conflicts = _music_conflicts(meta)
    contextual: list[str] = []
    if conflicts.get("year") and "year" not in missing:
        contextual.append("year")
    if _music_edition_year_needs_confirmation(meta, conflicts, missing):
        contextual.append("edition_year")
    if conflicts.get("artist"):
        contextual.append("artist")
    return contextual


def _music_fields_to_prompt(meta: Meta) -> list[str]:
    missing = _music_missing_prompt_fields(meta)
    contextual = _music_contextual_prompt_fields(meta, missing)
    return list(dict.fromkeys([*missing, *contextual]))


def _music_prompt_labels() -> dict[str, str]:
    return {
        "artist": "main artist(s), separated by &",
        "album": "album title",
        "year": "original release year",
        "edition_year": "edition/remaster year",
    }


def _valid_music_year(value: str) -> bool:
    return bool(
        value.isdigit() and len(value) == 4 and 1000 <= int(value) <= 3000
    )


def _music_year_prompt_text(meta: Meta, field: str, label: str) -> str:
    current = str(_music_field(meta, field) or "")
    current_hint = f" (current: {current})" if current else ""
    return f"Enter {label}{current_hint} (leave blank to keep/skip): "


def _music_year_input_result(
    meta: Meta, field: str, value: str
) -> tuple[bool, bool]:
    if not value:
        return False, True
    if _valid_music_year(value):
        _set_music_field(meta, field, int(value))
        return True, True
    logger.info(
        "[red]Invalid year (must be a 4-digit number between 1000 and 3000).[/red]"
    )
    return False, False


def _prompt_music_year(meta: Meta, field: str, label: str) -> bool:
    while True:
        value = str(
            CLI_UI.ask_string(_music_year_prompt_text(meta, field, label))
            or ""
        ).strip()
        changed, done = _music_year_input_result(meta, field, value)
        if done:
            return changed


def _prompt_music_text(meta: Meta, field: str, label: str) -> bool:
    value = str(
        CLI_UI.ask_string(f"Enter {label} (leave blank to skip): ") or ""
    ).strip()
    if not value:
        return False
    _set_music_field(meta, field, value)
    return True


def _prompt_labeled_music_field(meta: Meta, field: str, label: str) -> bool:
    if field in {"year", "edition_year"}:
        return _prompt_music_year(meta, field, label)
    return _prompt_music_text(meta, field, label)


def _music_choice_spec(field: str) -> tuple[str, tuple[str, ...]] | None:
    specs: dict[str, tuple[str, tuple[str, ...]]] = {
        "media": ("Select source media:", MUSIC_MEDIA_CHOICES),
        "release_type": ("Select release type:", MUSIC_RELEASE_TYPE_CHOICES),
    }
    return specs.get(field)


def _prompt_music_choice(
    meta: Meta, field: str, spec: tuple[str, tuple[str, ...]]
) -> bool:
    prompt, choices = spec
    value = CLI_UI.ask_choice(prompt, choices=list(choices), sort=False)
    if not value:
        return False
    _set_music_field(meta, field, value)
    return True


def _music_artwork_input_result(meta: Meta, value: str) -> tuple[bool, bool]:
    if not value:
        logger.info(
            "[red]Artwork is required for MUSIC uploads. Please enter a valid file path or image URL.[/red]"
        )
        return False, False
    if _is_http_url(value):
        _set_music_field(meta, "cover_url", value)
        meta.artwork_url = value
        return True, True
    path_obj = Path(value).expanduser()
    if is_valid_cover_image(path_obj):
        meta.artwork_path = str(path_obj.resolve())
        _set_music_field(meta, "cover_url", meta.artwork_path, source="user")
        return True, True
    logger.info(
        "[red]Invalid artwork path or URL. The file does not exist or URL is invalid. Please try again.[/red]"
    )
    return False, False


def _prompt_music_artwork(meta: Meta) -> bool:
    prompt = (
        "Enter path to cover artwork image (or public image URL) for MUSIC: "
    )
    while True:
        value = str(CLI_UI.ask_string(prompt) or "").strip()
        changed, done = _music_artwork_input_result(meta, value)
        if done:
            return changed


def _prompt_music_field(
    meta: Meta, field: str, labels: dict[str, str]
) -> bool:
    label = labels.get(field)
    if label is not None:
        return _prompt_labeled_music_field(meta, field, label)
    choice_spec = _music_choice_spec(field)
    if choice_spec is not None:
        return _prompt_music_choice(meta, field, choice_spec)
    if field in {"artwork", "cover_url"}:
        return _prompt_music_artwork(meta)
    return False


async def _refresh_music_name(meta: Meta) -> None:
    year = f" [{meta.year}]" if meta.year else ""
    media = str(_music_field(meta, "media") or "")
    meta.name_notag = (
        f"{meta.artist} - {meta.title}{year} [{media} {meta.format}]".strip()
    )
    (
        meta.name_notag,
        meta.name,
        meta.clean_name,
        meta.potential_missing,
    ) = await name_manager.get_name(meta)


def _run_music_prompts(
    meta: Meta, fields_to_prompt: list[str], labels: dict[str, str]
) -> bool | None:
    changed = False
    try:
        for field in fields_to_prompt:
            changed = _prompt_music_field(meta, field, labels) or changed
    except EOFError:
        logger.info(
            "[yellow]Input cancelled — continuing with missing music fields.[/yellow]"
        )
        return None
    return changed


async def _prompt_music_meta(meta: Meta) -> None:
    """Ask for minimum Orpheus music metadata, never technical stream fields."""
    fields_to_prompt = _music_fields_to_prompt(meta)
    if not fields_to_prompt:
        return
    if meta.unattended:
        logger.info(
            f"[yellow]MUSIC metadata requires confirmation for: {', '.join(fields_to_prompt)}. Trackers that require confirmed values may skip this upload.[/yellow]"
        )
        return
    logger.info(
        "\n[bold yellow]MUSIC metadata required or requiring confirmation:[/bold yellow]"
    )
    changed = _run_music_prompts(
        meta, fields_to_prompt, _music_prompt_labels()
    )
    if not changed:
        return
    await _write_music_snapshot(meta)
    await _refresh_music_name(meta)


def _optional_artifact_defaults(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("DEFAULT", {})
    return cast(dict[str, Any], raw) if isinstance(raw, dict) else {}


def _disc_menu_artifact_needed(meta: Meta, defaults: dict[str, Any]) -> bool:
    if not meta.is_disc:
        return False
    wants_menus = bool(
        meta.path_to_menu_screenshots or defaults.get("auto_dvd_menus", False)
    )
    if not wants_menus or meta.menu_images:
        return False
    path = Path(meta.base_dir) / "tmp" / meta.uuid / "menu_images.json"
    return not path.exists()


async def _process_optional_disc_menus(
    meta: Meta, config: dict[str, Any], defaults: dict[str, Any]
) -> None:
    if not _disc_menu_artifact_needed(meta, defaults):
        return
    try:
        await process_disc_menus(meta, config)
    except Exception as error:
        logger.warning(
            f"[yellow]Optional disc-menu processing failed; continuing with the release screenshots: {error}[/yellow]"
        )


def _spectrogram_artifact_needed(meta: Meta, defaults: dict[str, Any]) -> bool:
    supported_category = meta.category not in {"BOOK", "GAME"} or bool(
        meta.audiobook
    )
    requested = bool(
        meta.audio_spectrogram
        or meta.audio_spectrogram_tracks
        or defaults.get("add_audio_spectrogram", False)
    )
    return supported_category and requested


async def _process_optional_spectrogram(
    meta: Meta,
    config: dict[str, Any],
    uploadscreens_manager: UploadScreensManager,
    defaults: dict[str, Any],
) -> None:
    if not _spectrogram_artifact_needed(meta, defaults):
        return
    if meta.debug:
        logger.info(
            "[yellow]Debug mode: audio spectrogram hosting skipped.[/yellow]"
        )
        return
    try:
        await process_audio_spectrograms(meta, config, uploadscreens_manager)
    except Exception as error:
        logger.warning(
            f"[yellow]Optional audio spectrogram processing failed; continuing with the release screenshots: {error}[/yellow]"
        )


async def _process_optional_hdr_plot(
    meta: Meta,
    config: dict[str, Any],
    uploadscreens_manager: UploadScreensManager,
) -> None:
    if not dynamic_hdr_plot_enabled(meta, config):
        return
    try:
        await process_dynamic_hdr_plots(meta, config, uploadscreens_manager)
    except Exception as error:
        logger.warning(
            f"[yellow]Optional dynamic HDR plot processing failed; continuing with the release screenshots: {error}[/yellow]"
        )


async def _process_optional_image_artifacts(
    meta: Meta,
    config: dict[str, Any],
    uploadscreens_manager: UploadScreensManager,
) -> None:
    """Generate and host auxiliary images only after mandatory screenshots."""
    defaults = _optional_artifact_defaults(config)
    await _process_optional_disc_menus(meta, config, defaults)
    await _process_optional_spectrogram(
        meta, config, uploadscreens_manager, defaults
    )
    await _process_optional_hdr_plot(meta, config, uploadscreens_manager)


async def _validate_screenshots_then_process_optional(
    meta: Meta,
    config: dict[str, Any],
    uploadscreens_manager: UploadScreensManager,
) -> None:
    """Validate mandatory screenshots before any optional image artifact."""
    local_screens, required_screens = available_screens(
        meta, configured_screenshot_minimum(config)
    )
    if meta.debug and meta.category in {"MOVIE", "TV"}:
        if local_screens < required_screens:
            raise ItemProcessingError(
                f"Minimum of {required_screens} local screenshots required in debug mode, but only {local_screens} were captured.",
                meta.path,
            )
    else:
        screenshot_error = screenshot_requirement_error(
            meta, config, local_available=local_screens
        )
        if screenshot_error:
            raise ItemProcessingError(screenshot_error, meta.path)

    await _process_optional_image_artifacts(
        meta, config, uploadscreens_manager
    )


def available_screens(
    meta: Meta, min_successful_uploads: int
) -> tuple[int, int]:
    screenshot_files = list(
        screenshots_dir(meta.base_dir, meta.uuid).glob("*.png")
    )
    actual_screens = len(screenshot_files)
    return actual_screens, min_successful_uploads


def _valid_release_title(meta: Meta) -> bool:
    title = str(meta.title or "").strip()
    return bool(title and re.search(r"\w", title, re.UNICODE))


def _valid_numeric_metadata_id(value: Any) -> bool:
    return re.fullmatch(r"[1-9]\d*", str(value or "").strip()) is not None


def _normalized_imdb_candidate(value: object) -> str:
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return f"{value:07d}"
    return str(value or "").strip()


def _valid_imdb_metadata_id(value: object) -> bool:
    candidate = _normalized_imdb_candidate(value)
    if not candidate:
        return False
    match = re.fullmatch(
        r"(?:https?://(?:www\.)?imdb\.com/title/)?(?:tt)?(\d{7,10})(?:/)?(?:[?#].*)?",
        candidate,
        re.IGNORECASE,
    )
    return bool(match and int(match.group(1)) > 0)


def _imdb_info_id(meta: Meta) -> Any:
    raw = meta.imdb_info
    if not isinstance(raw, dict):
        return None
    return cast(dict[str, Any], raw).get("imdbID")


def _has_automatic_metadata_identity(meta: Meta) -> bool:
    numeric_ids = (
        meta.tmdb,
        meta.tmdb_id,
        meta.tvdb_id,
        meta.tvmaze_id,
        meta.mal_id,
    )
    if any(_valid_numeric_metadata_id(value) for value in numeric_ids):
        return True
    imdb_ids = (meta.imdb, meta.imdb_id, _imdb_info_id(meta))
    return any(_valid_imdb_metadata_id(value) for value in imdb_ids)


def _anime_episode_identity_error(meta: Meta) -> str | None:
    incomplete = all(
        (
            meta.category == "TV",
            bool(meta.anime),
            not bool(meta.tv_pack),
            bool(meta.episode_int),
            bool(meta.tvdb_id),
            not bool(meta.tvdb_episode_id),
        )
    )
    if not incomplete:
        return None
    return "Unattended anime episode could not be mapped to a TVDB episode. Refusing to process the upload."


def _movie_tv_identity_error(meta: Meta) -> str | None:
    if meta.category not in {"MOVIE", "TV"}:
        return None
    if not _valid_release_title(meta):
        return f"{meta.category} metadata has no valid title. Refusing to process the upload."
    if not meta.unattended:
        return None
    if not _has_automatic_metadata_identity(meta):
        return f"Unattended {meta.category} metadata has no valid TMDb, IMDb, TVDB, TVmaze, or MAL identifier. Refusing to process the upload."
    return _anime_episode_identity_error(meta)


def _tracker_upload_failed(status: Any) -> bool:
    if not isinstance(status, Mapping):
        return False
    mapping = cast(Mapping[str, Any], status)
    attempted = (
        mapping.get("upload") is True or mapping.get("upload_success") is False
    )
    return bool(
        mapping.get("dupe") is not True
        and attempted
        and mapping.get("upload_success") is not True
    )


def _failed_tracker_names(tracker_status: Mapping[str, Any]) -> list[str]:
    return [
        tracker
        for tracker, status in tracker_status.items()
        if _tracker_upload_failed(status)
    ]


def _sync_single_episode(meta: Meta) -> None:
    if sync_single_episode_from_filename(meta):
        logger.warning(
            f"[yellow]Updated single-episode metadata to {meta.season}{meta.episode} to match the video filename.[/yellow]"
        )


def xxx_min_successful_uploads(meta: Meta, min_successful_uploads: int) -> int:
    """Cap XXX image uploads to its one-contact-sheet-per-video contract."""
    try:
        contact_sheet_count = int(meta.screens or 0)
    except TypeError, ValueError:
        contact_sheet_count = 0
    return min(min_successful_uploads, max(1, contact_sheet_count))


def _process_default_config() -> dict[str, Any]:
    raw = config.get("DEFAULT", {})
    return cast(dict[str, Any], raw) if isinstance(raw, dict) else {}


def _oeimg_host_keys(default_config: dict[str, Any]) -> list[str]:
    return [
        key
        for key, value in default_config.items()
        if key.startswith("img_host_") and value == "oeimg"
    ]


def _migrate_oeimg_hosts(default_config: dict[str, Any]) -> None:
    oeimg_keys = _oeimg_host_keys(default_config)
    if not oeimg_keys:
        return
    logger.info(
        "[yellow]oeimg is now onlyimage; updating the active configuration.[/yellow]"
    )
    update_oeimg_to_onlyimage()
    for key in oeimg_keys:
        default_config[key] = "onlyimage"


def _requested_image_host(meta: Meta) -> str:
    requested = str(meta.imghost or "").strip().lower()
    return "onlyimage" if requested == "oeimg" else requested


def _resolve_process_image_host(meta: Meta) -> bool:
    try:
        default_config = _process_default_config()
        _migrate_oeimg_hosts(default_config)
        configured_hosts = configured_image_hosts(default_config)
        requested_host = _requested_image_host(meta)
        if requested_host:
            meta.imghost = requested_host
            return True
        if configured_hosts:
            meta.imghost = configured_hosts[0]
            return True
        logger.error(
            "[bold red]No image host is configured. Set DEFAULT.img_host_1 (and its credential when required) before uploading.[/bold red]"
        )
        return False
    except Exception as error:
        logger.error(f"[red]Error resolving image hosts: {error}[/red]")
        return False


def _apply_process_auto_mode(meta: Meta) -> None:
    if meta.unattended:
        return
    auto_mode = _process_default_config().get("auto_mode", False)
    if str(auto_mode).lower() == "true":
        meta.unattended = True
        logger.info("[yellow]Running in Auto Mode")


async def _gather_process_meta(meta: Meta, prep: Prep) -> Meta | None:
    try:
        return await prep.gather_prep(meta=meta, mode="cli")
    except ItemProcessingError:
        raise
    except AmbiguousMetadataError as error:
        raise ItemProcessingError(
            str(error), item_path=str(meta.path or "")
        ) from error
    except Exception as error:
        logger.info(f"Error in gather_prep: {error}")
        logger.info(traceback.format_exc())
        return None


def _covers_cache_path(meta: Meta) -> Path:
    return Path(meta.base_dir) / "tmp" / meta.uuid / "covers.json"


async def _read_hosted_artwork_cache(path: Path) -> list[dict[str, Any]]:
    try:
        async with aiofiles.open(path, encoding="utf-8") as handle:
            loaded = json.loads(await handle.read())
    except Exception as error:
        logger.debug(
            f"[red]Error loading covers.json into meta.hosted_artwork: {error}"
        )
        return []
    if not isinstance(loaded, list):
        return []
    return [
        cast(dict[str, Any], item)
        for item in cast(list[Any], loaded)
        if isinstance(item, dict)
    ]


async def _load_hosted_artwork_cache(meta: Meta) -> None:
    path = _covers_cache_path(meta)
    if not path.exists() or meta.hosted_artwork:
        return
    records = await _read_hosted_artwork_cache(path)
    if not records:
        return
    meta.hosted_artwork = records
    logger.debug(
        f"[green]Loaded {len(records)} hosted artwork records from covers.json"
    )


def _raw_tracker_values(value: Any) -> list[Any]:
    if isinstance(value, str):
        return value.split(",") if value else []
    return cast(list[Any], value) if isinstance(value, list) else []


def _normalized_tracker_values(value: Any) -> list[str]:
    return [
        str(item).strip().upper()
        for item in _raw_tracker_values(value)
        if str(item).strip()
    ]


def _apply_tracker_removals(meta: Meta, trackers: list[str]) -> list[str]:
    remove_values = _normalized_tracker_values(meta.trackers_remove)
    if not remove_values:
        return trackers
    remove_set = set(remove_values)
    return [tracker for tracker in trackers if tracker not in remove_set]


def _prepare_meta_trackers(meta: Meta) -> list[str]:
    trackers = _apply_tracker_removals(
        meta, _normalized_tracker_values(meta.trackers)
    )
    meta.trackers = trackers
    TrackerSetup(config=config).filter_unsupported_trackers(meta)
    return cast(list[str], meta.trackers)


async def _persist_process_meta(meta: Meta) -> None:
    path = Path(meta.base_dir) / "tmp" / meta.uuid / "meta.json"
    async with aiofiles.open(path, "w", encoding="utf-8") as handle:
        await handle.write(
            json.dumps(meta.to_dict(), indent=4, cls=PathAwareEncoder)
        )


async def _refresh_process_name(meta: Meta) -> None:
    (
        meta.name_notag,
        meta.name,
        meta.clean_name,
        meta.potential_missing,
    ) = await name_manager.get_name(meta)


async def _prompt_process_category_meta(meta: Meta) -> None:
    if meta.category == "BOOK":
        await _prompt_book_meta(meta)
        await _prepare_book_artwork(meta)
    elif meta.category == "GAME":
        await _prompt_game_meta(meta)
    elif meta.category == "MUSIC":
        await _prompt_music_meta(meta)


async def _confirmation_interrupted() -> None:
    logger.info("\n[red]Exiting on user request (Ctrl+C)[/red]")
    await cleanup_manager.cleanup()
    cleanup_manager.reset_terminal()
    raise KeyboardInterrupt


def _parsed_edit_args(editargs_str: str) -> tuple[str, ...] | None:
    try:
        return tuple(shlex.split(editargs_str))
    except Exception:
        logger.info("[red]Bad input detected[/red]")
        return None


def _edit_original_args(meta: Meta) -> list[str]:
    return (
        list(meta.item_args)
        if meta.item_args is not None
        else list(sys.argv[1:])
    )


def _normalize_edited_trackers(
    meta: Meta, previous_trackers: list[str]
) -> None:
    if not meta.trackers:
        meta.trackers = previous_trackers
    meta.trackers = _normalized_tracker_values(meta.trackers)


def _remove_client_trackers_after_edit(meta: Meta) -> None:
    current = _normalized_tracker_values(meta.trackers)
    remove_values = _normalized_tracker_values(meta.remove_trackers)
    removed: list[str] = []
    for tracker in remove_values:
        if tracker in current:
            current.remove(tracker)
            removed.append(tracker)
        elif meta.debug:
            logger.debug(
                f"[DEBUG] Would have removed {tracker} found in client"
            )
    meta.trackers = current
    if removed:
        logger.info(
            f"[yellow]Removing trackers already in your client: {', '.join(removed)}[/yellow]"
        )


async def _reprepare_edited_meta(
    meta: Meta,
    prep: Prep,
    parser: Any,
    previous_trackers: list[str],
    editargs_tracking: tuple[str, ...],
) -> Meta:
    parsed = cast(
        tuple[Meta, Any, Any],
        parser.parse(
            _edit_original_args(meta) + list(editargs_tracking), meta
        ),
    )
    meta = parsed[0]
    _normalize_edited_trackers(meta, previous_trackers)
    logger.debug(f"Trackers list during edit process: {meta.trackers}")
    meta.edit = True
    _sync_single_episode(meta)
    meta = await prep.gather_prep(meta=meta, mode="cli")
    TrackerSetup(config=config).filter_unsupported_trackers(meta)
    await _refresh_process_name(meta)
    await _persist_process_meta(meta)
    return meta


async def _get_process_confirmation(helper: Any, meta: Meta) -> bool:
    try:
        return bool(await helper.get_confirmation(meta))
    except EOFError:
        await _confirmation_interrupted()
    return False


async def _process_edit_input() -> str:
    try:
        return str(
            CLI_UI.ask_string(
                "Input args that need correction e.g. (--tag NTb --category tv --tmdb 12345)"
            )
            or ""
        )
    except EOFError:
        await _confirmation_interrupted()
        return "continue"


def _parsed_process_edit_input(editargs_str: str) -> tuple[str, ...] | None:
    if editargs_str == "continue":
        return None
    if not editargs_str.strip():
        logger.info(
            "[yellow]No input provided. Please enter arguments, type `continue` to continue or press Ctrl+C to exit.[/yellow]"
        )
        return ()
    return _parsed_edit_args(editargs_str) or ()


async def _next_process_edit_args() -> tuple[str, ...] | None:
    return _parsed_process_edit_input(await _process_edit_input())


async def _process_confirmation_edits(
    meta: Meta,
    prep: Prep,
    parser: Any,
    helper: Any,
) -> Meta:
    previous_trackers = _normalized_tracker_values(meta.trackers)
    editargs_tracking: tuple[str, ...] = ()
    confirm = await _get_process_confirmation(helper, meta)
    while confirm is False:
        editargs = await _next_process_edit_args()
        if editargs is None:
            break
        if not editargs:
            continue
        editargs_tracking += editargs
        meta = await _reprepare_edited_meta(
            meta, prep, parser, previous_trackers, editargs_tracking
        )
        confirm = await _get_process_confirmation(helper, meta)
        _remove_client_trackers_after_edit(meta)
    return meta


def _tracker_skip_upload_names() -> tuple[str, ...]:
    return (
        "1PTBA",
        "ASIANCINEMA",
        "AITHER",
        "AMIGOSSHARE",
        "BJSHARE",
        "BRASILTRACKER",
        "CAPYBARABR",
        "CURUPIRA",
        "DARKPEERS",
        "FUNFILE",
        "GREATPOSTERWALL",
        "HAWKEUNO",
        "INFINITYHD",
        "LAJIDUI",
        "LASTDIGITALUNDERGROUND",
        "LEMONHD",
        "LONGPT",
        "LATTEAM",
        "MAKINGOFF",
        "ONLYENCODES",
        "PTCAFE",
        "PTGTK",
        "PTSKIT",
        "PTZONE",
        "RAILGUNPT",
        "SAMARITANO",
        "SHAREISLAND",
        "SPEEDAPP",
        "SUIO",
        "TORRENTEROS",
        "TVCHAOSUK",
        "ULCX",
        "XINGYUNGEPT",
    )


def _apply_tracker_skip_flags(meta: Meta, trackers: list[str]) -> None:
    should_skip = bool(
        meta.unattended_audio_skip or meta.unattended_subtitle_skip
    )
    status_map = _tracker_status_map(meta)
    for tracker in _tracker_skip_upload_names():
        if tracker in trackers:
            status_map.setdefault(tracker, {})["skip_upload"] = should_skip


async def _validate_process_tracker_logins(
    meta: Meta, trackers: list[str]
) -> None:
    try:
        await validate_tracker_logins(meta, trackers)
        await asyncio.sleep(0.2)
    except Exception as error:
        logger.warning(
            f"[yellow]Warning: Tracker validation encountered an error: {error}[/yellow]"
        )


def _configured_tracker_pass_checks() -> int:
    raw = _process_default_config().get("tracker_pass_checks")
    return int(raw) if isinstance(raw, (int, str)) else 1


def _apply_tracker_pass_threshold(meta: Meta) -> None:
    meta.skip_uploading = (
        meta.trackers_pass
        if meta.trackers_pass is not None
        else _configured_tracker_pass_checks()
    )


async def _process_tracker_status_phase(meta: Meta) -> tuple[int, list[str]]:
    trackers = _normalized_tracker_values(meta.trackers)
    if not trackers:
        logger.info("[red]No trackers remain after removal.[/red]")
        meta.skip_uploading = 10
        return 0, []
    logger.info(f"Processing for upload: [green]{meta.name}[/green]...")
    meta.trackers = trackers
    _apply_tracker_skip_flags(meta, trackers)
    await asyncio.sleep(0.2)
    await _persist_process_meta(meta)
    await asyncio.sleep(0.2)
    await _validate_process_tracker_logins(meta, trackers)
    successful = await TrackerStatusManager(
        config=config
    ).process_all_trackers(meta)
    _apply_tracker_pass_threshold(meta)
    return int(successful), trackers


def _successful_tracker_threshold(meta: Meta) -> int:
    value = meta.skip_uploading
    return int(value) if value else 0


def _tracker_threshold_passed(meta: Meta, successful_trackers: int) -> bool:
    required = _successful_tracker_threshold(meta)
    if successful_trackers >= required or meta.debug:
        return True
    logger.info(
        f"[red]Not enough successful trackers ({successful_trackers}/{required}). No uploads being processed.[/red]"
    )
    return False


def _site_check_tracker_enabled(meta: Meta, tracker: str) -> bool:
    status_map = _tracker_status_map(meta)
    upload_status = status_map.get(tracker, {}).get("upload", False)
    if upload_status:
        return tracker in status_map
    return bool(
        tracker == "AITHER"
        and meta.aither_trumpable
        and len(meta.aither_trumpable) > 0
        and tracker in status_map
    )


def _site_check_log_path(base_dir: str, tracker: str) -> Path:
    return Path(base_dir) / "tmp" / f"{tracker}_search_results.json"


def _parsed_site_check_json(content: str) -> Any:
    if not content.strip():
        return []
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return []


def _site_check_entries_from_json(content: str) -> list[dict[str, Any]]:
    loaded = _parsed_site_check_json(content)
    if not isinstance(loaded, list):
        return []
    return [
        cast(dict[str, Any], entry)
        for entry in cast(list[Any], loaded)
        if isinstance(entry, dict)
    ]


async def _read_site_check_entries(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        async with aiofiles.open(path, encoding="utf-8") as handle:
            content = await handle.read()
    except Exception:
        return []
    return _site_check_entries_from_json(content)


def _site_check_entry(meta: Meta, tracker: str) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "uuid": meta.uuid,
        "path": meta.path,
        "imdb_id": meta.imdb_id,
        "tmdb_id": meta.tmdb_id,
        "tvdb_id": meta.tvdb_id,
        "mal_id": meta.mal_id,
        "tvmaze_id": meta.tvmaze_id,
    }
    if tracker == "AITHER":
        entry["trumpable"] = meta.aither_trumpable
    return entry


async def _record_site_check_tracker(
    meta: Meta, base_dir: str, tracker: str, common: Common
) -> None:
    if not _site_check_tracker_enabled(meta, tracker):
        return
    path = _site_check_log_path(base_dir, tracker)
    if not await common.path_exists(str(path)):
        await common.makedirs(str(path.parent))
    entries = await _read_site_check_entries(path)
    if meta.uuid in {entry.get("uuid") for entry in entries}:
        return
    entries.append(_site_check_entry(meta, tracker))
    async with aiofiles.open(path, "w", encoding="utf-8") as handle:
        await handle.write(json.dumps(entries, indent=4))


async def _run_site_check(meta: Meta, base_dir: str, common: Common) -> bool:
    if not meta.site_check:
        return False
    for tracker in _normalized_tracker_values(meta.trackers):
        await _record_site_check_tracker(meta, base_dir, tracker, common)
    meta.we_are_uploading = False
    return True


def _first_process_file(meta: Meta) -> str:
    return next((str(path) for path in meta.filelist if str(path)), "")


def _hddvd_process_file(meta: Meta) -> str:
    if meta.is_disc != "HDDVD" or not meta.discs:
        return ""
    first = meta.discs[0]
    if not isinstance(first, dict):
        return ""
    return str(cast(dict[str, Any], first).get("largest_evo", "") or "")


def _process_video_path(meta: Meta) -> str:
    return _first_process_file(meta) or _hddvd_process_file(meta)


def _tracker_upload_enabled(
    status_map: dict[str, dict[str, Any]], tracker: str
) -> bool:
    return status_map.get(tracker, {}).get("upload", False) is True


def _configure_frame_overlay(
    meta: Meta, status_map: dict[str, dict[str, Any]]
) -> None:
    meta.frame_overlay = _process_default_config().get("frame_overlay", False)
    if not meta.frame_overlay:
        return
    blocked = {"AVISTAZ", "CINEMAZ", "PRIVATEHD"}
    active_blocked = any(
        tracker in meta.trackers
        and _tracker_upload_enabled(status_map, tracker)
        for tracker in blocked
    )
    if not active_blocked:
        return
    meta.frame_overlay = False
    logger.info(
        "[yellow]AVISTAZ, CINEMAZ, and PRIVATEHD do not allow frame overlays. Frame overlay will be disabled for this upload.[/yellow]"
    )


def _bdmv_mediainfo_needed(
    trackers: list[str], status_map: dict[str, dict[str, Any]]
) -> bool:
    required = {"ANTHELION", "DIGITALCORE", "HAWKEUNO", "LOCADORA"}
    return any(
        tracker in trackers and _tracker_upload_enabled(status_map, tracker)
        for tracker in required
    )


async def _prepare_visual_preflight(
    meta: Meta, trackers: list[str], common: Common
) -> str:
    logger.debug(f"Processing {meta.title} for upload.....")
    status_map = _tracker_status_map(meta)
    _configure_frame_overlay(meta, status_map)
    if _bdmv_mediainfo_needed(trackers, status_map):
        await common.get_bdmv_mediainfo(meta)
    return _process_video_path(meta)


async def _read_json_mapping(path: Path) -> dict[str, Any]:
    try:
        async with aiofiles.open(path, encoding="utf-8") as handle:
            content = await handle.read()
        loaded: Any = json.loads(content) if content.strip() else {}
    except Exception:
        return {}
    return cast(dict[str, Any], loaded) if isinstance(loaded, dict) else {}


def _visual_image_data_path(meta: Meta) -> Path:
    return Path(meta.base_dir) / "tmp" / meta.uuid / "image_data.json"


def _visual_menu_data_path(meta: Meta) -> Path:
    return Path(meta.base_dir) / "tmp" / meta.uuid / "menu_images.json"


def _restore_cached_image_list(meta: Meta, data: dict[str, Any]) -> None:
    image_list = data.get("image_list")
    if not isinstance(image_list, list) or meta.image_list:
        return
    meta.image_list = cast(list[Any], image_list)
    logger.debug(
        f"[cyan]Loaded {len(image_list)} previously saved image links"
    )


def _restore_cached_image_sizes(meta: Meta, data: dict[str, Any]) -> None:
    image_sizes = data.get("image_sizes")
    if image_sizes and not meta.image_sizes:
        meta.image_sizes = image_sizes
        logger.debug("[cyan]Loaded previously saved image sizes")


def _restore_cached_tonemapped(meta: Meta, data: dict[str, Any]) -> None:
    tonemapped = data.get("tonemapped")
    if tonemapped and not meta.tonemapped:
        meta.tonemapped = tonemapped
        logger.debug("[cyan]Loaded previously saved tonemapped status[/cyan]")


async def _restore_saved_image_data(meta: Meta) -> None:
    path = _visual_image_data_path(meta)
    if not path.exists() or meta.image_list:
        return
    data = await _read_json_mapping(path)
    _restore_cached_image_list(meta, data)
    _restore_cached_image_sizes(meta, data)
    _restore_cached_tonemapped(meta, data)


async def _restore_saved_menu_images(meta: Meta) -> None:
    if not meta.is_disc:
        return
    path = _visual_menu_data_path(meta)
    if not path.exists():
        return
    data = await _read_json_mapping(path)
    menu_images = data.get("menu_images")
    if isinstance(menu_images, list) and not meta.menu_images:
        meta.menu_images = cast(list[Any], menu_images)
        logger.debug(
            f"[cyan]Loaded {len(menu_images)} previously saved disc menus"
        )


async def _restore_visual_caches(meta: Meta) -> None:
    await _restore_saved_image_data(meta)
    await _restore_saved_menu_images(meta)


async def _capture_bdmv_screenshots(
    meta: Meta, base_dir: str, bdmv_filename: Any, bdinfo: Any
) -> None:
    await takescreens_manager.disc_screenshots(
        meta,
        bdmv_filename,
        bdinfo,
        meta.uuid,
        base_dir,
        meta.vapoursynth,
        meta.image_list,
        meta.ffdebug,
        0,
        cleanup_after_capture=False,
    )


async def _capture_dvd_screenshots(meta: Meta) -> None:
    await takescreens_manager.dvd_screenshots(
        meta,
        disc_num=0,
        num_screens=0,
        retry_cap=False,
        cleanup_after_capture=False,
    )


async def _capture_video_screenshots(
    meta: Meta,
    base_dir: str,
    videopath: str,
    filename: str,
    manual_frames: Any,
) -> None:
    logger.debug(
        f"videopath: {videopath}, filename: {filename}, meta: {meta.uuid}, base_dir: {base_dir}, manual_frames: {manual_frames}"
    )
    await takescreens_manager.screenshots(
        videopath,
        filename,
        meta.uuid,
        base_dir,
        meta,
        manual_frames=manual_frames,
        cleanup_after_capture=False,
    )


async def _cleanup_failed_screenshot_capture(meta: Meta) -> None:
    await cleanup_screenshot_temp_files(meta)
    await asyncio.sleep(0.1)
    await cleanup_manager.cleanup()
    gc.collect()
    cleanup_manager.reset_terminal()


async def _capture_release_screenshot_mode(
    meta: Meta,
    base_dir: str,
    videopath: str,
    filename: str,
    bdmv_filename: Any,
    bdinfo: Any,
    manual_frames: Any,
) -> None:
    if meta.category in {"MUSIC", "PODCAST"}:
        logger.debug(
            f"[cyan]{meta.category}: skipping video screenshots and MediaInfo-dependent image processing.[/cyan]"
        )
        return
    if meta.is_disc == "BDMV":
        await _capture_bdmv_screenshots(meta, base_dir, bdmv_filename, bdinfo)
        return
    if meta.is_disc == "DVD":
        await _capture_dvd_screenshots(meta)
        return
    await _capture_video_screenshots(
        meta, base_dir, videopath, filename, manual_frames
    )


async def _capture_release_screenshots(
    meta: Meta,
    base_dir: str,
    videopath: str,
    filename: str,
    bdmv_filename: Any,
    bdinfo: Any,
    manual_frames: Any,
) -> None:
    try:
        await _capture_release_screenshot_mode(
            meta,
            base_dir,
            videopath,
            filename,
            bdmv_filename,
            bdinfo,
            manual_frames,
        )
    except asyncio.CancelledError as error:
        await _cleanup_failed_screenshot_capture(meta)
        raise Exception("Error during screenshot capture") from error
    except Exception as error:
        if "workers" in str(error):
            logger.info(
                "[red]max workers issue, see https://github.com/wastaken7/Upload-Assistant/blob/development/docs/ffmpeg-max-workers-issues.md[/red]"
            )
        logger.debug(traceback.format_exc())
        await _cleanup_failed_screenshot_capture(meta)
        raise Exception(f"Error during screenshot capture: {error}") from error
    finally:
        await asyncio.sleep(0.1)
        gc.collect()
        cleanup_manager.reset_terminal()


def _ensure_process_image_list(meta: Meta) -> None:
    if "image_list" not in meta:
        meta.image_list = []


async def _prepare_music_cover_for_visuals(meta: Meta) -> bool:
    if meta.category != "MUSIC":
        return True
    allowed_hosts = _music_cover_allowed_hosts(cast(list[Any], meta.trackers))
    if allowed_hosts == []:
        logger.warning(
            "[yellow]MUSIC: no image host is approved by all selected trackers.[/yellow]"
        )
        return False
    await _host_music_cover(meta, uploadscreens_manager, allowed_hosts)
    return True


def _manual_frames_count(meta: Meta) -> int:
    raw = meta.manual_frames
    if not isinstance(raw, str):
        return 0
    count = len([frame.strip() for frame in raw.split(",") if frame.strip()])
    logger.debug(f"Manual frames entered: {count}")
    return count


def _apply_manual_frame_count(meta: Meta) -> int:
    count = _manual_frames_count(meta)
    if count > 0:
        meta.screens = count
    return count


def _required_screenshot_minimum(meta: Meta) -> int:
    if meta.category not in {"MOVIE", "TV"}:
        return 0
    return configured_screenshot_minimum(config)


def _release_screenshot_upload_needed(meta: Meta) -> bool:
    current = len(meta.image_list or [])
    required = max(int(meta.cutoff or 0), _required_screenshot_minimum(meta))
    return all(
        (
            not meta.debug,
            current < required,
            meta.skip_imghost_upload is False,
            meta.category not in {"GAME", "MUSIC", "PODCAST"},
        )
    )


def _image_host_requirement_trackers() -> frozenset[str]:
    return frozenset(
        {
            "AURA4K",
            "BEYONDHD",
            "DIGITALCORE",
            "GREATPOSTERWALL",
            "HAWKEUNO",
            "ONLYENCODES",
            "PASSTHEPOPCORN",
            "SKIPTHECOMMERCIALS",
            "TVCHAOSUK",
        }
    )


def _relevant_image_host_trackers(meta: Meta) -> list[str]:
    required = _image_host_requirement_trackers()
    return [
        tracker
        for tracker in _normalized_tracker_values(meta.trackers)
        if tracker in required and tracker in tracker_class_map
    ]


def _smart_image_host_selection_enabled(
    meta: Meta, trackers: list[str]
) -> bool:
    return bool(
        trackers
        and _process_default_config().get("smart_image_host_selection", True)
        and not meta.imghost_from_cli
    )


def _tracker_image_host_instance(tracker_name: str) -> Any:
    return tracker_class_map[tracker_name](config=config)


def _declared_tracker_image_hosts(instance: Any) -> set[str] | None:
    raw = getattr(instance, "approved_image_hosts", None)
    if not raw or not isinstance(raw, (list, set, tuple)):
        return None
    return {str(host) for host in cast(Iterable[Any], raw)}


def _tracker_can_extend_image_hosts(instance: Any) -> bool:
    return bool(
        getattr(instance, "can_rehost_unapproved_images", False)
        and getattr(instance, "api_key", "")
    )


def _tracker_approved_image_hosts(
    tracker_name: str, configured_hosts: Sequence[str]
) -> set[str] | None:
    instance = _tracker_image_host_instance(tracker_name)
    hosts = _declared_tracker_image_hosts(instance)
    if hosts is None:
        return None
    if _tracker_can_extend_image_hosts(instance):
        hosts.update(configured_hosts)
    logger.debug(
        f"[cyan]Image host debug: {tracker_name}.approved_image_hosts={sorted(hosts)}[/cyan]"
    )
    return hosts


def _image_host_policy_sets(
    trackers: list[str], configured_hosts: Sequence[str]
) -> dict[str, set[str]] | None:
    policies: dict[str, set[str]] = {}
    for tracker in trackers:
        hosts = _tracker_approved_image_hosts(tracker, configured_hosts)
        if hosts is None:
            return None
        policies[tracker] = hosts
    return policies


def _common_image_hosts(policies: dict[str, set[str]]) -> set[str]:
    values = [set(hosts) for hosts in policies.values()]
    if not values:
        return set()
    first, *rest = values
    return first.intersection(*rest)


def _common_configured_image_hosts(
    policies: dict[str, set[str]], configured_hosts: Sequence[str]
) -> list[str]:
    common_hosts = _common_image_hosts(policies)
    return [host for host in configured_hosts if host in common_hosts]


def _incompatible_image_host_trackers(
    policies: dict[str, set[str]], configured_hosts: Sequence[str]
) -> list[str]:
    configured = set(configured_hosts)
    return [
        tracker
        for tracker, approved in policies.items()
        if not approved & configured
    ]


def _skip_image_host_incompatible_trackers(
    meta: Meta, trackers: list[str], configured_hosts: Sequence[str]
) -> None:
    if not trackers:
        return
    logger.warning(
        "[yellow]Skipping tracker(s) with no compatible configured image host: "
        f"{', '.join(trackers)}. Configured hosts: {', '.join(configured_hosts)}.[/yellow]"
    )
    status_map = _tracker_status_map(meta)
    for tracker in trackers:
        status = status_map.setdefault(tracker, {})
        status["upload"] = False
        status["skipped"] = True
        status["status_message"] = "No compatible configured image host"
    blocked = set(trackers)
    meta.trackers = [
        tracker
        for tracker in _normalized_tracker_values(meta.trackers)
        if tracker not in blocked
    ]


def _switch_to_common_image_host(meta: Meta, common_hosts: list[str]) -> None:
    if not common_hosts:
        return
    current = str(
        meta.imghost or _process_default_config().get("img_host_1") or ""
    )
    if current in common_hosts:
        return
    preferred = common_hosts[0]
    logger.debug(
        f"[cyan]Image host debug: current host '{current}' is not common to all trackers; "
        f"switching meta.imghost from '{meta.imghost}' to '{preferred}'.[/cyan]"
    )
    meta.imghost = preferred


def _log_image_host_policy_inputs(
    meta: Meta, relevant_trackers: list[str], configured: list[str]
) -> None:
    logger.debug(
        f"[cyan]Image host debug: meta.imghost={meta.imghost} img_host_1={_process_default_config().get('img_host_1')}[/cyan]"
    )
    logger.debug(
        f"[cyan]Image host debug: relevant_trackers={relevant_trackers}[/cyan]"
    )
    logger.debug(
        f"[cyan]Image host debug: configured_hosts={configured}[/cyan]"
    )


def _log_image_host_policy_result(
    policies: dict[str, set[str]], configured: list[str]
) -> list[str]:
    common_hosts = _common_image_hosts(policies)
    common_configured = _common_configured_image_hosts(policies, configured)
    logger.debug(
        f"[cyan]Image host debug: common_hosts={sorted(common_hosts)}[/cyan]"
    )
    logger.debug(
        f"[cyan]Image host debug: common_configured_hosts={common_configured}[/cyan]"
    )
    return common_configured


def _remaining_image_host_trackers(
    meta: Meta,
    relevant_trackers: list[str],
    policies: dict[str, set[str]],
    configured: list[str],
) -> list[str]:
    incompatible = _incompatible_image_host_trackers(policies, configured)
    _skip_image_host_incompatible_trackers(meta, incompatible, configured)
    blocked = set(incompatible)
    remaining = [
        tracker for tracker in relevant_trackers if tracker not in blocked
    ]
    if remaining:
        logger.info(
            "[yellow]No single configured image host supports every remaining tracker. "
            "Compatible trackers will use their own configured image-host fallback when needed.[/yellow]"
        )
    return remaining


def _compute_image_host_policy(
    meta: Meta,
    relevant_trackers: list[str],
    configured: list[str],
) -> tuple[list[str] | None, list[str]]:
    policies = _image_host_policy_sets(relevant_trackers, configured)
    if policies is None or not configured:
        logger.debug(
            "[cyan]Image host debug: cannot compute common host because at least one tracker has no declared host policy or no hosts are configured.[/cyan]"
        )
        return None, relevant_trackers
    common_configured = _log_image_host_policy_result(policies, configured)
    if common_configured:
        _switch_to_common_image_host(meta, common_configured)
        return common_configured, relevant_trackers
    return None, _remaining_image_host_trackers(
        meta, relevant_trackers, policies, configured
    )


def _resolve_image_host_policy(
    meta: Meta,
    relevant_trackers: list[str],
    configured_hosts: Sequence[str],
) -> tuple[list[str] | None, list[str]]:
    if not _smart_image_host_selection_enabled(meta, relevant_trackers):
        return None, relevant_trackers
    configured = list(configured_hosts)
    try:
        _log_image_host_policy_inputs(meta, relevant_trackers, configured)
        return _compute_image_host_policy(meta, relevant_trackers, configured)
    except Exception as error:
        logger.debug(
            f"[yellow]Could not determine a common approved image host: {error}[/yellow]"
        )
        return None, relevant_trackers


def _screenshot_upload_minimum(meta: Meta) -> int:
    minimum = configured_screenshot_minimum(config)
    actual_screens, required_minimum = available_screens(meta, minimum)
    if meta.category == "BOOK":
        meta.screens = actual_screens
        return min(required_minimum, actual_screens)
    if meta.category == "XXX":
        return xxx_min_successful_uploads(meta, minimum)
    return minimum


def _configured_allowed_host_order(
    allowed_hosts: list[str] | None, configured_hosts: Sequence[str]
) -> list[str]:
    return [
        str(host)
        for host in configured_hosts
        if allowed_hosts is None or str(host) in allowed_hosts
    ]


def _current_image_host(meta: Meta) -> str:
    return str(
        meta.imghost or _process_default_config().get("img_host_1") or ""
    )


def _prepend_current_image_host(
    order: list[str], current: str, allowed_hosts: list[str] | None
) -> list[str]:
    if not current:
        return order
    allowed = allowed_hosts is None or current in allowed_hosts
    if allowed and current not in order:
        return [current, *order]
    return order


def _fallback_image_host_order(
    order: list[str], allowed_hosts: list[str] | None
) -> list[str]:
    if order or not allowed_hosts:
        return order
    return list(allowed_hosts)


def _allowed_screenshot_host_order(
    meta: Meta,
    allowed_hosts: list[str] | None,
    configured_hosts: Sequence[str],
) -> list[str]:
    current = _current_image_host(meta)
    order = _configured_allowed_host_order(allowed_hosts, configured_hosts)
    order = _prepend_current_image_host(order, current, allowed_hosts)
    order = _fallback_image_host_order(order, allowed_hosts)
    resolved = current or (order[0] if order else "")
    if resolved:
        meta.imghost = resolved
    return order


async def _populate_tracker_image_keys(
    meta: Meta, trackers: list[str]
) -> None:
    for tracker_name in trackers:
        tracker_instance = tracker_class_map[tracker_name](config=config)
        await check_tracker_image_hosts(meta, tracker_instance)


def _validate_uploaded_screenshot_count(meta: Meta, minimum: int) -> None:
    count = len(meta.image_list or [])
    logger.debug(
        f"[cyan]Image host debug: post-upload_screens image_list={count}[/cyan]"
    )
    if count >= minimum:
        return
    requirements_error = screenshot_requirement_error(meta, config)
    if requirements_error:
        raise Exception(requirements_error)
    logger.info(
        f"[yellow]Only {count} images uploaded; minimum is {minimum}, but continuing without hosted screenshots. "
        "Configure --skip-imagehost-upload or another approved host to avoid this warning.[/yellow]"
    )


async def _execute_release_screenshot_upload(
    meta: Meta,
    relevant_trackers: list[str],
    allowed_hosts: list[str] | None,
    configured_hosts: Sequence[str],
) -> None:
    minimum = _screenshot_upload_minimum(meta)
    _allowed_screenshot_host_order(meta, allowed_hosts, configured_hosts)
    await uploadscreens_manager.upload_screens(
        meta,
        meta.screens,
        1,
        0,
        meta.screens,
        [],
        return_dict={},
        allowed_hosts=allowed_hosts,
    )
    _validate_uploaded_screenshot_count(meta, minimum)
    await _populate_tracker_image_keys(meta, relevant_trackers)


async def _upload_required_release_screenshots(
    meta: Meta,
    configured_hosts: Sequence[str],
) -> bool:
    if not _release_screenshot_upload_needed(meta):
        if meta.skip_imghost_upload is True and not meta.image_list:
            meta.image_list = []
        return True
    relevant = _relevant_image_host_trackers(meta)
    allowed_hosts, relevant = _resolve_image_host_policy(
        meta, relevant, configured_hosts
    )
    try:
        await _execute_release_screenshot_upload(
            meta, relevant, allowed_hosts, configured_hosts
        )
        return True
    except asyncio.CancelledError:
        logger.info(
            "\n[red]Upload process interrupted! Cancelling tasks...[/red]"
        )
        return False
    finally:
        cleanup_manager.reset_terminal()
        logger.debug("[yellow]Cleaning up resources...[/yellow]")
        gc.collect()


def _existing_cover_path(value: Any) -> Path | None:
    rendered = str(value or "")
    if not rendered:
        return None
    path = Path(rendered)
    return path if path.is_file() else None


def _existing_book_cover_path(meta: Meta) -> Path | None:
    return _existing_cover_path(meta.artwork_path) or _existing_cover_path(
        meta.artwork_url
    )


async def _downloaded_book_cover_path(meta: Meta) -> Path | None:
    artwork_url = str(meta.artwork_url or "")
    if not _is_http_url(artwork_url):
        return None
    content = await asyncio.to_thread(_download_music_cover, artwork_url)
    if content is None:
        logger.error(
            f"[red]Error downloading artwork from {artwork_url}: unsafe, invalid, or oversized image response[/red]"
        )
        return None
    destination = artwork_dir(meta.base_dir, meta.uuid) / "poster.jpg"
    destination.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(destination.write_bytes, content)
    meta.artwork_path = str(destination)
    return destination


async def _materialized_book_cover(meta: Meta) -> Path | None:
    existing = _existing_book_cover_path(meta)
    return (
        existing
        if existing is not None
        else await _downloaded_book_cover_path(meta)
    )


async def _reuse_cached_book_cover(meta: Meta, cache_path: Path) -> bool:
    if not cache_path.exists():
        return False
    try:
        async with aiofiles.open(cache_path, encoding="utf-8") as handle:
            loaded = json.loads(await handle.read())
    except Exception as error:
        logger.debug(f"[red]Error reading covers.json cache: {error}")
        return False
    records = _music_cover_records(loaded)
    raw_url = _first_music_cover_url(records)
    if not raw_url:
        return False
    meta.hosted_artwork = records
    meta.artwork_url = raw_url
    meta.rehosted_artwork_url = raw_url
    logger.debug(f"[green]Using cached cover from covers.json: {raw_url}")
    return True


async def _upload_book_cover(
    meta: Meta, artwork_path: Path, cache_path: Path
) -> None:
    try:
        uploaded_raw, _ = await uploadscreens_manager.upload_screens(
            meta, 1, 1, 0, 1, [str(artwork_path)], {}
        )
    except Exception as error:
        logger.error(f"[red]Error uploading book cover: {error}[/red]")
        return
    uploaded = _music_cover_records(uploaded_raw)
    if not uploaded:
        logger.error(
            "[red]Failed to upload book cover: upload_screens returned empty result"
        )
        return
    raw_url = _first_music_cover_url(uploaded)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(cache_path, "w", encoding="utf-8") as handle:
        await handle.write(json.dumps(uploaded, indent=4))
    meta.hosted_artwork = uploaded
    if raw_url:
        meta.artwork_url = raw_url
        meta.rehosted_artwork_url = raw_url
    logger.debug(
        f"[green]Successfully uploaded book cover and saved to covers.json: {raw_url}"
    )


def _book_cover_hosting_needed(meta: Meta) -> bool:
    return meta.category == "BOOK" and not meta.debug


async def _host_materialized_book_cover(
    meta: Meta, artwork_path: Path
) -> None:
    cache_path = _covers_cache_path(meta)
    if await _reuse_cached_book_cover(meta, cache_path):
        return
    await _upload_book_cover(meta, artwork_path, cache_path)


async def _host_book_cover_for_visuals(meta: Meta) -> None:
    if not _book_cover_hosting_needed(meta):
        return
    artwork_path = await _materialized_book_cover(meta)
    if artwork_path is None or not artwork_path.exists():
        return
    await _host_materialized_book_cover(meta, artwork_path)


async def _save_visual_image_data(meta: Meta) -> None:
    image_list = cast(list[Any], meta.image_list or [])
    if not image_list:
        return
    image_data: dict[str, Any] = {
        "image_list": image_list,
        "image_sizes": meta.image_sizes,
        "tonemapped": meta.tonemapped,
    }
    try:
        async with aiofiles.open(
            _visual_image_data_path(meta), "w", encoding="utf-8"
        ) as handle:
            await handle.write(json.dumps(image_data, indent=4))
        logger.debug(
            f"[cyan]Saved {len(image_list)} images to image_data.json"
        )
    except Exception as error:
        logger.info(f"[yellow]Failed to save image data: {error!s}")


async def _process_standard_visual_artifacts(
    meta: Meta,
    base_dir: str,
    configured_hosts: Sequence[str],
    videopath: str,
    filename: str,
    bdmv_filename: Any,
    bdinfo: Any,
) -> bool:
    manual_frames = meta.manual_frames or ""
    meta.manual_frames = manual_frames
    await _restore_visual_caches(meta)
    await _capture_release_screenshots(
        meta,
        base_dir,
        videopath,
        filename,
        bdmv_filename,
        bdinfo,
        manual_frames,
    )
    _ensure_process_image_list(meta)
    if not await _prepare_music_cover_for_visuals(meta):
        return False
    _apply_manual_frame_count(meta)
    if not await _upload_required_release_screenshots(meta, configured_hosts):
        return False
    await _validate_screenshots_then_process_optional(
        meta, config, uploadscreens_manager
    )
    await _host_book_cover_for_visuals(meta)
    await _persist_process_meta(meta)
    await _save_visual_image_data(meta)
    return True


async def _process_visual_artifacts_phase(
    meta: Meta,
    base_dir: str,
    configured_hosts: Sequence[str],
    videopath: str,
    filename: str,
    bdmv_filename: Any,
    bdinfo: Any,
) -> bool:
    if meta.comparison:
        await ComparisonManager(meta, config).add_comparison()
        return True
    return await _process_standard_visual_artifacts(
        meta,
        base_dir,
        configured_hosts,
        videopath,
        filename,
        bdmv_filename,
        bdinfo,
    )


def _process_early_artifact_tasks(meta: Meta) -> tuple[Any, Any]:
    tasks = get_early_artifact_tasks(meta.uuid) or start_early_artifact_tasks(
        meta, client, config
    )
    return tasks[0], tasks[1]


async def _run_visual_artifacts_with_progress(
    meta: Meta,
    base_dir: str,
    configured_hosts: Sequence[str],
    videopath: str,
    filename: str,
    bdmv_filename: Any,
    bdinfo: Any,
) -> bool:
    progress_task = asyncio.create_task(
        print_progress("[yellow]Still processing, please wait...", interval=10)
    )
    try:
        return await _process_visual_artifacts_phase(
            meta,
            base_dir,
            configured_hosts,
            videopath,
            filename,
            bdmv_filename,
            bdinfo,
        )
    finally:
        progress_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await progress_task


def _base_torrent_paths(meta: Meta) -> tuple[str, str]:
    directory = Path(meta.base_dir) / "tmp" / meta.uuid
    return (
        str((directory / "BASE.torrent").resolve()),
        str((directory / "BASE_SUBS.torrent").resolve()),
    )


async def _await_process_early_artifacts(
    meta: Meta, base_task: Any, usenet_task: Any
) -> None:
    try:
        await asyncio.gather(base_task, usenet_task)
    finally:
        await cancel_and_drain_early_artifact_tasks(meta.uuid)


async def _force_recheck_process_torrent(meta: Meta) -> None:
    if not meta.force_recheck:
        return
    await Wait(config).select_and_recheck_best_torrent(
        meta, cast(str, meta.path), check_interval=5
    )


def _reuse_search_needed(meta: Meta, torrent_path: str) -> bool:
    return bool(
        meta.rehash is False
        and not Path(torrent_path).exists()
        and not meta.base_torrent_created
        and not meta.we_checked_them_all
    )


async def _reuse_existing_base_torrent(meta: Meta, torrent_path: str) -> None:
    if not _reuse_search_needed(meta, torrent_path):
        return
    reuse_torrent = meta.reuse_torrent_path
    if not reuse_torrent or not Path(reuse_torrent).exists():
        reuse_torrent = await client.find_existing_torrent(meta)
    if reuse_torrent is None:
        return
    await TORRENT_CREATOR.create_base_from_existing_torrent(
        reuse_torrent, meta.base_dir, meta.uuid
    )


async def _create_rehashed_base_torrents(
    meta: Meta, has_local_subs: bool
) -> None:
    await TORRENT_CREATOR.create_torrent(
        meta, Path(cast(str, meta.path)), "BASE"
    )
    if has_local_subs:
        await TORRENT_CREATOR.create_torrent(
            meta, Path(cast(str, meta.path)), "BASE_SUBS"
        )


def _base_reuse_candidate_allowed(meta: Meta, has_local_subs: bool) -> bool:
    candidate = meta.base_reuse_torrent_path
    if not candidate or not Path(candidate).exists():
        return False
    return bool(
        not has_local_subs or client._torrent_has_no_subtitles(candidate)
    )


async def _reuse_base_candidate_if_needed(
    meta: Meta, torrent_path: str, has_local_subs: bool
) -> None:
    if Path(torrent_path).exists():
        return
    if not _base_reuse_candidate_allowed(meta, has_local_subs):
        return
    await TORRENT_CREATOR.create_base_from_existing_torrent(
        cast(str, meta.base_reuse_torrent_path), meta.base_dir, meta.uuid
    )


async def _create_base_if_missing(meta: Meta, torrent_path: str) -> None:
    if Path(torrent_path).exists() or meta.nohash is not False:
        return
    await TORRENT_CREATOR.create_torrent(
        meta, Path(cast(str, meta.path)), "BASE"
    )


async def _create_subs_base_if_missing(
    meta: Meta, subs_torrent_path: str, has_local_subs: bool
) -> None:
    if not has_local_subs or Path(subs_torrent_path).exists():
        return
    if meta.nohash is not False:
        return
    await TORRENT_CREATOR.create_torrent(
        meta, Path(cast(str, meta.path)), "BASE_SUBS"
    )


async def _create_missing_base_torrents(
    meta: Meta,
    torrent_path: str,
    subs_torrent_path: str,
    has_local_subs: bool,
) -> None:
    await _reuse_base_candidate_if_needed(meta, torrent_path, has_local_subs)
    await _create_base_if_missing(meta, torrent_path)
    await _create_subs_base_if_missing(meta, subs_torrent_path, has_local_subs)


async def _prepare_process_base_torrents(
    meta: Meta,
    torrent_path: str,
    subs_torrent_path: str,
    has_local_subs: bool,
) -> bool:
    is_usenet_only = _is_usenet_only(meta)
    if is_usenet_only:
        return True
    await _reuse_existing_base_torrent(meta, torrent_path)
    if meta.rehash is True and meta.nohash is False:
        await _create_rehashed_base_torrents(meta, has_local_subs)
    else:
        await _create_missing_base_torrents(
            meta, torrent_path, subs_torrent_path, has_local_subs
        )
    return False


def _base_piece_cache_needed(meta: Meta, torrent_path: str) -> bool:
    if (
        not Path(torrent_path).exists()
        or meta.base_torrent_piece_mb is not None
    ):
        return False
    trackers = set(_normalized_tracker_values(meta.trackers))
    return bool(trackers & {"HDBITS", "PASSTHEPOPCORN"})


async def _cache_process_base_piece_size(
    meta: Meta, torrent_path: str
) -> None:
    if not _base_piece_cache_needed(meta, torrent_path):
        return
    try:
        torrent = await asyncio.to_thread(TORF_Torrent.read, torrent_path)
        meta.base_torrent_piece_mb = torrent.piece_size // (1024 * 1024)
    except Exception as error:
        logger.debug(
            f"[yellow]Unable to cache BASE.torrent piece size: {error}"
        )


def _create_process_randomized_torrents(
    meta: Meta, is_usenet_only: bool
) -> None:
    if meta.randomized < 1 or meta.mkbrr or is_usenet_only:
        return
    TORRENT_CREATOR.create_random_torrents(
        meta.base_dir, meta.uuid, meta.randomized, cast(str, meta.path)
    )


async def _finalize_process_torrents(
    meta: Meta, torrent_path: str, is_usenet_only: bool
) -> None:
    if meta.nohash:
        meta.client = "none"
    await _cache_process_base_piece_size(meta, torrent_path)
    _create_process_randomized_torrents(meta, is_usenet_only)
    await _persist_process_meta(meta)


async def _initial_process_meta(
    meta: Meta,
) -> tuple[Meta, Sequence[str]] | None:
    if not _resolve_process_image_host(meta):
        return None
    configured_hosts = configured_image_hosts(_process_default_config())
    _apply_process_auto_mode(meta)
    _sync_single_episode(meta)
    prep = Prep(
        screens=meta.screens,
        img_host=meta.imghost,
        config=config,
        argument_parser_factory=Args,
    )
    gathered = await _gather_process_meta(meta, prep)
    if gathered is None:
        return None
    meta = gathered
    identity_error = _movie_tv_identity_error(meta)
    if identity_error:
        logger.info(f"[bold red]{identity_error}[/bold red]")
        await cancel_and_drain_early_artifact_tasks(meta.uuid)
        return None
    await _load_hosted_artwork_cache(meta)
    parser: Any = Args(config)
    helper: Any = UploadHelper(config)
    _prepare_meta_trackers(meta)
    await _refresh_process_name(meta)
    logger.debug(f"Trackers list before editing: {meta.trackers}")
    await _persist_process_meta(meta)
    await _prompt_process_category_meta(meta)
    meta = await gen_desc(meta, takescreens_manager, uploadscreens_manager)
    meta = await _process_confirmation_edits(meta, prep, parser, helper)
    return meta, configured_hosts


async def _tracker_gated_process_meta(
    meta: Meta, base_dir: str
) -> tuple[list[str], Common] | None:
    successful_trackers, trackers = await _process_tracker_status_phase(meta)
    if not _tracker_threshold_passed(meta, successful_trackers):
        return None
    meta.we_are_uploading = True
    common = Common(config)
    if await _run_site_check(meta, base_dir, common):
        return None
    return trackers, common


async def _complete_process_meta(
    meta: Meta,
    base_dir: str,
    configured_hosts: Sequence[str],
    trackers: list[str],
    common: Common,
) -> bool:
    early_base_torrent_task, early_usenet_prepare_task = (
        _process_early_artifact_tasks(meta)
    )
    videopath = await _prepare_visual_preflight(meta, trackers, common)
    if not await _run_visual_artifacts_with_progress(
        meta,
        base_dir,
        configured_hosts,
        videopath,
        str(meta.title),
        meta.filename,
        meta.bdinfo,
    ):
        return False
    has_local_subs = bool(meta.subtitle_files)
    torrent_path, subs_torrent_path = _base_torrent_paths(meta)
    await _await_process_early_artifacts(
        meta, early_base_torrent_task, early_usenet_prepare_task
    )
    await _force_recheck_process_torrent(meta)
    is_usenet_only = await _prepare_process_base_torrents(
        meta, torrent_path, subs_torrent_path, has_local_subs
    )
    await _finalize_process_torrents(meta, torrent_path, is_usenet_only)
    return True


async def process_meta(meta: Meta, base_dir: str) -> bool:
    """Process the metadata for each queued path."""
    initial = await _initial_process_meta(meta)
    if initial is None:
        return False
    meta, configured_hosts = initial
    gated = await _tracker_gated_process_meta(meta, base_dir)
    if gated is None:
        return True
    trackers, common = gated
    return await _complete_process_meta(
        meta, base_dir, configured_hosts, trackers, common
    )


def _temporary_screenshot_files(screenshot_path: Path) -> list[Path]:
    if not screenshot_path.exists():
        return []
    return [
        path
        for path in screenshot_path.iterdir()
        if path.is_file() and path.suffix.lower() in {".png", ".jpg"}
    ]


def _remove_temporary_screenshot_file(path: Path) -> None:
    path.unlink()
    logger.debug(f"[yellow]Removed temporary screenshot file: {path}[/yellow]")


async def cleanup_screenshot_temp_files(meta: Meta) -> None:
    """Cleanup temporary screenshot files to prevent orphaned files in case of failures."""
    screenshot_path = screenshots_dir(meta.base_dir, meta.uuid)
    try:
        for path in _temporary_screenshot_files(screenshot_path):
            _remove_temporary_screenshot_file(path)
    except Exception as error:
        logger.error(
            f"[red]Error cleaning up temporary screenshot files: {error}[/red]",
            extra={"highlighter": None},
        )


def _processed_file_entries(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    return [str(item) for item in cast(list[object], value)]


async def _read_processed_file_log(log_path: Path) -> list[str]:
    if not log_path.exists():
        return []
    try:
        async with aiofiles.open(log_path, encoding="utf-8") as handle:
            loaded = json.loads(await handle.read())
    except Exception as error:
        logger.error(
            f"[red]Error reading log file {log_path}: {error}[/red]",
            extra={"highlighter": None},
        )
        return []
    entries = _processed_file_entries(loaded)
    if entries is not None:
        return entries
    logger.warning(
        f"Log file {log_path} does not contain a JSON list.",
        extra={"highlighter": None},
    )
    return []


def _processed_file_log_with_latest(
    entries: list[str], file_path: str
) -> list[str]:
    return [entry for entry in entries if entry != file_path] + [file_path]


async def save_processed_file(log_file: str, file_path: str) -> None:
    """Add a processed file to the log, deduplicating and appending it last."""
    log_path = Path(log_file)
    entries = await _read_processed_file_log(log_path)
    updated = _processed_file_log_with_latest(entries, file_path)
    async with aiofiles.open(log_path, "w", encoding="utf-8") as handle:
        await handle.write(json.dumps(updated, indent=4))


def get_local_version(version_file: str | Path) -> str:
    """Resolve the installed application version without depending on CWD."""
    path = Path(version_file)
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as error:
        logger.debug(
            f"Version metadata file unavailable at {path}: {error}. Using packaged version {application_version.__version__}."
        )
        return application_version.__version__
    match = re.search(r'__version__\s*=\s*"([^"]+)"', content)
    if match:
        return match.group(1)
    logger.debug(
        f"Version metadata missing from {path}; using packaged version {application_version.__version__}."
    )
    return application_version.__version__


def get_remote_version(url: str) -> tuple[str | None, str | None]:
    """Fetches the latest version information from the remote repository."""
    try:
        response = requests.get(url, timeout=30)
        if response.status_code == 200:
            content = response.text
            match = re.search(r'__version__\s*=\s*"([^"]+)"', content)
            if match:
                return match.group(1), content
            logger.info("[red]Version not found in remote file.")
            return None, None
        logger.error(
            f"[red]Failed to fetch remote version file. Status code: {response.status_code}"
        )
        return None, None
    except requests.RequestException as e:
        logger.info(
            f"[red]An error occurred while fetching the remote version file: {e}"
        )
        return None, None


def _update_notification_cache_path() -> Path:
    return STATE_DIR / "update_notification.json"


def _parsed_update_notification_cache(
    value: Any,
) -> tuple[float, str, str] | None:
    if not isinstance(value, dict):
        return None
    cached = cast(dict[str, Any], value)
    checked_at = cached.get("checked_at")
    remote_version = cached.get("remote_version")
    remote_content = cached.get("remote_content")
    if not isinstance(checked_at, (int, float)):
        return None
    if not isinstance(remote_version, str) or not isinstance(
        remote_content, str
    ):
        return None
    return float(checked_at), remote_version, remote_content


def _fresh_update_notification_cache(
    parsed: tuple[float, str, str], cache_hours: float
) -> tuple[str, str] | None:
    checked_at, remote_version, remote_content = parsed
    if time.time() - checked_at >= cache_hours * 3600:
        return None
    return remote_version, remote_content


def _read_update_notification_cache(
    cache_hours: float,
) -> tuple[str, str] | None:
    """Return a still-valid remote version response from the runtime cache."""
    try:
        parsed_json = json.loads(
            _update_notification_cache_path().read_text(encoding="utf-8")
        )
    except (
        FileNotFoundError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ):
        return None
    parsed = _parsed_update_notification_cache(parsed_json)
    return (
        _fresh_update_notification_cache(parsed, cache_hours)
        if parsed
        else None
    )


def _write_update_notification_cache(
    remote_version: str, remote_content: str
) -> None:
    """Persist a successful remote version response for later runs."""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path = _update_notification_cache_path()
        temporary_path = cache_path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(
                {
                    "checked_at": time.time(),
                    "remote_version": remote_version,
                    "remote_content": remote_content,
                }
            ),
            encoding="utf-8",
        )
        temporary_path.replace(cache_path)
    except OSError as exc:
        logger.debug(f"Could not cache update notification: {exc}")


def _clean_changelog(text: str) -> str:
    return re.sub(r"^# ", "", text.strip(), flags=re.MULTILINE)


def _version_assignment_target(node: ast.stmt) -> ast.Name | None:
    if not isinstance(node, ast.Assign) or len(node.targets) != 1:
        return None
    target = node.targets[0]
    return target if isinstance(target, ast.Name) else None


def _version_assignment_value(node: ast.stmt) -> Any:
    target = _version_assignment_target(node)
    if target is None or target.id != "__version__":
        return None
    if not isinstance(node, ast.Assign):
        return None
    return node.value.value if isinstance(node.value, ast.Constant) else None


def _version_assignment_matches(node: ast.stmt, versions: set[str]) -> bool:
    return _version_assignment_value(node) in versions


def _changelog_expr_text(node: ast.stmt) -> str:
    if not isinstance(node, ast.Expr) or not isinstance(
        node.value, ast.Constant
    ):
        return ""
    return node.value.value if isinstance(node.value.value, str) else ""


def _ast_changelog(content: str, to_version: str) -> str | None:
    try:
        body = ast.parse(content).body
    except SyntaxError:
        return None
    versions = {to_version, to_version.lstrip("v")}
    for index, node in enumerate(body[:-1]):
        if not _version_assignment_matches(node, versions):
            continue
        text = _changelog_expr_text(body[index + 1])
        if text:
            return _clean_changelog(text)
    return None


def _legacy_changelog_patterns(to_version: str) -> tuple[str, str]:
    escaped = re.escape(to_version)
    plain = re.escape(to_version.lstrip("v"))
    return (
        rf'__version__\s*=\s*"{escaped}"\s*\n\s*"""\s*(.*?)\s*"""',
        rf'__version__\s*=\s*"{plain}"\s*\n\s*"""\s*(.*?)\s*"""',
    )


def _legacy_changelog(content: str, to_version: str) -> str | None:
    for pattern in _legacy_changelog_patterns(to_version):
        match = re.search(pattern, content, re.DOTALL)
        if match:
            return _clean_changelog(match.group(1))
    return None


def extract_changelog(content: str, to_version: str) -> str | None:
    """Extract changelog entries for the requested version."""
    return _ast_changelog(content, to_version) or _legacy_changelog(
        content, to_version
    )


def _update_default_config() -> dict[str, Any]:
    raw = config.get("DEFAULT", {})
    return cast(dict[str, Any], raw) if isinstance(raw, dict) else {}


def _update_cache_hours(value: Any) -> float:
    try:
        return max(0.0, float(value))
    except TypeError, ValueError:
        logger.warning(
            "[yellow]Invalid update_notification_cache_hours; using 4 hours.[/yellow]"
        )
        return 4.0


def _remote_update_response(
    cache_hours: float,
) -> tuple[str | None, str | None]:
    if cache_hours:
        cached = _read_update_notification_cache(cache_hours)
        if cached is not None:
            return cached
    remote_version_url = "https://raw.githubusercontent.com/wastaken7/Upload-Assistant/master/src/version.py"
    remote_version, remote_content = get_remote_version(remote_version_url)
    if remote_version and remote_content:
        _write_update_notification_cache(remote_version, remote_content)
    return remote_version, remote_content


def _update_available(local_version: str, remote_version: str) -> bool:
    return _parse_version_tuple(remote_version) > _parse_version_tuple(
        local_version
    )


async def _emit_update_notice(
    local_version: str,
    remote_version: str,
    remote_content: str | None,
    verbose: bool,
) -> None:
    logger.info(
        f"[red][NOTICE] [green]Update available: [/green][yellow]{remote_version}"
    )
    logger.info(
        f"[red][NOTICE] [green]Current version: [/green][yellow]{local_version}"
    )
    if not verbose or not remote_content:
        return
    changelog = extract_changelog(remote_content, remote_version)
    if not changelog:
        logger.info("[yellow]Changelog not found between versions.[/yellow]")
        return
    await asyncio.sleep(1)
    logger.info(changelog)


async def update_notification() -> str:
    settings = _update_default_config()
    local_version = get_local_version(CODE_DIR / "src" / "version.py")
    if not local_version:
        return ""
    if not bool(settings.get("update_notification", True)):
        return local_version
    cache_hours = _update_cache_hours(
        settings.get("update_notification_cache_hours", 4)
    )
    remote_version, remote_content = _remote_update_response(cache_hours)
    if remote_version and _update_available(local_version, remote_version):
        await _emit_update_notice(
            local_version,
            remote_version,
            remote_content,
            bool(settings.get("verbose_notification", False)),
        )
    return local_version


def _tracker_is_usenet(tracker: str) -> bool:
    tracker_class = tracker_class_map.get(tracker.upper().strip())
    return bool(tracker_class and getattr(tracker_class, "is_usenet", False))


def _partition_upload_trackers(meta: Meta) -> tuple[list[str], list[str]]:
    torrent_trackers: list[str] = []
    usenet_trackers: list[str] = []
    for tracker in _normalized_tracker_values(meta.trackers):
        if tracker == "USENET":
            continue
        if _tracker_is_usenet(tracker):
            usenet_trackers.append(tracker)
        else:
            torrent_trackers.append(tracker)
    return torrent_trackers, usenet_trackers


def _eligible_usenet_trackers(
    meta: Meta, usenet_trackers: list[str]
) -> list[str]:
    status_map = _tracker_status_map(meta)
    return [
        tracker
        for tracker in usenet_trackers
        if status_map.get(tracker, {}).get("upload", False)
    ]


def _need_usenet_post(meta: Meta, eligible_usenet_trackers: list[str]) -> bool:
    explicit = "USENET" in _normalized_tracker_values(meta.trackers) or bool(
        meta.usenet
    )
    return explicit or bool(eligible_usenet_trackers)


def _mark_usenet_tracker_failure(
    meta: Meta, tracker: str, message: str
) -> None:
    status = _tracker_status_map(meta).setdefault(tracker, {})
    if status.get("upload_success") is True:
        return
    status.update(
        status_message=message,
        upload=True,
        upload_success=False,
    )


def _mark_usenet_failures(
    meta: Meta, trackers: list[str], message: str
) -> None:
    for tracker in trackers:
        _mark_usenet_tracker_failure(meta, tracker, message)


async def _prepare_usenet_nzb(
    meta: Meta,
) -> tuple[str | None, Exception | None]:
    from src.integrations.usenet.creator import prepare_and_upload_usenet

    try:
        return await prepare_and_upload_usenet(meta, config), None
    except Exception as error:
        return None, error


async def _process_usenet_indexers(meta: Meta, trackers: list[str]) -> None:
    if not trackers:
        return
    meta_usenet = meta.copy()
    meta_usenet["trackers"] = trackers
    meta_usenet.tracker_status = meta.tracker_status
    logger.info(
        f"[yellow]Processing uploads to Usenet indexers: {', '.join(trackers)}....."
    )
    await process_trackers(
        meta_usenet,
        config,
        client,
        list(api_trackers),
        tracker_class_map,
        list(http_trackers),
        list(other_api_trackers),
        upload_target="usenet indexer",
        argument_parser_factory=Args,
    )


async def _process_usenet_indexers_safely(
    meta: Meta, trackers: list[str]
) -> None:
    try:
        await _process_usenet_indexers(meta, trackers)
    except Exception as error:
        logger.info(
            f"[bold red]Error in Usenet upload pipeline: {error}[/bold red]"
        )
        logger.info(traceback.format_exc())
        _mark_usenet_failures(
            meta,
            trackers,
            f"data error: Usenet upload failed: {error}",
        )


async def _upload_usenet_flow(
    meta: Meta,
    usenet_trackers: list[str],
    need_usenet_post: bool,
    has_usenet_trackers: bool,
) -> None:
    if not need_usenet_post:
        if has_usenet_trackers:
            logger.info(
                "[yellow]Skipping NNTP Usenet post because no Usenet indexers passed the upload checks.[/yellow]"
            )
        return
    nzb_path, error = await _prepare_usenet_nzb(meta)
    if error is not None:
        logger.info(
            f"[bold red]Error in Usenet upload pipeline: {error}[/bold red]"
        )
        logger.info(traceback.format_exc())
        _mark_usenet_failures(
            meta,
            usenet_trackers,
            f"data error: Usenet upload failed: {error}",
        )
        return
    if not nzb_path:
        logger.info("[bold red]Usenet upload failed.[/bold red]")
        _mark_usenet_failures(
            meta,
            usenet_trackers,
            "data error: Usenet upload failed, NZB missing",
        )
        return
    meta.nzb_path = nzb_path
    logger.info("[bold green]Usenet upload completed successfully!")
    await _process_usenet_indexers_safely(meta, usenet_trackers)


async def _upload_torrent_flow(
    meta: Meta, torrent_trackers: list[str]
) -> None:
    if not torrent_trackers:
        return
    meta_torrent = meta.copy()
    meta_torrent["trackers"] = torrent_trackers
    meta_torrent.tracker_status = meta.tracker_status
    await process_trackers(
        meta_torrent,
        config,
        client,
        list(api_trackers),
        tracker_class_map,
        list(http_trackers),
        list(other_api_trackers),
        argument_parser_factory=Args,
    )


def _normalized_upload_order(meta: Meta) -> str:
    raw = meta.upload_order or _process_default_config().get(
        "upload_order", "concurrent"
    )
    return raw.strip().lower() if isinstance(raw, str) else "concurrent"


def _bandwidth_settings(meta: Meta) -> tuple[int, int]:
    defaults = _process_default_config()
    raw_threshold = meta.qbit_bandwidth_threshold or defaults.get(
        "qbit_bandwidth_threshold", 0
    )
    raw_time = meta.qbit_bandwidth_time or defaults.get(
        "qbit_bandwidth_time", 0
    )
    try:
        return int(raw_threshold), int(raw_time)
    except (TypeError, ValueError) as error:
        logger.info(
            f"[red]Invalid bandwidth settings: {error}, skipping bandwidth wait before Usenet upload.[/red]"
        )
        return 0, 0


async def _wait_before_usenet(meta: Meta) -> None:
    from src.integrations.torrent_clients.bandwidth import Wait

    try:
        threshold, seconds = _bandwidth_settings(meta)
        if threshold > 0 and seconds > 0:
            await Wait(config).wait_for_bandwidth(threshold, seconds)
            return
        logger.info(
            "[yellow]Bandwidth control threshold or time is 0 or not configured. Skipping bandwidth check.[/yellow]"
        )
    except Exception as error:
        logger.info(
            f"[red]Error initializing bandwidth check: {error}, skipping bandwidth wait before Usenet upload.[/red]"
        )


async def _run_upload_flows(
    meta: Meta,
    torrent_trackers: list[str],
    eligible_usenet_trackers: list[str],
    need_usenet_post: bool,
    has_usenet_trackers: bool,
) -> None:
    order = _normalized_upload_order(meta)
    if order == "usenet":
        await _upload_usenet_flow(
            meta,
            eligible_usenet_trackers,
            need_usenet_post,
            has_usenet_trackers,
        )
        await _upload_torrent_flow(meta, torrent_trackers)
        return
    if order == "tracker":
        await _upload_torrent_flow(meta, torrent_trackers)
        if need_usenet_post and torrent_trackers:
            logger.info(
                "\n[yellow]Torrent uploads completed. Checking bandwidth before starting Usenet upload...[/yellow]"
            )
            await _wait_before_usenet(meta)
        await _upload_usenet_flow(
            meta,
            eligible_usenet_trackers,
            need_usenet_post,
            has_usenet_trackers,
        )
        return
    await asyncio.gather(
        _upload_usenet_flow(
            meta,
            eligible_usenet_trackers,
            need_usenet_post,
            has_usenet_trackers,
        ),
        _upload_torrent_flow(meta, torrent_trackers),
    )


@dataclass
class _BatchProgress:
    processed: int = 0
    skipped: int = 0
    failed_items: list[tuple[str, str]] = field(default_factory=list)
    partial_items: list[tuple[str, str]] = field(default_factory=list)
    outcomes: dict[int, tuple[str, str, str]] = field(default_factory=dict)


def _queue_item_mapping(queue_item: Any) -> dict[str, Any]:
    if not isinstance(queue_item, Mapping):
        return {}
    source = cast(Mapping[Any, Any], queue_item)
    mapping: dict[str, Any] = {}
    for key, value in source.items():
        mapping[str(key)] = value
    return mapping


def _queue_item_identifier(queue_item: Any) -> str:
    mapping = _queue_item_mapping(queue_item)
    if not mapping:
        return str(queue_item)
    return str(mapping.get("line") or mapping.get("path") or queue_item)


def _reload_runtime_configuration() -> None:
    try:
        reloaded = cast(dict[str, Any], _CONFIGURATION_SERVICE.load_mutable())
    except ConfigurationError as error:
        logger.warning(
            f"[yellow]Warning: could not reload config from disk: {error}[/yellow]"
        )
        return
    config.clear()
    config.update(reloaded)
    default_runtime_config = cast(dict[str, Any], config.get("DEFAULT", {}))
    configure_console(default_runtime_config)
    configure_binary_paths(default_runtime_config)


def _ensure_secure_tmp_subdir(subdir_path: str | Path) -> None:
    """Ensure tmp subdirectories are created with secure permissions."""
    path = Path(subdir_path)
    if not path.exists():
        mode = 0o700 if os.name != "nt" else 0o777
        path.mkdir(parents=True, mode=mode, exist_ok=True)
        return
    if os.name != "nt":
        path.chmod(0o700)


def _apply_pasted_paths(
    pasted_paths: list[str], remaining_args: list[str]
) -> bool:
    if not pasted_paths:
        return False
    resolved, missing = partition_existing_paths(pasted_paths)
    if missing:
        logger.warning(
            "[yellow]Skipping pasted paths that do not exist:[/yellow]"
        )
        for missing_path in missing:
            logger.warning(f"[yellow]  - {missing_path}[/yellow]")
    if not resolved:
        logger.error("[red]Error: None of the pasted paths exist.[/red]")
        raise SystemExit(2)
    sys.argv[1:] = [*resolved, *remaining_args]
    return True


def _read_runtime_paths() -> tuple[list[str], bool]:
    try:
        remaining_args, pasted_paths = read_paths_from_stdin(
            sys.argv[1:], sys.stdin
        )
    except ValueError as error:
        logger.error(f"[red]Error: {error}.[/red]")
        raise SystemExit(2) from error
    used_pasted = _apply_pasted_paths(pasted_paths, remaining_args)
    paths: list[str] = []
    for each in sys.argv[1:]:
        candidate = Path(each)
        if not candidate.exists():
            break
        paths.append(str(candidate.resolve()))
    return paths, used_pasted


async def _runtime_meta(base_dir: str) -> Meta:
    meta = Meta()
    meta.ua_name = "Upload-Assistant"
    meta.current_version = await update_notification()
    signature = f"Shared with {meta.ua_name}"
    if meta.current_version:
        signature += f" {meta.current_version}"
    meta.ua_signature = signature + " (fork)"
    meta.base_dir = base_dir
    return meta


def _cleanup_only_requested() -> bool:
    return bool(
        any(arg in {"--cleanup", "-cleanup"} for arg in sys.argv)
        and len(sys.argv) <= 2
    )


def _parse_runtime_meta(meta: Meta, cleanup_only: bool) -> Meta:
    args_list = (
        [*sys.argv[1:], "dummy_path"] if cleanup_only else list(sys.argv[1:])
    )
    parsed = cast(tuple[Meta, Any, Any], parser.parse(args_list, meta))[0]
    if cleanup_only:
        parsed.path = None
    return parsed


def _enable_runtime_debug(meta: Meta) -> None:
    if not (meta.debug or bool(_process_default_config().get("debug", False))):
        return
    meta.debug = True
    logger.setLevel(logging.DEBUG)
    defaults = _process_default_config()
    RICH_HANDLER._log_render.show_time = bool(
        defaults.get("console_debug_show_time", True)
    )
    RICH_HANDLER._log_render.show_level = bool(
        defaults.get("console_debug_show_level", True)
    )
    RICH_HANDLER._log_render.show_path = bool(
        defaults.get("console_debug_show_path", True)
    )
    RICH_HANDLER.markup = bool(defaults.get("console_debug_markup", True))


def _active_config_trackers(meta: Meta) -> list[str] | None:
    trackers = _normalized_tracker_values(meta.trackers)
    return trackers or None


def _active_config_imghost(meta: Meta) -> str | None:
    value = str(meta.imghost or "").strip()
    return value or None


def _log_configuration_errors(errors: list[str]) -> None:
    logger.info("[bold red]Configuration validation failed:[/bold red]")
    for error in errors:
        logger.info(f"[red]  ✗ {error}[/red]")
    logger.info("[red]\nPlease fix the above errors in your config.py[/red]")
    logger.info(
        "[yellow]Reference: https://github.com/wastaken7/Upload-Assistant/blob/development/data/example_config.py[/yellow]"
    )


def _log_configuration_warnings(warnings: list[Any]) -> None:
    if not warnings or _process_default_config().get(
        "suppress_warnings", False
    ):
        return
    from src.services.configuration_validation_service import group_warnings

    grouped = group_warnings(warnings)
    logger.info(
        f"[yellow]Config validation passed with {len(grouped)} warning(s):[/yellow]"
    )
    for warning in grouped:
        logger.info(f"[yellow]  ⚠ {warning}[/yellow]")
    logger.info("")


def _validate_runtime_configuration(meta: Meta) -> None:
    from src.services.configuration_validation_service import validate_config

    valid, errors, warnings = validate_config(
        config, _active_config_trackers(meta), _active_config_imghost(meta)
    )
    if not valid:
        _log_configuration_errors(errors)
        raise SystemExit(1)
    _log_configuration_warnings(warnings)


def _execute_cleanup_request(
    meta: Meta, base_dir: str, cleanup_only: bool
) -> None:
    if not meta.cleanup:
        return
    tmp_dir = Path(base_dir) / "tmp"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
        logger.info("[yellow]Successfully emptied tmp directory[/yellow]")
        logger.info("")
    if not meta.path or cleanup_only:
        raise SystemExit(0)


async def _ensure_runtime_media_binaries() -> None:
    if not configured_binary("ffmpeg_path", config):
        os.environ[
            "UA_FFMPEG_PATH"
        ] = await FfmpegBinaryManager.ensure_ffmpeg_binary(STATE_DIR)
    await ensure_mediainfo_binary(config, state_dir=STATE_DIR)


def _resolved_runtime_path(meta: Meta) -> str:
    if not meta.path:
        raise SystemExit(0)
    path = str(Path(meta.path).resolve())
    return path[:-1] if path.endswith('"') else path


async def _configure_runtime_mkbrr(meta: Meta, base_dir: str) -> None:
    binary_available = await get_mkbrr_path(base_dir)
    if not meta.mkbrr:
        try:
            meta.mkbrr = _process_default_config().get("mkbrr", False)
        except ValueError:
            logger.debug(
                "[yellow]Invalid mkbrr config value, defaulting to False[/yellow]"
            )
            meta.mkbrr = False
    if not meta.mkbrr or binary_available:
        return
    logger.info(
        "[bold red]mkbrr binary is not available. Please ensure it is installed correctly.[/bold red]"
    )
    logger.info("[bold red]Reverting to Torf[/bold red]")
    logger.info("")
    meta.mkbrr = False


async def _runtime_queue(
    meta: Meta, path: str, paths: list[str], base_dir: str
) -> tuple[list[Any], str | None, bool]:
    queue, log_file = await QueueManager.handle_queue(
        path, meta, paths, base_dir
    )
    queue_list = cast(list[Any], queue)
    is_batch = bool(
        len(queue_list) > 1
        or meta.queue
        or meta.site_upload_queue
        or meta.args_line_queue
    )
    return queue_list, log_file, is_batch


def _parser_action_options(action: Any) -> tuple[str, list[str]] | None:
    dest = getattr(action, "dest", None)
    options = getattr(action, "option_strings", None)
    if not dest or not options:
        return None
    return str(dest), [str(option) for option in options]


def _parser_actions(parser_obj: Any) -> list[Any]:
    raw: Any = getattr(parser_obj, "_actions", None)
    if not isinstance(raw, list):
        return []
    return list(cast(list[Any], raw))


def _parser_option_map(parser_obj: Any) -> dict[str, list[str]]:
    entries = filter(
        None, map(_parser_action_options, _parser_actions(parser_obj))
    )
    return dict(cast(Iterable[tuple[str, list[str]]], entries))


def _arg_option_was_set(args_list: list[str], options: list[str]) -> bool:
    return any(
        arg == option or arg.startswith(option + "=")
        for option in options
        for arg in args_list
    )


def _preserve_args_queue_defaults(
    meta: Meta, base_meta: Meta, parser_obj: Any, args_list: list[str]
) -> None:
    option_map = _parser_option_map(parser_obj)
    for key, value in cast(dict[str, Any], base_meta).items():
        if value in (None, False, []):
            continue
        options = option_map.get(key, [])
        if options and not _arg_option_was_set(args_list, options):
            meta[key] = value


async def _site_queue_item_meta(
    base_meta: Meta, queue_item: Any
) -> tuple[Meta, str]:
    meta = base_meta.copy()
    mapping = cast(Mapping[str, Any], queue_item)
    path = await QueueManager.process_site_upload_item(mapping, meta)
    meta.item_args = [path]
    return meta, path


def _args_queue_item_meta(
    base_meta: Meta, queue_item: Mapping[str, Any]
) -> tuple[Meta, str]:
    meta = base_meta.copy()
    args_list = [str(value) for value in cast(list[Any], queue_item["args"])]
    meta, parser_obj, _before_args = cast(
        tuple[Meta, Any, Any], parser.parse(args_list, meta)
    )
    _preserve_args_queue_defaults(meta, base_meta, parser_obj, args_list)
    path = str(queue_item.get("path") or meta.path or "")
    meta.item_args = args_list
    return meta, path


def _regular_queue_item_meta(
    base_meta: Meta, queue_item: Any
) -> tuple[Meta, str]:
    meta = base_meta.copy()
    path = queue_item if isinstance(queue_item, str) else str(queue_item)
    meta.item_args = [path] if meta.queue else list(sys.argv[1:])
    return meta, path


async def _resolve_queue_item_meta(
    base_meta: Meta, queue_item: Any
) -> tuple[Meta, str]:
    if base_meta.site_upload_queue:
        return await _site_queue_item_meta(base_meta, queue_item)
    if (
        base_meta.args_line_queue
        and isinstance(queue_item, Mapping)
        and "args" in queue_item
    ):
        return _args_queue_item_meta(base_meta, queue_item)
    return _regular_queue_item_meta(base_meta, queue_item)


def _reset_item_tmp_directory(meta: Meta, tmp_path: Path, path: str) -> None:
    if not meta.delete_tmp or not tmp_path.exists():
        return
    try:
        shutil.rmtree(tmp_path)
        _ensure_secure_tmp_subdir(tmp_path)
        logger.debug(
            f"[yellow]Successfully cleaned temp directory for {Path(path).name}[/yellow]"
        )
        logger.debug("")
    except Exception as error:
        logger.info(f"[bold red]Failed to delete temp directory: {error!s}")


def _item_meta_path(base_dir: str, path: str) -> Path:
    return Path(base_dir) / "tmp" / Path(path).name / "meta.json"


def _delete_item_meta_cache(
    meta: Meta, meta_file: Path, keep_meta: bool
) -> None:
    if (keep_meta and not meta.delete_meta) or not meta_file.exists():
        return
    try:
        meta_file.unlink()
        logger.debug(
            f"[bold yellow]Found and deleted existing metadata file: {meta_file}"
        )
    except Exception as error:
        logger.info(
            f"[bold red]Failed to delete metadata file {meta_file}: {error!s}"
        )


async def _merge_item_meta_cache(
    meta: Meta, meta_file: Path, keep_meta: bool
) -> None:
    if not keep_meta or not meta_file.exists():
        return
    async with aiofiles.open(meta_file, encoding="utf-8") as handle:
        content = await handle.read()
    loaded: Any = json.loads(content) if content.strip() else {}
    saved_meta = (
        cast(dict[str, Any], loaded) if isinstance(loaded, dict) else {}
    )
    logger.info("[yellow]Existing metadata file found, it holds cached values")
    await merge_meta(meta, saved_meta)


async def _prepare_queue_item(
    base_meta: Meta, queue_item: Any, base_dir: str
) -> tuple[Meta, str, Path]:
    meta, path = await _resolve_queue_item_meta(base_meta, queue_item)
    meta.path = path
    meta.uuid = ""
    if not path:
        raise ValueError("The 'path' variable is not defined or is empty.")
    tmp_path = Path(base_dir) / "tmp" / Path(path).name
    _ensure_secure_tmp_subdir(tmp_path)
    current_release_log_path.set(
        str(tmp_path / f"upload_{int(time.time())}.log")
    )
    _reset_item_tmp_directory(meta, tmp_path, path)
    meta_file = _item_meta_path(base_dir, path)
    keep_meta = bool(_process_default_config().get("keep_meta", False))
    _delete_item_meta_cache(meta, meta_file, keep_meta)
    await _merge_item_meta_cache(meta, meta_file, keep_meta)
    return meta, path, tmp_path


async def _prepare_queue_item_safely(
    base_meta: Meta, queue_item: Any, base_dir: str
) -> tuple[Meta, str, Path | None, str, BaseException | None]:
    identifier = _queue_item_identifier(queue_item)
    meta = base_meta.copy()
    try:
        meta, path, tmp_path = await _prepare_queue_item(
            base_meta, queue_item, base_dir
        )
        return meta, path, tmp_path, "", None
    except KeyboardInterrupt:
        raise
    except SystemExit as error:
        return meta, identifier, None, str(error) or "SystemExit", error
    except Exception as error:
        logger.info(f"[red]Exception: '{identifier}': {error}")
        return meta, identifier, None, str(error), error


def _batch_can_continue(is_batch: bool) -> bool:
    return is_batch and not _shutdown_requested


def _item_error_outcome(error: BaseException | None) -> str:
    return "skipped" if isinstance(error, ItemProcessingError) else "failed"


async def _save_processed_item(
    meta: Meta, log_file: str | None, item_path: str
) -> None:
    if not log_file or (meta.debug and "debug" not in Path(log_file).name):
        return
    if meta.site_upload_queue:
        await QueueManager.save_processed_path(log_file, item_path)
    else:
        await save_processed_file(log_file, item_path)


async def _cleanup_item_state() -> None:
    await cleanup_manager.cleanup()
    gc.collect()
    cleanup_manager.reset_terminal()


async def _record_batch_item_error(
    progress: _BatchProgress,
    item_index: int,
    item_path: str,
    item_error: str,
    item_abort: BaseException | None,
    total_files: int,
    meta: Meta,
    log_file: str | None,
) -> None:
    outcome = _item_error_outcome(item_abort)
    if outcome == "failed":
        progress.failed_items.append((item_path, item_error))
    progress.outcomes[item_index] = (item_path, outcome, item_error)
    progress.processed += 1
    progress.skipped += 1
    logger.info(f"[yellow]Skipping {item_path}: {item_error}[/yellow]")
    logger.info(
        f"[cyan]Processed {progress.processed}/{total_files} files with {progress.skipped} skipped uploading.\n\n"
    )
    await _save_processed_item(meta, log_file, item_path)


def _raise_item_abort(
    item_error: str, item_abort: BaseException | None
) -> None:
    if item_abort is not None:
        raise item_abort
    raise RuntimeError(item_error)


async def _handle_item_error(
    progress: _BatchProgress,
    item_index: int,
    item_path: str,
    item_error: str,
    item_abort: BaseException | None,
    total_files: int,
    meta: Meta,
    log_file: str | None,
    is_batch: bool,
) -> bool:
    if not item_error:
        return False
    if not _batch_can_continue(is_batch):
        _raise_item_abort(item_error, item_abort)
    await _record_batch_item_error(
        progress,
        item_index,
        item_path,
        item_error,
        item_abort,
        total_files,
        meta,
        log_file,
    )
    await _cleanup_item_state()
    return True


def _item_process_error_result(
    error: BaseException,
) -> tuple[bool, str, BaseException | None]:
    detail = str(error) or (
        "SystemExit"
        if isinstance(error, SystemExit)
        else error.__class__.__name__
    )
    return False, detail, error


async def _run_item_process_meta_core(
    meta: Meta, base_dir: str
) -> tuple[bool, str, BaseException | None]:
    try:
        return bool(await process_meta(meta, base_dir)), "", None
    except (SystemExit, Exception) as error:
        return _item_process_error_result(error)


async def _run_item_process_meta(
    meta: Meta, base_dir: str
) -> tuple[bool, str, BaseException | None]:
    try:
        return await _run_item_process_meta_core(meta, base_dir)
    finally:
        await cancel_and_drain_early_artifact_tasks(meta.uuid)


async def _record_metadata_failure(
    progress: _BatchProgress,
    item_index: int,
    item_path: str,
    item_error: str,
    total_files: int,
    meta: Meta,
    log_file: str | None,
    is_batch: bool,
) -> None:
    if is_batch:
        progress.failed_items.append((item_path, item_error))
        progress.outcomes[item_index] = (item_path, "failed", item_error)
        progress.processed += 1
        progress.skipped += 1
        logger.info(
            f"[cyan]Processed {progress.processed}/{total_files} files with {progress.skipped} skipped uploading.\n\n"
        )
        await _save_processed_item(meta, log_file, item_path)
    await _cleanup_item_state()


async def _runtime_batch_setup(
    base_dir: str,
) -> tuple[Meta, list[Any], str | None, bool, bool]:
    _reload_runtime_configuration()
    await asyncio.sleep(0.1)
    ensure_temp_root(base_dir)
    paths, used_pasted_paths = _read_runtime_paths()
    meta = await _runtime_meta(base_dir)
    cleanup_only = _cleanup_only_requested()
    sanitize_meta = bool(_process_default_config().get("sanitize_meta", True))
    meta = _parse_runtime_meta(meta, cleanup_only)
    meta.paths_from_stdin = used_pasted_paths
    _enable_runtime_debug(meta)
    _validate_runtime_configuration(meta)
    _execute_cleanup_request(meta, base_dir, cleanup_only)
    path = _resolved_runtime_path(meta)
    await _ensure_runtime_media_binaries()
    await _configure_runtime_mkbrr(meta, base_dir)
    queue_list, log_file, is_batch = await _runtime_queue(
        meta, path, paths, base_dir
    )
    return meta, queue_list, log_file, is_batch, sanitize_meta


def _tracker_status_mappings(meta: Meta) -> dict[str, Mapping[str, Any]]:
    return {
        tracker: cast(Mapping[str, Any], status)
        for tracker, status in _tracker_status_map(meta).items()
        if isinstance(status, Mapping)
    }


def _status_tracker_names(
    statuses: Mapping[str, Mapping[str, Any]], key: str
) -> list[str]:
    return [
        tracker
        for tracker, status in statuses.items()
        if status.get(key) is True
    ]


def _status_skip_reasons(
    statuses: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    return list(
        dict.fromkeys(
            str(reason)
            for status in statuses.values()
            if (reason := status.get("skip_reason"))
        )
    )


def _duplicate_tracker_names(meta: Meta) -> list[str]:
    return _status_tracker_names(_tracker_status_mappings(meta), "dupe")


def _skipped_tracker_names(meta: Meta) -> list[str]:
    return _status_tracker_names(_tracker_status_mappings(meta), "skipped")


def _status_reason_text(
    statuses: Mapping[str, Mapping[str, Any]],
) -> str:
    reasons = _status_skip_reasons(statuses)
    return "; ".join(reasons) if reasons else ""


def _status_duplicate_text(
    statuses: Mapping[str, Mapping[str, Any]],
) -> str:
    duplicates = _status_tracker_names(statuses, "dupe")
    if not duplicates:
        return ""
    return f"Release already exists on trackers ({', '.join(duplicates)})"


def _status_skipped_text(
    statuses: Mapping[str, Mapping[str, Any]],
) -> str:
    skipped = _status_tracker_names(statuses, "skipped")
    if not skipped:
        return ""
    return f"No eligible trackers after checks ({', '.join(skipped)})"


def _no_eligible_upload_reason(meta: Meta) -> str:
    statuses = _tracker_status_mappings(meta)
    for resolver in (
        _status_reason_text,
        _status_duplicate_text,
        _status_skipped_text,
    ):
        reason = resolver(statuses)
        if reason:
            return reason
    return "No eligible trackers after checks"


async def _record_no_eligible_upload(
    meta: Meta,
    progress: _BatchProgress,
    item_index: int,
    item_path: str,
    total_files: int,
    log_file: str | None,
    is_batch: bool,
) -> None:
    if not is_batch:
        return
    progress.outcomes[item_index] = (
        item_path,
        "skipped",
        _no_eligible_upload_reason(meta),
    )
    progress.processed += 1
    progress.skipped += 1
    logger.info(
        f"[cyan]Processed {progress.processed}/{total_files} files with {progress.skipped} skipped uploading.\n\n"
    )
    await _save_processed_item(meta, log_file, item_path)


async def _handle_no_upload_item(
    meta: Meta,
    progress: _BatchProgress,
    item_index: int,
    item_path: str,
    total_files: int,
    log_file: str | None,
    is_batch: bool,
) -> None:
    if bool(_process_default_config().get("cross_seeding", True)):
        await process_cross_seeds(meta)
    if meta.site_check:
        return
    logger.info(
        "[yellow]No eligible uploads remain after tracker checks.[/yellow]"
    )
    await _record_no_eligible_upload(
        meta,
        progress,
        item_index,
        item_path,
        total_files,
        log_file,
        is_batch,
    )


def _trump_skip_trackers(meta: Meta) -> set[str]:
    return set(_normalized_tracker_values(meta.skip_upload_trackers))


def _mark_trump_statuses(
    tracker_status: dict[str, dict[str, Any]], skipped: set[str]
) -> None:
    for tracker in skipped:
        status = tracker_status.setdefault(tracker, {})
        status["upload"] = False
        status["skipped"] = True


def _remaining_after_trump_skips(meta: Meta, skipped: set[str]) -> list[str]:
    return [
        tracker
        for tracker in _normalized_tracker_values(meta.trackers)
        if tracker not in skipped
    ]


def _log_trump_skip_result(meta: Meta, skipped: set[str]) -> None:
    logger.debug(
        f"[yellow]Skipping trackers due to trump report selection: {', '.join(sorted(skipped))}[/yellow]"
    )
    if not meta.trackers:
        logger.info(
            "[bold red]No trackers left to upload after trump checking.[/bold red]"
        )


def _mark_trump_skips(
    meta: Meta,
    tracker_status: dict[str, dict[str, Any]],
    skipped: set[str],
) -> None:
    _mark_trump_statuses(tracker_status, skipped)
    if not skipped:
        return
    meta.trackers = _remaining_after_trump_skips(meta, skipped)
    _log_trump_skip_result(meta, skipped)


def _tracker_is_selected_trump(
    is_trumping: bool, tracker: str, skipped: set[str]
) -> bool:
    return bool(is_trumping and tracker not in skipped)


async def _apply_trump_checks(meta: Meta, tracker_setup: TrackerSetup) -> None:
    if not meta.were_trumping:
        return
    logger.info("[yellow]Checking for existing trump reports.....")
    tracker_status = _tracker_status_map(meta)
    trumping: list[str] = []
    for tracker in _normalized_tracker_values(meta.trackers):
        is_trumping = bool(
            await tracker_setup.process_trumpables(meta, tracker=tracker)
        )
        skipped = _trump_skip_trackers(meta)
        _mark_trump_skips(meta, tracker_status, skipped)
        if _tracker_is_selected_trump(is_trumping, tracker, skipped):
            # Sequential by design: process_trumpables mutates per-tracker state.
            trumping.append(tracker)
    meta.trumping_trackers = trumping


def _double_dupe_trackers(meta: Meta) -> list[str]:
    status_map = _tracker_status_map(meta)
    eligible: list[str] = []
    for tracker in _normalized_tracker_values(meta.trackers):
        if status_map.get(tracker, {}).get("upload") is True:
            eligible.append(tracker)
        else:
            logger.debug(
                f"[yellow]{tracker} was previously marked to skip upload. Skipping double dupe check.[/yellow]"
            )
            status_map.pop(tracker, None)
    meta.trackers = eligible
    return eligible


async def _double_dupe_success_count(meta: Meta) -> int:
    if not meta.dupe_again:
        return 10
    logger.info(
        "[yellow]Performing double dupe check on trackers that passed initial upload checks.....[/yellow]"
    )
    trackers = _double_dupe_trackers(meta)
    if not trackers:
        return 0
    return int(
        await TrackerStatusManager(config=config).process_all_trackers(meta)
    )


def _upload_success_threshold(meta: Meta) -> int:
    value = meta.skip_uploading
    return int(value) if isinstance(value, (int, str)) else 0


def _upload_threshold_met(meta: Meta, successful_trackers: int) -> bool:
    required = _upload_success_threshold(meta)
    if successful_trackers >= required or meta.debug:
        return True
    logger.info(
        f"[red]Not enough successful trackers ({successful_trackers}/{required}). No uploads being processed.[/red]"
    )
    return False


def _upload_succeeded(meta: Meta) -> bool:
    return any(
        status.get("upload_success") is True
        for status in _tracker_status_mappings(meta).values()
    )


async def _run_meta_uploads(meta: Meta) -> None:
    torrent_trackers, usenet_trackers = _partition_upload_trackers(meta)
    eligible_usenet = _eligible_usenet_trackers(meta, usenet_trackers)
    await _run_upload_flows(
        meta,
        torrent_trackers,
        eligible_usenet,
        _need_usenet_post(meta, eligible_usenet),
        bool(usenet_trackers),
    )
    if bool(_process_default_config().get("cross_seeding", True)):
        await process_cross_seeds(meta)


def _successful_upload_result(failed: list[str]) -> tuple[str, str]:
    return ("partial", ", ".join(failed)) if failed else ("successful", "")


def _unsuccessful_upload_result(
    meta: Meta, failed: list[str]
) -> tuple[str, str]:
    duplicates = _duplicate_tracker_names(meta)
    if duplicates and not failed:
        return (
            "skipped",
            f"Release already exists on trackers ({', '.join(duplicates)})",
        )
    detail = ", ".join(failed) or "no eligible trackers"
    return "failed", f"No tracker upload succeeded ({detail})"


def _upload_result_kind(meta: Meta) -> tuple[str, str]:
    if meta.debug:
        return "checked", "Debug checks completed"
    failed = _failed_tracker_names(_tracker_status_map(meta))
    if _upload_succeeded(meta):
        return _successful_upload_result(failed)
    return _unsuccessful_upload_result(meta, failed)


def _fully_successful_count(progress: _BatchProgress) -> int:
    return sum(
        outcome == "successful"
        for _path, outcome, _detail in progress.outcomes.values()
    )


def _record_success_outcome(
    meta: Meta,
    progress: _BatchProgress,
    item_index: int,
    item_path: str,
    total_files: int,
) -> None:
    progress.outcomes[item_index] = (item_path, "successful", "")
    successful = _fully_successful_count(progress)
    if meta.limit_queue > 0:
        logger.info(
            f"[cyan]Successfully uploaded {successful} of {meta.limit_queue} in limit with {total_files} files."
        )
    else:
        logger.info(
            f"[cyan]Successfully uploaded {successful}/{total_files} files."
        )


def _record_partial_outcome(
    progress: _BatchProgress,
    item_index: int,
    item_path: str,
    detail: str,
) -> None:
    progress.partial_items.append((item_path, detail))
    progress.outcomes[item_index] = (item_path, "partial", detail)
    logger.info(
        f"[yellow]Upload completed partially; failed trackers: {detail}.[/yellow]"
    )


def _record_skipped_outcome(
    progress: _BatchProgress,
    item_index: int,
    item_path: str,
    detail: str,
    total_files: int,
) -> None:
    progress.skipped += 1
    progress.outcomes[item_index] = (item_path, "skipped", detail)
    logger.info(
        f"[yellow]Processed {progress.processed}/{total_files} files; {detail}.[/yellow]"
    )


def _record_failed_outcome(
    progress: _BatchProgress,
    item_index: int,
    item_path: str,
    detail: str,
    total_files: int,
) -> None:
    progress.skipped += 1
    progress.failed_items.append((item_path, detail))
    progress.outcomes[item_index] = (item_path, "failed", detail)
    logger.info(
        f"[yellow]Processed {progress.processed}/{total_files} files; no tracker upload succeeded.[/yellow]"
    )


def _record_checked_outcome(
    progress: _BatchProgress,
    item_index: int,
    item_path: str,
    detail: str,
    total_files: int,
) -> None:
    progress.outcomes[item_index] = (item_path, "checked", detail)
    logger.info(
        f"[cyan]Processed {progress.processed}/{total_files} files in debug mode; no tracker upload was attempted.[/cyan]"
    )


def _record_batch_upload_kind(
    meta: Meta,
    progress: _BatchProgress,
    item_index: int,
    item_path: str,
    total_files: int,
    kind: str,
    detail: str,
) -> None:
    if kind == "successful":
        _record_success_outcome(
            meta, progress, item_index, item_path, total_files
        )
        return
    if kind == "partial":
        _record_partial_outcome(progress, item_index, item_path, detail)
        return
    if kind == "skipped":
        _record_skipped_outcome(
            progress, item_index, item_path, detail, total_files
        )
        return
    if kind == "checked":
        _record_checked_outcome(
            progress, item_index, item_path, detail, total_files
        )
        return
    _record_failed_outcome(
        progress, item_index, item_path, detail, total_files
    )


async def _record_upload_outcome(
    meta: Meta,
    progress: _BatchProgress,
    item_index: int,
    item_path: str,
    total_files: int,
    log_file: str | None,
    is_batch: bool,
) -> None:
    if not is_batch:
        return
    progress.processed += 1
    kind, detail = _upload_result_kind(meta)
    _record_batch_upload_kind(
        meta, progress, item_index, item_path, total_files, kind, detail
    )
    await _save_processed_item(meta, log_file, item_path)


async def _run_item_uploads(
    meta: Meta,
    progress: _BatchProgress,
    item_index: int,
    item_path: str,
    total_files: int,
    log_file: str | None,
    is_batch: bool,
) -> None:
    tracker_setup = TrackerSetup(config=config)
    if not meta.we_are_uploading:
        await _handle_no_upload_item(
            meta,
            progress,
            item_index,
            item_path,
            total_files,
            log_file,
            is_batch,
        )
        return
    await _apply_trump_checks(meta, tracker_setup)
    successful_trackers = await _double_dupe_success_count(meta)
    if not _upload_threshold_met(meta, successful_trackers):
        if is_batch:
            progress.outcomes[item_index] = (
                item_path,
                "skipped",
                "Not enough eligible trackers",
            )
        return
    await _run_meta_uploads(meta)
    await _record_upload_outcome(
        meta, progress, item_index, item_path, total_files, log_file, is_batch
    )


async def _submit_trump_reports(
    meta: Meta, tracker_setup: TrackerSetup
) -> None:
    for tracker in _normalized_tracker_values(meta.trumping_trackers):
        logger.info(f"[yellow]Submitting trumpable report to {tracker}.....")
        await tracker_setup.make_trumpable_report(meta, tracker)


def _request_search_enabled(meta: Meta) -> bool:
    configured = _process_default_config().get("search_requests", False)
    enabled = (
        configured if meta.search_requests is None else meta.search_requests
    )
    if not enabled or not _normalized_tracker_values(meta.trackers):
        return False
    return not (meta.site_check and not meta.is_disc)


def _request_search_trackers(meta: Meta) -> list[str]:
    return (
        _normalized_tracker_values(meta.requested_trackers)
        if meta.site_check
        else _normalized_tracker_values(meta.trackers)
    )


async def _search_tracker_requests(
    meta: Meta, tracker_setup: TrackerSetup
) -> None:
    if not _request_search_enabled(meta):
        return
    logger.info("[green]Searching for requests on supported trackers.....")
    trackers = _request_search_trackers(meta)
    label = (
        "requested trackers for site check"
        if meta.site_check
        else "trackers for request search"
    )
    logger.debug(f"[cyan]Using {label}: {trackers}[/cyan]")
    await tracker_setup.tracker_request(meta, trackers)


async def _record_site_check_item(
    meta: Meta,
    progress: _BatchProgress,
    item_index: int,
    item_path: str,
    total_files: int,
    log_file: str | None,
) -> None:
    if not meta.site_check or meta.queue is None:
        return
    progress.processed += 1
    progress.skipped += 1
    logger.info(f"[cyan]Processed {progress.processed}/{total_files} files.")
    await _save_processed_item(meta, log_file, item_path)
    progress.outcomes.setdefault(
        item_index, (item_path, "checked", "Site check completed")
    )


def _ensure_default_item_outcome(
    meta: Meta,
    progress: _BatchProgress,
    item_index: int,
    item_path: str,
    is_batch: bool,
) -> None:
    if not is_batch or item_index in progress.outcomes:
        return
    kind = "checked" if meta.site_check else "successful"
    detail = "Site check completed" if meta.site_check else ""
    progress.outcomes[item_index] = (item_path, kind, detail)


def _queue_limit_reached(meta: Meta, progress: _BatchProgress) -> bool:
    return bool(
        meta.limit_queue > 0
        and _fully_successful_count(progress) >= meta.limit_queue
    )


def _record_remaining_queue_limit_skips(
    meta: Meta,
    progress: _BatchProgress,
    queue_list: list[Any],
    item_index: int,
) -> None:
    reason = f"Queue limit of {meta.limit_queue} successful upload(s) reached"
    for remaining_index in range(item_index + 1, len(queue_list)):
        remaining_path = _queue_item_identifier(queue_list[remaining_index])
        progress.outcomes[remaining_index] = (
            remaining_path,
            "skipped",
            reason,
        )
        progress.failed_items.append((remaining_path, reason))


async def _sanitize_item_meta(meta: Meta, sanitize_meta: bool) -> Meta:
    if not sanitize_meta:
        return meta
    try:
        await asyncio.sleep(0.2)
        return await Redaction.clean_meta_for_export(meta)
    except Exception as error:
        logger.error(f"[red]Error cleaning meta for export: {error}")
        return meta


async def _postprocess_item(
    meta: Meta,
    progress: _BatchProgress,
    item_index: int,
    item_path: str,
    total_files: int,
    log_file: str | None,
    is_batch: bool,
    queue_list: list[Any],
    sanitize_meta: bool,
) -> tuple[Meta, bool]:
    tracker_setup = TrackerSetup(config=config)
    await _submit_trump_reports(meta, tracker_setup)
    await _search_tracker_requests(meta, tracker_setup)
    await _record_site_check_item(
        meta, progress, item_index, item_path, total_files, log_file
    )
    _ensure_default_item_outcome(
        meta, progress, item_index, item_path, is_batch
    )
    stop = _queue_limit_reached(meta, progress)
    if stop:
        _record_remaining_queue_limit_skips(
            meta, progress, queue_list, item_index
        )
    meta = await _sanitize_item_meta(meta, sanitize_meta)
    await _cleanup_item_state()
    return meta, stop


def _batch_outcome_count(progress: _BatchProgress, outcome_name: str) -> int:
    return sum(
        outcome == outcome_name
        for _path, outcome, _detail in progress.outcomes.values()
    )


def _batch_failed_count(progress: _BatchProgress) -> int:
    return sum(
        outcome in {"failed", "skipped"}
        for _path, outcome, _detail in progress.outcomes.values()
    )


def _batch_items_by_outcome(
    progress: _BatchProgress, outcome_name: str
) -> list[tuple[str, str]]:
    return [
        (path, detail)
        for _index, (path, outcome, detail) in sorted(
            progress.outcomes.items()
        )
        if outcome == outcome_name
    ]


def _log_batch_item_section(
    title: str, items: list[tuple[str, str]], style: str
) -> None:
    if not items:
        return
    logger.info(f"[{style}]{title}[/{style}]")
    for path, detail in items:
        logger.info(f"- {path}: {detail}")


def _log_batch_summary(progress: _BatchProgress, queue_size: int) -> None:
    success = _batch_outcome_count(progress, "successful")
    checked = _batch_outcome_count(progress, "checked")
    partial = _batch_outcome_count(progress, "partial")
    skipped = _batch_outcome_count(progress, "skipped")
    failed_only = _batch_outcome_count(progress, "failed")
    failed = _batch_failed_count(progress)
    logger.info(
        f"[bold green]Batch summary: total queued {queue_size}, fully successful {success}, "
        f"partial {partial}, skipped/failed {failed}, site checks completed {checked}, "
        f"skipped {skipped}, failed {failed_only}[/bold green]"
    )
    _log_batch_item_section(
        "Items with partial uploads:", progress.partial_items, "bold yellow"
    )
    _log_batch_item_section(
        "Skipped items:",
        _batch_items_by_outcome(progress, "skipped"),
        "bold yellow",
    )
    _log_batch_item_section(
        "Failed items:",
        _batch_items_by_outcome(progress, "failed"),
        "bold red",
    )


async def _process_runtime_queue_item(
    base_meta: Meta,
    queue_item: Any,
    base_dir: str,
    progress: _BatchProgress,
    item_index: int,
    queue_list: list[Any],
    log_file: str | None,
    is_batch: bool,
    sanitize_meta: bool,
) -> tuple[Meta, bool]:
    total_files = len(queue_list)
    current_release_log_path.set(None)
    (
        meta,
        item_path,
        _tmp_path,
        item_error,
        item_abort,
    ) = await _prepare_queue_item_safely(base_meta, queue_item, base_dir)
    if await _handle_item_error(
        progress,
        item_index,
        item_path,
        item_error,
        item_abort,
        total_files,
        meta,
        log_file,
        is_batch,
    ):
        return meta, False
    started = time.time()
    logger.info(f"[green]Gathering info for {escape(Path(item_path).name)}")
    meta_success, item_error, item_abort = await _run_item_process_meta(
        meta, base_dir
    )
    if await _handle_item_error(
        progress,
        item_index,
        item_path,
        item_error,
        item_abort,
        total_files,
        meta,
        log_file,
        is_batch,
    ):
        return meta, False
    if not meta_success:
        await _record_metadata_failure(
            progress,
            item_index,
            item_path,
            "Metadata preparation failed.",
            total_files,
            meta,
            log_file,
            is_batch,
        )
        return meta, False
    await _run_item_uploads(
        meta, progress, item_index, item_path, total_files, log_file, is_batch
    )
    logger.debug(f"Uploads processed in {time.time() - started:.4f} seconds")
    return await _postprocess_item(
        meta,
        progress,
        item_index,
        item_path,
        total_files,
        log_file,
        is_batch,
        queue_list,
        sanitize_meta,
    )


async def _run_batch_queue(
    base_meta: Meta,
    queue_list: list[Any],
    base_dir: str,
    progress: _BatchProgress,
    log_file: str | None,
    is_batch: bool,
    sanitize_meta: bool,
) -> Meta:
    meta = base_meta
    for item_index, queue_item in enumerate(queue_list):
        meta, stop = await _process_runtime_queue_item(
            base_meta,
            queue_item,
            base_dir,
            progress,
            item_index,
            queue_list,
            log_file,
            is_batch,
            sanitize_meta,
        )
        if stop:
            break
    return meta


async def _execute_batch_runtime(
    base_dir: str,
) -> tuple[Meta, bool]:
    (
        meta,
        queue_list,
        log_file,
        is_batch,
        sanitize_meta,
    ) = await _runtime_batch_setup(base_dir)
    progress = _BatchProgress()
    meta = await _run_batch_queue(
        meta.copy(),
        queue_list,
        base_dir,
        progress,
        log_file,
        is_batch,
        sanitize_meta,
    )
    if is_batch:
        _log_batch_summary(progress, len(queue_list))
    current_release_log_path.set(None)
    return meta, sanitize_meta


def _handle_item_processing_abort(
    error: ItemProcessingError, meta: Meta
) -> None:
    item_path = error.item_path or meta.path or ""
    item_label = f"{item_path}: " if item_path else ""
    logger.info(f"[yellow]Skipping {item_label}{error}[/yellow]")
    cleanup_manager.reset_terminal()


async def _handle_unexpected_batch_error(
    error: Exception, meta: Meta, sanitize_meta: bool
) -> Meta:
    logger.info(f"[bold red]An unexpected error occurred: {error}")
    if sanitize_meta:
        meta = await Redaction.clean_meta_for_export(meta)
    logger.info(traceback.format_exc())
    cleanup_manager.reset_terminal()
    return meta


def _finalize_batch_runtime() -> None:
    current_release_log_path.set(None)
    if not sys.stdin.closed:
        cleanup_manager.reset_terminal()


async def _handle_batch_runtime_exception(
    error: Exception, meta: Meta, sanitize_meta: bool
) -> Meta:
    if isinstance(error, ItemProcessingError):
        _handle_item_processing_abort(error, meta)
        return meta
    return await _handle_unexpected_batch_error(error, meta, sanitize_meta)


async def do_the_thing(base_dir: str) -> None:
    meta = Meta()
    sanitize_meta = True
    try:
        meta, sanitize_meta = await _execute_batch_runtime(base_dir)
    except Exception as error:
        meta = await _handle_batch_runtime_exception(
            error, meta, sanitize_meta
        )
    finally:
        _finalize_batch_runtime()


def _all_cross_seed_trackers() -> set[str]:
    return set(api_trackers) | set(http_trackers) | set(other_api_trackers)


def _cross_seed_remove_set(meta: Meta) -> set[str]:
    return set(_normalized_tracker_values(meta.remove_trackers))


def _cross_seed_checked_set(meta: Meta) -> set[str]:
    raw = meta.dupe_checked_trackers
    if not isinstance(raw, list):
        return set()
    return {
        str(value).upper()
        for value in cast(list[Any], raw)
        if isinstance(value, str) and value.strip()
    }


def _tracker_cross_seed_config(tracker: str) -> dict[str, Any]:
    trackers = config.get("TRACKERS", {})
    if not isinstance(trackers, dict):
        return {}
    raw = cast(dict[str, Any], trackers).get(tracker, {})
    return cast(dict[str, Any], raw) if isinstance(raw, dict) else {}


def _cross_seed_config_credentials(tracker: str) -> tuple[str, str]:
    tracker_config = _tracker_cross_seed_config(tracker)
    api_key = str(tracker_config.get("api_key") or "").strip()
    announce_url = str(tracker_config.get("announce_url") or "").strip()
    return api_key, announce_url


def _announce_url_is_placeholder(announce_url: str) -> bool:
    placeholders = (
        "<PASSKEY>",
        "customannounceurl",
        "get from upload page",
        "Custom_Announce_URL",
        "PASS_KEY",
        "insertyourpasskeyhere",
    )
    lowered = announce_url.casefold()
    return any(pattern.casefold() in lowered for pattern in placeholders)


def _cross_seed_credentials_present(api_key: str, announce_url: str) -> bool:
    return bool(api_key or announce_url)


def _cross_seed_announce_allowed(tracker: str, announce_url: str) -> bool:
    if not announce_url or not _announce_url_is_placeholder(announce_url):
        return True
    logger.debug(
        f"[yellow]Tracker {tracker} has placeholder announce_url, skipping[/yellow]"
    )
    return False


def _cross_seed_tracker_has_config(tracker: str) -> bool:
    return bool(_tracker_cross_seed_config(tracker))


def _cross_seed_tracker_configured(tracker: str) -> bool:
    if not _cross_seed_tracker_has_config(tracker):
        return False
    api_key, announce_url = _cross_seed_config_credentials(tracker)
    if not _cross_seed_credentials_present(api_key, announce_url):
        return False
    return _cross_seed_announce_allowed(tracker, announce_url)


def _cross_seed_check_everything_enabled() -> bool:
    return bool(
        _process_default_config().get("cross_seed_check_everything", False)
    )


def _cross_seed_tracker_unchecked(
    meta: Meta, tracker: str, checked: set[str], removed: set[str]
) -> bool:
    if tracker in checked or tracker in removed:
        return False
    if meta.get(f"{tracker}_cross_seed", None) is not None:
        return False
    return _cross_seed_tracker_configured(tracker)


def _eligible_unchecked_cross_seed_trackers(
    meta: Meta,
    all_trackers: set[str],
    checked: set[str],
    removed: set[str],
) -> list[str]:
    return [
        tracker
        for tracker in sorted(all_trackers)
        if _cross_seed_tracker_unchecked(meta, tracker, checked, removed)
    ]


def _unchecked_cross_seed_trackers(
    meta: Meta, all_trackers: set[str]
) -> list[str]:
    if not _cross_seed_check_everything_enabled():
        return []
    return _eligible_unchecked_cross_seed_trackers(
        meta,
        all_trackers,
        _cross_seed_checked_set(meta),
        _cross_seed_remove_set(meta),
    )


async def _validate_cross_seed_trackers(
    meta: Meta, trackers: list[str]
) -> None:
    if not trackers:
        return
    try:
        await validate_tracker_logins(meta, trackers)
        await asyncio.sleep(0.2)
    except Exception as error:
        logger.warning(
            f"[yellow]Warning: Tracker validation encountered an error: {error}[/yellow]"
        )


async def _cross_seed_additional_checks(tracker_obj: Any, meta: Meta) -> bool:
    check = getattr(tracker_obj, "get_additional_checks", None)
    if check is None:
        return True
    result = check(meta)
    if inspect.isawaitable(result):
        result = await result
    return bool(result)


async def _ptp_cross_seed_group_id(meta: Meta, ptp: PassThePopcorn) -> Any:
    group_id = meta.ptp_groupid
    if group_id or not meta.imdb:
        return group_id
    group_id = await ptp.get_group_by_imdb(meta.imdb)
    meta.ptp_groupid = group_id
    return group_id


async def _ptp_cross_seed_search(meta: Meta, ptp: PassThePopcorn) -> list[Any]:
    group_id = await _ptp_cross_seed_group_id(meta, ptp)
    if group_id is None:
        return []
    result = await ptp.search_existing(group_id, meta)
    return list(result or [])


async def _ptp_cross_seed_dupes(meta: Meta) -> list[Any]:
    ptp = PassThePopcorn(config=config)
    if not await _cross_seed_additional_checks(ptp, meta):
        meta.skipping = "PASSTHEPOPCORN"
        return []
    return await _ptp_cross_seed_search(meta, ptp)


async def _generic_cross_seed_dupes(meta: Meta, tracker: str) -> list[Any]:
    tracker_obj = tracker_class_map[tracker](config=config)
    if not await _cross_seed_additional_checks(tracker_obj, meta):
        meta.skipping = tracker
        return []
    result = await tracker_obj.search_existing(meta)
    return list(result or [])


async def _cross_seed_dupe_candidates(meta: Meta, tracker: str) -> list[Any]:
    if tracker == "PASSTHEPOPCORN":
        return await _ptp_cross_seed_dupes(meta)
    return await _generic_cross_seed_dupes(meta, tracker)


async def _check_cross_seed_tracker_dupes(
    meta: Meta,
    tracker: str,
    helper: UploadHelper,
    dupe_checker: DupeChecker,
) -> None:
    try:
        dupes = await _cross_seed_dupe_candidates(meta, tracker)
        if not dupes:
            return
        filtered = await dupe_checker.filter_dupes(dupes, meta, tracker)
        _is_dupe, updated_meta = await helper.dupe_check(
            cast(list[Any], filtered), meta, tracker
        )
        if updated_meta is not meta:
            meta.update(updated_meta)
    except Exception as error:
        logger.warning(
            f"[yellow]Warning: Failed to check duplicates for cross-seed on {tracker}: {error}[/yellow]"
        )


async def _check_unchecked_cross_seeds(
    meta: Meta, trackers: list[str]
) -> None:
    if not trackers:
        return
    logger.info(
        f"[cyan]Checking for cross-seeds on unchecked trackers: {trackers}[/cyan]"
    )
    await _validate_cross_seed_trackers(meta, trackers)
    original_unattended = meta.unattended
    meta.unattended = True
    try:
        helper = UploadHelper(config)
        dupe_checker = DupeChecker(config)
        await asyncio.gather(
            *(
                _check_cross_seed_tracker_dupes(
                    meta, tracker, helper, dupe_checker
                )
                for tracker in trackers
            ),
            return_exceptions=True,
        )
    finally:
        meta.unattended = original_unattended


def _cross_seed_trackers_with_data(
    meta: Meta, all_trackers: set[str]
) -> list[str]:
    return sorted(
        tracker
        for tracker in all_trackers
        if meta.get(f"{tracker}_cross_seed", None) is not None
    )


def _cross_seed_concurrency_limit() -> int:
    try:
        configured = int(
            _process_default_config().get("cross_seed_concurrency", 8)
        )
    except TypeError, ValueError:
        configured = 8
    return max(1, configured)


def _cross_seed_download_url(meta: Meta, tracker: str) -> str:
    value = getattr(meta, f"{tracker}_cross_seed", False)
    logger.debug(
        f"[cyan]Debug: {tracker} - cross_seed: {Redaction.redact_private_info(value)}"
    )
    if isinstance(value, str) and value.startswith("http"):
        return value
    if value:
        logger.debug(
            f"[yellow]Invalid cross-seed URL for {tracker}, skipping[/yellow]"
        )
    return ""


def _retroflix_cross_seed_headers(tracker: str) -> dict[str, str] | None:
    if tracker != "RETROFLIX":
        return None
    api_key = str(
        _tracker_cross_seed_config(tracker).get("api_key") or ""
    ).strip()
    return {"accept": "application/json", "Authorization": api_key}


def _alpharatio_torrent_pass() -> str:
    announce_url = str(
        _tracker_cross_seed_config("ALPHARATIO").get("announce_url") or ""
    )
    match = re.search(r":\d+/([^/]+)/announce", announce_url)
    return match.group(1) if match else ""


async def _alpharatio_cross_seed_url(meta: Meta, download_url: str) -> str:
    try:
        auth_key = await AlphaRatio(config=config).get_auth_key(meta)
        torrent_pass = _alpharatio_torrent_pass()
        if not auth_key or not torrent_pass:
            return download_url
        separator = "&" if "?" in download_url else "?"
        logger.debug(
            "[cyan]Added ALPHARATIO auth_key and torrent_pass to download URL[/cyan]"
        )
        return f"{download_url}{separator}authkey={auth_key}&torrent_pass={torrent_pass}"
    except Exception as error:
        logger.debug(
            f"[yellow]Error getting ALPHARATIO auth credentials: {error}[/yellow]"
        )
        return download_url


async def _prepared_cross_seed_download(
    meta: Meta, tracker: str
) -> tuple[str, dict[str, str] | None]:
    download_url = _cross_seed_download_url(meta, tracker)
    if not download_url:
        return "", None
    if tracker == "ALPHARATIO":
        download_url = await _alpharatio_cross_seed_url(meta, download_url)
    return download_url, _retroflix_cross_seed_headers(tracker)


async def _handle_cross_seed_tracker(
    meta: Meta,
    tracker: str,
    common: Common,
    semaphore: asyncio.Semaphore,
) -> None:
    download_url, headers = await _prepared_cross_seed_download(meta, tracker)
    if not download_url:
        return
    logger.debug(f"[green]Found cross-seed for {tracker}!")
    async with semaphore:
        await common.download_tracker_torrent(
            meta,
            tracker,
            headers=headers,
            params=None,
            downurl=download_url,
            hash_is_id=False,
            cross=True,
            use_cookie_auth=tracker in http_trackers,
        )
        await client.add_to_client(meta, tracker, cross=True)


def _cross_seed_download_tasks(
    meta: Meta,
    trackers: list[str],
    common: Common,
    semaphore: asyncio.Semaphore,
) -> list[tuple[str, asyncio.Task[None]]]:
    return [
        (
            tracker,
            asyncio.create_task(
                _handle_cross_seed_tracker(meta, tracker, common, semaphore)
            ),
        )
        for tracker in trackers
    ]


def _log_cross_seed_download_results(
    tasks: list[tuple[str, asyncio.Task[None]]],
    results: Sequence[Any],
) -> None:
    for (tracker, _task), result in zip(tasks, results, strict=False):
        if isinstance(result, Exception):
            logger.info(
                f"[red]Cross-seed handling failed for {tracker}: {result}[/red]"
            )


async def _run_cross_seed_downloads(meta: Meta, trackers: list[str]) -> None:
    if not trackers:
        return
    logger.info(
        f"[cyan]Valid trackers for cross-seed check: {trackers}[/cyan]"
    )
    common = Common(config)
    semaphore = asyncio.Semaphore(_cross_seed_concurrency_limit())
    tasks = _cross_seed_download_tasks(meta, trackers, common, semaphore)
    results = await asyncio.gather(
        *(task for _tracker, task in tasks), return_exceptions=True
    )
    _log_cross_seed_download_results(tasks, results)


async def process_cross_seeds(meta: Meta) -> None:
    if meta.debug or meta.site_check:
        logger.debug(
            "[cyan]Skipping cross-seed processing in debug/site-check mode[/cyan]"
        )
        return
    all_trackers = _all_cross_seed_trackers()
    unchecked = _unchecked_cross_seed_trackers(meta, all_trackers)
    await _check_unchecked_cross_seeds(meta, unchecked)
    valid_trackers = _cross_seed_trackers_with_data(meta, all_trackers)
    if not valid_trackers:
        logger.debug("[yellow]No trackers found with cross-seed data[/yellow]")
        return
    await _run_cross_seed_downloads(meta, valid_trackers)


async def get_mkbrr_path(base_dir: str | None = None) -> str | None:
    try:
        # Prefer the immutable binary shipped with the application. Downloads
        # are cached in the user-owned runtime directory only when needed.
        if bundled_mkbrr := MkbrrBinaryManager.find_existing_binary(CODE_DIR):
            return bundled_mkbrr
        resolved_base_dir = base_dir or str(STATE_DIR)
        mkbrr_path = await MkbrrBinaryManager.ensure_mkbrr_binary(
            resolved_base_dir, version="v1.24.0"
        )
        return mkbrr_path if mkbrr_path else None
    except Exception as e:
        logger.error(f"[red]Error setting up mkbrr binary: {e}[/red]")
        return None


def check_python_version() -> None:
    pyver = platform.python_version_tuple()
    if int(pyver[0]) != 3 or int(pyver[1]) < 9:
        logger.info(
            "[bold red]Python version is too low. Please use Python 3.9 or higher."
        )
        sys.exit(1)


def _main_exception_log(error: BaseException) -> tuple[str, str] | None:
    if isinstance(error, asyncio.CancelledError):
        return (
            "info",
            "[red]Tasks were cancelled. Exiting safely.[/red]",
        )
    if isinstance(error, (NoWorkAvailableError, OperationAbortedError)):
        return "info", f"[yellow]{error}[/yellow]"
    if isinstance(error, (EOFError, KeyboardInterrupt)):
        return None
    return "error", f"[bold red]Unexpected error: {error}[/bold red]"


def _handle_main_exception(error: BaseException) -> None:
    log_entry = _main_exception_log(error)
    if log_entry is None:
        return
    level, message = log_entry
    if _shutdown_requested and not isinstance(
        error, (NoWorkAvailableError, OperationAbortedError)
    ):
        return
    if level == "info":
        logger.info(message)
    else:
        logger.error(message)


async def main() -> None:
    _reset_shutdown_state()
    try:
        await do_the_thing(base_dir)
    except BaseException as error:
        _handle_main_exception(error)


if __name__ == "__main__":
    check_python_version()
    exit_code = 0

    # Register signal handlers only when run as main script (not when imported)
    signal.signal(signal.SIGINT, _handle_shutdown_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_shutdown_signal)

    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit) as exc:
        if not _shutdown_requested:
            logger.info("\n[yellow]Shutting down...[/yellow]")
        exit_code = (
            (exc.code if isinstance(exc.code, int) else 1)
            if isinstance(exc, SystemExit)
            else 130
        )
    except BaseException as e:
        if not _shutdown_requested:
            logger.info(f"[bold red]Critical error: {e}[/bold red]")
        exit_code = 1
    finally:
        with contextlib.suppress(Exception):

            async def _cleanup_with_timeout() -> None:
                """Bound shutdown cleanup so the CLI cannot hang indefinitely."""
                try:
                    await asyncio.wait_for(
                        cleanup_manager.cleanup(), timeout=10.0
                    )
                except TimeoutError, asyncio.CancelledError:
                    logger.info(
                        "[yellow]Cleanup timed out or was cancelled, forcing exit...[/yellow]"
                    )

            asyncio.run(_cleanup_with_timeout())

        gc.collect()
        cleanup_manager.reset_terminal()

        if _shutdown_requested:
            logger.info("[green]Shutdown complete[/green]")

        sys.exit(exit_code)
