# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from pathlib import Path
from typing import Any

from src.console import logger
from src.meta import Meta
from src.trackers.common import Common
from src.trackers.UNIT3D import UNIT3D


class Luminarr(UNIT3D):
    """
    Luminarr is a Private Torrent Tracker for MOVIES / TV
    """

    tracker = "LUMINARR"
    display_name = "Luminarr"
    allows_bloated_audio = True
    base_url = "https://luminarr.me"
    banned_groups: tuple[str, ...] = ()
    id_url = f"{base_url}/api/torrents/"
    upload_url = f"{base_url}/api/torrents/upload"
    requests_url = f"{base_url}/api/requests/filter"
    search_url = f"{base_url}/api/torrents/filter"
    torrent_url = f"{base_url}/torrents/"
    supported_categories = ("TV", "MOVIE")
    tracker_urls = ("https://luminarr.me",)
    _DISC_TYPES: frozenset[str] = frozenset({"BDMV", "DVD", "HDDVD_TS", "VIDEO_TS"})
    _ARCHIVE_EXTENSIONS: frozenset[str] = frozenset({".rar", ".r00", ".r01", ".r02", ".r03", ".r04", ".r05", ".r06", ".r07", ".r08", ".r09"})
    _EXTRA_FILE_EXTENSIONS: frozenset[str] = frozenset({".nfo", ".srt", ".sub", ".ssa", ".ass", ".vtt", ".idx", ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"})
    _VIDEO_EXTENSIONS: frozenset[str] = frozenset({".mkv", ".mp4", ".avi", ".mov", ".m4v", ".mpg", ".mpeg", ".m2ts", ".ts", ".wmv", ".flv"})
    _BOOTLEG_MARKERS: tuple[str, ...] = ("cam", "telesync", "tele-sync", "telecine", "tc", "r5")
    _TV_ENDED_STATUSES: frozenset[str] = frozenset({"ended", "canceled", "cancelled", "finished", "completed"})
    _TV_ONGOING_STATUSES: frozenset[str] = frozenset({"returning", "continuing", "in production", "upcoming", "ongoing"})

    @staticmethod
    def _is_path_like_file(filename: Any) -> bool:
        return str(filename).strip() != ""

    @staticmethod
    def _normalize_path(path_text: Any) -> str:
        return re.sub(r"[._]", " ", str(path_text or "").lower())

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config, tracker_name="LUMINARR")
        self.config = config
        self.common = Common(config)

    @staticmethod
    def _contains_extension(files: list[Any], extensions: set[str] | frozenset[str]) -> str:
        for item in files:
            filename = Path(str(item))
            if filename.suffix.lower() in extensions:
                return filename.name
        return ""

    @staticmethod
    def _contains_sample_file(files: list[Any]) -> str:
        for item in files:
            filename = str(item).lower()
            path = Path(filename)
            if "sample" in path.name and path.suffix.lower() in Luminarr._VIDEO_EXTENSIONS:
                return path.name
        return ""

    @staticmethod
    def _collect_video_paths(files: list[Any]) -> list[Path]:
        return [Path(str(item)) for item in files if Path(str(item)).suffix.lower() in Luminarr._VIDEO_EXTENSIONS]

    @staticmethod
    def _is_nested_relative_path(path: Path) -> bool:
        if path.is_absolute():
            return False
        normalized = str(path).replace("\\", "/")
        if normalized.startswith("./"):
            normalized = normalized[2:]
        if normalized.startswith("../"):
            return False
        return "/" in normalized

    @staticmethod
    def _top_level_folder(path: Path) -> str:
        if path.is_absolute():
            return ""
        normalized = str(path).replace("\\", "/")
        if normalized.startswith("./"):
            normalized = normalized[2:]
        parts = [part for part in normalized.split("/") if part and part not in {".", ".."}]
        if len(parts) <= 1:
            return ""
        return parts[0]

    @staticmethod
    def _has_bootleg_marker(value: str) -> bool:
        normalized = Luminarr._normalize_path(value)
        normalized = f" {normalized} "
        return any(re.search(rf"(?:^|[\s-]){re.escape(marker)}(?:$|[\s-])", normalized) for marker in Luminarr._BOOTLEG_MARKERS)

    async def _confirm_or_skip(self, message: str, meta: Meta) -> bool:
        if meta.unattended:
            return bool(meta.unattended_confirm)
        logger.info(f"{self.tracker}: [yellow]{message}[/yellow]")
        return await self.common.prompt_user_for_confirmation("Do you want to continue anyway?", meta)

    async def get_additional_data(self, meta: Meta) -> dict[str, Any]:
        return {
            "mod_queue_opt_in": await self.get_flag(meta, "modq"),
        }

    async def get_additional_checks(self, meta: Meta) -> bool:
        category = str(meta.category or "").upper()
        if category not in {"TV", "MOVIE"}:
            logger.info(f"{self.tracker}: [bold red]Only MOVIE and TV uploads are supported on {self.tracker}.[/bold red]")
            return False

        if not meta.tmdb and not meta.tmdb_id and not meta.imdb_id:
            logger.info(f"{self.tracker}: [bold red]TMDB/IMDb entry is required on {self.tracker}.[/bold red]")
            return False

        if meta.adult_media:
            logger.info(f"{self.tracker}: [bold red]Pornography is not allowed.[/bold red]")
            return False

        if self._has_bootleg_marker(str(meta.name or "")):
            logger.info(f"{self.tracker}: [bold red]Release markers indicate this is likely a bootleg/unauthorized source.[/bold red]")
            return False

        filelist = [item for item in (meta.filelist or []) if self._is_path_like_file(item)]
        video_paths = self._collect_video_paths(filelist)
        if not meta.is_disc and video_paths:
            if len(video_paths) == 1 and self._is_nested_relative_path(video_paths[0]):
                logger.info(f"{self.tracker}: [bold red]Single-file Movie/TV uploads must not be inside a folder.[/bold red]")
                return False

            if len(video_paths) > 1 and any(self._top_level_folder(path) == "" for path in video_paths):
                logger.info(f"{self.tracker}: [bold red]For multi-file Movie/TV uploads, all files must be placed in one top-level folder.[/bold red]")
                return False

            top_folders = {self._top_level_folder(path) for path in video_paths if self._top_level_folder(path)}
            if len(video_paths) > 1 and len(top_folders) != 1:
                logger.info(f"{self.tracker}: [bold red]Multi-file uploads must use a single top-level folder.[/bold red]")
                return False

            if category in {"MOVIE", "TV"} and meta.is_disc != "BDMV" and len(video_paths) != 1 and not meta.tv_pack:
                logger.info(f"{self.tracker}: [bold red]Movie and non-collection TV uploads should contain one video file per title.[/bold red]")
                return False

        if meta.is_disc not in Luminarr._DISC_TYPES and not await self.common.check_language_requirements(
            meta, self.tracker, languages_to_check=["english"], check_subtitle=True, prompt_on_failure=False
        ):
            return False

        original_language = str(meta.original_language or "").strip()
        if meta.is_disc not in Luminarr._DISC_TYPES and original_language and not await self.common.check_language_requirements(
            meta, self.tracker, languages_to_check=[original_language], check_audio=True, original_language=True, original_required=True, prompt_on_failure=False
        ):
            return False

        if meta.is_disc not in Luminarr._DISC_TYPES and meta.resolution not in ["8640p", "4320p", "2160p", "1440p", "1080p", "1080i", "720p"]:
            if not meta.unattended or (meta.unattended and meta.unattended_confirm):
                logger.info(f"{self.tracker}: [bold red]only allows SD releases when the content does not have a higher resolution release.[/bold red]")
                if not await self._confirm_or_skip("low resolution upload", meta):
                    return False
            else:
                return False

        if not meta.is_disc and meta.container != "mkv":
            logger.info(f"{self.tracker}: [bold red]only allows MKV containers for non-disc uploads.[/bold red]")
            return False

        if category in {"TV", "MOVIE"} and meta.screens < 3:
            logger.info(f"{self.tracker}: [bold red]{self.tracker} requires at least 3 screenshots in the description for Movie/TV uploads.[/bold red]")
            return False

        if not meta.is_disc:
            archive = self._contains_extension(filelist, self._ARCHIVE_EXTENSIONS)
            if archive:
                logger.info(f"{self.tracker}: [bold red]Archive or multipart files are not allowed for {self.tracker}. Found: {archive}[/bold red]")
                return False

            extra_file = self._contains_extension(filelist, self._EXTRA_FILE_EXTENSIONS) or self._contains_sample_file(filelist)
            if extra_file:
                logger.info(f"{self.tracker}: [bold red]Extra file '{extra_file}' is not allowed for {self.tracker}.[/bold red]")
                return False

        if category == "TV":
            seasons = self.common.extract_tv_seasons(filelist)
            if len(seasons) > 1:
                logger.info(f"{self.tracker}: [bold red]TV uploads must target a single season or a single episode on {self.tracker}.[/bold red]")
                return False

            tv_pack_allowed = self.common.is_tv_series_ended(meta, self._TV_ENDED_STATUSES, self._TV_ONGOING_STATUSES)
            if meta.tv_pack and not tv_pack_allowed:
                logger.info(f"{self.tracker}: [bold red]TV season packs are restricted to ended shows on {self.tracker}.[/bold red]")
                return False

            if not meta.tv_pack and self.common.count_tv_episodes(filelist) > 1:
                logger.info(f"{self.tracker}: [bold red]Non-pack TV uploads must be a single episode on {self.tracker}.[/bold red]")
                return False

        if meta.is_disc == "BDMV" and not meta.bdinfo:
            logger.info(f"{self.tracker}: [bold red]Full Blu-ray disc uploads require BDInfo information on {self.tracker}.[/bold red]")
            return False

        if meta.is_disc != "BDMV" and not meta.valid_mi_settings:
            logger.info(f"{self.tracker}: [bold red]No encoding settings in mediainfo, skipping {self.tracker} upload.[/bold red]")
            return False

        return self.common.check_and_confirm_adult_media_upload(meta, self.tracker)
