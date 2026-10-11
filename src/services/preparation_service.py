import asyncio
import json
import re
import time

# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import aiofiles

import src.services.preparation_helpers as prep_helpers
from src.domain_models.errors import OperationAbortedError
from src.domain_models.release import Meta
from src.engines.region_mapping import get_service
from src.integrations.cache.metadata_cache import set_run_disabled
from src.integrations.external_apis.radarr import RadarrManager
from src.integrations.external_apis.sonarr import SonarrManager
from src.integrations.external_apis.tmdb import TmdbManager
from src.integrations.external_apis.tvdb import TvdbData
from src.integrations.filesystem.screenshot_manifest import (
    files as manifest_files,
)
from src.integrations.image_hosts.rehosting import RehostImagesManager
from src.integrations.media.artwork import (
    audiobook_cover_missing_or_invalid,
    prepare_artwork,
)
from src.integrations.media.audio import AudioManager
from src.integrations.media.disc_info import DiscInfoManager
from src.integrations.media.language_adapter import languages_manager
from src.integrations.media.scene_detection import SceneManager
from src.integrations.media.screenshot_capture import TakeScreensManager
from src.integrations.observability.runtime_support import logger
from src.integrations.security.redaction import PathAwareEncoder
from src.services.argument_override_service import (
    ApplyOverrides,
    ArgumentParserFactory,
)
from src.services.book_preparation import (
    gather_book_prep as _gather_book_prep_fn,
)
from src.services.book_preparation import (
    resolve_book_filelist as _resolve_book_filelist_fn,
)
from src.services.early_artifact_service import restart_early_artifact_tasks
from src.services.episode_service import (
    SeasonEpisodeManager,
    sync_single_episode_from_filename,
)
from src.services.game_preparation import (
    gather_game_prep as _gather_game_prep_fn,
)
from src.services.game_preparation import (
    resolve_game_filelist as _resolve_game_filelist_fn,
)
from src.services.metadata_service import MetadataSearchingManager
from src.services.music_preparation import (
    enrich_music_from_discogs as _enrich_music_from_discogs_fn,
)
from src.services.music_preparation import (
    enrich_music_from_orpheus as _enrich_music_from_orpheus_fn,
)
from src.services.music_preparation import (
    gather_music_prep as _gather_music_prep_fn,
)
from src.services.podcast_preparation import (
    gather_podcast_prep as _gather_podcast_prep_fn,
)
from src.services.release_naming_service import NameManager
from src.services.tracker_metadata_service import TrackerDataManager

console: Any = None

ADULT_MEDIA_KEYWORDS = ("xxx", "erotic", "porn", "adult", "orgy")
MUSIC_EXTENSIONS = frozenset(
    {
        ".flac",
        ".mp3",
        ".m4a",
        ".aac",
        ".ac3",
        ".dts",
        ".wav",
        ".aiff",
        ".alac",
        ".ogg",
        ".opus",
        ".ape",
        ".wv",
    }
)
TV_PATH_PATTERNS = (
    r"(?i)[\\/](?:tv|tvshows|tv.shows|series|shows)[\\/]",
    r"(?i)[\\/](?:season\s*\d+|s\d+)[\\/]",
    r"(?i)[\\/](?:s\d{1,2}e\d{1,2}|s\d{1,2}|season\s*\d+)",
    r"(?i)(?:tv pack|season\s*\d+)",
)
TV_FILENAME_PATTERNS = (
    r"(?i)s\d{1,2}e\d{1,2}",
    r"(?i)s\d{1,2}",
    r"(?i)\b\d{1,2}x\d{2}\b",
    r"(?i)(?:season|series)\s*\d+",
    r"(?i)e\d{2,3}\s*\-",
    r"(?i)\d{4}\.\d{1,2}\.\d{1,2}",
)
SUBSPLEASE_ANIME_PATTERN = r"(?:\s-\s)?(\d{1,3})\s*\((?:\d+p|480p|480i|576i|576p|720p|1080i|1080p|2160p)\)"


async def populate_hdr_for_early_capture(
    meta: Meta, mi: dict[str, Any] | None, bdinfo: dict[str, Any] | None
) -> None:
    """Populate HDR before category detection starts asynchronous screenshot capture."""
    if meta.hdr or not (mi or bdinfo):
        return
    meta.hdr = await prep_helpers.video_manager.get_hdr(mi or {}, bdinfo)


