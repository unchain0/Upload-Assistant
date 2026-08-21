# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from pathlib import Path
from typing import Any, cast

from src.domain_models.release import Meta
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.common import Common
from src.integrations.trackers.UNIT3D import UNIT3D


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
    _DISC_TYPES: frozenset[str] = frozenset(
        {"BDMV", "DVD", "HDDVD_TS", "VIDEO_TS"}
    )
    _ARCHIVE_EXTENSIONS: frozenset[str] = frozenset(
        {
            ".rar",
            ".r00",
            ".r01",
            ".r02",
            ".r03",
            ".r04",
            ".r05",
            ".r06",
            ".r07",
            ".r08",
            ".r09",
        }
    )
    _EXTRA_FILE_EXTENSIONS: frozenset[str] = frozenset(
        {
            ".nfo",
            ".srt",
            ".sub",
            ".ssa",
            ".ass",
            ".vtt",
            ".idx",
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
            ".gif",
            ".bmp",
        }
    )
    _VIDEO_EXTENSIONS: frozenset[str] = frozenset(
        {
            ".mkv",
            ".mp4",
            ".avi",
            ".mov",
            ".m4v",
            ".mpg",
            ".mpeg",
            ".m2ts",
            ".ts",
            ".wmv",
            ".flv",
        }
    )
    _BOOTLEG_MARKERS: tuple[str, ...] = (
        "cam",
        "telesync",
        "tele-sync",
        "telecine",
        "tc",
        "r5",
    )
    _TV_ENDED_STATUSES: frozenset[str] = frozenset(
        {"ended", "canceled", "cancelled", "finished", "completed"}
    )
    _TV_ONGOING_STATUSES: frozenset[str] = frozenset(
        {"returning", "continuing", "in production", "upcoming", "ongoing"}
    )

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
    def _contains_extension(
        files: list[Any], extensions: set[str] | frozenset[str]
    ) -> str:
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
            if (
                "sample" in path.name
                and path.suffix.lower() in Luminarr._VIDEO_EXTENSIONS
            ):
                return path.name
        return ""

    @staticmethod
    def _collect_video_paths(files: list[Any]) -> list[Path]:
        return [
            Path(str(item))
            for item in files
            if Path(str(item)).suffix.lower() in Luminarr._VIDEO_EXTENSIONS
        ]

    @classmethod
    def _renamed_tagged_video_file(
        cls, video_paths: list[Path], tag: str | None
    ) -> str:
        group = cls._normalized_group(tag)
        if not group:
            return ""
        return next(
            (
                path.name
                for path in video_paths
                if cls._looks_renamed_for_group(path, group)
            ),
            "",
        )

    @staticmethod
    def _normalized_group(tag: str | None) -> str:
        return str(tag or "").lstrip("-").strip().casefold()

    @staticmethod
    def _looks_renamed_for_group(path: Path, group: str) -> bool:
        return any(
            char.isspace() for char in path.stem
        ) and path.stem.casefold().endswith(f"-{group}")

    @classmethod
    def _is_nested_relative_path(cls, path: Path) -> bool:
        normalized = cls._normalized_relative_path(path)
        return bool(normalized) and "/" in normalized

    @classmethod
    def _top_level_folder(cls, path: Path) -> str:
        normalized = cls._normalized_relative_path(path)
        if not normalized:
            return ""
        parts = [
            part
            for part in normalized.split("/")
            if part not in {"", ".", ".."}
        ]
        return parts[0] if len(parts) > 1 else ""

    @staticmethod
    def _normalized_relative_path(path: Path) -> str:
        if path.is_absolute():
            return ""
        normalized = str(path).replace("\\", "/")
        normalized = (
            normalized[2:] if normalized.startswith("./") else normalized
        )
        return "" if normalized.startswith("../") else normalized

    @staticmethod
    def _has_bootleg_marker(value: str) -> bool:
        value_without_tc_group = re.sub(
            r"-TC$", "", str(value).strip(), flags=re.IGNORECASE
        )
        normalized = Luminarr._normalize_path(value_without_tc_group)
        normalized = f" {normalized} "
        return any(
            re.search(
                rf"(?:^|[\s-]){re.escape(marker)}(?:$|[\s-])", normalized
            )
            for marker in Luminarr._BOOTLEG_MARKERS
        )

    @classmethod
    def _audio_codec(cls, track: dict[str, Any]) -> str:
        format_name = str(track.get("Format") or "").strip().upper()
        if format_name == "MPEG AUDIO":
            return cls._mpeg_audio_codec(track)
        return (
            "VORBIS"
            if format_name in {"VORBIS", "OGG VORBIS"}
            else format_name
        )

    @classmethod
    def _mpeg_audio_codec(cls, track: dict[str, Any]) -> str:
        profile = " ".join(
            str(track.get(key) or "")
            for key in ("Format_Profile", "CodecID", "CodecID_Hint")
        ).upper()
        if cls._contains_any(profile, ("LAYER 3", "MPEG/L3")):
            return "MP3"
        if cls._contains_any(profile, ("LAYER 2", "MPEG/L2")):
            return "MP2"
        return "MPEG AUDIO"

    @staticmethod
    def _contains_any(value: str, needles: tuple[str, ...]) -> bool:
        return any(needle in value for needle in needles)

    @staticmethod
    def _setting_value(settings: str, key: str) -> str:
        match = re.search(
            rf"(?:^|[\s/;,:]){re.escape(key)}\s*[:=]\s*([^\s/;,:]+)",
            settings,
            re.IGNORECASE,
        )
        return "" if match is None else match.group(1).strip().lower()

    @staticmethod
    def _is_multi_pass_abr(settings: str) -> bool:
        values = (
            Luminarr._setting_value(settings, key)
            for key in ("pass", "stats-read")
        )
        return any(value.isdigit() and int(value) >= 2 for value in values)

    def _invalid_encode_settings_reason(self, meta: Meta) -> str:
        return next(
            (
                reason
                for track in self._video_tracks(meta)
                if (reason := self._encode_settings_reason(track))
            ),
            "",
        )

    @classmethod
    def _video_tracks(cls, meta: Meta) -> list[dict[str, Any]]:
        return [
            track
            for track in cls._media_tracks(meta)
            if track.get("@type") == "Video"
        ]

    @classmethod
    def _media_tracks(cls, meta: Meta) -> list[dict[str, Any]]:
        media = cls._media_mapping(meta)
        tracks = media.get("track", [])
        if not isinstance(tracks, list):
            return []
        return [
            cast(dict[str, Any], track)
            for track in tracks
            if isinstance(track, dict)
        ]

    @staticmethod
    def _media_mapping(meta: Meta) -> dict[str, Any]:
        if not isinstance(meta.mediainfo, dict):
            return {}
        media = meta.mediainfo.get("media", {})
        return cast(dict[str, Any], media) if isinstance(media, dict) else {}

    @classmethod
    def _encode_settings_reason(cls, track: dict[str, Any]) -> str:
        settings = cls._settings_text(
            track.get("Encoded_Library_Settings", "")
        )
        if not settings:
            return ""
        rate_control = cls._setting_value(settings.casefold(), "rc")
        if rate_control == "abr" and not cls._is_multi_pass_abr(settings):
            return (
                "Single-pass ABR is not permitted. Use CRF or multi-pass ABR."
            )
        return ""

    @staticmethod
    def _settings_text(raw_settings: Any) -> str:
        if isinstance(raw_settings, dict):
            return " / ".join(
                f"{key!s}={value!s}" for key, value in raw_settings.items()
            ).strip()
        if raw_settings is None:
            return ""
        return str(raw_settings).strip()

    @staticmethod
    def _is_supplementary_audio(track: dict[str, Any]) -> bool:
        if track.get("is_commentary") is True:
            return True
        description = " ".join(
            str(track.get(key) or "")
            for key in ("Title", "title", "ServiceKind", "ServiceKind_String")
        ).casefold()
        return any(
            marker in description
            for marker in (
                "commentary",
                "audio description",
                "descriptive audio",
                "isolated score",
            )
        )

    @staticmethod
    def _allows_primary_mp2(meta: Meta) -> bool:
        release_type = str(meta.type or "").upper()
        source = str(meta.source or "").upper()
        return release_type in {"HDTV", "SDTV"} or (
            release_type == "REMUX" and "DVD" in source
        )

    def _invalid_audio_reason(self, meta: Meta) -> str:
        return next(
            (
                reason
                for track in self._audio_tracks(meta)
                if (reason := self._audio_track_reason(meta, track))
            ),
            "",
        )

    @classmethod
    def _audio_tracks(cls, meta: Meta) -> list[dict[str, Any]]:
        return [
            track
            for track in cls._media_tracks(meta)
            if track.get("@type") == "Audio"
        ]

    def _audio_track_reason(self, meta: Meta, track: dict[str, Any]) -> str:
        codec = self._audio_codec(track)
        if self._invalid_supplementary_codec(codec, track):
            return f"{codec} is permitted only for supplementary audio tracks (for example, commentary) under rule 6.2.5.3."
        if self._invalid_primary_mp2(meta, codec, track):
            return "MP2 primary audio is permitted only when untouched in HDTV/SDTV or DVD releases under rule 6.2.5.3."
        return ""

    def _invalid_supplementary_codec(
        self, codec: str, track: dict[str, Any]
    ) -> bool:
        return codec in {"MP3", "VORBIS"} and not self._is_supplementary_audio(
            track
        )

    def _invalid_primary_mp2(
        self, meta: Meta, codec: str, track: dict[str, Any]
    ) -> bool:
        if codec != "MP2" or self._is_supplementary_audio(track):
            return False
        return not self._allows_primary_mp2(meta)

    async def _confirm_or_skip(self, message: str, meta: Meta) -> bool:
        if meta.unattended:
            return bool(meta.unattended_confirm)
        logger.info(f"{self.tracker}: [yellow]{message}[/yellow]")
        return await self.common.prompt_user_for_confirmation(
            "Do you want to continue anyway?", meta
        )

    async def get_additional_data(self, meta: Meta) -> dict[str, Any]:
        return {
            "mod_queue_opt_in": await self.get_flag(meta, "modq"),
        }

    async def get_additional_checks(self, meta: Meta) -> bool:
        category = self._category_name(meta)
        filelist = self._validated_filelist(meta)
        if filelist is None:
            return False
        if not self._initial_policy_passes(meta, category, filelist):
            return False
        if not await self._async_policy_passes(meta):
            return False
        if not self._final_policy_passes(meta, category, filelist):
            return False
        return await self.common.check_and_confirm_adult_media_upload(
            meta, self.tracker
        )

    @staticmethod
    def _category_name(meta: Meta) -> str:
        return str(meta.category or "").upper()

    def _initial_policy_passes(
        self, meta: Meta, category: str, filelist: list[Any]
    ) -> bool:
        if not self._identity_policy_passes(meta, category):
            return False
        return self._file_layout_policy_passes(meta, filelist)

    async def _async_policy_passes(self, meta: Meta) -> bool:
        if not await self._language_policy_passes(meta):
            return False
        return await self._resolution_policy_passes(meta)

    def _final_policy_passes(
        self, meta: Meta, category: str, filelist: list[Any]
    ) -> bool:
        if not self._format_and_screen_policy_passes(meta, category):
            return False
        if not self._extra_files_policy_passes(meta, filelist):
            return False
        if not self._tv_policy_passes(meta, category, filelist):
            return False
        return self._disc_metadata_policy_passes(meta)

    def _format_and_screen_policy_passes(
        self, meta: Meta, category: str
    ) -> bool:
        return self._format_policy_passes(
            meta
        ) and self._screens_policy_passes(meta, category)

    def _identity_policy_passes(self, meta: Meta, category: str) -> bool:
        if not self._category_and_id_policy_passes(meta, category):
            return False
        return self._content_identity_policy_passes(meta)

    def _category_and_id_policy_passes(
        self, meta: Meta, category: str
    ) -> bool:
        if category not in {"TV", "MOVIE"}:
            logger.info(
                f"{self.tracker}: [bold red]Only MOVIE and TV uploads are supported on {self.tracker}.[/bold red]"
            )
            return False
        if not (meta.tmdb or meta.tmdb_id or meta.imdb_id):
            logger.info(
                f"{self.tracker}: [bold red]TMDB/IMDb entry is required on {self.tracker}.[/bold red]"
            )
            return False
        return True

    def _content_identity_policy_passes(self, meta: Meta) -> bool:
        if meta.adult_media:
            logger.info(
                f"{self.tracker}: [bold red]Pornography is not allowed.[/bold red]"
            )
            return False
        if self._has_bootleg_marker(str(meta.name or "")):
            logger.info(
                f"{self.tracker}: [bold red]Release markers indicate this is likely a bootleg/unauthorized source.[/bold red]"
            )
            return False
        return True

    def _validated_filelist(self, meta: Meta) -> list[Any] | None:
        raw_filelist = [] if meta.filelist is None else meta.filelist
        if not isinstance(raw_filelist, (list, tuple, set)):
            logger.info(
                f"{self.tracker}: [bold red]File list metadata is invalid.[/bold red]"
            )
            return None
        return [item for item in raw_filelist if self._is_path_like_file(item)]

    def _file_layout_policy_passes(
        self, meta: Meta, filelist: list[Any]
    ) -> bool:
        video_paths = self._collect_video_paths(filelist)
        if meta.is_disc or not video_paths:
            return True
        reason = self._file_layout_failure(meta, video_paths)
        if not reason:
            return True
        logger.info(f"{self.tracker}: [bold red]{reason}[/bold red]")
        return False

    def _file_layout_failure(self, meta: Meta, video_paths: list[Path]) -> str:
        renamed = self._renamed_tagged_video_file(video_paths, meta.tag)
        if renamed:
            return f"Tagged release file appears to have been renamed with spaces: {renamed}. Restore the original filename before uploading."
        if len(video_paths) == 1 and self._is_nested_relative_path(
            video_paths[0]
        ):
            return "Single-file Movie/TV uploads must not be inside a folder."
        if len(video_paths) > 1:
            return self._multi_file_layout_failure(meta, video_paths)
        return ""

    def _multi_file_layout_failure(
        self, meta: Meta, video_paths: list[Path]
    ) -> str:
        top_folders = [self._top_level_folder(path) for path in video_paths]
        folder_reason = self._top_folder_failure(top_folders)
        if folder_reason:
            return folder_reason
        return (
            ""
            if meta.tv_pack
            else "Movie and non-collection TV uploads should contain one video file per title."
        )

    @staticmethod
    def _top_folder_failure(top_folders: list[str]) -> str:
        if any(folder == "" for folder in top_folders):
            return "For multi-file Movie/TV uploads, all files must be placed in one top-level folder."
        if len(set(top_folders)) != 1:
            return "Multi-file uploads must use a single top-level folder."
        return ""

    async def _language_policy_passes(self, meta: Meta) -> bool:
        if meta.is_disc in self._DISC_TYPES:
            return True
        if not await self.common.check_language_requirements(
            meta,
            self.tracker,
            languages_to_check=["english"],
            check_audio=True,
            check_subtitle=True,
            prompt_on_failure=False,
        ):
            return False
        return await self._original_language_policy_passes(meta)

    async def _original_language_policy_passes(self, meta: Meta) -> bool:
        original_language = str(meta.original_language or "").strip()
        if not original_language:
            return True
        return await self.common.check_language_requirements(
            meta,
            self.tracker,
            languages_to_check=[original_language],
            check_audio=True,
            original_language=True,
            original_required=True,
            prompt_on_failure=False,
        )

    async def _resolution_policy_passes(self, meta: Meta) -> bool:
        if meta.is_disc in self._DISC_TYPES or meta.resolution in {
            "8640p",
            "4320p",
            "2160p",
            "1440p",
            "1080p",
            "1080i",
            "720p",
        }:
            return True
        if meta.unattended and not meta.unattended_confirm:
            return False
        logger.info(
            f"{self.tracker}: [bold red]only allows SD releases when the content does not have a higher resolution release.[/bold red]"
        )
        return await self._confirm_or_skip("low resolution upload", meta)

    def _format_policy_passes(self, meta: Meta) -> bool:
        if not meta.is_disc and meta.container != "mkv":
            logger.info(
                f"{self.tracker}: [bold red]only allows MKV containers for non-disc uploads.[/bold red]"
            )
            return False
        if meta.is_disc in self._DISC_TYPES:
            return True
        return self._codec_and_encode_policy_passes(meta)

    def _codec_and_encode_policy_passes(self, meta: Meta) -> bool:
        audio_reason = self._invalid_audio_reason(meta)
        if audio_reason:
            logger.info(
                f"{self.tracker}: [bold red]{audio_reason} Skipping upload.[/bold red]"
            )
            return False
        encode_reason = self._invalid_encode_settings_reason(meta)
        if encode_reason:
            logger.info(
                f"{self.tracker}: [bold red]{encode_reason} Skipping upload.[/bold red]"
            )
            return False
        return True

    def _screens_policy_passes(self, meta: Meta, category: str) -> bool:
        screens = self._screen_count(meta.screens)
        if category in {"TV", "MOVIE"} and screens < 3:
            logger.info(
                f"{self.tracker}: [bold red]{self.tracker} requires at least 3 screenshots in the description for Movie/TV uploads.[/bold red]"
            )
            return False
        return True

    @staticmethod
    def _screen_count(value: Any) -> int:
        try:
            return int(value)
        except TypeError, ValueError, OverflowError:
            return 0

    def _extra_files_policy_passes(
        self, meta: Meta, filelist: list[Any]
    ) -> bool:
        if meta.is_disc:
            return True
        archive = self._contains_extension(filelist, self._ARCHIVE_EXTENSIONS)
        if archive:
            logger.info(
                f"{self.tracker}: [bold red]Archive or multipart files are not allowed for {self.tracker}. Found: {archive}[/bold red]"
            )
            return False
        extra_file = self._contains_extension(
            filelist, self._EXTRA_FILE_EXTENSIONS
        ) or self._contains_sample_file(filelist)
        if extra_file:
            logger.info(
                f"{self.tracker}: [bold red]Extra file '{extra_file}' is not allowed for {self.tracker}.[/bold red]"
            )
            return False
        return True

    def _tv_policy_passes(
        self, meta: Meta, category: str, filelist: list[Any]
    ) -> bool:
        if category != "TV":
            return True
        seasons = self.common.extract_tv_seasons(filelist)
        if len(seasons) > 1:
            logger.info(
                f"{self.tracker}: [bold red]TV uploads must target a single season or a single episode on {self.tracker}.[/bold red]"
            )
            return False
        return self._tv_pack_and_episode_policy_passes(meta, filelist)

    def _tv_pack_and_episode_policy_passes(
        self, meta: Meta, filelist: list[Any]
    ) -> bool:
        tv_pack_allowed = self.common.is_tv_series_ended(
            meta, self._TV_ENDED_STATUSES, self._TV_ONGOING_STATUSES
        )
        if meta.tv_pack and not tv_pack_allowed:
            logger.info(
                f"{self.tracker}: [bold red]TV season packs are restricted to ended shows on {self.tracker}.[/bold red]"
            )
            return False
        if not meta.tv_pack and self.common.count_tv_episodes(filelist) > 1:
            logger.info(
                f"{self.tracker}: [bold red]Non-pack TV uploads must be a single episode on {self.tracker}.[/bold red]"
            )
            return False
        return True

    def _disc_metadata_policy_passes(self, meta: Meta) -> bool:
        if meta.is_disc == "BDMV" and not meta.bdinfo:
            logger.info(
                f"{self.tracker}: [bold red]Full Blu-ray disc uploads require BDInfo information on {self.tracker}.[/bold red]"
            )
            return False
        if meta.is_disc not in self._DISC_TYPES and not meta.valid_mi_settings:
            logger.info(
                f"{self.tracker}: [bold red]No encoding settings in mediainfo, skipping {self.tracker} upload.[/bold red]"
            )
            return False
        return True
