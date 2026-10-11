# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import contextlib
import ntpath
import os
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import aiofiles
import cli_ui
import guessit
from torf import Torrent

from src.domain_models.adult_platforms import XXX_PLATFORM_KEYWORDS
from src.domain_models.errors import AmbiguousMetadataError, MediaInfoError
from src.domain_models.processing import ItemProcessingError, NoAudioMediaError
from src.domain_models.release import Meta
from src.engines.adult_content_detection import extract_xxx_keywords
from src.engines.region_mapping import get_distributor, get_region, get_service
from src.integrations.external_apis.bluray import get_bluray_releases
from src.integrations.external_apis.imdb import imdb_manager
from src.integrations.external_apis.tvmaze import tvmaze_manager
from src.integrations.filesystem.cleanup import cleanup_manager
from src.integrations.filesystem.tags import get_tag, tag_override
from src.integrations.media.language_adapter import languages_manager
from src.integrations.media.media_info_export import (
    export_info,
    get_conformance_error,
    mi_resolution,
    validate_mediainfo,
)
from src.integrations.media.video import video_manager
from src.integrations.observability.runtime_support import logger
from src.integrations.torrent_clients.client_manager import Clients
from src.services.book_preparation import BOOK_EXTENSIONS
from src.services.edition_identity_service import get_edition
from src.services.media_source_service import get_source

guessit_module: Any = cast(Any, guessit)

_URL_TOKEN_RE = re.compile(r"https?://[^\s<>'\"()]+", re.IGNORECASE)
_GAME_EXTENSIONS = {
    ".3ds",
    ".3dsx",
    ".cci",
    ".cdi",
    ".chd",
    ".cia",
    ".cso",
    ".dmg",
    ".exe",
    ".gcm",
    ".gdi",
    ".hdf",
    ".iso",
    ".nca",
    ".nds",
    ".nsp",
    ".nsz",
    ".pbp",
    ".pkg",
    ".rap",
    ".srl",
    ".szs",
    ".vpk",
    ".wbfs",
    ".wud",
    ".wux",
    ".xbe",
    ".xci",
    ".xcz",
    ".xex",
}

XXX_RELEASE_MARKERS = XXX_PLATFORM_KEYWORDS | {"xxx"}
_XXX_RELEASE_MARKER_RE = re.compile(
    rf"(?<![a-z0-9])(?:{'|'.join(re.escape(marker) for marker in sorted(XXX_RELEASE_MARKERS))})(?![a-z0-9])",
    re.IGNORECASE,
)
_VIDEO_EXTENSIONS = frozenset(
    {
        ".avi",
        ".m2ts",
        ".m4v",
        ".mkv",
        ".mov",
        ".mp4",
        ".mpeg",
        ".mpg",
        ".ts",
        ".webm",
        ".wmv",
    }
)
_AUDIOBOOK_ONLY_EXTENSIONS = frozenset({".m4b", ".aax", ".aaxc"})


def _xxx_file_release(candidate: Path) -> bool:
    return candidate.suffix.lower() in _VIDEO_EXTENSIONS and bool(
        _XXX_RELEASE_MARKER_RE.search(candidate.name)
    )


def _xxx_directory_release(candidate: Path) -> bool:
    if not _XXX_RELEASE_MARKER_RE.search(candidate.name):
        return False
    return any(
        file.is_file() and file.suffix.lower() in _VIDEO_EXTENSIONS
        for file in candidate.rglob("*")
    )


def is_xxx_video_release(path: str | Path) -> bool:
    """Return whether a video release name carries a specific XXX platform marker."""
    candidate = Path(path)
    if candidate.is_file():
        return _xxx_file_release(candidate)
    return candidate.is_dir() and _xxx_directory_release(candidate)