class Prep:
    """
    Prepare for upload:
        Mediainfo/BDInfo
        Screenshots
        Database Identifiers (TMDB/IMDB/MAL/etc)
        Create Name
    """

    def __init__(
        self,
        screens: int,
        img_host: str,
        config: dict[str, Any],
        argument_parser_factory: ArgumentParserFactory | None = None,
    ) -> None:
        self.screens = screens
        self.config = config
        self.img_host = img_host.lower()
        self.tvdb_handler = TvdbData(config)
        self.overrides = ApplyOverrides(config, argument_parser_factory)
        self.audio_manager = AudioManager(config)
        self.disc_info_manager = DiscInfoManager(config)
        self.name_manager = NameManager(config)
        self.tracker_data_manager = TrackerDataManager(config)
        self.scene_manager = SceneManager(config)
        self.metadata_searching_manager = MetadataSearchingManager(config)
        self.tmdb_manager = TmdbManager(config)
        self.season_episode_manager = SeasonEpisodeManager(config)
        self.radarr_manager = RadarrManager(config)
        self.sonarr_manager = SonarrManager(config)
        self.rehost_images_manager = RehostImagesManager(config)
        self.takescreens_manager = TakeScreensManager(config)

    @staticmethod
    def _resolve_book_filelist(
        meta: Meta,
        videoloc: str,
    ) -> tuple[str, list[str], str, str]:
        """Delegate to :func:`src.services.book_preparation.resolve_book_filelist`."""
        return _resolve_book_filelist_fn(meta, videoloc)

    async def _gather_book_prep(
        self,
        meta: Meta,
        videopath: str,
        base_dir: str,
    ) -> None:
        """Delegate to :func:`src.services.book_preparation.gather_book_prep`."""
        await _gather_book_prep_fn(meta, videopath, base_dir, self.config)

    @staticmethod
    def _ensure_audiobook_cover(meta: Meta) -> None:
        if not audiobook_cover_missing_or_invalid(meta):
            return
        reason = "Audiobook cover is required but no valid prepared cover is available"
        logger.error(
            f"[bold red]{reason}. Upload preparation aborted.[/bold red]"
        )
        raise OperationAbortedError(reason)

    @staticmethod
    def _resolve_game_filelist(
        meta: Meta,
        videoloc: str,
    ) -> tuple[str, list[str], str, str]:
        """Delegate to :func:`src.services.game_preparation.resolve_game_filelist`."""
        return _resolve_game_filelist_fn(meta, videoloc)

    async def _gather_game_prep(
        self,
        meta: Meta,
        videopath: str,
        base_dir: str,
    ) -> None:
        """Delegate to :func:`src.services.game_preparation.gather_game_prep`."""
        await _gather_game_prep_fn(meta, videopath, base_dir, self.config)

    async def _gather_music_prep(self, meta: Meta) -> None:
        """Run the non-destructive MUSIC pipeline instead of video preparation."""
        await _gather_music_prep_fn(meta, self.config)

    @staticmethod
    def _podcast_requested(meta: Meta) -> bool:
        if meta.category == "PODCAST":
            return True
        manual_category = meta.manual_category
        return (
            isinstance(manual_category, str)
            and manual_category.strip().upper() == "PODCAST"
        )

    async def _process_special_tracker_stage(
        self,
        meta: Meta,
        client: Any,
        hash_ids: Any,
        tracker_ids: Any,
    ) -> None:
        prep_helpers.calculate_source_size(self, meta, str(meta.path or ""))
        await prep_helpers.process_trackers_and_torrent(
            self, meta, client, hash_ids, tracker_ids, "", ""
        )

    async def _gather_podcast_flow(
        self,
        meta: Meta,
        client: Any,
        hash_ids: Any,
        tracker_ids: Any,
        start_time: float,
    ) -> Meta:
        meta.category = "PODCAST"
        await _gather_podcast_prep_fn(meta)
        await self._process_special_tracker_stage(
            meta, client, hash_ids, tracker_ids
        )
        logger.debug(
            f"Podcast metadata processed in {time.time() - start_time:.2f} seconds"
        )
        return meta

    async def _gather_music_flow(
        self,
        meta: Meta,
        client: Any,
        hash_ids: Any,
        tracker_ids: Any,
        start_time: float,
    ) -> Meta:
        await self._gather_music_prep(meta)
        await self._process_special_tracker_stage(
            meta, client, hash_ids, tracker_ids
        )
        await _enrich_music_from_orpheus_fn(meta, self.config)
        await _enrich_music_from_discogs_fn(meta, self.config)
        await prepare_artwork(meta)
        logger.debug(
            f"Music metadata processed in {time.time() - start_time:.2f} seconds"
        )
        return meta

    def _xxx_max_videos(self) -> int:
        settings = getattr(
            self.takescreens_manager,
            "xxx_contact_sheet_settings",
            None,
        )
        if not callable(settings):
            return 6
        settings_fn = cast(Callable[[], tuple[int, int, int]], settings)
        _rows, _columns, max_videos = settings_fn()
        return max_videos

    def _configure_xxx_screens(self, meta: Meta) -> None:
        if meta.category != "XXX" or meta.screens <= 0:
            return
        meta.screens = min(len(meta.filelist or []), self._xxx_max_videos())

    def _start_early_screenshots(
        self,
        meta: Meta,
        filename: str,
        videopath: str,
        bdinfo: dict[str, Any],
    ) -> asyncio.Task[None] | None:
        if meta.keep_images:
            logger.debug(
                "[cyan]Deferring screenshot capture until description images have been checked.[/cyan]"
            )
            return None
        return asyncio.create_task(
            self._capture_early_screenshots(
                meta.copy(), filename, videopath, bdinfo
            )
        )

    @staticmethod
    async def _wait_early_screenshots(
        early_screenshots_task: asyncio.Task[None] | None,
    ) -> None:
        if early_screenshots_task is not None:
            await early_screenshots_task

    @staticmethod
    def _sync_xxx_screen_count(meta: Meta) -> None:
        if meta.category == "XXX":
            meta.screens = len(
                manifest_files(meta.base_dir, meta.uuid, "main")
            )

    async def _finalize_book_artwork(self, meta: Meta, videopath: str) -> None:
        if meta.category != "BOOK":
            return
        await (
            self.rehost_images_manager.takescreens_manager.prepare_book_cover(
                videopath, meta.uuid, meta.base_dir, meta
            )
        )
        await prepare_artwork(meta)
        meta_path = Path(meta.base_dir) / "tmp" / meta.uuid / "meta.json"
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(
            meta_path, "w", encoding="utf-8"
        ) as meta_file:
            await meta_file.write(
                json.dumps(meta.to_dict(), indent=4, cls=PathAwareEncoder)
            )

    async def _finish_video_prep(
        self,
        meta: Meta,
        videopath: str,
        early_screenshots_task: asyncio.Task[None] | None,
    ) -> None:
        await prepare_artwork(meta)
        await languages_manager.apply_confirmed_single_audio_language(meta)
        await languages_manager.process_desc_language(meta)
        await self._wait_early_screenshots(early_screenshots_task)
        self._sync_xxx_screen_count(meta)
        await self._finalize_book_artwork(meta, videopath)

    async def gather_prep(self, meta: Meta, mode: str) -> Meta:
        meta_start_time = time.time()
        set_run_disabled(bool(getattr(meta, "no_metadata_cache", False)))
        (
            use_sonarr,
            use_radarr,
            client,
            skip_tracker_descriptions,
            hash_ids,
            tracker_ids,
        ) = prep_helpers.init_meta(self, meta, mode)

        if self._podcast_requested(meta):
            return await self._gather_podcast_flow(
                meta, client, hash_ids, tracker_ids, meta_start_time
            )

        videoloc, bdinfo = await prep_helpers.detect_disc_and_category(
            self, meta
        )
        if meta.category == "MUSIC":
            return await self._gather_music_flow(
                meta, client, hash_ids, tracker_ids, meta_start_time
            )

        (
            filename,
            untouched_filename,
            videopath,
            search_term,
            search_file_folder,
            mi,
            video,
        ) = await prep_helpers.process_media_files(
            self, meta, videoloc, bdinfo
        )
        sync_single_episode_from_filename(meta)
        self._configure_xxx_screens(meta)
        await populate_hdr_for_early_capture(meta, mi, bdinfo)
        early_screenshots_task = self._start_early_screenshots(
            meta, filename, videopath, bdinfo
        )

        prep_helpers.calculate_source_size(self, meta, videopath)
        await prep_helpers.validate_media(self, meta)
        await prep_helpers.process_trackers_and_torrent(
            self,
            meta,
            client,
            hash_ids,
            tracker_ids,
            search_term,
            search_file_folder,
        )
        await restart_early_artifact_tasks(meta, client, self.config)
        await prep_helpers.search_metadata(
            self,
            meta,
            filename,
            untouched_filename,
            videopath,
            search_term,
            search_file_folder,
            use_sonarr,
            use_radarr,
            skip_tracker_descriptions,
            client,
            bdinfo,
            mi,
        )
        await prep_helpers.finalize_metadata(
            self,
            meta,
            videopath,
            bdinfo,
            mi,
            filename,
            untouched_filename,
            video,
        )
        await self._finish_video_prep(meta, videopath, early_screenshots_task)
        logger.debug(
            f"Metadata processed in {time.time() - meta_start_time:.2f} seconds"
        )
        return meta

    @staticmethod
    def _skip_early_screenshots(meta: Meta) -> bool:
        if meta.keep_images or meta.screens <= 0:
            return True
        return meta.category in {"MUSIC", "PODCAST", "GAME", "BOOK"}

    def _multi_screens_count(self) -> int:
        default_config = self.config.get("DEFAULT", {})
        if not isinstance(default_config, dict):
            return 2
        typed_config = cast(dict[str, Any], default_config)
        return int(typed_config.get("multiScreens", 2))

    async def _capture_bdmv_main(
        self, meta: Meta, filename: str, bdinfo: dict[str, Any]
    ) -> None:
        await self.takescreens_manager.disc_screenshots(
            meta,
            meta.filename or filename,
            bdinfo,
            meta.uuid,
            meta.base_dir,
            meta.vapoursynth,
            meta.image_list,
            meta.ffdebug,
            0,
            cleanup_after_capture=False,
            capture_group="main",
        )

    async def _capture_playlist_extras(
        self, meta: Meta, disc: dict[str, Any], multi_screens: int
    ) -> None:
        playlist_keys = [key for key in disc if key.startswith("bdinfo")]
        for index, key in enumerate(playlist_keys[1:], start=1):
            playlist_bdinfo = disc.get(key)
            if not isinstance(playlist_bdinfo, dict):
                continue
            await self.takescreens_manager.disc_screenshots(
                meta,
                f"PLAYLIST_{index}",
                playlist_bdinfo,
                meta.uuid,
                meta.base_dir,
                meta.vapoursynth,
                [],
                meta.ffdebug,
                multi_screens,
                True,
                False,
                f"PLAYLIST_{index}",
            )

    async def _capture_disc_extras(
        self, meta: Meta, discs: list[dict[str, Any]], multi_screens: int
    ) -> None:
        for index, disc in enumerate(discs[1:], start=1):
            disc_bdinfo = disc.get("bdinfo")
            if disc.get("type") != "BDMV" or not isinstance(disc_bdinfo, dict):
                continue
            await self.takescreens_manager.disc_screenshots(
                meta,
                f"FILE_{index}",
                disc_bdinfo,
                meta.uuid,
                meta.base_dir,
                meta.vapoursynth,
                [],
                meta.ffdebug,
                multi_screens,
                True,
                False,
                f"FILE_{index}",
            )

    async def _capture_bdmv_extras(self, meta: Meta) -> None:
        multi_screens = self._multi_screens_count()
        if multi_screens <= 0:
            return
        discs = cast(list[dict[str, Any]], list(meta.discs or []))
        if len(discs) == 1:
            await self._capture_playlist_extras(meta, discs[0], multi_screens)
            return
        await self._capture_disc_extras(meta, discs, multi_screens)

    async def _capture_bdmv(
        self, meta: Meta, filename: str, bdinfo: dict[str, Any]
    ) -> None:
        await self._capture_bdmv_main(meta, filename, bdinfo)
        await self._capture_bdmv_extras(meta)

    async def _capture_dvd(self, meta: Meta) -> None:
        await self.takescreens_manager.dvd_screenshots(
            meta,
            disc_num=0,
            num_screens=0,
            retry_cap=False,
            cleanup_after_capture=False,
        )

    async def _capture_xxx(self, meta: Meta) -> None:
        await self.takescreens_manager.xxx_contact_sheets(
            meta.filelist or [], meta.uuid, meta.base_dir, meta
        )

    async def _capture_video(
        self, meta: Meta, filename: str, videopath: str
    ) -> None:
        if not videopath:
            return
        await self.takescreens_manager.screenshots(
            videopath,
            filename,
            meta.uuid,
            meta.base_dir,
            meta,
            manual_frames=meta.manual_frames or "",
            cleanup_after_capture=False,
            capture_group="main",
        )

    async def _capture_early_by_kind(
        self,
        meta: Meta,
        filename: str,
        videopath: str,
        bdinfo: dict[str, Any],
    ) -> None:
        if meta.is_disc == "BDMV":
            await self._capture_bdmv(meta, filename, bdinfo)
        elif meta.is_disc == "DVD":
            await self._capture_dvd(meta)
        elif meta.category == "XXX":
            await self._capture_xxx(meta)
        else:
            await self._capture_video(meta, filename, videopath)

    async def _capture_early_screenshots(
        self, meta: Meta, filename: str, videopath: str, bdinfo: dict[str, Any]
    ) -> None:
        """Generate local screenshots while metadata and tracker IDs are fetched."""
        if self._skip_early_screenshots(meta):
            return
        try:
            await self._capture_early_by_kind(
                meta, filename, videopath, bdinfo
            )
            logger.debug("[cyan]Early screenshot generation completed.[/cyan]")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(
                f"[yellow]Early screenshot generation failed; upload stage will retry: {error}[/yellow]"
            )

    @staticmethod
    def _adult_genres_text(meta: Meta) -> str:
        genres = meta.combined_genres
        if isinstance(genres, list):
            return ", ".join(str(genre) for genre in genres)
        return str(genres)

    @classmethod
    def _adult_searchable_text(cls, meta: Meta) -> str:
        keywords = ", ".join(meta.keywords)
        genres = cls._adult_genres_text(meta)
        return ", ".join(part for part in (keywords, genres) if part)

    @staticmethod
    def _contains_adult_keyword(searchable: str) -> bool:
        for keyword in ADULT_MEDIA_KEYWORDS:
            if re.search(
                rf"(^|,\s*){re.escape(keyword)}(\s*,|$)",
                searchable,
                re.IGNORECASE,
            ):
                return True
        return False

    def check_adult_media(self, meta: Meta) -> bool:
        if meta.category == "XXX" or meta.tmdb_adult_media:
            return True
        return self._contains_adult_keyword(self._adult_searchable_text(meta))

    @staticmethod
    def _manual_category(meta: Meta) -> tuple[bool, str | None]:
        manual = meta.manual_category
        if not manual:
            return False, None
        if isinstance(manual, str):
            return True, manual.upper()
        return True, None

    @staticmethod
    def _music_category(meta: Meta) -> bool:
        return Path(meta.path or "").suffix.lower() in MUSIC_EXTENSIONS

    @staticmethod
    async def _xxx_category(meta: Meta) -> bool:
        if meta.is_disc:
            return False
        candidate = Path(meta.path or "")
        return await asyncio.to_thread(
            prep_helpers.is_xxx_video_release, candidate
        )

    @staticmethod
    def _matched_pattern(value: str, patterns: tuple[str, ...]) -> str | None:
        for pattern in patterns:
            if re.search(pattern, value):
                return pattern
        return None

    @classmethod
    def _path_tv_pattern(cls, path: str) -> str | None:
        return cls._matched_pattern(path, TV_PATH_PATTERNS)

    @classmethod
    def _filename_tv_pattern(cls, path: str, uuid: str) -> str | None:
        filename = Path(path).name
        for pattern in TV_FILENAME_PATTERNS:
            if re.search(pattern, uuid) or re.search(pattern, filename):
                return pattern
        return None

    @staticmethod
    def _subsplease_tv(path: str, uuid: str) -> bool:
        path_lower = path.lower()
        uuid_lower = uuid.lower()
        if "subsplease" not in path_lower and "subsplease" not in uuid_lower:
            return False
        return bool(
            re.search(SUBSPLEASE_ANIME_PATTERN, path_lower)
            or re.search(SUBSPLEASE_ANIME_PATTERN, uuid_lower)
        )

    @classmethod
    def _tv_category(cls, meta: Meta) -> bool:
        path = meta.path or ""
        uuid = meta.uuid
        logger.debug(
            f"[cyan]Checking category for path: {path} and uuid: {uuid}[/cyan]"
        )
        path_pattern = cls._path_tv_pattern(path)
        if path_pattern is not None:
            logger.debug(
                f"[cyan]Matched TV pattern in path: {path_pattern}[/cyan]"
            )
            return True
        filename_pattern = cls._filename_tv_pattern(path, uuid)
        if filename_pattern is not None:
            logger.debug(
                f"[cyan]Matched TV pattern in filename: {filename_pattern}[/cyan]"
            )
            return True
        if cls._subsplease_tv(path, uuid):
            logger.debug(
                f"[cyan]Matched Anime pattern for SubsPlease: {SUBSPLEASE_ANIME_PATTERN}[/cyan]"
            )
            return True
        return False

    async def get_cat(self, _video: str, meta: Meta) -> str | None:
        has_manual, manual_category = Prep._manual_category(meta)
        if has_manual:
            return manual_category
        if Prep._music_category(meta):
            return "MUSIC"
        if await Prep._xxx_category(meta):
            logger.debug(
                "[cyan]Matched XXX platform marker in release name[/cyan]"
            )
            return "XXX"
        return "TV" if Prep._tv_category(meta) else "MOVIE"

    async def stream_optimized(self, stream_opt: bool) -> int:
        return 1 if stream_opt is True else 0

    @staticmethod
    def _nfo_source(nfo_content: str) -> str:
        source_match = re.search(
            r"^Source\s*:\s*(.+?)$",
            nfo_content,
            re.MULTILINE | re.IGNORECASE,
        )
        if source_match is None:
            return ""
        return source_match.group(1).strip()

    @staticmethod
    def _matched_service(
        source: str, services: dict[str, str]
    ) -> tuple[str, str] | None:
        normalized = source.upper()
        for service_name, service_code in services.items():
            if normalized in {service_name.upper(), service_code.upper()}:
                return service_name, service_code
        return None

    @staticmethod
    def _apply_scene_service(
        meta: Meta, match: tuple[str, str] | None
    ) -> None:
        if match is None:
            return
        service_name, service_code = match
        meta.service = service_code
        meta.service_longname = service_name
        logger.debug(
            f"[green]Matched service: {service_code} ({service_name})[/green]"
        )

    async def _scene_nfo_source(self, nfo_file: str) -> str:
        logger.debug(f"[cyan]Parsing NFO file: {nfo_file}[/cyan]")
        async with aiofiles.open(
            nfo_file, encoding="utf-8", errors="ignore"
        ) as file:
            nfo_content = await file.read()
        source = self._nfo_source(nfo_content)
        if source:
            logger.debug(f"[cyan]Found source in NFO: {source}[/cyan]")
        return source

    async def parse_scene_nfo(self, meta: Meta) -> None:
        nfo_file = meta.scene_nfo_file
        if not isinstance(nfo_file, str) or not nfo_file.strip():
            logger.debug(
                "[yellow]No valid NFO file found for scene release[/yellow]"
            )
            return
        try:
            source = await self._scene_nfo_source(nfo_file)
            if not source:
                return
            services = cast(
                dict[str, str], await get_service(get_services_only=True)
            )
            self._apply_scene_service(
                meta, self._matched_service(source, services)
            )
        except Exception as error:
            logger.debug(f"[red]Error parsing NFO file: {error}[/red]")


_legacy_gather_prep = Prep.gather_prep


async def _validated_gather_prep(self: Prep, meta: Meta, mode: str) -> Meta:
    result = await _legacy_gather_prep(self, meta, mode)
    self._ensure_audiobook_cover(result)
    return result


Prep.gather_prep = _validated_gather_prep