def _is_igdb_url(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return host == "igdb.com" or host.endswith(".igdb.com")


def _is_steam_app_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""
    return host == "store.steampowered.com" and path.startswith("/app/")


def _nfo_has_store_link(content: str) -> bool:
    for match in _URL_TOKEN_RE.finditer(content):
        url = match.group(0)
        if _is_steam_app_url(url) or _is_igdb_url(url):
            return True
    return False


def guessit_fn(
    value: str, options: dict[str, Any] | None = None
) -> dict[str, Any]:
    return cast(dict[str, Any], guessit_module.guessit(value, options))


def _first_normalized_year(values: Any) -> str | None:
    for candidate in cast(list[Any] | tuple[Any, ...] | set[Any], values):
        normalized = _normalize_search_year(candidate)
        if normalized is not None:
            return normalized
    return None


def _normalize_search_year(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, (list, tuple, set)):
        return _first_normalized_year(value)
    year_match = re.search(r"\b(?:18|19|20)\d{2}\b", str(value))
    return year_match.group(0) if year_match else None


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except TypeError, ValueError:
        return default


def check_pre_release(meta: Meta) -> bool:
    release_type = (meta.type or "").upper()
    rel_source = (meta.source or "").upper()
    forbidden_types = (
        "CAM",
        "DCP",
        "HDCAM",
        "SCR",
        "SCREENER",
        "TC",
        "TELECINE",
        "TELESYNC",
        "TS",
        "WORKPRINT",
        "WP",
    )
    return (
        release_type in forbidden_types
        or rel_source in forbidden_types
        or "CAM" in release_type
    )


def _title_without_leading_article(title: str) -> str:
    return re.sub(
        r"^(the|a|an)\s+", "", title.strip().lower(), flags=re.IGNORECASE
    )


def _valid_title_pair(
    current_title: Any, tvdb_title: str
) -> tuple[str, str] | None:
    if not isinstance(current_title, str):
        return None
    current = current_title.strip().lower()
    tvdb = tvdb_title.strip().lower()
    if not current or not tvdb:
        return None
    return current, tvdb


def _has_leading_article(title: str) -> bool:
    return re.match(r"^(the|a|an)\s+", title, flags=re.IGNORECASE) is not None


def _tvdb_title_drops_existing_leading_article(
    current_title: Any, tvdb_title: str
) -> bool:
    pair = _valid_title_pair(current_title, tvdb_title)
    if pair is None:
        return False
    current, tvdb = pair
    return all(
        (
            _has_leading_article(current),
            current != tvdb,
            _title_without_leading_article(current) == tvdb,
        )
    )


def _tracker_id_fields() -> tuple[list[str], list[str]]:
    from src.integrations.trackers.registry import api_trackers

    hash_ids = ["infohash", "torrent_hash", "skip_auto_torrent"]
    tracker_ids = [tracker.lower() for tracker in api_trackers]
    tracker_ids.extend(["ptp", "btn", "hdb", "orpheus"])
    return hash_ids, tracker_ids


def _apply_description_mode(meta: Meta, defaults: dict[str, Any]) -> None:
    from src.engines.tracker_description_policy import resolve_description_mode

    configured = defaults.get("tracker_description_mode", "text")
    description_mode = resolve_description_mode(
        "ids" if meta.only_id else configured
    )
    meta.tracker_description_mode = description_mode.value
    meta.keep_images = description_mode.imports_images
    meta.skip_tracker_descriptions = not description_mode.imports_text


def _reset_preparation_meta(meta: Meta) -> None:
    meta.we_checked_tvdb = False
    meta.we_checked_tmdb = False
    meta.we_asked_tvmaze = False
    meta.audio_languages = None
    meta.subtitle_languages = None
    meta.aither_trumpable = None
    meta.anime = False
    meta.not_anime = False
    meta.subtitle_files = []
    meta.adult_media = False
    meta.pre_release = check_pre_release(meta)


def _prepare_meta_paths(meta: Meta) -> None:
    folder_id = Path(meta.path or "").name
    if not meta.uuid:
        meta.uuid = folder_id
    meta.basename_no_ext = folder_id if meta.isdir else Path(folder_id).stem
    tmp_path = Path(meta.base_dir) / "tmp" / meta.uuid
    tmp_path.mkdir(parents=True, mode=0o700, exist_ok=True)
    logger.debug(f"[cyan]ID: {meta.uuid}")


def init_meta(
    prep_instance: Any, meta: Meta, mode: str
) -> tuple[bool, bool, Clients, bool, list[str], list[str]]:
    defaults = cast(dict[str, Any], prep_instance.config["DEFAULT"])
    meta.cutoff = int(defaults.get("cutoff_screens", 1))
    meta.mode = mode
    meta.isdir = Path(meta.path or "").is_dir()
    meta.saved_description = False
    meta.skip_auto_torrent = bool(
        meta.skip_auto_torrent or defaults.get("skip_auto_torrent", False)
    )
    meta.print_tracker_messages = bool(
        defaults.get("print_tracker_messages", False)
    )
    meta.print_tracker_links = bool(defaults.get("print_tracker_links", True))
    meta.mkbrr_threads = defaults.get("mkbrr_threads", "0")
    _apply_description_mode(meta, defaults)
    _reset_preparation_meta(meta)
    _prepare_meta_paths(meta)
    hash_ids, tracker_ids = _tracker_id_fields()
    return (
        bool(defaults.get("use_sonarr", False)),
        bool(defaults.get("use_radarr", False)),
        Clients(config=prep_instance.config),
        meta.skip_tracker_descriptions,
        hash_ids,
        tracker_ids,
    )


def _apply_manual_category(meta: Meta) -> None:
    if isinstance(meta.manual_category, str) and meta.manual_category.strip():
        meta.category = meta.manual_category.strip().upper()


def _category_detection_path(meta: Meta) -> Path | None:
    if not meta.path:
        return None
    path = Path(meta.path)
    return path if path.exists() else None


def _automatic_category_allowed(meta: Meta) -> bool:
    return not meta.category and not meta.manual_category and not meta.is_disc


def _path_has_extension(
    path: Path, extensions: set[str] | frozenset[str]
) -> bool:
    if path.is_file():
        return path.suffix.lower() in extensions
    if not path.is_dir():
        return False
    return any(
        item.is_file() and item.suffix.lower() in extensions
        for item in path.rglob("*")
    )


def _detect_game_package(meta: Meta) -> None:
    if not _automatic_category_allowed(meta):
        return
    path = _category_detection_path(meta)
    if path is None or not _path_has_extension(path, _GAME_EXTENSIONS):
        return
    meta.category = "GAME"
    logger.debug("[cyan]Auto-detected category: GAME (package file)[/cyan]")


def _manual_book_audio_extensions() -> set[str]:
    from src.services.audio_classification_service import (
        AUDIOBOOK_CONTAINER_EXTENSIONS,
        SHARED_AUDIO_EXTENSIONS,
    )

    return set(SHARED_AUDIO_EXTENSIONS | AUDIOBOOK_CONTAINER_EXTENSIONS)


def _detect_manual_book_audiobook(meta: Meta) -> None:
    if meta.category != "BOOK" or meta.audiobook:
        return
    path = _category_detection_path(meta)
    if path is not None and _path_has_extension(
        path, _manual_book_audio_extensions()
    ):
        meta.audiobook = True


def _audio_evidence_logger(prefix: str) -> Any:
    return logger.warning if prefix == "warning" else logger.debug


def _audio_evidence_label(prefix: str) -> str:
    return (
        "[yellow]Evidence evaluated:[/yellow]"
        if prefix == "warning"
        else "[cyan]Evidence:[/cyan]"
    )


def _audio_evidence_item(prefix: str, item: Any) -> str:
    return (
        f"[yellow]- {item}[/yellow]"
        if prefix == "warning"
        else f"[cyan]- {item}[/cyan]"
    )


def _log_audio_evidence(prefix: str, evidence: Any) -> None:
    if not evidence:
        return
    logger_func = _audio_evidence_logger(prefix)
    logger_func(_audio_evidence_label(prefix))
    for item in cast(list[Any], evidence):
        logger_func(_audio_evidence_item(prefix, item))


def _audio_choice_value(choice: Any) -> tuple[str, bool]:
    text = str(choice)
    lowered = text.lower()
    if text.startswith("1") or lowered == "music":
        return "MUSIC", False
    if text.startswith("2") or lowered == "audiobook":
        return "BOOK", True
    return "PODCAST", False


def _audio_selection_error(meta: Meta, reason: str) -> ItemProcessingError:
    return ItemProcessingError(reason, str(meta.path or ""))


def _interactive_audio_category(meta: Meta) -> None:
    try:
        choice = cli_ui.ask_choice(
            "Choose category for audio release:",
            choices=["1. Music", "2. Audiobook", "3. Podcast"],
        )
    except EOFError, KeyboardInterrupt:
        logger.error(
            "[bold red]Category selection cancelled or failed.[/bold red]"
        )
        raise _audio_selection_error(
            meta,
            "Could not determine if release is MUSIC, PODCAST, or BOOK from interactive cancellation.",
        ) from None
    if choice is None:
        logger.error(
            "[bold red]Category selection cancelled or failed.[/bold red]"
        )
        raise _audio_selection_error(
            meta,
            "Could not determine if release is MUSIC, PODCAST, or BOOK from interactive selection.",
        )
    meta.category, meta.audiobook = _audio_choice_value(choice)
    logger.info(
        f"[cyan]Category selected interactively: {meta.category}[/cyan]"
    )


def _ambiguous_audio_unattended(meta: Meta) -> bool:
    unattended = bool(getattr(meta, "unattended", False))
    unattended_confirm = bool(getattr(meta, "unattended_confirm", False))
    return unattended and not unattended_confirm


def _reject_ambiguous_audio(meta: Meta) -> None:
    logger.error(
        "[bold red]Could not confidently distinguish MUSIC, PODCAST, or AUDIOBOOK in unattended mode.[/bold red]"
    )
    logger.error(
        "[yellow]Specify one of: -c book, -c music, or -c podcast[/yellow]"
    )
    logger.error(
        "[yellow]Skipping this release instead of assigning an unsafe category.[/yellow]"
    )
    raise _audio_selection_error(
        meta,
        "Could not determine if release is MUSIC, PODCAST, or BOOK from mixed audio signals. Specify --category music, --category podcast, or --category book.",
    )


def _handle_ambiguous_audio(meta: Meta, audio_res: Any) -> None:
    logger.warning(
        "[yellow]Audio category is ambiguous: could not confidently determine whether this is MUSIC, a PODCAST, or an AUDIOBOOK.[/yellow]"
    )
    _log_audio_evidence("warning", getattr(audio_res, "evidence", None))
    if _ambiguous_audio_unattended(meta):
        _reject_ambiguous_audio(meta)
    _interactive_audio_category(meta)


def _apply_detected_audio(meta: Meta, audio_res: Any) -> None:
    meta.category = audio_res.category
    meta.audiobook = audio_res.is_audiobook
    logger.debug(f"[cyan]Auto-detected category: {meta.category}[/cyan]")
    if audio_res.is_audiobook:
        logger.debug("[cyan]Subtype: AUDIOBOOK[/cyan]")
    _log_audio_evidence("debug", audio_res.evidence)


async def _detect_audio_release_category(meta: Meta) -> None:
    if not _automatic_category_allowed(meta):
        return
    path = _category_detection_path(meta)
    if path is None:
        return
    from src.services.audio_classification_service import detect_audio_category

    audio_res = await detect_audio_category(meta, path)
    if audio_res.category in ("BOOK", "MUSIC", "PODCAST"):
        _apply_detected_audio(meta, audio_res)
        return
    if audio_res.category == "AMBIGUOUS":
        _handle_ambiguous_audio(meta, audio_res)


def _directory_extensions(path: Path) -> set[str]:
    return {
        Path(filename).suffix.lower()
        for _root, _dirs, files in os.walk(path)
        for filename in files
    }


def _book_directory_signals(path: Path) -> tuple[bool, bool, bool]:
    extensions = _directory_extensions(path)
    video_extensions = {".mkv", ".mp4", ".ts", ".avi"}
    return (
        bool(extensions & BOOK_EXTENSIONS),
        bool(extensions & _AUDIOBOOK_ONLY_EXTENSIONS),
        bool(extensions & video_extensions),
    )


def _book_file_fallback(path: Path) -> bool:
    extension = path.suffix.lower()
    return (
        extension in BOOK_EXTENSIONS or extension in _AUDIOBOOK_ONLY_EXTENSIONS
    )


def _book_directory_fallback(path: Path) -> bool:
    has_books, has_audio, has_video = _book_directory_signals(path)
    return (has_books or has_audio) and not has_video


def _book_fallback_match(path: Path) -> bool:
    if path.is_file():
        return _book_file_fallback(path)
    return path.is_dir() and _book_directory_fallback(path)


def _detect_book_fallback(meta: Meta) -> None:
    if not _automatic_category_allowed(meta):
        return
    path = _category_detection_path(meta)
    if path is None or not _book_fallback_match(path):
        return
    meta.category = "BOOK"
    logger.debug("[cyan]Auto-detected category: BOOK[/cyan]")


def _game_release_groups() -> set[str]:
    return {
        "tenoke",
        "rune",
        "flt",
        "plaza",
        "codex",
        "skidrow",
        "prophet",
        "gog",
        "darkzer0",
        "doge",
        "tinyiso",
        "razor1911",
        "outlaws",
        "alias",
        "simplex",
    }


def _has_game_release_group(path: Path) -> bool:
    name = path.name.lower()
    return any(
        f"-{group}" in name or name.endswith(group)
        for group in _game_release_groups()
    )


async def _nfo_has_game_store_link(path: Path) -> bool:
    try:
        async with aiofiles.open(
            path, encoding="utf-8", errors="ignore"
        ) as handle:
            return _nfo_has_store_link(await handle.read())
    except Exception:
        with contextlib.suppress(Exception):
            async with aiofiles.open(
                path, encoding="latin-1", errors="ignore"
            ) as handle:
                return _nfo_has_store_link(await handle.read())
    return False


def _directory_nfo_paths(path: Path) -> list[Path]:
    return [
        Path(root) / filename
        for root, _dirs, files in os.walk(path)
        for filename in files
        if Path(filename).suffix.lower() == ".nfo"
    ]


async def _directory_has_game_store_link(path: Path) -> bool:
    for nfo_path in _directory_nfo_paths(path):
        if await _nfo_has_game_store_link(nfo_path):
            return True
    return False


async def _game_directory_signals(path: Path) -> tuple[bool, bool]:
    video_extensions = {".mkv", ".mp4", ".ts", ".avi"}
    has_video = bool(_directory_extensions(path) & video_extensions)
    return has_video, await _directory_has_game_store_link(path)


async def _game_fallback_match(path: Path) -> bool:
    has_group = _has_game_release_group(path)
    if not path.is_dir():
        return False
    has_video, has_store_link = await _game_directory_signals(path)
    return has_store_link or (has_group and not has_video)


async def _detect_game_fallback(meta: Meta) -> None:
    if not _automatic_category_allowed(meta):
        return
    path = _category_detection_path(meta)
    if path is None or not await _game_fallback_match(path):
        return
    meta.category = "GAME"
    logger.debug("[cyan]Auto-detected category: GAME[/cyan]")


async def _detect_xxx_category(meta: Meta) -> None:
    if not _automatic_category_allowed(meta) or not meta.path:
        return
    if not await asyncio.to_thread(is_xxx_video_release, meta.path):
        return
    meta.category = "XXX"
    logger.debug("[cyan]Auto-detected category: XXX[/cyan]")


async def detect_disc_and_category(
    prep_instance: Any, meta: Meta
) -> tuple[str, dict[str, Any]]:
    (
        meta.is_disc,
        videoloc,
        bdinfo,
        meta.discs,
    ) = await prep_instance.disc_info_manager.get_disc(meta)
    logger.debug(f"[blue]is_disc: [yellow]{meta.is_disc}[/yellow][/blue]")
    _apply_manual_category(meta)
    _detect_game_package(meta)
    _detect_manual_book_audiobook(meta)
    await _detect_audio_release_category(meta)
    _detect_book_fallback(meta)
    await _detect_game_fallback(meta)
    await _detect_xxx_category(meta)
    return videoloc, bdinfo


@dataclass
class _MediaProcessingResult:
    filename: str = ""
    untouched_filename: str = ""
    videopath: str = ""
    search_term: str = ""
    search_file_folder: str = ""
    mi: dict[str, Any] | None = None
    video: str = ""


def _category_is(meta: Meta, category: str) -> bool:
    manual = str(meta.manual_category or "").upper()
    return meta.category == category or manual == category


async def _scene_video(prep_instance: Any, meta: Meta, path: str) -> str:
    (
        video,
        meta.scene,
        meta.imdb_id,
    ) = await prep_instance.scene_manager.is_scene(path, meta, meta.imdb_id)
    return video


def _guess_title_from_value(value: str) -> str:
    cleaned = re.sub(r"[^0-9a-zA-Z\[\\\]]+", " ", value)
    return str(
        guessit_fn(cleaned, {"excludes": ["country", "language"]}).get(
            "title", ""
        )
    )


def _safe_search_year(value: str) -> Any:
    try:
        return guessit_fn(value)["year"]
    except Exception:
        return ""


def _apply_extracted_title(
    meta: Meta,
    title: Any,
    secondary_title: Any,
    extracted_year: Any,
) -> None:
    logger.debug(
        f"Title: {title}, Secondary Title: {secondary_title}, Year: {extracted_year}"
    )
    if secondary_title:
        meta.secondary_title = secondary_title
    if extracted_year and not meta.year:
        meta.year = int(extracted_year)


def _bdmv_hfr(bdinfo: dict[str, Any]) -> bool:
    try:
        video_tracks = cast(list[dict[str, Any]], bdinfo.get("video", []))
        fps = video_tracks[0].get("fps", "25") if video_tracks else "25"
        return int(float(str(fps).split()[0])) > 30
    except Exception:
        return False


async def _bdmv_title_result(
    prep_instance: Any,
    meta: Meta,
    bdinfo: dict[str, Any],
    video: str,
    search_term: str,
) -> tuple[str, str]:
    try:
        (
            title,
            secondary,
            year,
        ) = await prep_instance.name_manager.extract_title_and_year(
            meta, video
        )
        _apply_extracted_title(meta, title, secondary, year)
        if title:
            meta.hfr = _bdmv_hfr(bdinfo)
            meta.search_year = _safe_search_year(str(bdinfo["title"]))
            return str(title), search_term
        raw_title = str(bdinfo["title"])
        meta.hfr = _bdmv_hfr(bdinfo)
        meta.search_year = _safe_search_year(raw_title)
        return _guess_title_from_value(raw_title.replace("-", " ")), raw_title
    except Exception:
        raw_label = str(bdinfo["label"])
        meta.search_year = _safe_search_year(raw_label)
        return _guess_title_from_value(raw_label.replace("-", " ")), raw_label


async def _ensure_bdmv_resolution(
    meta: Meta, bdinfo: dict[str, Any], video: str
) -> None:
    if meta.resolution:
        return
    video_tracks = cast(list[dict[str, Any]], bdinfo.get("video", []))
    resolution = str(video_tracks[0].get("res", "")) if video_tracks else ""
    meta.resolution = await mi_resolution(
        resolution, guessit_fn(video), width="OTHER", scan="p"
    )


async def _process_bdmv(
    prep_instance: Any, meta: Meta, bdinfo: dict[str, Any]
) -> _MediaProcessingResult:
    meta_path = str(meta.path or "")
    video = await _scene_video(prep_instance, meta, meta_path)
    meta.filelist = []
    search_term = Path(meta_path).name
    filename, untouched = await _bdmv_title_result(
        prep_instance, meta, bdinfo, video, search_term
    )
    await _ensure_bdmv_resolution(meta, bdinfo, video)
    meta.sd = await video_manager.is_sd(meta.resolution)
    return _MediaProcessingResult(
        filename=filename,
        untouched_filename=untouched,
        search_term=search_term,
        search_file_folder="folder",
        video=video,
    )


def _first_disc(meta: Meta) -> dict[str, Any]:
    discs = cast(list[dict[str, Any]], meta.discs or [])
    return discs[0] if discs else {}


async def _process_dvd(
    prep_instance: Any, meta: Meta
) -> _MediaProcessingResult:
    meta_path = str(meta.path or "")
    video = await _scene_video(prep_instance, meta, meta_path)
    meta.filelist = []
    result = _MediaProcessingResult(
        search_term=Path(meta_path).name,
        search_file_folder="folder",
        video=video,
    )
    (
        title,
        secondary,
        year,
    ) = await prep_instance.name_manager.extract_title_and_year(meta, video)
    _apply_extracted_title(meta, title, secondary, year)
    disc = _first_disc(meta)
    disc_path = str(disc.get("path", ""))
    if title:
        result.filename = str(title)
        result.untouched_filename = result.search_term
    else:
        result.filename = str(
            guessit_fn(
                disc_path.replace("-", " "),
                {"excludes": ["country", "language"]},
            ).get("title", "")
        )
        result.untouched_filename = Path(disc_path).parent.name
    meta.search_year = _safe_search_year(disc_path)
    if not meta.edit:
        main_set = cast(list[str], disc.get("main_set", []))
        number = main_set[0][:2] if main_set else ""
        result.mi = await export_info(
            f"{disc_path}/VTS_{number}_0.IFO",
            False,
            meta.uuid,
            meta.base_dir,
            is_dvd=True,
        )
        meta.mediainfo = result.mi
    else:
        result.mi = meta.mediainfo
    meta.dvd_size = await prep_instance.disc_info_manager.get_dvd_size(
        meta.discs, meta.manual_dvds
    )
    meta.resolution, meta.hfr = await video_manager.get_resolution(
        guessit_fn(video), meta.uuid, meta.base_dir, meta
    )
    meta.sd = await video_manager.is_sd(meta.resolution)
    return result


async def _process_hddvd(
    prep_instance: Any, meta: Meta
) -> _MediaProcessingResult:
    meta_path = str(meta.path or "")
    video = await _scene_video(prep_instance, meta, meta_path)
    meta.filelist = []
    disc = _first_disc(meta)
    disc_path = str(disc.get("path", ""))
    largest_evo = str(disc.get("largest_evo", ""))
    result = _MediaProcessingResult(
        filename=str(
            guessit_fn(
                disc_path.replace("-", ""),
                {"excludes": ["country", "language"]},
            ).get("title", "")
        ),
        untouched_filename=Path(disc_path).name,
        videopath=largest_evo,
        search_term=Path(meta_path).name,
        search_file_folder="folder",
        video=video,
    )
    meta.search_year = _safe_search_year(disc_path)
    if not meta.edit:
        result.mi = await export_info(
            largest_evo, False, meta.uuid, meta.base_dir
        )
        meta.mediainfo = result.mi
    else:
        result.mi = meta.mediainfo
    meta.resolution, meta.hfr = await video_manager.get_resolution(
        guessit_fn(video), meta.uuid, meta.base_dir, meta
    )
    meta.sd = await video_manager.is_sd(meta.resolution)
    return result


def _subtitle_extensions() -> set[str]:
    return {".srt", ".sub", ".vtt", ".ssa", ".ass", ".idx"}


def _subtitle_root_allowed(root: str) -> bool:
    upper = root.upper()
    return not any(
        marker in upper for marker in ("BDMV", "VIDEO_TS", "HVDVD_TS")
    )


def _subtitle_files_in_root(root: str, files: list[str]) -> list[str]:
    return [
        str((Path(root) / filename).resolve())
        for filename in files
        if Path(filename).suffix.lower() in _subtitle_extensions()
    ]


def _directory_subtitles(meta_path: str) -> list[str]:
    found: list[str] = []
    for root, _dirs, files in os.walk(meta_path):
        if _subtitle_root_allowed(root):
            found.extend(_subtitle_files_in_root(root, files))
    return found


def _is_sibling_subtitle(path: Path, base_name: str) -> bool:
    return all(
        (
            path.is_file(),
            path.suffix.lower() in _subtitle_extensions(),
            path.name.lower().startswith(base_name),
        )
    )


def _sibling_subtitles(meta_path: str) -> list[str]:
    parent = Path(meta_path).parent
    if not parent.exists():
        return []
    base_name = Path(meta_path).stem.lower()
    return [
        str(path.resolve())
        for path in parent.iterdir()
        if _is_sibling_subtitle(path, base_name)
    ]


def _scan_external_subtitles(meta: Meta, meta_path: str) -> None:
    found = (
        _directory_subtitles(meta_path)
        if meta.isdir
        else _sibling_subtitles(meta_path)
    )
    meta.subtitle_files = sorted(set(found))


def _resolve_non_disc_filelist(
    prep_instance: Any, meta: Meta, videoloc: str
) -> tuple[str, str, str, str]:
    if _category_is(meta, "BOOK"):
        videopath, _filelist, search_term, search_file_folder = (
            prep_instance._resolve_book_filelist(meta, videoloc)
        )
        return videopath, videopath, search_term, search_file_folder
    if _category_is(meta, "GAME"):
        videopath, _filelist, search_term, search_file_folder = (
            prep_instance._resolve_game_filelist(meta, videoloc)
        )
        return videopath, videopath, search_term, search_file_folder
    raise RuntimeError("general video filelist requires async resolution")


async def _resolve_general_video(
    meta: Meta, videoloc: str
) -> tuple[str, str, str, str]:
    videopath, filelist = await video_manager.get_video(
        videoloc,
        (meta.mode if meta.mode is not None else "non_cli"),
        meta.sorted_filelist,
    )
    meta.filelist = filelist
    search_term = Path(filelist[0]).name if filelist else ""
    _scan_external_subtitles(meta, str(meta.path or ""))
    return videopath, videopath, search_term, "file"


async def _resolved_non_disc_video(
    prep_instance: Any, meta: Meta, videoloc: str
) -> tuple[str, str, str, str]:
    if _category_is(meta, "BOOK") or _category_is(meta, "GAME"):
        return _resolve_non_disc_filelist(prep_instance, meta, videoloc)
    return await _resolve_general_video(meta, videoloc)


def _restore_book_extension(video: str, videopath: str) -> str:
    original_extension = Path(videopath).suffix
    if video.endswith(".mkv") and not videopath.endswith(".mkv"):
        return video[:-4] + original_extension
    return video


def _non_disc_guess_name(meta: Meta, video: str) -> str:
    if meta.isdir:
        return (
            Path(str(meta.path or "")).name.replace("_", "").replace("-", "")
        )
    return ntpath.basename(video).replace("-", " ")


def _guess_filename_candidates(guess_name: str, video: str) -> str:
    for candidate in (guess_name, ntpath.basename(video).replace("-", " ")):
        try:
            cleaned = re.sub(r"[^0-9a-zA-Z\[\\\]]+", " ", candidate)
            fallback = re.sub("[^0-9a-zA-Z]+", " ", candidate)
            return str(
                guessit_fn(
                    cleaned,
                    {"excludes": ["country", "language"]},
                ).get(
                    "title",
                    str(
                        guessit_fn(
                            fallback,
                            {"excludes": ["country", "language"]},
                        ).get("title", "")
                    ),
                )
            )
        except Exception as error:
            logger.debug(
                f"[yellow]Title guess failed for '{candidate}': {error}[/yellow]"
            )
    raise RuntimeError("Unable to derive title from video name")


async def _non_disc_title(
    prep_instance: Any,
    meta: Meta,
    video: str,
) -> tuple[str, str]:
    try:
        (
            title,
            secondary,
            year,
        ) = await prep_instance.name_manager.extract_title_and_year(
            meta, video
        )
    except Exception as error:
        logger.error(f"[red]Error extracting title and year: {error}[/red]")
        raise Exception(f"Error extracting title and year: {error}") from error
    _apply_extracted_title(meta, title, secondary, year)
    guess_name = _non_disc_guess_name(meta, video)
    if title:
        filename = str(title)
        meta.regex_title = title
        meta.regex_secondary_title = secondary
        meta.regex_year = year
    else:
        try:
            filename = _guess_filename_candidates(guess_name, video)
        except Exception as error:
            logger.error(
                f"[red]Error extracting title from video name: {error}[/red]"
            )
            raise Exception(f"Error processing filename: {error}") from error
    return filename, Path(video).name


async def _gather_regular_media(
    prep_instance: Any,
    meta: Meta,
    videopath: str,
    video: str,
) -> dict[str, Any] | None:
    if _category_is(meta, "BOOK"):
        await prep_instance._gather_book_prep(meta, videopath, meta.base_dir)
        return None
    if _category_is(meta, "GAME"):
        await prep_instance._gather_game_prep(meta, videopath, meta.base_dir)
        return None
    meta.search_year = _safe_search_year(video)
    if meta.edit:
        mi = meta.mediainfo
    else:
        mi = await export_info(
            videopath,
            meta.isdir,
            meta.uuid,
            meta.base_dir,
            is_dvd=(meta.is_disc == "DVD"),
        )
        meta.mediainfo = mi
    if not meta.resolution:
        meta.resolution, meta.hfr = await video_manager.get_resolution(
            guessit_fn(video), meta.uuid, meta.base_dir, meta
        )
    meta.sd = await video_manager.is_sd(meta.resolution)
    return mi


async def _safe_gather_regular_media(
    prep_instance: Any, meta: Meta, videopath: str, video: str
) -> dict[str, Any] | None:
    try:
        return await _gather_regular_media(
            prep_instance, meta, videopath, video
        )
    except ItemProcessingError:
        raise
    except MediaInfoError as error:
        logger.debug(f"[red]{error.debug_details}[/red]")
        raise ItemProcessingError(
            f"MediaInfo could not inspect {Path(videopath).name}: {error}",
            str(meta.path or videopath),
        ) from error
    except Exception as error:
        logger.error(f"[red]Error processing media metadata: {error}[/red]")
        raise


async def _process_non_disc(
    prep_instance: Any, meta: Meta, videoloc: str
) -> _MediaProcessingResult:
    (
        videopath,
        video,
        search_term,
        search_file_folder,
    ) = await _resolved_non_disc_video(prep_instance, meta, videoloc)
    video = await _scene_video(prep_instance, meta, videopath)
    if _category_is(meta, "BOOK"):
        video = _restore_book_extension(video, videopath)
    filename, untouched = await _non_disc_title(prep_instance, meta, video)
    if _category_is(meta, "GAME"):
        meta.filename = filename
    mi = await _safe_gather_regular_media(
        prep_instance, meta, videopath, video
    )
    return _MediaProcessingResult(
        filename=filename,
        untouched_filename=untouched,
        videopath=videopath,
        search_term=search_term,
        search_file_folder=search_file_folder,
        mi=mi,
        video=video,
    )


def _finalize_media_processing(
    meta: Meta, bdinfo: dict[str, Any], result: _MediaProcessingResult
) -> _MediaProcessingResult:
    result.filename = str(result.filename)
    result.untouched_filename = str(result.untouched_filename)
    if " AKA " in result.filename.replace(".", " "):
        result.filename = result.filename.split("AKA")[0]
    meta.filename = result.filename
    meta.bdinfo = bdinfo
    if meta.category == "XXX":
        meta.keywords = extract_xxx_keywords(
            meta.basename_no_ext, meta.keywords
        )
    return result


async def process_media_files(
    prep_instance: Any, meta: Meta, videoloc: str, bdinfo: dict[str, Any]
) -> tuple[str, str, str, str, str, dict[str, Any] | None, str]:
    if meta.is_disc == "BDMV":
        result = await _process_bdmv(prep_instance, meta, bdinfo)
    elif meta.is_disc == "DVD":
        result = await _process_dvd(prep_instance, meta)
    elif meta.is_disc == "HDDVD":
        result = await _process_hddvd(prep_instance, meta)
    else:
        result = await _process_non_disc(prep_instance, meta, videoloc)
    result = _finalize_media_processing(meta, bdinfo, result)
    return (
        result.filename,
        result.untouched_filename,
        result.videopath,
        result.search_term,
        result.search_file_folder,
        result.mi,
        result.video,
    )


def _measured_file_size(file_path: str | Path) -> int:
    path = Path(file_path)
    if not path.is_file():
        logger.debug(f"[yellow]Skipping size check for missing file: {path}")
        return 0
    try:
        return path.stat().st_size
    except OSError as exc:
        logger.debug(f"[yellow]Unable to stat {path}: {exc}")
        return 0


def _non_disc_source_size(meta: Meta, videopath: str) -> int:
    filelist = cast(list[str], meta.filelist or [])
    files = filelist if filelist else ([videopath] if videopath else [])
    return sum(_measured_file_size(file_path) for file_path in files)


def _disc_source_files(path: str) -> list[Path]:
    return [
        Path(root) / name
        for root, _dirs, files in os.walk(path)
        for name in files
    ]


def _disc_source_size(meta: Meta) -> int:
    disc_root = meta.path if isinstance(meta.path, str) else ""
    if not disc_root or not Path(disc_root).exists():
        logger.debug(
            f"[yellow]Disc path missing, source size set to 0: {disc_root}"
        )
        return 0
    return sum(
        _measured_file_size(path) for path in _disc_source_files(disc_root)
    )


def calculate_source_size(
    _prep_instance: Any, meta: Meta, videopath: str
) -> None:
    meta.source_size = (
        _disc_source_size(meta)
        if meta.is_disc
        else _non_disc_source_size(meta, videopath)
    )
    logger.debug(f"[cyan]Calculated source size: {meta.source_size} bytes")


def _conformance_prompt_enabled(meta: Meta) -> bool:
    return not meta.unattended or bool(meta.unattended_confirm)


def _conformance_tmp_dir(meta: Meta) -> Path:
    return Path(meta.base_dir) / "tmp" / meta.uuid


def _is_conformance_metadata_file(path: Path) -> bool:
    return path.is_file() and path.suffix in {".txt", ".json"}


def _remove_conformance_metadata_file(path: Path) -> None:
    path.unlink()
    logger.debug(f"[yellow]Removed temporary metadata file: {path}[/yellow]")


def _cleanup_conformance_metadata(tmp_dir: Path) -> None:
    if not tmp_dir.exists():
        return
    try:
        for file_path in tmp_dir.iterdir():
            if _is_conformance_metadata_file(file_path):
                _remove_conformance_metadata_file(file_path)
    except Exception as error:
        logger.error(
            f"[red]Error cleaning up temporary metadata files: {error}[/red]",
            extra={"highlighter": None},
        )


async def _ask_conformance_override(meta: Meta) -> bool:
    try:
        return bool(
            cli_ui.ask_yes_no(
                "Found Conformance errors in mediainfo (possible cause: corrupted file, incomplete download, new codec, etc...), proceed to upload anyway?",
                default=False,
            )
        )
    except EOFError:
        logger.info("\n[red]Exiting on user request (Ctrl+C)[/red]")
        await cleanup_manager.cleanup()
        cleanup_manager.reset_terminal()
        raise ItemProcessingError(
            "Conformance check skipped by user request",
            str(meta.path or ""),
        ) from None


async def _handle_conformance_issues(meta: Meta) -> None:
    if not await get_conformance_error(meta):
        return
    if not _conformance_prompt_enabled(meta):
        return
    if await _ask_conformance_override(meta):
        return
    logger.info(
        "[red]Not uploading. Check if the file has finished downloading and can be played back properly (uncorrupted)."
    )
    _cleanup_conformance_metadata(_conformance_tmp_dir(meta))
    logger.info("[red]Not uploading due to conformance errors.[/red]")
    raise ItemProcessingError(
        "Conformance errors found in mediainfo",
        str(meta.path or ""),
    )


def _mediainfo_validation_allowed(meta: Meta) -> bool:
    return not meta.is_disc and meta.category not in ("BOOK", "GAME")


def _run_mediainfo_validation(meta: Meta) -> bool:
    try:
        return bool(validate_mediainfo(meta))
    except NoAudioMediaError as error:
        logger.info(f"[red]MediaInfo validation failed: {error!s}[/red]")
        raise NoAudioMediaError(
            f"{meta.ua_name} does not support no audio media. Details: {error!s}"
        ) from error
    except Exception as error:
        logger.info(f"[red]MediaInfo validation failed: {error!s}[/red]")
        raise


async def _validate_mediainfo_payload(meta: Meta) -> None:
    meta.valid_mi = True
    if not _mediainfo_validation_allowed(meta):
        return
    if _run_mediainfo_validation(meta):
        return
    logger.info(
        "[red]MediaInfo validation failed. This file does not contain (Unique ID)."
    )
    meta.valid_mi = False
    await asyncio.sleep(2)


def _mediainfo_track_mappings(meta: Meta) -> list[dict[str, Any]]:
    media = meta.mediainfo.get("media", {})
    if not isinstance(media, dict):
        return []
    tracks = cast(dict[str, Any], media).get("track", [])
    if not isinstance(tracks, list):
        return []
    return [
        cast(dict[str, Any], track)
        for track in cast(list[Any], tracks)
        if isinstance(track, dict)
    ]


def _has_multiple_default_subtitles(meta: Meta) -> bool:
    count = sum(
        1
        for track in _mediainfo_track_mappings(meta)
        if track.get("@type") == "Text" and track.get("Default") == "Yes"
    )
    return count > 1


def _required_languages(meta: Meta) -> list[str]:
    return str(meta.has_languages or "").lower().split(",")


def _audio_languages(parsed_info: dict[str, Any]) -> list[str]:
    tracks = parsed_info.get("audio", [])
    if not isinstance(tracks, list):
        return []
    return [
        str(track.get("language")).lower()
        for track in cast(list[dict[str, Any]], tracks)
        if isinstance(track, dict) and track.get("language")
    ]


def _validate_language_names(required: list[str]) -> None:
    if required and all(len(language.strip()) == 2 for language in required):
        raise Exception(
            f"Warning: Languages should be full names, not ISO codes. Found: {required}"
        )


def _validate_language_overlap(
    meta: Meta, required: list[str], available: list[str]
) -> None:
    if not required or not available:
        return
    if set(required).intersection(set(available)):
        return
    logger.info(
        f"[red] None of the required languages ({meta.has_languages}) is available on the file {available}"
    )
    raise Exception("No matching languages")


async def _validate_required_languages(meta: Meta) -> None:
    if not meta.has_languages:
        return
    try:
        parsed_info = await languages_manager.parsed_mediainfo(meta)
        parsed = (
            cast(dict[str, Any], parsed_info)
            if isinstance(parsed_info, dict)
            else {}
        )
        required = _required_languages(meta)
        available = _audio_languages(parsed)
        _validate_language_names(required)
        _validate_language_overlap(meta, required, available)
    except Exception as error:
        logger.info(f"[red]{error}[/red]")
        raise Exception("Language check failed") from error


async def validate_media(_prep_instance: Any, meta: Meta) -> None:
    await _handle_conformance_issues(meta)
    await _validate_mediainfo_payload(meta)
    meta.has_multiple_default_subtitle_tracks = (
        _has_multiple_default_subtitles(meta)
    )
    await _validate_required_languages(meta)


def _raw_tracker_values(prep_instance: Any, meta: Meta) -> Any:
    if meta.trackers:
        return meta.trackers
    defaults = prep_instance.config["TRACKERS"].get("default_trackers", "")
    return [tracker.strip() for tracker in str(defaults).split(",")]


def _tracker_items(values: Any) -> list[Any]:
    if isinstance(values, str):
        return values.split(",") if "," in values else [values]
    return cast(list[Any], values)


def _tracker_values(prep_instance: Any, meta: Meta) -> list[str]:
    return [
        str(item).strip().upper()
        for item in _tracker_items(_raw_tracker_values(prep_instance, meta))
    ]


def _apply_requested_trackers(prep_instance: Any, meta: Meta) -> None:
    trackers = _tracker_values(prep_instance, meta)
    meta.trackers = trackers
    meta.requested_trackers = list(trackers)


def _reuse_torrent_search_allowed(meta: Meta, hash_ids: list[str]) -> bool:
    has_known_id = any(meta.get(id_type) for id_type in hash_ids)
    return (
        not has_known_id
        and not meta.tracker_ids
        and not meta.skip_trackers
        and not meta.edit
    )


def _reuse_includes_subtitles(client: Clients, path: str, meta: Meta) -> bool:
    if not meta.subtitle_files:
        return False
    return client._torrent_includes_all_local_subtitles(path, meta)


def _apply_reuse_torrent_path(client: Clients, meta: Meta, path: str) -> None:
    meta.reuse_torrent_path = path
    if _reuse_includes_subtitles(client, path, meta):
        meta.subs_reuse_torrent_path = path
    else:
        meta.base_reuse_torrent_path = path


def _cache_reuse_infohash(meta: Meta, path: str) -> None:
    try:
        meta.infohash = Torrent.read(path).infohash
    except Exception as error:
        logger.debug(
            f"[yellow]Unable to read infohash from cached torrent: {error}"
        )


async def _find_reusable_torrent(
    client: Clients, meta: Meta, hash_ids: list[str]
) -> None:
    if not _reuse_torrent_search_allowed(meta, hash_ids):
        return
    path = await client.find_existing_torrent(meta)
    if not path:
        return
    _apply_reuse_torrent_path(client, meta, path)
    _cache_reuse_infohash(meta, path)
    await client.get_ptp_from_hash(
        meta, pathed=True, client_name=meta.reuse_torrent_client
    )


async def process_trackers_and_torrent(
    prep_instance: Any,
    meta: Meta,
    client: Clients,
    hash_ids: list[str],
    _tracker_ids: list[str],
    _search_term: str,
    _search_file_folder: str,
) -> None:
    if "description" not in meta or meta.description is None:
        meta.description = ""
    meta.skip_trackers = False
    _apply_requested_trackers(prep_instance, meta)
    await _find_reusable_torrent(client, meta, hash_ids)


_SPECIAL_METADATA_CATEGORIES = {"BOOK", "GAME", "XXX"}


def _manual_numeric_id(value: Any) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except TypeError, ValueError:
        return None


def _manual_imdb_id(value: Any) -> int | None:
    if not value:
        return None
    text = str(value)
    if text.startswith("tt"):
        text = text[2:]
    return _manual_numeric_id(text)


def _default_manual_metadata_ids(meta: Meta) -> None:
    for attribute in (
        "tmdb_manual",
        "imdb_manual",
        "mal_manual",
        "tvdb_manual",
        "tvmaze_manual",
    ):
        setattr(meta, attribute, getattr(meta, attribute) or 0)


def _apply_manual_numeric_pair(
    meta: Meta, manual_attr: str, target_attr: str
) -> None:
    manual = _manual_numeric_id(getattr(meta, manual_attr))
    if manual is not None:
        setattr(meta, target_attr, manual)
        return
    if not getattr(meta, target_attr):
        setattr(meta, target_attr, 0)


def _apply_manual_imdb(meta: Meta) -> None:
    if not meta.imdb_id:
        meta.imdb_id = _manual_imdb_id(meta.imdb_manual) or 0


def _apply_manual_tvmaze(meta: Meta) -> None:
    if meta.tvmaze_manual:
        meta.tvmaze_id = meta.tvmaze_manual
    elif not meta.tvmaze_id:
        meta.tvmaze_id = 0


def _apply_manual_metadata_ids(meta: Meta) -> None:
    _default_manual_metadata_ids(meta)
    for manual_attr, target_attr in (
        ("tmdb_manual", "tmdb_id"),
        ("mal_manual", "mal_id"),
        ("tvdb_manual", "tvdb_id"),
    ):
        _apply_manual_numeric_pair(meta, manual_attr, target_attr)
    _apply_manual_imdb(meta)
    _apply_manual_tvmaze(meta)


async def _ensure_metadata_category(
    prep_instance: Any, meta: Meta, videopath: str
) -> None:
    if not meta.category:
        meta.category = await prep_instance.get_cat(videopath, meta)
        return
    meta.category = meta.category.upper()


def _id_is_zero(value: Any) -> bool:
    return _to_int(value) == 0


def _fill_zero_id(meta: Meta, attribute: str, value: Any) -> None:
    if _id_is_zero(getattr(meta, attribute)) and value is not None:
        setattr(meta, attribute, value)


def _result_genres(result: dict[str, Any]) -> list[str]:
    value = result.get("genres", [])
    if not isinstance(value, list):
        return []
    return [str(item) for item in cast(list[Any], value)]


def _apply_automation_year(meta: Meta, result: dict[str, Any]) -> None:
    if meta.manual_year == 0 and result.get("year") is not None:
        meta.manual_year = result["year"]


def _log_sonarr_result(result: dict[str, Any]) -> None:
    for label, key in (
        ("TVDB ID", "tvdb_id"),
        ("IMDB ID", "imdb_id"),
        ("TVMAZE ID", "tvmaze_id"),
        ("TMDB ID", "tmdb_id"),
        ("Genres", "genres"),
        ("Release Group", "release_group"),
        ("Year", "year"),
    ):
        logger.debug(f"{label}: {result.get(key)}")


def _apply_sonarr_result(meta: Meta, result: dict[str, Any]) -> None:
    _log_sonarr_result(result)
    if "anime" not in [genre.lower() for genre in _result_genres(result)]:
        meta.not_anime = True
    for key, attribute in (
        ("tvdb_id", "tvdb_id"),
        ("imdb_id", "imdb_id"),
        ("tvmaze_id", "tvmaze_id"),
        ("tmdb_id", "tmdb_id"),
    ):
        _fill_zero_id(meta, attribute, result.get(key))
    _apply_automation_year(meta, result)


def _log_radarr_result(result: dict[str, Any]) -> None:
    for label, key in (
        ("IMDB ID", "imdb_id"),
        ("TMDB ID", "tmdb_id"),
        ("Genres", "genres"),
        ("Year", "year"),
        ("Release Group", "release_group"),
    ):
        logger.debug(f"{label}: {result.get(key)}")


def _apply_radarr_result(meta: Meta, result: dict[str, Any]) -> None:
    _log_radarr_result(result)
    _fill_zero_id(meta, "imdb_id", result.get("imdb_id"))
    _fill_zero_id(meta, "tmdb_id", result.get("tmdb_id"))
    _apply_automation_year(meta, result)


def _automation_result(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict) and value:
        return cast(dict[str, Any], value)
    return None


async def _initial_sonarr_ids(
    prep_instance: Any, meta: Meta
) -> dict[str, Any] | None:
    result = _automation_result(
        await prep_instance.sonarr_manager.get_sonarr_data(
            filename=meta.path, title=meta.filename
        )
    )
    if result is not None:
        _apply_sonarr_result(meta, result)
    return result


async def _initial_radarr_ids(
    prep_instance: Any, meta: Meta
) -> dict[str, Any] | None:
    result = _automation_result(
        await prep_instance.radarr_manager.get_radarr_data(filename=meta.uuid)
    )
    if result is not None:
        _apply_radarr_result(meta, result)
    return result


def _initial_sonarr_needed(meta: Meta, use_sonarr: bool) -> bool:
    return all((meta.category == "TV", use_sonarr, _id_is_zero(meta.tvdb_id)))


def _initial_radarr_needed(meta: Meta, use_radarr: bool) -> bool:
    return all(
        (meta.category == "MOVIE", use_radarr, _id_is_zero(meta.tmdb_id))
    )


async def _initial_automation_ids(
    prep_instance: Any,
    meta: Meta,
    use_sonarr: bool,
    use_radarr: bool,
) -> dict[str, Any] | None:
    if _initial_sonarr_needed(meta, use_sonarr):
        return await _initial_sonarr_ids(prep_instance, meta)
    if _initial_radarr_needed(meta, use_radarr):
        return await _initial_radarr_ids(prep_instance, meta)
    return None


async def _maybe_hash_metadata(
    client: Clients, meta: Meta, ids: dict[str, Any] | None
) -> Meta:
    if (
        meta.infohash is not None
        and not meta.base_torrent_created
        and not meta.we_checked_them_all
        and not ids
    ):
        return await client.get_ptp_from_hash(meta)
    return meta


async def _maybe_tracker_metadata(
    prep_instance: Any,
    meta: Meta,
    videopath: str,
    search_term: str,
    search_file_folder: str,
    skip_tracker_descriptions: bool,
    ids: dict[str, Any] | None,
) -> None:
    if meta.edit or ids:
        return
    await prep_instance.tracker_data_manager.get_tracker_data(
        videopath,
        meta,
        search_term,
        search_file_folder,
        meta.category,
        skip_tracker_descriptions=skip_tracker_descriptions,
    )


async def _secondary_sonarr_ids(
    prep_instance: Any, meta: Meta
) -> dict[str, Any] | None:
    result = _automation_result(
        await prep_instance.sonarr_manager.get_sonarr_data(
            tvdb_id=meta.tvdb_id
        )
    )
    if result is not None:
        _apply_sonarr_result(meta, result)
    return result


async def _secondary_radarr_ids(
    prep_instance: Any, meta: Meta
) -> dict[str, Any] | None:
    result = _automation_result(
        await prep_instance.radarr_manager.get_radarr_data(
            tmdb_id=meta.tmdb_id
        )
    )
    if result is not None:
        _apply_radarr_result(meta, result)
    return result


def _secondary_sonarr_needed(meta: Meta, use_sonarr: bool) -> bool:
    return all(
        (meta.category == "TV", use_sonarr, not _id_is_zero(meta.tvdb_id))
    )


def _secondary_radarr_needed(meta: Meta, use_radarr: bool) -> bool:
    return all(
        (meta.category == "MOVIE", use_radarr, not _id_is_zero(meta.tmdb_id))
    )


async def _secondary_automation_ids(
    prep_instance: Any,
    meta: Meta,
    use_sonarr: bool,
    use_radarr: bool,
    ids: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if ids is not None or meta.matched_tracker:
        return ids
    if _secondary_sonarr_needed(meta, use_sonarr):
        return await _secondary_sonarr_ids(prep_instance, meta)
    if _secondary_radarr_needed(meta, use_radarr):
        return await _secondary_radarr_ids(prep_instance, meta)
    return None


async def _tracker_metadata_phase(
    prep_instance: Any,
    meta: Meta,
    videopath: str,
    search_term: str,
    search_file_folder: str,
    use_sonarr: bool,
    use_radarr: bool,
    skip_tracker_descriptions: bool,
    client: Clients,
) -> Meta:
    if meta.skip_trackers:
        return meta
    ids = await _initial_automation_ids(
        prep_instance, meta, use_sonarr, use_radarr
    )
    meta = await _maybe_hash_metadata(client, meta, ids)
    await _maybe_tracker_metadata(
        prep_instance,
        meta,
        videopath,
        search_term,
        search_file_folder,
        skip_tracker_descriptions,
        ids,
    )
    await _secondary_automation_ids(
        prep_instance, meta, use_sonarr, use_radarr, ids
    )
    return meta


def _unit3d_ping_allowed(prep_instance: Any, meta: Meta) -> bool:
    enabled = bool(prep_instance.config["DEFAULT"].get("ping_unit3d", False))
    return all(
        (
            meta.is_disc in ("BDMV", "DVD"),
            enabled,
            not meta.edit,
            not meta.site_check,
        )
    )


async def _maybe_ping_unit3d(prep_instance: Any, meta: Meta) -> None:
    needs_data = not meta.region or not meta.distributor
    if needs_data and _unit3d_ping_allowed(prep_instance, meta):
        await prep_instance.tracker_data_manager.ping_unit3d(meta)


async def _maybe_apply_source_override(prep_instance: Any, meta: Meta) -> Meta:
    enabled = bool(
        prep_instance.config["DEFAULT"].get("user_overrides", False)
    )
    if not enabled or (
        _id_is_zero(meta.imdb_id) and _id_is_zero(meta.tvdb_id)
    ):
        return meta
    meta = await prep_instance.overrides.get_source_override(
        meta, other_id=True
    )
    meta.category = (
        str(meta.category).upper() if meta.category is not None else ""
    )
    meta.no_override = True
    return meta


def _log_metadata_id_inputs(meta: Meta) -> None:
    logger.debug("ID inputs into prep")
    logger.debug(f"Category: {meta.category}")
    for label, value in (
        ("TVDB", meta.tvdb_id),
        ("IMDb", meta.imdb_id),
        ("TMDb", meta.tmdb_id),
        ("TVMAZE", meta.tvmaze_id),
        ("MAL", meta.mal_id),
    ):
        logger.debug(f"Raw {label} ID: {value} (type: {type(value).__name__})")


def _apply_anime_identity(meta: Meta) -> None:
    if meta.mal_id != 0:
        meta.anime = True
        meta.not_anime = True


def _apply_manual_language(meta: Meta) -> None:
    if isinstance(meta.manual_language, str) and meta.manual_language:
        meta.original_language = meta.manual_language.lower()


async def _set_search_media_type(meta: Meta, videopath: str) -> None:
    if meta.category == "BOOK":
        meta.type = Path(videopath).suffix.lstrip(".").upper()
        if meta.type in ("CBR", "CBZ"):
            meta.comic = True
        return
    if meta.category == "GAME":
        meta.type = "GAME"
        return
    meta.type = await video_manager.get_type(
        videopath, meta.scene, meta.is_disc, meta
    )


async def _maybe_fast_season_episode(
    prep_instance: Any, meta: Meta, videopath: str
) -> Meta:
    if meta.not_anime and meta.category == "TV":
        return await prep_instance.season_episode_manager.get_season_episode(
            videopath, meta
        )
    return meta


def _external_metadata_category(meta: Meta) -> bool:
    return meta.category not in _SPECIAL_METADATA_CATEGORIES


async def _maybe_mediainfo_ids(
    prep_instance: Any,
    meta: Meta,
    mi: dict[str, Any] | None,
) -> None:
    if not _external_metadata_category(meta):
        return
    if not (_id_is_zero(meta.tmdb_id) or _id_is_zero(meta.imdb_id)):
        return
    (
        category,
        tmdb_id,
        imdb_id,
        tvdb_id,
    ) = await prep_instance.tmdb_manager.get_tmdb_imdb_from_mediainfo(
        mi or {}, meta
    )
    meta.category = category
    meta.tmdb_id = tmdb_id
    meta.imdb_id = imdb_id
    meta.tvdb_id = tvdb_id


def _first_year_value(values: tuple[Any, ...]) -> Any:
    return next((value for value in values if value), None)


def _identity_search_year(meta: Meta) -> str | None:
    values = (
        (meta.manual_year, meta.search_year, meta.year)
        if meta.category == "TV"
        else (meta.manual_year, meta.year, meta.search_year)
    )
    return _normalize_search_year(_first_year_value(values))


def _identity_unattended(meta: Meta) -> bool:
    return bool(meta.unattended)


async def _search_missing_primary_ids(
    prep_instance: Any,
    meta: Meta,
    filename: str,
    untouched_filename: str,
    duration: Any,
) -> None:
    if not _external_metadata_category(meta):
        return
    if not (_id_is_zero(meta.tmdb_id) and _id_is_zero(meta.imdb_id)):
        return
    year = _identity_search_year(meta)
    category = meta.category or ""
    unattended = _identity_unattended(meta)
    tmdb_task = asyncio.create_task(
        prep_instance.tmdb_manager.get_tmdb_id(
            filename,
            year,
            category,
            untouched_filename,
            attempted=0,
            debug=bool(meta.debug),
            secondary_title=meta.secondary_title,
            unattended=unattended,
        )
    )
    imdb_task = asyncio.create_task(
        imdb_manager.search_imdb(
            filename,
            year,
            quickie=True,
            category=category,
            secondary_title=meta.secondary_title,
            untouched_filename=untouched_filename,
            duration=duration,
            unattended=unattended,
        )
    )
    (tmdb_id, resolved_category), imdb_id = await asyncio.gather(
        tmdb_task, imdb_task
    )
    meta.category = resolved_category
    meta.tmdb_id = _to_int(tmdb_id)
    meta.imdb_id = _to_int(imdb_id)
    meta.quickie_search = True
    meta.no_ids = True


def _tmdb_from_imdb_needed(meta: Meta) -> bool:
    if not _external_metadata_category(meta):
        return False
    return not _id_is_zero(meta.imdb_id) and _id_is_zero(meta.tmdb_id)


def _log_tmdb_from_imdb_attempt(announce: bool) -> None:
    if announce:
        logger.info(
            "[yellow]No TMDB ID found, attempting to fetch from IMDb...[/yellow]"
        )


async def _tmdb_from_imdb(
    prep_instance: Any,
    meta: Meta,
    filename: str,
    *,
    announce: bool,
) -> None:
    if not _tmdb_from_imdb_needed(meta):
        return
    _log_tmdb_from_imdb_attempt(announce)
    (
        category,
        tmdb_id,
        original_language,
        filename_search,
    ) = await prep_instance.tmdb_manager.get_tmdb_from_imdb(
        _to_int(meta.imdb_id),
        (_to_int(meta.tvdb_id) or None),
        _normalize_search_year(meta.search_year),
        filename,
        debug=meta.debug,
        mode=(meta.mode if meta.mode is not None else "non_cli"),
        category_preference=meta.category,
        imdb_info=meta.imdb_info,
        unattended=meta.unattended,
    )
    meta.category = category
    meta.tmdb_id = _to_int(tmdb_id)
    meta.original_language = original_language
    meta.no_ids = filename_search


async def _maybe_daily_episode(
    prep_instance: Any, meta: Meta, videopath: str
) -> Meta:
    daily = re.search(r"\d{4}[-.]\d{2}[-.]\d{2}", videopath)
    if (
        meta.category == "TV"
        and not _id_is_zero(meta.tmdb_id)
        and (meta.manual_date or daily)
    ):
        return await prep_instance.season_episode_manager.get_season_episode(
            videopath, meta
        )
    return meta


def _ids_present(meta: Meta, *attributes: str) -> bool:
    return all(
        not _id_is_zero(getattr(meta, attribute)) for attribute in attributes
    )


async def _partial_combined_metadata_search(
    prep_instance: Any, meta: Meta, filename: str
) -> Meta:
    manager = prep_instance.metadata_searching_manager
    if _ids_present(meta, "imdb_id", "tvdb_id", "tmdb_id"):
        return await manager.imdb_tmdb_tvdb(meta, filename)
    if _ids_present(meta, "imdb_id", "tvdb_id"):
        return await manager.imdb_tvdb(meta, filename)
    if _ids_present(meta, "imdb_id", "tmdb_id"):
        return await manager.imdb_tmdb(meta, filename)
    return meta


async def _combined_metadata_search(
    prep_instance: Any, meta: Meta, filename: str
) -> Meta:
    if _ids_present(meta, "imdb_id", "tvdb_id", "tmdb_id", "tvmaze_id"):
        return await prep_instance.metadata_searching_manager.all_ids(meta)
    if meta.quickie_search:
        return meta
    return await _partial_combined_metadata_search(
        prep_instance, meta, filename
    )


async def _set_tmdb_metadata_if_present(
    prep_instance: Any,
    meta: Meta,
    filename: str,
    *,
    include_special: bool,
) -> None:
    if _id_is_zero(meta.tmdb_id):
        return
    if not include_special and not _external_metadata_category(meta):
        return
    await prep_instance.tmdb_manager.set_tmdb_metadata(meta, filename)


def _stale_tvdb_series_name(meta: Meta, no_original_language: bool) -> bool:
    language = (
        meta.original_language if meta.original_language is not None else "en"
    )
    return all(
        (
            bool(meta.tvdb_series_name),
            language == "en",
            not _id_is_zero(meta.tmdb_id),
            no_original_language,
        )
    )


def _clear_stale_tvdb_series_name(
    meta: Meta, no_original_language: bool
) -> None:
    if _stale_tvdb_series_name(meta, no_original_language):
        meta.tvdb_series_name = None


def _resolve_imdb_mismatch(meta: Meta) -> None:
    if not meta.imdb_mismatch or "subsplease" in meta.uuid.lower():
        return
    logger.debug(
        "[yellow]IMDb ID mismatch detected, attempting to resolve...[/yellow]"
    )
    if meta.unattended:
        logger.warning(
            "[yellow]IMDb/TMDb identity conflict remains unresolved in unattended mode; refusing to choose an ID automatically.[/yellow]"
        )
        raise AmbiguousMetadataError(
            "IMDb/TMDb identity conflict is ambiguous; automatic mode will skip this release."
        )
    meta.imdb_id = meta.mismatched_imdb_id
    meta.imdb_info = {}


async def _search_imdb_if_missing(
    meta: Meta,
    filename: str,
    untouched_filename: str,
    duration: Any,
) -> None:
    if not _external_metadata_category(meta) or not _id_is_zero(meta.imdb_id):
        return
    try:
        meta.imdb_id = await imdb_manager.search_imdb(
            filename,
            _normalize_search_year(meta.search_year),
            quickie=False,
            category=meta.category,
            secondary_title=meta.secondary_title,
            untouched_filename=untouched_filename,
            attempted=0,
            duration=duration,
            unattended=_identity_unattended(meta),
        )
    except AmbiguousMetadataError:
        raise
    except Exception as error:
        logger.error(f"[red]Error searching IMDb: {error}[/red]")
        raise Exception(f"Error searching IMDb: {error}") from error


async def _fetch_imdb_info_if_needed(prep_instance: Any, meta: Meta) -> None:
    imdb_id = _to_int(meta.imdb_id)
    if meta.imdb_info or imdb_id == 0 or not _external_metadata_category(meta):
        return
    meta.imdb_info = await imdb_manager.get_imdb_info_api(
        imdb_id,
        manual_language=meta.manual_language,
        base_dir=meta.base_dir,
        config=prep_instance.config,
    )


async def search_metadata(
    prep_instance: Any,
    meta: Meta,
    filename: str,
    untouched_filename: str,
    videopath: str,
    search_term: str,
    search_file_folder: str,
    use_sonarr: bool,
    use_radarr: bool,
    skip_tracker_descriptions: bool,
    client: Clients,
    _bdinfo: dict[str, Any],
    mi: dict[str, Any] | None,
) -> None:
    _apply_manual_metadata_ids(meta)
    await _ensure_metadata_category(prep_instance, meta, videopath)
    meta = await _tracker_metadata_phase(
        prep_instance,
        meta,
        videopath,
        search_term,
        search_file_folder,
        use_sonarr,
        use_radarr,
        skip_tracker_descriptions,
        client,
    )
    await _maybe_ping_unit3d(prep_instance, meta)
    meta = await _maybe_apply_source_override(prep_instance, meta)
    _log_metadata_id_inputs(meta)
    _apply_anime_identity(meta)
    logger.info("[yellow]Building meta data.....")
    _apply_manual_language(meta)
    await _set_search_media_type(meta, videopath)
    meta = await _maybe_fast_season_episode(prep_instance, meta, videopath)
    await _maybe_mediainfo_ids(prep_instance, meta, mi)
    meta.video_duration = await video_manager.get_video_duration(meta)
    duration = meta.video_duration
    await _search_missing_primary_ids(
        prep_instance, meta, filename, untouched_filename, duration
    )
    await _tmdb_from_imdb(prep_instance, meta, filename, announce=False)
    meta = await _maybe_daily_episode(prep_instance, meta, videopath)
    no_original_language = meta.original_language is None
    meta = await _combined_metadata_search(prep_instance, meta, filename)
    await _set_tmdb_metadata_if_present(
        prep_instance, meta, filename, include_special=True
    )
    _clear_stale_tvdb_series_name(meta, no_original_language)
    _resolve_imdb_mismatch(meta)
    await _search_imdb_if_missing(meta, filename, untouched_filename, duration)
    await _tmdb_from_imdb(prep_instance, meta, filename, announce=True)
    await _set_tmdb_metadata_if_present(
        prep_instance, meta, filename, include_special=False
    )
    await _fetch_imdb_info_if_needed(prep_instance, meta)
    meta.populate_cast()


def _aka_identity(value: str, year_text: str) -> str:
    if year_text:
        value = re.sub(
            rf"\s*[\[(]?{re.escape(year_text)}[\])]?\s*$", "", value
        )
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _optional_text(value: Any, *, strip: bool = True) -> str:
    text = "" if value is None else str(value)
    return text.strip() if strip else text


def _aka_is_distinct(title_text: str, candidate: str, year_text: str) -> bool:
    same_identity = _aka_identity(title_text, year_text) == _aka_identity(
        candidate, year_text
    )
    return all((bool(candidate), not same_identity))


def _distinct_aka(title: Any, aka: Any, year: Any = None) -> str:
    aka_text = _optional_text(aka)
    candidate = re.sub(r"^AKA\s*[:\-]?\s*", "", aka_text, flags=re.I).strip()
    year_text = _optional_text(year)
    title_text = _optional_text(title, strip=False)
    return (
        aka_text if _aka_is_distinct(title_text, candidate, year_text) else ""
    )


def _aka_similarity_candidate(title: str, candidate: str) -> str | None:
    normalized = (
        candidate[4:].strip().lower()
        if candidate.lower().startswith("aka")
        else candidate.lower()
    )
    if not normalized:
        return None
    difference = SequenceMatcher(None, title, normalized).ratio()
    if difference >= 0.7 or normalized in title:
        return None
    return candidate


def _imdb_aka_value(meta: Meta, key: str, year: str) -> str:
    value = str(meta.imdb_info.get(key, "")).strip()
    if year and f"({year})" in value:
        return value.replace(f"({year})", "").strip()
    return value


def _initial_imdb_aka_key(meta: Meta) -> str | None:
    title = _optional_text(meta.title).lower()
    primary = _aka_similarity_candidate(
        title, _optional_text(meta.imdb_info.get("title")).lower()
    )
    if primary is not None:
        return "title"
    secondary = _aka_similarity_candidate(
        title, _optional_text(meta.imdb_info.get("aka")).lower()
    )
    return "aka" if secondary is not None else None


def _set_initial_imdb_aka(meta: Meta, key: str | None) -> None:
    if key is None:
        meta.aka = ""
        return
    year = _optional_text(meta.imdb_info.get("year"))
    meta.aka = f"AKA {_imdb_aka_value(meta, key, year)}"
    meta.title = _optional_text(meta.title)


def _apply_initial_imdb_aka(meta: Meta) -> None:
    if not meta.imdb_info.get("title") or meta.aka:
        if not meta.aka:
            meta.aka = ""
        return
    _set_initial_imdb_aka(meta, _initial_imdb_aka_key(meta))


async def _finalize_season_state(
    prep_instance: Any, meta: Meta, video: str
) -> Meta:
    if not meta.not_anime and meta.category == "TV":
        meta = await prep_instance.season_episode_manager.get_season_episode(
            video, meta
        )
    if meta.category == "TV" and meta.tv_pack:
        await prep_instance.season_episode_manager.check_season_pack_completeness(
            meta
        )
    return meta


def _imdb_tv_movie_type(value: Any) -> bool:
    text = str(value or "")
    if not text:
        return False
    pattern = re.compile(
        r"(^|,\s*)(?:tv\ movie|tv\ special|tvmovie)(\s*,|$)", re.IGNORECASE
    )
    return bool(pattern.search(text))


def _detect_tv_movie(meta: Meta) -> None:
    meta.tv_movie = False
    if _id_is_zero(meta.imdb_id):
        return
    imdb_type = meta.imdb_info.get("type", "")
    if not _imdb_tv_movie_type(imdb_type):
        return
    logger.debug(
        f"[yellow]Identified as TV Movie based on IMDb type: {imdb_type}[/yellow]"
    )
    meta.tv_movie = True


def _tv_metadata_target(meta: Meta) -> bool:
    return (meta.category == "TV" or meta.tv_movie) and meta.category not in {
        "BOOK",
        "GAME",
    }


def _apply_tvmaze_tvdb_tuple(
    meta: Meta,
    tvmaze: Any,
    tvdb: Any,
    tvdb_data: Any,
    tvdb_name: Any,
) -> None:
    if tvmaze:
        meta.tvmaze_id = tvmaze
        logger.debug(f"[blue]Found TVMAZE ID from search: {tvmaze}[/blue]")
    if tvdb:
        meta.tvdb_id = tvdb
        logger.debug(f"[blue]Found TVDB ID from search: {tvdb}[/blue]")
    if tvdb_data:
        meta.tvdb_search_results = tvdb_data
        logger.debug("[blue]Found TVDB search results from search.[/blue]")
    if tvdb_name:
        meta.tvdb_series_name = tvdb_name
        logger.debug(
            f"[blue]Found TVDB series name from search: {tvdb_name}[/blue]"
        )


async def _search_both_tv_ids(
    prep_instance: Any,
    meta: Meta,
    filename: str,
    search_year: str,
) -> bool:
    if not (_id_is_zero(meta.tvmaze_id) and _id_is_zero(meta.tvdb_id)):
        return False
    (
        tvmaze,
        tvdb,
        tvdb_data,
        tvdb_name,
    ) = await prep_instance.metadata_searching_manager.get_tvmaze_tvdb(
        filename,
        search_year,
        meta.imdb_id,
        meta.tmdb_id,
        meta.manual_date,
        meta.tvmaze_manual,
        year=meta.year,
        tv_movie=meta.tv_movie,
        base_dir=meta.base_dir,
        unattended=meta.unattended,
    )
    _apply_tvmaze_tvdb_tuple(meta, tvmaze, tvdb, tvdb_data, tvdb_name)
    return True


async def _search_missing_tvmaze(
    prep_instance: Any,
    meta: Meta,
    filename: str,
    search_year: str,
    both_ids_searched: bool,
) -> None:
    if not _id_is_zero(meta.tvmaze_id) or both_ids_searched:
        return
    logger.debug("[yellow]No TVMAZE ID found, attempting to fetch...[/yellow]")
    result = await tvmaze_manager.search_tvmaze(
        filename,
        search_year,
        meta.imdb_id,
        meta.tvdb_id,
        manual_date=meta.manual_date,
        tvmaze_manual=meta.tvmaze_manual,
        return_full_tuple=False,
        base_dir=meta.base_dir,
        config=prep_instance.config,
        unattended=meta.unattended,
    )
    meta.tvmaze_id = result if isinstance(result, int) else result[0]


async def _tvdb_search_result(
    prep_instance: Any, meta: Meta, filename: str
) -> tuple[Any, Any] | None:
    try:
        return await prep_instance.tvdb_handler.search_tvdb_series(
            filename=filename, year=meta.year
        )
    except Exception as error:
        logger.error(f"[red]Error searching TVDB: {error}[/red]")
        return None


def _apply_tvdb_search_result(meta: Meta, result: tuple[Any, Any]) -> None:
    series_results, series_id = result
    if series_id:
        meta.tvdb_id = series_id
        logger.info(
            f"[blue]Found TVDB series ID from search: {series_id}[/blue]"
        )
    if series_results:
        meta.tvdb_search_results = series_results


async def _search_missing_tvdb(
    prep_instance: Any,
    meta: Meta,
    filename: str,
    both_ids_searched: bool,
) -> None:
    if not _id_is_zero(meta.tvdb_id) or both_ids_searched:
        return
    logger.debug("[yellow]No TVDB ID found, attempting to fetch...[/yellow]")
    result = await _tvdb_search_result(prep_instance, meta, filename)
    if result is not None:
        _apply_tvdb_search_result(meta, result)


def _episode_imdb_candidate(meta: Meta) -> int | None:
    value = str(meta.tvdb_imdb_id or "").replace("tt", "")
    if not value.isdigit():
        return None
    candidate = int(value)
    return candidate if candidate != _to_int(meta.imdb_id) else None


def _series_imdb_from_episode_info(info: Any) -> int | None:
    if not isinstance(info, dict):
        return None
    series = cast(dict[str, Any], info).get("series", {})
    if not isinstance(series, dict):
        return None
    value = str(cast(dict[str, Any], series).get("series_id", "")).replace(
        "tt", ""
    )
    return int(value) if value.isdigit() else None


def _apply_episode_imdb_aka(meta: Meta) -> None:
    if not meta.imdb_info.get("title"):
        return
    title = str(meta.title or "").strip()
    year = str(meta.imdb_info.get("year", ""))
    candidate = _aka_similarity_candidate(
        title.lower(), str(meta.imdb_info.get("aka", "")).strip()
    )
    meta.aka = (
        f"AKA {_imdb_aka_value(meta, 'aka', year)}"
        if candidate is not None
        else ""
    )


async def _refresh_series_imdb_from_episode(
    prep_instance: Any, meta: Meta
) -> None:
    episode_imdb = _episode_imdb_candidate(meta)
    if episode_imdb is None:
        return
    episode_info = await imdb_manager.get_imdb_from_episode(str(episode_imdb))
    series_imdb = _series_imdb_from_episode_info(episode_info)
    if series_imdb is None or series_imdb == _to_int(meta.imdb_id):
        return
    logger.debug(f"[yellow]Updating IMDb ID from episode data: {series_imdb}")
    meta.imdb_id = series_imdb
    meta.imdb_info = await imdb_manager.get_imdb_info_api(
        meta.imdb_id,
        manual_language=meta.manual_language,
        base_dir=meta.base_dir,
        config=prep_instance.config,
    )
    _apply_episode_imdb_aka(meta)


def _normalized_tvdb_series_name(series_name: str) -> str:
    value = re.sub(r"\s*\b(?:19|20)\d{2}\b\s*", "", series_name).strip()
    return value.replace("(", "").replace(")", "").strip()


def _tvdb_series_title_candidate(meta: Meta) -> str:
    if not meta.tvdb_series_name or meta.category != "TV":
        return ""
    if meta.title == meta.tvdb_series_name:
        return ""
    logger.debug(f"[yellow]tvdb series name: {meta.tvdb_series_name}")
    series_name = _normalized_tvdb_series_name(str(meta.tvdb_series_name))
    if _tvdb_title_drops_existing_leading_article(meta.title, series_name):
        return ""
    return series_name


def _apply_tvdb_series_title(meta: Meta) -> None:
    candidate = _tvdb_series_title_candidate(meta)
    if candidate:
        meta.title = candidate


async def _finalize_tv_metadata(
    prep_instance: Any, meta: Meta, filename: str
) -> Meta:
    if not _tv_metadata_target(meta):
        return meta
    search_year = _normalize_search_year(meta.search_year) or ""
    both_ids_searched = await _search_both_tv_ids(
        prep_instance, meta, filename, search_year
    )
    await _search_missing_tvmaze(
        prep_instance, meta, filename, search_year, both_ids_searched
    )
    await _search_missing_tvdb(
        prep_instance, meta, filename, both_ids_searched
    )
    meta = await prep_instance.metadata_searching_manager.get_tv_data(meta)
    await _refresh_series_imdb_from_episode(prep_instance, meta)
    _apply_tvdb_series_title(meta)
    return meta


def _apply_bluray_config(prep_instance: Any, meta: Meta) -> bool:
    defaults = prep_instance.config["DEFAULT"]
    meta.bluray_score = int(float(defaults.get("bluray_score", 100)))
    meta.bluray_single_score = int(
        float(defaults.get("bluray_single_score", 100))
    )
    meta.use_bluray_images = defaults.get("use_bluray_images", False)
    return bool(defaults.get("get_bluray_info", False))


def _bluray_lookup_allowed(meta: Meta, enabled: bool) -> bool:
    return all(
        (
            meta.is_disc in ("BDMV", "DVD"),
            enabled,
            meta.distributor is None or meta.region is None,
            not _id_is_zero(meta.imdb_id),
            not meta.edit,
            not meta.site_check,
        )
    )


async def _rehost_bluray_images(prep_instance: Any, meta: Meta) -> None:
    url_host_mapping = {
        "ibb.co": "imgbb",
        "pixhost.to": "pixhost",
        "imgbox.com": "imgbox",
        "lostimg.cc": "lostimg",
    }
    await prep_instance.rehost_images_manager.check_hosts(
        meta,
        "covers",
        url_host_mapping=url_host_mapping,
        img_host_index=1,
        approved_image_hosts=["imgbox", "imgbb", "pixhost"],
    )


async def _maybe_bluray_metadata(prep_instance: Any, meta: Meta) -> None:
    enabled = _apply_bluray_config(prep_instance, meta)
    if not _bluray_lookup_allowed(meta, enabled):
        return
    releases = await get_bluray_releases(meta)
    if releases and meta.use_bluray_images:
        await _rehost_bluray_images(prep_instance, meta)


async def _final_source_override(prep_instance: Any, meta: Meta) -> Meta:
    enabled = bool(
        prep_instance.config["DEFAULT"].get("user_overrides", False)
    )
    if enabled and not meta.no_override:
        return await prep_instance.overrides.get_source_override(meta)
    return meta


def _media_info_tracks(mi_data: dict[str, Any]) -> list[dict[str, Any]]:
    media = mi_data.get("media", {})
    if not isinstance(media, dict):
        return []
    tracks = cast(dict[str, Any], media).get("track", [])
    if not isinstance(tracks, list):
        return []
    return [
        cast(dict[str, Any], track)
        for track in cast(list[Any], tracks)
        if isinstance(track, dict)
    ]


def _first_media_track(
    tracks: list[dict[str, Any]], track_type: str
) -> dict[str, Any] | None:
    return next(
        (track for track in tracks if track.get("@type") == track_type), None
    )


def _disc_embedded_bdinfo(meta: Meta) -> dict[str, Any]:
    if not meta.discs:
        return {}
    first = cast(list[dict[str, Any]], meta.discs)[0]
    value = first.get("bdinfo", {})
    if isinstance(value, dict) and value:
        return cast(dict[str, Any], value)
    return {}


def _stored_bdinfo(meta: Meta) -> dict[str, Any]:
    return (
        cast(dict[str, Any], meta.bdinfo)
        if isinstance(meta.bdinfo, dict)
        else {}
    )


def _effective_bdinfo(meta: Meta, bdinfo: dict[str, Any]) -> dict[str, Any]:
    if bdinfo:
        return bdinfo
    embedded = _disc_embedded_bdinfo(meta)
    return embedded if embedded else _stored_bdinfo(meta)


def _first_bd_track_value(bd_data: dict[str, Any], kind: str, key: str) -> Any:
    tracks = bd_data.get(kind, [])
    if not isinstance(tracks, list) or not tracks:
        return None
    first = cast(list[Any], tracks)[0]
    return (
        cast(dict[str, Any], first).get(key)
        if isinstance(first, dict)
        else None
    )


def _numeric_text(value: Any, pattern: str) -> str | None:
    if value is None or isinstance(value, dict):
        return None
    match = re.search(pattern, str(value).replace(",", ""))
    return match.group() if match else None


def _disc_bitrate(meta: Meta, bdinfo: dict[str, Any], kind: str) -> int | None:
    raw = _first_bd_track_value(
        _effective_bdinfo(meta, bdinfo), kind, "bitrate"
    )
    if raw is None:
        return None
    match = re.search(r"\d+", str(raw).replace(".", "").replace(",", ""))
    return int(match.group()) if match else None


def _primary_track_bitrate(track: dict[str, Any]) -> Any:
    for key in ("BitRate", "NominalBitRate", "BitRate_Maximum"):
        value = track.get(key)
        if value:
            return value
    return None


def _track_value(track: dict[str, Any] | None, key: str) -> Any:
    if track is None:
        return None
    return cast(Any, track.get(key))


def _video_general_bitrate(tracks: list[dict[str, Any]], raw: Any) -> Any:
    if raw and not isinstance(raw, dict):
        return raw
    general_value = _track_value(
        _first_media_track(tracks, "General"), "OverallBitRate"
    )
    return cast(Any, general_value if general_value is not None else raw)


def _media_bitrate(
    tracks: list[dict[str, Any]], track_type: str
) -> int | None:
    track = _first_media_track(tracks, track_type)
    if track is None:
        return None
    raw: Any = _primary_track_bitrate(track)
    if track_type == "Video":
        raw = _video_general_bitrate(tracks, raw)
    text = _numeric_text(raw, r"\d+")
    return int(text) // 1000 if text is not None else None


def _media_frame_rate_raw(tracks: list[dict[str, Any]]) -> Any:
    raw = _track_value(_first_media_track(tracks, "Video"), "FrameRate")
    if raw and not isinstance(raw, dict):
        return raw
    general_value = _track_value(
        _first_media_track(tracks, "General"), "FrameRate"
    )
    return cast(Any, general_value if general_value is not None else raw)


def _video_frame_rate(
    meta: Meta, bdinfo: dict[str, Any], tracks: list[dict[str, Any]]
) -> float | None:
    raw: Any = (
        _first_bd_track_value(_effective_bdinfo(meta, bdinfo), "video", "fps")
        if meta.is_disc == "BDMV"
        else _media_frame_rate_raw(tracks)
    )
    text = _numeric_text(raw, r"\d+(?:\.\d+)?")
    return float(text) if text is not None else None


def _disc_dimensions(meta: Meta) -> tuple[int | None, int | None]:
    if not meta.resolution:
        return None, None
    try:
        height = int(
            str(meta.resolution).lower().replace("p", "").replace("i", "")
        )
    except TypeError, ValueError:
        return None, None
    return round((16 / 9) * height), height


def _media_dimensions(
    tracks: list[dict[str, Any]],
) -> tuple[int | None, int | None]:
    video_track = _first_media_track(tracks, "Video")
    if video_track is None:
        return None, None
    try:
        return (
            int(float(video_track.get("Width", 0))),
            int(float(video_track.get("Height", 0))),
        )
    except TypeError, ValueError:
        return None, None


def _apply_numeric_media_metrics(
    meta: Meta, bdinfo: dict[str, Any], mi_data: dict[str, Any]
) -> None:
    tracks = _media_info_tracks(mi_data)
    if meta.is_disc == "BDMV":
        meta.video_bitrate = _disc_bitrate(meta, bdinfo, "video")
        meta.audio_bitrate = _disc_bitrate(meta, bdinfo, "audio")
        meta.video_width, meta.video_height = _disc_dimensions(meta)
    else:
        meta.video_bitrate = _media_bitrate(tracks, "Video")
        meta.audio_bitrate = _media_bitrate(tracks, "Audio")
        meta.video_width, meta.video_height = _media_dimensions(tracks)
    meta.frame_rate = _video_frame_rate(meta, bdinfo, tracks)


async def _apply_media_source_characteristics(
    meta: Meta,
    video: str,
    folder_id: str,
    base_dir: str,
    bdinfo: dict[str, Any],
    mi_data: dict[str, Any],
) -> None:
    meta.container = await video_manager.get_container(meta)
    meta.three_d = await video_manager.is_3d(bdinfo)
    source_type = _optional_text(meta.type, strip=False)
    media_path = _optional_text(meta.path, strip=False)
    disc_type = _optional_text(meta.is_disc, strip=False)
    meta.source, meta.type = await get_source(
        source_type,
        video,
        media_path,
        disc_type,
        meta,
        folder_id,
        base_dir,
    )
    meta.uhd = await video_manager.get_uhd(
        _optional_text(meta.type, strip=False),
        guessit_fn(media_path),
        str(meta.resolution),
        media_path,
    )
    meta.hdr = await video_manager.get_hdr(mi_data, bdinfo)
    _apply_numeric_media_metrics(meta, bdinfo, mi_data)


async def _apply_distributor_codec(
    meta: Meta, bdinfo: dict[str, Any], mi_data: dict[str, Any]
) -> None:
    meta.distributor = await get_distributor(meta.distributor)
    if meta.distributor is None:
        meta.distributor = ""
    if meta.is_disc == "BDMV":
        meta.region = await get_region(bdinfo, meta.region)
        meta.video_codec = await video_manager.get_video_codec(bdinfo)
    else:
        (
            meta.video_encode,
            meta.video_codec,
            meta.has_encode_settings,
            meta.bit_depth,
        ) = await video_manager.get_video_encode(
            mi_data, str(meta.type or ""), bdinfo
        )
    if meta.region is None:
        meta.region = ""


async def _apply_edition_metadata(meta: Meta, bdinfo: dict[str, Any]) -> None:
    if meta.no_edition:
        meta.edition = ""
        return
    manual_edition = meta.manual_edition or ""
    meta.edition, meta.repack, meta.webdv = await get_edition(
        meta.uuid, bdinfo, meta.filelist, manual_edition, meta
    )
    if "REPACK" not in meta.edition:
        return
    repack_match = re.search(r"REPACK[\d]?", meta.edition)
    if repack_match:
        meta.repack = repack_match.group(0)
    meta.edition = (
        re.sub(r"REPACK[\d]?", "", meta.edition).strip().replace("  ", " ")
    )


async def _validate_encode_settings(meta: Meta) -> None:
    meta.valid_mi_settings = True
    if meta.is_disc or meta.type != "ENCODE" or meta.video_codec == "AV1":
        return
    if validate_mediainfo(meta, settings=True):
        return
    logger.info(
        "[red]MediaInfo validation failed. This file does not contain encode settings."
    )
    meta.valid_mi_settings = False
    await asyncio.sleep(2)


def _subsplease_bitrates(meta: Meta) -> tuple[str, str]:
    tracks = _mediainfo_track_mappings(meta)
    video = tracks[1] if len(tracks) > 1 else {}
    general = tracks[0] if tracks else {}
    bitrate = video.get("BitRate", "")
    overall = general.get("OverallBitRate", "")
    return (
        "" if isinstance(bitrate, dict) else str(bitrate),
        "" if isinstance(overall, dict) else str(overall),
    )


def _numeric_bitrate(value: str) -> int:
    return int(value) if value.isdigit() else 0


def _subsplease_threshold(resolution: str) -> int:
    return {"1080p": 8_000_000, "720p": 4_000_000}.get(resolution, 0)


def _subsplease_service(resolution: str, bitrate: str, overall: str) -> str:
    effective = max(_numeric_bitrate(bitrate), _numeric_bitrate(overall))
    threshold = _subsplease_threshold(resolution)
    if not effective or not threshold:
        return ""
    return "CR" if effective >= threshold else "HIDI"


def _apply_subsplease_service(meta: Meta) -> None:
    if meta.tag != "-SubsPlease":
        return
    bitrate, overall = _subsplease_bitrates(meta)
    meta.episode_title = ""
    service = _subsplease_service(str(meta.resolution), bitrate, overall)
    if service:
        meta.service = service


async def _service_longname_for_code(code: str) -> str:
    services = cast(dict[str, str], await get_service(get_services_only=True))
    matches = [name for name, value in services.items() if value == code]
    return max(matches, key=len, default=code)


async def _resolve_service_metadata(meta: Meta, video: str) -> None:
    if meta.service in (None, ""):
        meta.service, meta.service_longname = await get_service(
            video, meta.tag, meta.audio, meta.filename
        )
        return
    code = str(meta.service or "")
    meta.service_longname = await _service_longname_for_code(code)


def _scene_nfo_service_needed(meta: Meta) -> bool:
    return all((bool(meta.scene), not meta.service, meta.category == "TV"))


async def _apply_service_metadata(
    prep_instance: Any, meta: Meta, video: str
) -> None:
    await _resolve_service_metadata(meta, video)
    if _scene_nfo_service_needed(meta):
        await prep_instance.parse_scene_nfo(meta)


def _nonempty_genre_values(values: Any) -> list[str]:
    return [
        str(value).strip()
        for value in cast(list[Any], values or [])
        if str(value).strip()
    ]


def _imdb_genre_values(meta: Meta) -> list[str]:
    return [
        value.strip()
        for value in str(meta.imdb_info.get("genres") or "").split(",")
        if value.strip()
    ]


def _unique_casefold_values(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        key = value.lower()
        if key not in seen:
            seen.add(key)
            unique.append(value)
    return unique


def _combined_genres(meta: Meta) -> str:
    values = [*_nonempty_genre_values(meta.genres), *_imdb_genre_values(meta)]
    return ", ".join(_unique_casefold_values(values))


async def _finalize_video_metadata(
    prep_instance: Any,
    meta: Meta,
    video: str,
    bdinfo: dict[str, Any],
    mi_data: dict[str, Any],
) -> None:
    if meta.category not in ("TV", "MOVIE"):
        return
    (
        meta.audio,
        meta.channels,
        meta.has_commentary,
    ) = await prep_instance.audio_manager.get_audio_v2(mi_data, meta, bdinfo)
    await _apply_media_source_characteristics(
        meta,
        video,
        Path(str(meta.path)).name,
        meta.base_dir,
        bdinfo,
        mi_data,
    )
    await _apply_distributor_codec(meta, bdinfo, mi_data)
    await _apply_edition_metadata(meta, bdinfo)
    await _validate_encode_settings(meta)
    meta.stream = await prep_instance.stream_optimized(meta.stream)
    _apply_subsplease_service(meta)
    await _apply_service_metadata(prep_instance, meta, video)
    meta.combined_genres = _combined_genres(meta)


async def _initial_tag(meta: Meta, video: str) -> None:
    if meta.tag is not None:
        return
    if meta.we_need_tag:
        meta.tag = await get_tag(meta.scene_name, meta)
        return
    meta.tag = await get_tag(video, meta)


def _scene_tag_retry_allowed(meta: Meta) -> bool:
    return all(
        (
            not meta.scene,
            bool(meta.tag),
            not meta.we_need_tag,
            not meta.is_disc or bool(meta.keep_folder),
        )
    )


def _release_filename_stem(video: str) -> str:
    match = re.match(r"^(.+)\.[a-zA-Z0-9]{3,4}$", Path(video).name)
    return match.group(1) if match is not None else ""


def _lowercase_release_stem(meta: Meta, video: str) -> str:
    if not _scene_tag_retry_allowed(meta):
        return ""
    stem = _release_filename_stem(video)
    return stem if stem.islower() else ""


async def _scene_release_name_from_lowercase(
    prep_instance: Any, meta: Meta, videopath: str
) -> str:
    release_name, _scene, _imdb = await prep_instance.scene_manager.is_scene(
        videopath, meta, meta.imdb_id, lower=True
    )
    return str(release_name or "")


async def _apply_scene_release_tag(meta: Meta, release_name: str) -> None:
    try:
        meta.scene_name = release_name
        meta.tag = await get_tag(release_name, meta)
    except Exception:
        logger.error(
            "[red]Error getting tag from scene name, check group tag.[/red]"
        )


async def _maybe_scene_tag_retry(
    prep_instance: Any, meta: Meta, videopath: str, video: str
) -> None:
    if not _lowercase_release_stem(meta, video):
        return
    release_name = await _scene_release_name_from_lowercase(
        prep_instance, meta, videopath
    )
    if release_name:
        await _apply_scene_release_tag(meta, release_name)


def _normalize_tag_prefix(meta: Meta) -> None:
    if meta.tag is None:
        return
    if meta.tag and not meta.tag.startswith("-"):
        meta.tag = f"-{meta.tag}"


def _personal_release_groups(prep_instance: Any) -> set[str]:
    groups = prep_instance.config["DEFAULT"].get("personal_release_groups", [])
    if not isinstance(groups, list):
        return set()
    return {
        str(group).lstrip("-").lower()
        for group in cast(list[Any], groups)
        if group
    }


def _apply_personal_release(prep_instance: Any, meta: Meta) -> None:
    if not meta.tag:
        return
    detected = meta.tag.lstrip("-").lower()
    if detected not in _personal_release_groups(prep_instance):
        return
    meta.personalrelease = True
    logger.debug(
        f"[green]Detected release group in personal_release_groups, automatically setting --personalrelease to True - {detected}[/green]"
    )


def _strip_channel_tag(meta: Meta) -> None:
    channels = meta.channels
    if not channels or meta.tag is None:
        return
    if meta.tag[1:].startswith(channels):
        meta.tag = meta.tag.replace(f"-{channels}", "")


async def _finalize_tag(
    prep_instance: Any, meta: Meta, videopath: str, video: str
) -> Meta:
    had_tag = meta.tag is not None
    await _initial_tag(meta, video)
    if not had_tag:
        await _maybe_scene_tag_retry(prep_instance, meta, videopath, video)
    else:
        _normalize_tag_prefix(meta)
    meta = await tag_override(meta)
    _apply_personal_release(prep_instance, meta)
    _strip_channel_tag(meta)
    if meta.no_tag:
        meta.tag = ""
    return meta


def _populate_duplicate_ids(meta: Meta) -> None:
    meta.tmdb = meta.tmdb_id
    imdb_id = _to_int(meta.imdb_id)
    if imdb_id:
        meta.imdb = str(imdb_id).zfill(7)
        meta.imdb_tt = f"tt{meta.imdb}"
    else:
        meta.imdb = "0"
        meta.imdb_tt = ""
    meta.mal = meta.mal_id
    meta.tvdb = meta.tvdb_id
    meta.tvmaze = meta.tvmaze_id


def _apply_common_nonvideo_defaults(meta: Meta, videopath: str) -> None:
    meta.container = Path(videopath).suffix.lstrip(".").lower()
    meta.audio = ""
    meta.channels = ""
    meta.has_commentary = False
    meta.three_d = ""
    meta.uhd = ""
    meta.hdr = ""
    meta.distributor = ""
    meta.region = ""
    meta.video_codec = ""
    meta.video_encode = ""
    meta.has_encode_settings = False
    meta.bit_depth = "0"
    meta.repack = ""
    meta.webdv = False
    meta.title = str(meta.title or "")
    meta.year = meta.year or None
    meta.overview = str(meta.overview or "")
    meta.genres = list(meta.genres or [])


def _apply_book_type_defaults(meta: Meta, videopath: str) -> None:
    if not meta.type:
        meta.type = Path(videopath).suffix.lstrip(".").upper()
    if str(meta.type).upper() in ("CBR", "CBZ"):
        meta.comic = True


def _apply_book_defaults(meta: Meta, videopath: str) -> None:
    if meta.category != "BOOK":
        return
    _apply_common_nonvideo_defaults(meta, videopath)
    meta.source = "WEB"
    _apply_book_type_defaults(meta, videopath)
    if not meta.edition:
        meta.edition = str(meta.manual_edition or "").strip()


def _apply_game_defaults(meta: Meta, videopath: str) -> None:
    if meta.category != "GAME":
        return
    _apply_common_nonvideo_defaults(meta, videopath)
    meta.source = str(meta.source or "")
    meta.type = str(meta.type or "GAME")
    meta.edition = ""


def _episode_localization_allowed(
    prep_instance: Any, meta: Meta, tracker_name: str
) -> bool:
    if meta.category != "TV" or meta.tv_pack:
        return False
    if tracker_name not in ("BJSHARE", "BRASILTRACKER"):
        return True
    return bool(prep_instance.config["DEFAULT"].get("episode_overview", False))


def _localization_data_type_allowed(
    prep_instance: Any,
    meta: Meta,
    tracker_name: str,
    data_type: str,
) -> bool:
    if data_type == "season":
        return meta.category == "TV"
    if data_type == "episode":
        return _episode_localization_allowed(prep_instance, meta, tracker_name)
    if data_type == "main":
        return meta.category in ("TV", "MOVIE")
    return True


def _merged_localization_tags(existing: str, appended: str) -> str:
    values = [
        value.strip()
        for value in f"{existing},{appended}".split(",")
        if value.strip()
    ]
    return ",".join(sorted(set(values)))


def _tracker_localization_map(tracker_class: Any) -> dict[str, Any]:
    raw = getattr(tracker_class, "tmdb_localization_requirements", None)
    return cast(dict[str, Any], raw) if isinstance(raw, dict) else {}


def _merge_tracker_localization_requirements(
    prep_instance: Any,
    meta: Meta,
    tracker_name: str,
    raw: dict[str, Any],
    requirements: dict[str, dict[str, str]],
) -> None:
    for language, types in raw.items():
        if not isinstance(types, dict):
            continue
        for data_type, append_to_response in cast(
            dict[str, Any], types
        ).items():
            if not _localization_data_type_allowed(
                prep_instance, meta, tracker_name, data_type
            ):
                continue
            current = requirements.setdefault(language, {}).get(data_type, "")
            requirements[language][data_type] = _merged_localization_tags(
                current, str(append_to_response)
            )


def _localization_requirements(
    prep_instance: Any, meta: Meta
) -> dict[str, dict[str, str]]:
    from src.integrations.trackers.registry import tracker_class_map

    requirements: dict[str, dict[str, str]] = {}
    for tracker_name in meta.trackers:
        tracker_class = tracker_class_map.get(tracker_name)
        if tracker_class is None:
            continue
        raw = _tracker_localization_map(tracker_class)
        if raw:
            _merge_tracker_localization_requirements(
                prep_instance, meta, tracker_name, raw, requirements
            )
    return requirements


def _localization_tasks(
    prep_instance: Any,
    meta: Meta,
    requirements: dict[str, dict[str, str]],
) -> list[tuple[str, str, Any]]:
    return [
        (
            language,
            data_type,
            prep_instance.tmdb_manager.get_tmdb_localized_data(
                meta,
                data_type=data_type,
                language=language,
                append_to_response=append_to_response,
            ),
        )
        for language, types in requirements.items()
        for data_type, append_to_response in types.items()
    ]


def _store_localized_results(
    meta: Meta, tasks: list[tuple[str, str, Any]], results: list[Any]
) -> None:
    meta.tmdb_localized_data = {}
    for (language, data_type, _coroutine), result in zip(
        tasks, results, strict=True
    ):
        if result:
            meta.tmdb_localized_data.setdefault(language, {})[data_type] = (
                result
            )


async def _run_localization_prefetch(prep_instance: Any, meta: Meta) -> None:
    requirements = _localization_requirements(prep_instance, meta)
    tasks = _localization_tasks(prep_instance, meta, requirements)
    if not tasks:
        return
    logger.debug(
        f"[cyan]Pre-fetching TMDB localized data for languages: {list(requirements.keys())}[/cyan]"
    )
    results = list(await asyncio.gather(*(task[2] for task in tasks)))
    _store_localized_results(meta, tasks, results)


async def _prefetch_tmdb_localized_data(
    prep_instance: Any, meta: Meta
) -> None:
    if _id_is_zero(meta.tmdb_id) or meta.category not in ("TV", "MOVIE"):
        return
    try:
        await _run_localization_prefetch(prep_instance, meta)
    except Exception as error:
        logger.error(
            f"[red]Error pre-fetching TMDB localized data: {error}[/red]"
        )


async def finalize_metadata(
    prep_instance: Any,
    meta: Meta,
    videopath: str,
    bdinfo: dict[str, Any],
    mi: dict[str, Any] | None,
    filename: str,
    _untouched_filename: str,
    video: str,
) -> None:
    _apply_initial_imdb_aka(meta)
    meta = await _finalize_season_state(prep_instance, meta, video)
    _detect_tv_movie(meta)
    meta = await _finalize_tv_metadata(prep_instance, meta, filename)
    meta.aka = _distinct_aka(meta.title, meta.aka, meta.year)
    await _maybe_bluray_metadata(prep_instance, meta)
    meta = await _final_source_override(prep_instance, meta)
    meta.video = video
    await _finalize_video_metadata(
        prep_instance, meta, video, bdinfo, mi or {}
    )
    if meta.category in ("TV", "MOVIE", "XXX"):
        meta.adult_media = prep_instance.check_adult_media(meta)
    meta = await _finalize_tag(prep_instance, meta, videopath, video)
    _populate_duplicate_ids(meta)
    _apply_book_defaults(meta, videopath)
    _apply_game_defaults(meta, videopath)
    await _prefetch_tmdb_localized_data(prep_instance, meta)
    meta.pre_release = check_pre_release(meta)
