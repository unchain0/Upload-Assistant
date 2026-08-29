# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from contextlib import suppress
from pathlib import Path
from typing import Any, ClassVar, cast

import httpx

from src.domain_models.music import MusicRelease
from src.domain_models.release import Meta
from src.integrations.external_apis.tmdb import TmdbManager
from src.integrations.mapping.value_coercion import to_int
from src.integrations.media.book_extractors import validate_isbn_checksum
from src.integrations.media.language_adapter import languages_manager
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.description_builder import DescriptionBuilder
from src.integrations.trackers.music_validation import (
    MusicValidator,
    ValidationLevel,
)
from src.integrations.trackers.UNIT3D import UNIT3D


class DarkPeers(UNIT3D):
    """
    Darkpeers is a Private Torrent Tracker for MOVIES / TV / GENERAL
    """

    tracker = "DARKPEERS"
    display_name = "DarkPeers"
    allows_bloated_audio = True
    prefers_repack = True
    reject_episode_if_season_pack_exists = True
    _AUDIO_TRACK_PATTERN = re.compile(
        r"^(?:\d{1,3}(?:-\d{1,2})?\.\s+.+|\d{1,3}(?:-\d{1,2})?\s+-\s+.+|\d{1,3}(?:-\d{1,2})?-(?!-).+|.+-\d{1,3}(?:-\d{1,2})?-(?!-).+)$"
    )
    _AUDIO_CODEC_PATTERN = re.compile(
        r"\s+(?:DTS Headphone:X|DTS-HD MA|DTS-HD HRA|DTS-ES|DTS:X|TrueHD|DD\+ EX|DD EX|DD\+|DD|LPCM|FLAC|ALAC|AAC|Opus|MP3|MP2|Vorbis)(?=\s|$)"
    )
    _DUB_ELEMENT_PATTERN = re.compile(
        r"\s+(?:(?:[A-Z][A-Za-z]*(?:\s+[A-Z][A-Za-z]*){0,2}\s+)?(?:MULTi|Dubbed)|Dual-Audio)"
        r"(?=\s+(?:DTS Headphone:X|DTS-HD MA|DTS-HD HRA|DTS-ES|DTS:X|TrueHD|DD\+ EX|DD EX|DD\+|DD|LPCM|FLAC|ALAC|AAC|Opus|MP3|MP2|Vorbis)(?:\s|$)|-[A-Za-z0-9]+$)"
    )
    base_url = "https://darkpeers.org"
    banned_groups = (
        "ARCADE",
        "aXXo",
        "BANDOLEROS",
        "BONE",
        "BRrip",
        "CM8",
        "CrEwSaDe",
        "CTFOH",
        "dAV1nci",
        "DNL",
        "eranger2",
        "FaNGDiNG0",
        "FGT",
        "FiSTER",
        "flower",
        "GalaxyTV",
        "Goki",
        "H4XO",
        "HD2DVD",
        "HDTime",
        "HorribleSubs",
        "iHYTECH",
        "ION10",
        "iPlanet",
        "KiNGDOM",
        "LAMA",
        "MeGusta",
        "mHD",
        "mSD",
        "NaNi",
        "NhaNc3",
        "nHD",
        "nikt0",
        "nSD",
        "OFT",
        "PiTBULL",
        "PRODJi",
        "PSA",
        "RARBG",
        "Rifftrax",
        "ROCKETRACCOON",
        "SANTi",
        "SARTRE",
        "SasukeducK",
        "SEEDSTER",
        "ShAaNiG",
        "Sicario",
        "STUTTERSHIT",
        "Subsplease",
        "SyncUp",
        "TAoE",
        "TGALAXY",
        "TGx",
        "TORRENTGALAXY",
        "ToVaR",
        "Trix",
        "TSP",
        "TSPxL",
        "ViSION",
        "VXT",
        "WAF",
        "WKS",
        "X0r",
        "YIFY",
        "YTS",
    )
    id_url = f"{base_url}/api/torrents/"
    upload_url = f"{base_url}/api/torrents/upload"
    requests_url = f"{base_url}/api/requests/filter"
    search_url = f"{base_url}/api/torrents/filter"
    torrent_url = f"{base_url}/torrents/"
    supported_categories = ("TV", "MOVIE", "BOOK", "GAME", "MUSIC")
    tracker_urls = ("https://darkpeers.org",)

    def __init__(self, config: dict[str, Any]):
        super().__init__(config, tracker_name="DARKPEERS")
        self.config = config
        self.tmdb_manager = TmdbManager(config)

    async def get_description(self, meta: Meta) -> dict[str, str]:
        audio_spectrogram = str(meta.category or "").strip().upper() == "MUSIC"
        description = await DescriptionBuilder(
            self.tracker, self.config
        ).general_description_generator(
            meta,
            audio_spectrogram=audio_spectrogram,
            mediainfo=False,
            nfo=False,
        )
        return {"description": description}

    @staticmethod
    def _category(meta: Meta) -> str:
        return str(meta.category or "").strip().upper()

    @staticmethod
    def _release_type(meta: Meta) -> str:
        return str(meta.type or "").strip().upper()

    @staticmethod
    def _group(meta: Meta) -> str:
        return str(meta.tag or "").lstrip("-").strip().upper()

    def _valid_filelist(self, meta: Meta) -> bool:
        filelist = [] if meta.filelist is None else meta.filelist
        if isinstance(filelist, (list, tuple, set)):
            return True
        logger.info(
            f"{self.tracker}: [bold red]File list metadata is invalid. "
            "Skipping upload.[/bold red]"
        )
        return False

    def _valid_generated_video_name(self, meta: Meta) -> bool:
        if not self._is_local_path_name(str(meta.name or "")):
            return True
        logger.info(
            f"{self.tracker}: [bold red]Generated upload title contains a "
            "local file path. Skipping upload.[/bold red]"
        )
        return False

    @staticmethod
    def _has_video_payload(meta: Meta) -> bool:
        return any(str(value).strip() for value in (meta.filelist or []))

    async def _video_payload_checks(self, meta: Meta) -> bool:
        if not await self.validate_video_quality(meta):
            return False
        if meta.is_disc:
            return True
        return self.validate_video_files(meta) and self.validate_video_content(
            meta
        )

    async def _video_folder_check(self, meta: Meta, category: str) -> bool:
        needs_confirmation = bool(
            meta.keep_folder
            and (category == "MOVIE" or not self._is_single_tv_season(meta))
        )
        if not needs_confirmation:
            return True
        return await self._confirm_or_skip(
            "does not allow an individual video file in an unnecessary folder.",
            meta,
        )

    async def _video_identity_checks(self, meta: Meta) -> bool:
        if not self._valid_generated_video_name(meta):
            return False
        if not await self.validate_video_languages(meta):
            return False
        return await self.validate_video_resolution(meta)

    async def _video_payload_if_required(
        self, meta: Meta, category: str
    ) -> bool:
        required = self._has_video_payload(meta) or category == "MOVIE"
        if not required:
            return True
        return await self._video_payload_checks(meta)

    async def _video_category_checks(self, meta: Meta, category: str) -> bool:
        if not await self._video_identity_checks(meta):
            return False
        if not await self._video_payload_if_required(meta, category):
            return False
        if not self.validate_video_screenshots(meta):
            return False
        return await self._video_folder_check(meta, category)

    async def _video_tv_checks(self, meta: Meta, category: str) -> bool:
        if not await self._video_category_checks(meta, category):
            return False
        return self.validate_tv_scope(meta) if category == "TV" else True

    async def _category_checks(self, meta: Meta, category: str) -> bool:
        if category in {"MOVIE", "TV"}:
            return await self._video_tv_checks(meta, category)
        if category == "BOOK":
            return await self.validate_book(meta)
        if category == "MUSIC":
            return self.validate_music(meta)
        if category == "GAME":
            return await self.validate_game(meta)
        return True

    def _group_type_allowed(self, group: str, release_type: str) -> bool:
        if group == "EVO" and release_type != "WEBDL":
            logger.info(
                f"{self.tracker}: [bold red]only allows EVO releases when "
                "they are WEB-DLs. Skipping upload."
            )
            return False
        if group == "HDT" and release_type != "REMUX":
            logger.info(
                f"{self.tracker}: [bold red]only allows HDT releases when "
                "they are Remuxes. Skipping upload."
            )
            return False
        return True

    def _hardcoded_subtitles_allowed(self, meta: Meta, category: str) -> bool:
        if category not in {"MOVIE", "TV"} or not meta.hardcoded_subs:
            return True
        logger.info(
            f"{self.tracker}: [bold red]does not allow Movies or TV releases "
            "with hardcoded subtitles. Skipping upload."
        )
        return False

    async def get_additional_checks(self, meta: Meta) -> bool:
        if not self._valid_filelist(meta):
            return False
        category = self._category(meta)
        if not await self._category_checks(meta, category):
            return False
        if not self._group_type_allowed(
            self._group(meta), self._release_type(meta)
        ):
            return False
        return self._hardcoded_subtitles_allowed(meta, category)

    _NORDIC_LANGUAGES: ClassVar[set[str]] = {
        "danish",
        "finnish",
        "icelandic",
        "norwegian bokmal",
        "norwegian nynorsk",
        "norwegian",
        "swedish",
    }
    _LANGUAGE_ALIASES: ClassVar[dict[str, str]] = {
        "da": "danish",
        "dan": "danish",
        "de": "german",
        "deu": "german",
        "en": "english",
        "eng": "english",
        "es": "spanish",
        "fi": "finnish",
        "fin": "finnish",
        "fr": "french",
        "fra": "french",
        "fre": "french",
        "ger": "german",
        "ice": "icelandic",
        "is": "icelandic",
        "isl": "icelandic",
        "ja": "japanese",
        "jpn": "japanese",
        "no": "norwegian",
        "nor": "norwegian",
        "por": "portuguese",
        "pt": "portuguese",
        "spa": "spanish",
        "sv": "swedish",
        "swe": "swedish",
    }
    _BOOK_FORMATS: ClassVar[set[str]] = {
        "AZW3",
        "CBR",
        "CBZ",
        "CHM",
        "DJVU",
        "DOC",
        "DOCX",
        "EPUB",
        "FB2",
        "HTML",
        "KFX",
        "LIT",
        "MOBI",
        "PDB",
        "PDF",
        "RTF",
        "TXT",
    }
    _AUDIOBOOK_FORMATS: ClassVar[set[str]] = {
        "AAC",
        "ALAC",
        "FLAC",
        "M4B",
        "MP3",
        "OPUS",
        "PCM",
        "VORBIS",
    }
    _VIDEO_EXTENSIONS: ClassVar[set[str]] = {
        ".3gp",
        ".avi",
        ".flv",
        ".m2ts",
        ".m4v",
        ".mkv",
        ".mov",
        ".mp4",
        ".mpeg",
        ".mpg",
        ".ts",
        ".vob",
        ".webm",
        ".wmv",
    }
    _WEB_DL_MIN_VIDEO_BITRATE_KBPS: ClassVar[dict[str, int]] = {
        "4320p": 35_000,
        "2160p": 18_000,
        "1080p": 2_500,
        "1080i": 2_500,
        "720p": 1_800,
    }
    _WEB_DL_MIN_AUDIO_BITRATE_KBPS: ClassVar[dict[str, int]] = {
        "default": 128,
        "4320p": 192,
        "2160p": 192,
        "1080p": 128,
        "720p": 96,
    }

    def _to_int(self, value: Any) -> int | None:
        try:
            return int(value)
        except TypeError, ValueError:
            return None

    def _config_bitrate_entry(
        self, key_name: object, raw_value: object
    ) -> tuple[str, int] | None:
        if not isinstance(key_name, str):
            return None
        parsed = self._to_int(raw_value)
        if parsed is None or parsed < 0:
            return None
        return key_name.lower(), parsed

    def _config_min_bitrate(self, key: str) -> dict[str, int]:
        raw = self.tracker_config.get(key, {})
        if not isinstance(raw, dict):
            return {}
        values: dict[str, int] = {}
        for key_name, raw_value in raw.items():
            entry = self._config_bitrate_entry(key_name, raw_value)
            if entry is not None:
                values[entry[0]] = entry[1]
        return values

    @classmethod
    def _base_bitrate_table(cls, bitrate_type: str) -> dict[str, int]:
        return (
            cls._WEB_DL_MIN_VIDEO_BITRATE_KBPS
            if bitrate_type == "video"
            else cls._WEB_DL_MIN_AUDIO_BITRATE_KBPS
        )

    @staticmethod
    def _bitrate_from_table(
        table: dict[str, int], resolution: str
    ) -> int | None:
        return table.get(resolution)

    def _min_webl_bitrate(
        self, bitrate_type: str, resolution: str
    ) -> int | None:
        resolution_key = str(resolution or "").lower()
        base = self._base_bitrate_table(bitrate_type)
        overrides = self._config_min_bitrate(f"webl_min_{bitrate_type}_kbps")
        override = self._bitrate_from_table(overrides, resolution_key)
        if override is not None:
            return override
        base_value = self._bitrate_from_table(base, resolution_key)
        if base_value is not None:
            return base_value
        return base.get("default") if bitrate_type == "audio" else None

    def _video_bitrate_allowed(self, meta: Meta, minimum: int | None) -> bool:
        if minimum is None:
            return True
        bitrate = self._to_int(meta.video_bitrate)
        if bitrate is None:
            logger.info(
                f"{self.tracker}: [bold red]Could not determine video "
                "bitrate for this WEBDL upload."
            )
            return False
        if bitrate >= minimum:
            return True
        logger.info(
            f"{self.tracker}: [bold red]Video bitrate too low for DARKPEERS "
            f"WEBDL ({bitrate} < {minimum} kbps). Skipping upload."
        )
        return False

    def _audio_bitrate_allowed(self, meta: Meta, minimum: int | None) -> bool:
        bitrate = self._to_int(meta.audio_bitrate)
        if minimum is None or bitrate is None or bitrate >= minimum:
            return True
        logger.info(
            f"{self.tracker}: [bold red]Audio bitrate too low for DARKPEERS "
            f"WEBDL ({bitrate} < {minimum} kbps). Skipping upload."
        )
        return False

    async def validate_video_quality(self, meta: Meta) -> bool:
        if str(meta.type or "").upper() != "WEBDL":
            return True
        resolution = str(meta.resolution or "").lower()
        min_video = self._min_webl_bitrate("video", resolution)
        min_audio = self._min_webl_bitrate("audio", resolution)
        if not self._video_bitrate_allowed(meta, min_video):
            return False
        return self._audio_bitrate_allowed(meta, min_audio)

    @classmethod
    def _normalise_language(cls, value: Any) -> str:
        language = re.sub(r"\s+", " ", str(value or "").strip().casefold())
        language = re.sub(r"\s*\([^)]*\)", "", language).strip()
        language = language.split("-", maxsplit=1)[0]
        language = language.replace("_", "-").split("-", maxsplit=1)[0].strip()
        return cls._LANGUAGE_ALIASES.get(language, language)

    @classmethod
    def _languages(cls, value: list[str] | str | None) -> set[str]:
        values = [value] if isinstance(value, str) else (value or [])
        return {
            norm for item in values if (norm := cls._normalise_language(item))
        }

    @classmethod
    def _accepted_languages(cls) -> set[str]:
        return {"english", *cls._NORDIC_LANGUAGES}

    @classmethod
    def _video_language_policy_valid(
        cls,
        audio: set[str],
        subtitles: set[str],
        original: str,
    ) -> bool:
        accepted = cls._accepted_languages()
        if audio & accepted:
            return True
        return bool(original and original in audio and subtitles & accepted)

    async def validate_video_languages(self, meta: Meta) -> bool:
        """Apply DP's audio/original-audio-and-subtitles rule."""
        if not meta.language_checked:
            await languages_manager.process_desc_language(
                meta, tracker=self.tracker
            )
        valid = self._video_language_policy_valid(
            self._languages(meta.audio_languages),
            self._languages(meta.subtitle_languages),
            self._normalise_language(meta.original_language),
        )
        if not valid:
            logger.info(
                f"{self.tracker}: [bold red]requires English/Nordic audio, "
                "or original audio with English/Nordic subtitles. Skipping upload."
            )
        return valid

    async def validate_video_resolution(self, meta: Meta) -> bool:
        resolution = str(meta.resolution or "")
        allowed = {
            "480i",
            "480p",
            "576i",
            "576p",
            "720p",
            "1080i",
            "1080p",
            "2160p",
            "4320p",
        }
        if resolution in allowed:
            return True
        if resolution == "360p":
            return await self._confirm_or_skip(
                "only permits 360p when no official higher-resolution release exists.",
                meta,
            )
        logger.info(
            f"{self.tracker}: [bold red]does not support {resolution or 'an unknown'} video resolution. Skipping upload."
        )
        return False

    @staticmethod
    def _archive_filename(filelist: list[object]) -> str:
        for item in filelist:
            path = Path(str(item))
            if path.suffix.lower() in {".rar", ".zip", ".7z"}:
                return path.name
        return ""

    @classmethod
    def _renamed_tagged_video_path(cls, path: Path, group: str) -> bool:
        if path.suffix.casefold() not in cls._VIDEO_EXTENSIONS:
            return False
        if not any(char.isspace() for char in path.stem):
            return False
        return path.stem.casefold().endswith(f"-{group}")

    @classmethod
    def _renamed_tagged_video(cls, filelist: list[object], group: str) -> str:
        if not group:
            return ""
        for item in filelist:
            path = Path(str(item))
            if cls._renamed_tagged_video_path(path, group):
                return path.name
        return ""

    def _video_file_items(self, meta: Meta) -> list[object] | None:
        filelist = [] if meta.filelist is None else meta.filelist
        if isinstance(filelist, (list, tuple, set)):
            return list(filelist)
        logger.info(
            f"{self.tracker}: [bold red]File list metadata is invalid. "
            "Skipping upload.[/bold red]"
        )
        return None

    def _archive_upload_allowed(self, items: list[object]) -> bool:
        archive = self._archive_filename(items)
        if not archive:
            return True
        logger.info(
            f"{self.tracker}: [bold red]does not permit archives in Movie/TV "
            f"uploads: {archive}. Skipping upload."
        )
        return False

    def _renamed_video_allowed(self, meta: Meta, items: list[object]) -> bool:
        group = str(meta.tag or "").lstrip("-").strip().casefold()
        renamed = self._renamed_tagged_video(items, group)
        if not renamed:
            return True
        logger.info(
            f"{self.tracker}: [bold red]Tagged release file appears to have "
            f"been renamed with spaces: {renamed}. Restore the original "
            "filename before uploading.[/bold red]"
        )
        return False

    def validate_video_files(self, meta: Meta) -> bool:
        items = self._video_file_items(meta)
        if items is None:
            return False
        if not self._archive_upload_allowed(items):
            return False
        return self._renamed_video_allowed(meta, items)

    @staticmethod
    def _is_local_path_name(value: str) -> bool:
        return bool(value) and (
            value.startswith(("/", "\\"))
            or Path(value).is_absolute()
            or bool(re.match(r"^[A-Za-z]:[\\/]", value))
        )

    @classmethod
    def _classify_video_paths(
        cls, paths: list[Path]
    ) -> tuple[list[str], list[str]]:
        videos: list[str] = []
        invalid: list[str] = []
        for path in paths:
            suffix = path.suffix.lower()
            if suffix in cls._VIDEO_EXTENSIONS:
                videos.append(path.name)
            else:
                invalid.append(path.name or str(path))
        return videos, invalid

    @staticmethod
    def _video_content_paths(meta: Meta) -> list[Path]:
        return [
            Path(str(item))
            for item in (meta.filelist or [])
            if str(item).strip()
        ]

    def validate_video_content(self, meta: Meta) -> bool:
        paths = self._video_content_paths(meta)
        if not paths:
            logger.info(
                f"{self.tracker}: [bold red]Movie/TV uploads require a "
                "payload of files. Skipping upload."
            )
            return False
        videos, invalid = self._classify_video_paths(paths)
        if not videos:
            logger.info(
                f"{self.tracker}: [bold red]Movie/TV uploads did not include "
                "a recognized video file extension. Skipping upload."
            )
            return False
        if not invalid:
            return True
        logger.info(
            f"{self.tracker}: [bold red]Movie/TV uploads should include video "
            f"files only. Remove non-video files: {', '.join(invalid)}. "
            "Skipping upload."
        )
        return False

    def validate_video_screenshots(self, meta: Meta) -> bool:
        screenshot_count = to_int(meta.screens, 0)
        if screenshot_count < 3:
            logger.info(
                f"{self.tracker}: [bold red]requires at least 3 screenshots for Movie/TV uploads. Skipping upload."
            )
            return False
        if screenshot_count > 5:
            logger.info(
                f"{self.tracker}: [bold red]supports at most 5 screenshots for Movie/TV uploads. Skipping upload."
            )
            return False
        return True

    @staticmethod
    def _tv_scope_name(meta: Meta) -> str:
        return " ".join(
            (str(meta.name or ""), Path(str(meta.path or "")).name)
        ).casefold()

    @staticmethod
    def _tv_scope_is_complete_series(name: str) -> bool:
        return bool(
            re.search(
                r"\b(?:complete[ ._-]*series|all[ ._-]*seasons?|"
                r"seasons?[ ._-]*\d+[ ._-]*(?:-|to)[ ._-]*\d+|"
                r"s\d{1,2}[ ._-]*-[ ._-]*s?\d{1,2})\b",
                name,
            )
        )

    @staticmethod
    def _season_numbers(meta: Meta) -> set[str]:
        seasons: set[str] = set()
        for item in meta.filelist or []:
            seasons.update(
                re.findall(
                    r"\bS(\d{1,2})(?:E\d{1,3})?\b",
                    Path(str(item)).name,
                    re.IGNORECASE,
                )
            )
        return {season.casefold() for season in seasons}

    def validate_tv_scope(self, meta: Meta) -> bool:
        if self._tv_scope_is_complete_series(self._tv_scope_name(meta)):
            logger.info(
                f"{self.tracker}: [bold red]only individual seasons or "
                "episodes are allowed. Skipping multi-season/complete-series "
                "upload."
            )
            return False
        if len(self._season_numbers(meta)) <= 1:
            return True
        logger.info(
            f"{self.tracker}: [bold red]torrent contains files from multiple "
            "seasons. Skipping upload."
        )
        return False

    def _is_single_tv_season(self, meta: Meta) -> bool:
        if meta.episode:
            return False
        return len(self._season_numbers(meta)) == 1 or bool(meta.season)

    @staticmethod
    def _book_author_value(meta: Meta) -> str:
        value = meta.author if meta.author else meta.book_author
        return str(value or "").strip()

    @staticmethod
    def _book_title_value(meta: Meta) -> str:
        value = meta.title if meta.title else meta.book_title
        return str(value or "").strip()

    @classmethod
    def _missing_book_core_field(cls, meta: Meta) -> str:
        if not cls._book_author_value(meta):
            return "author"
        if not cls._book_title_value(meta):
            return "title"
        return "release year" if not meta.year else ""

    async def _book_required_metadata(self, meta: Meta) -> bool:
        field = self._missing_book_core_field(meta)
        return True if not field else await self._missing_required(field, meta)

    def _book_format_allowed(self, meta: Meta, format_name: str) -> bool:
        allowed = (
            self._AUDIOBOOK_FORMATS if meta.audiobook else self._BOOK_FORMATS
        )
        if format_name in allowed:
            return True
        logger.info(
            f"{self.tracker}: [bold red]does not support "
            f"{format_name or 'an unspecified'} book format. Skipping upload."
        )
        return False

    @staticmethod
    def _validated_book_isbn(meta: Meta) -> str | None:
        return validate_isbn_checksum(str(meta.isbn or meta.book_isbn or ""))

    def _audiobook_isbn_allowed(
        self, meta: Meta, collection: bool, validated: str | None
    ) -> bool:
        if validated or collection:
            meta.isbn = validated or ""
            return True
        logger.info(
            f"{self.tracker}: [bold red]Audiobooks require a valid ISBN-10 "
            "or ISBN-13. Re-run with --isbn. Skipping upload.[/bold red]"
        )
        return False

    def _ebook_isbn_allowed(
        self, meta: Meta, collection: bool, validated: str | None
    ) -> bool:
        if collection:
            return True
        if validated:
            meta.isbn = validated
            return True
        logger.info(
            f"{self.tracker}: [bold red]Individual eBooks require a valid "
            "ISBN-10 or ISBN-13 in the upload title. Re-run with --isbn. "
            "Skipping upload.[/bold red]"
        )
        return False

    def _set_validated_book_isbn(self, meta: Meta, collection: bool) -> bool:
        validated = self._validated_book_isbn(meta)
        if meta.audiobook:
            return self._audiobook_isbn_allowed(meta, collection, validated)
        return self._ebook_isbn_allowed(meta, collection, validated)

    async def _book_identifier_allowed(
        self, meta: Meta, collection: bool
    ) -> bool:
        if self._book_identifier(meta) or collection:
            return True
        return await self._missing_required("a valid ISBN", meta)

    async def _book_publisher_allowed(self, meta: Meta) -> bool:
        publisher = str(meta.publisher or meta.book_publisher or "").strip()
        if publisher:
            return True
        return await self._missing_required("publisher", meta)

    @staticmethod
    def _missing_audiobook_field(meta: Meta) -> str:
        if not str(meta.narrator or "").strip():
            return "audiobook narrator"
        return "audiobook runtime" if not meta.audiobook_duration else ""

    async def _lossy_audiobook_bitrate_allowed(self, meta: Meta) -> bool:
        if not meta.audiobook_bitrate:
            return await self._missing_required(
                "lossy audiobook bitrate", meta
            )
        try:
            bitrate = int(meta.audiobook_bitrate)
        except TypeError, ValueError:
            bitrate = 0
        if bitrate >= 64:
            return True
        logger.info(
            f"{self.tracker}: [bold red]Speech-only audiobooks require a "
            "bitrate of at least 64 kbps. Skipping upload.[/bold red]"
        )
        return False

    async def _audiobook_required_metadata(
        self, meta: Meta, format_name: str
    ) -> bool:
        if not meta.audiobook:
            return True
        field = self._missing_audiobook_field(meta)
        if field:
            return await self._missing_required(field, meta)
        if format_name not in {"MP3", "AAC", "OPUS", "VORBIS"}:
            return True
        return await self._lossy_audiobook_bitrate_allowed(meta)

    @staticmethod
    def _audiobook_details(meta: Meta) -> str:
        publisher = str(meta.publisher or meta.book_publisher or "").strip()
        identifier = DarkPeers._book_identifier(meta)
        runtime = meta.audiobook_duration_formatted or meta.audiobook_duration
        return (
            f"Narrator: {meta.narrator}; Runtime: {runtime}; "
            f"Publisher: {publisher}; Year: {meta.year}; ISBN: {identifier}"
        )

    async def _audiobook_edition_allowed(self, meta: Meta) -> bool:
        if not meta.audiobook:
            return True
        details = self._audiobook_details(meta)
        if meta.unattended:
            logger.info(
                f"{self.tracker}: [bold red]Audiobook edition metadata cannot "
                "be verified safely in unattended mode. Run attended and "
                "verify that narrator/runtime match publisher, year, and ISBN. "
                f"{details}[/bold red]"
            )
            return False
        logger.info(
            f"{self.tracker}: [yellow]Verify that all audiobook edition fields "
            f"describe the same recording. {details}[/yellow]"
        )
        return await self.common.prompt_user_for_confirmation(
            "Do these audiobook edition details match the files?", meta
        )

    async def _ebook_page_count_allowed(
        self, meta: Meta, format_name: str
    ) -> bool:
        if meta.audiobook or format_name != "PDF":
            return True
        page_count = meta.get("page_count", None) or meta.get(
            "book_page_count", None
        )
        if page_count:
            return True
        return await self._missing_required("PDF page count", meta)

    @staticmethod
    def _ebook_source(meta: Meta) -> str:
        return str(meta.manual_source or meta.source or "").strip().upper()

    def _ebook_source_allowed(self, meta: Meta) -> bool:
        if meta.audiobook:
            return True
        source = self._ebook_source(meta)
        if source not in {"RETAIL", "SCAN", "OTHER"}:
            logger.info(
                f"{self.tracker}: [bold red]eBook provenance must be explicit. "
                "Re-run with --source RETAIL for an untouched digital retail "
                "file, --source SCAN (and --ocr when applicable), or --source "
                "OTHER for a verified non-retail born-digital file. Generic "
                "WEB metadata is not proof of a retail release. Skipping "
                "upload.[/bold red]"
            )
            return False
        if not meta.ocr or source == "SCAN":
            return True
        logger.info(
            f"{self.tracker}: [bold red]OCR cannot be combined with Retail. "
            "Use --source SCAN --ocr. Skipping upload.[/bold red]"
        )
        return False

    async def _book_identity_phase(
        self, meta: Meta
    ) -> tuple[str, bool] | None:
        if not await self._book_required_metadata(meta):
            return None
        format_name = self._book_format(meta)
        if not self._book_format_allowed(meta, format_name):
            return None
        collection = self._is_book_collection(meta)
        if not self._set_validated_book_isbn(meta, collection):
            return None
        return format_name, collection

    async def _book_metadata_phase(
        self, meta: Meta, format_name: str, collection: bool
    ) -> bool:
        if not await self._book_identifier_allowed(meta, collection):
            return False
        if not await self._book_publisher_allowed(meta):
            return False
        if not await self._audiobook_required_metadata(meta, format_name):
            return False
        return await self._audiobook_edition_allowed(meta)

    async def _book_source_phase(self, meta: Meta, format_name: str) -> bool:
        if not await self._ebook_page_count_allowed(meta, format_name):
            return False
        if not self._ebook_source_allowed(meta):
            return False
        return self._validate_book_file_layout(meta, format_name)

    async def validate_book(self, meta: Meta) -> bool:
        identity = await self._book_identity_phase(meta)
        if identity is None:
            return False
        format_name, collection = identity
        if not await self._book_metadata_phase(meta, format_name, collection):
            return False
        return await self._book_source_phase(meta, format_name)

    @classmethod
    def _book_payload_files(cls, meta: Meta) -> list[Path]:
        allowed = cls._BOOK_FORMATS | cls._AUDIOBOOK_FORMATS
        return [
            Path(str(item))
            for item in meta.filelist or []
            if Path(str(item)).suffix.upper().lstrip(".") in allowed
        ]

    @staticmethod
    def _book_collection_label(meta: Meta) -> str:
        return " ".join(
            (
                str(meta.title or ""),
                str(meta.name or ""),
                Path(str(meta.path or "")).name,
            )
        )

    @classmethod
    def _is_book_collection(cls, meta: Meta) -> bool:
        marked = bool(
            re.search(
                r"\b(?:collection|complete|books?\s+\d+\s*-\s*\d+|"
                r"series\s+pack)\b",
                cls._book_collection_label(meta),
                re.IGNORECASE,
            )
        )
        return marked and len(cls._book_payload_files(meta)) >= 5

    @classmethod
    def _audiobook_files(cls, meta: Meta) -> list[Path]:
        return [
            Path(str(item))
            for item in meta.filelist or []
            if Path(str(item)).suffix.upper().lstrip(".")
            in cls._AUDIOBOOK_FORMATS
        ]

    def _single_m4b_name_allowed(
        self, meta: Meta, audio_files: list[Path], format_name: str
    ) -> bool:
        if format_name != "M4B" or len(audio_files) != 1:
            return True
        expected = self._normalized_book_filename(
            f"{meta.author} {meta.title} {meta.year}"
        )
        actual = self._normalized_book_filename(audio_files[0].stem)
        if actual == expected:
            return True
        logger.info(
            f"{self.tracker}: [bold red]single-file M4B must be named "
            f"'Author - Title - Year.m4b': {audio_files[0].name}. "
            "Skipping upload.[/bold red]"
        )
        return False

    @staticmethod
    def _invalid_audiobook_numbering(audio_files: list[Path]) -> str:
        if len(audio_files) <= 1:
            return ""
        pattern = re.compile(
            r"^(?:\d{1,3}|chapter\s*\d+|(?:disc|part)\s*\d+)",
            re.IGNORECASE,
        )
        for item in audio_files:
            if not pattern.match(item.stem):
                return item.name
        return ""

    def _audiobook_file_layout_allowed(
        self, meta: Meta, source_path: Path, format_name: str
    ) -> bool:
        if source_path.exists() and source_path.is_file():
            logger.info(
                f"{self.tracker}: [bold red]Audiobooks must be uploaded inside "
                "a single descriptive folder. Skipping upload.[/bold red]"
            )
            return False
        audio_files = self._audiobook_files(meta)
        if not self._single_m4b_name_allowed(meta, audio_files, format_name):
            return False
        invalid = self._invalid_audiobook_numbering(audio_files)
        if not invalid:
            return True
        logger.info(
            f"{self.tracker}: [bold red]Audiobook files must use logical "
            f"chapter, disc, or part numbering: {invalid}. Skipping upload.[/bold red]"
        )
        return False

    @staticmethod
    def _book_filename_tokens(value: object) -> set[str]:
        return set(re.findall(r"[a-z0-9]+", str(value or "").casefold()))

    @classmethod
    def _ebook_files(cls, meta: Meta) -> list[Path]:
        return [
            Path(str(item))
            for item in meta.filelist or []
            if Path(str(item)).suffix.upper().lstrip(".") in cls._BOOK_FORMATS
        ]

    @classmethod
    def _ebook_filename_matches(
        cls,
        item: Path,
        author_tokens: set[str],
        title_tokens: set[str],
    ) -> bool:
        item_tokens = cls._book_filename_tokens(item.stem)
        return author_tokens.issubset(item_tokens) and title_tokens.issubset(
            item_tokens
        )

    @classmethod
    def _invalid_ebook_filename(cls, meta: Meta) -> str:
        author_tokens = cls._book_filename_tokens(
            meta.author or meta.book_author
        )
        title_tokens = cls._book_filename_tokens(meta.title or meta.book_title)
        for item in cls._ebook_files(meta):
            if not cls._ebook_filename_matches(
                item, author_tokens, title_tokens
            ):
                return item.name
        return ""

    def _ebook_file_layout_allowed(
        self, meta: Meta, source_path: Path
    ) -> bool:
        if not source_path.exists():
            return True
        invalid = self._invalid_ebook_filename(meta)
        if not invalid:
            return True
        logger.info(
            f"{self.tracker}: [bold red]eBook filename must include the author "
            f"and title: {invalid}. Skipping upload.[/bold red]"
        )
        return False

    def _validate_book_file_layout(self, meta: Meta, format_name: str) -> bool:
        source_path = Path(str(meta.path or ""))
        if meta.audiobook:
            return self._audiobook_file_layout_allowed(
                meta, source_path, format_name
            )
        return self._ebook_file_layout_allowed(meta, source_path)

    @staticmethod
    def _normalized_book_filename(value: str) -> str:
        return " ".join(re.findall(r"[\w]+", value.casefold()))

    @staticmethod
    def _raw_book_tracks(meta: Meta) -> list[object]:
        mediainfo = meta.mediainfo if isinstance(meta.mediainfo, dict) else {}
        media = mediainfo.get("media")
        if not isinstance(media, dict):
            return []
        tracks = media.get("track")
        return cast(list[object], tracks) if isinstance(tracks, list) else []

    @staticmethod
    def _book_audio_track(value: object) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        track = cast(dict[str, Any], value)
        track_type = track.get("@type") or track.get("type") or ""
        return track if str(track_type).casefold() == "audio" else None

    @classmethod
    def _book_audio_tracks(cls, meta: Meta) -> list[dict[str, Any]]:
        tracks: list[dict[str, Any]] = []
        for value in cls._raw_book_tracks(meta):
            track = cls._book_audio_track(value)
            if track is not None:
                tracks.append(track)
        return tracks

    @staticmethod
    def _book_track_codec_text(track: dict[str, Any]) -> str:
        for key in ("Format", "format", "CodecID", "codec"):
            value = track.get(key)
            if value:
                return str(value)
        return ""

    @classmethod
    def _book_audio_text(cls, tracks: list[dict[str, Any]]) -> str:
        return " ".join(
            cls._book_track_codec_text(track) for track in tracks
        ).casefold()

    @staticmethod
    def _m4a_audio_alias(audio_text: str) -> str:
        if "alac" in audio_text:
            return "ALAC"
        return "AAC" if "aac" in audio_text else "M4A"

    @staticmethod
    def _ogg_audio_alias(audio_text: str) -> str:
        if "opus" in audio_text:
            return "OPUS"
        return "VORBIS" if "vorbis" in audio_text else "OGG"

    @staticmethod
    def _book_audio_alias(format_name: str, audio_text: str) -> str:
        if format_name == "M4A":
            return DarkPeers._m4a_audio_alias(audio_text)
        if format_name == "OGG":
            return DarkPeers._ogg_audio_alias(audio_text)
        if format_name == "WAV":
            return "PCM" if "pcm" in audio_text else format_name
        return format_name

    @staticmethod
    def _raw_book_format(meta: Meta) -> str:
        value = meta.type if meta.type else meta.format
        return str(value or "").upper().strip()

    @classmethod
    def _book_format(cls, meta: Meta) -> str:
        """Resolve container aliases from MediaInfo when the codec is available."""
        format_name = cls._raw_book_format(meta)
        if format_name == "HTM":
            return "HTML"
        aliases = {"M4A", "OGG", "WAV"}
        if not meta.audiobook or format_name not in aliases:
            return format_name
        audio_text = cls._book_audio_text(cls._book_audio_tracks(meta))
        return cls._book_audio_alias(format_name, audio_text)

    @staticmethod
    def _normalized_isbn(value: str) -> str:
        cleaned = re.sub(r"[^0-9Xx]", "", value)
        if not cleaned:
            return ""
        return cleaned[:-1] + cleaned[-1:].upper()

    @staticmethod
    def _book_isbn_value(meta: Meta) -> str:
        value = meta.isbn if meta.isbn else meta.book_isbn
        return str(value or "").strip()

    @staticmethod
    def _book_asin_value(meta: Meta) -> str:
        value = meta.asin if meta.asin else meta.book_asin
        return str(value or "").strip()

    @classmethod
    def _book_identifier(cls, meta: Meta) -> str:
        isbn = cls._book_isbn_value(meta)
        if isbn:
            return cls._normalized_isbn(isbn)
        asin = cls._book_asin_value(meta)
        return re.sub(r"[^0-9A-Za-z]", "", asin).upper()

    @staticmethod
    def _music_release(meta: Meta) -> MusicRelease:
        data = (
            meta.music_release if isinstance(meta.music_release, dict) else {}
        )
        return (
            MusicRelease.from_dict(data)
            if data
            else MusicRelease(root=str(meta.path or ""))
        )

    def _music_validation_errors(self, release: MusicRelease) -> list[str]:
        return [
            issue.message
            for issue in MusicValidator().validate(release)
            if issue.level == ValidationLevel.ERROR
        ]

    @staticmethod
    def _release_music_paths(release: MusicRelease) -> list[str]:
        return [track.relative_path for track in release.tracks]

    @staticmethod
    def _filelist_music_paths(meta: Meta) -> list[str]:
        suffixes = {
            ".flac",
            ".mp3",
            ".m4a",
            ".aac",
            ".ogg",
            ".opus",
            ".wav",
            ".alac",
        }
        paths: list[str] = []
        for item in meta.filelist or []:
            value = str(item)
            if Path(value).suffix.lower() in suffixes:
                paths.append(value)
        return paths

    @classmethod
    def _music_paths(cls, meta: Meta, release: MusicRelease) -> list[str]:
        release_paths = cls._release_music_paths(release)
        return release_paths or cls._filelist_music_paths(meta)

    @staticmethod
    def _normalized_music_text(value: str) -> str:
        return re.sub(r"\W+", " ", value.casefold()).strip()

    @staticmethod
    def _music_root_is_file(root: Path) -> bool:
        return root.exists() and not root.is_dir()

    @classmethod
    def _album_matches_music_folder(cls, root: Path, album: str) -> bool:
        if not root.is_dir() or not album:
            return True
        album_text = cls._normalized_music_text(album)
        folder_text = cls._normalized_music_text(root.name)
        return album_text in folder_text

    def _music_root_allowed(self, root: Path, album: str) -> bool:
        if self._music_root_is_file(root):
            logger.info(
                f"{self.tracker}: [bold red]Music uploads must be contained "
                "in a descriptive folder. Skipping upload.[/bold red]"
            )
            return False
        if self._album_matches_music_folder(root, album):
            return True
        logger.info(
            f"{self.tracker}: [bold red]Music folder name must include the "
            "album title. Skipping upload.[/bold red]"
        )
        return False

    @staticmethod
    def _music_relative_path(root: Path, relative: str) -> str:
        prefix = root.name if root.is_dir() else ""
        return "/".join(part for part in (prefix, relative) if part)

    @staticmethod
    def _music_has_leading_space(relative: str) -> bool:
        return any(part.startswith(" ") for part in relative.split("/"))

    @classmethod
    def _music_path_invalid(cls, relative: str, root: Path) -> bool:
        full_relative = cls._music_relative_path(root, relative)
        if len(full_relative) > 180:
            return True
        if Path(relative).name.startswith(" "):
            return True
        return cls._music_has_leading_space(relative)

    @staticmethod
    def _music_filename_has_prohibited_chars(filename: str) -> bool:
        return any(char in filename for char in ':?<>|*"')

    def _music_filename_numbered(
        self, filename: str, unnumbered_single: bool
    ) -> bool:
        if re.search(r"\b\w+\.\w+\.\d{1,2}\.\w+", filename):
            return False
        if unnumbered_single:
            return True
        return bool(self._AUDIO_TRACK_PATTERN.match(Path(filename).stem))

    def _music_file_allowed(
        self, relative: str, root: Path, unnumbered_single: bool
    ) -> bool:
        filename = Path(relative).name
        if self._music_path_invalid(relative, root):
            logger.info(
                f"{self.tracker}: [bold red]invalid music path: {relative}. "
                "Skipping upload."
            )
            return False
        if self._music_filename_has_prohibited_chars(filename):
            logger.info(
                f"{self.tracker}: [bold red]music filename contains prohibited "
                f"filesystem characters: {filename}. Skipping upload."
            )
            return False
        if self._music_filename_numbered(filename, unnumbered_single):
            return True
        logger.info(
            f"{self.tracker}: [bold red]music filename must include a track "
            f"number and title: {filename}. Skipping upload."
        )
        return False

    def _music_release_valid(self, release: MusicRelease) -> bool:
        errors = self._music_validation_errors(release)
        if not errors:
            return True
        logger.info(
            f"{self.tracker}: [bold red]{' '.join(errors)} Skipping upload."
        )
        return False

    @staticmethod
    def _music_unnumbered_single(
        release: MusicRelease, paths: list[str]
    ) -> bool:
        release_type = str(release.get("release_type", "")).casefold()
        return len(paths) == 1 and release_type == "single"

    def _music_files_allowed(
        self,
        paths: list[str],
        root: Path,
        unnumbered_single: bool,
    ) -> bool:
        for path in paths:
            relative = str(path).replace("\\", "/")
            if not self._music_file_allowed(relative, root, unnumbered_single):
                return False
        return True

    def validate_music(self, meta: Meta) -> bool:
        release = self._music_release(meta)
        if not self._music_release_valid(release):
            return False
        paths = self._music_paths(meta, release)
        album = str(release.get("album", meta.title or "")).strip()
        root = Path(str(meta.path or ""))
        if not self._music_root_allowed(root, album):
            return False
        return self._music_files_allowed(
            paths,
            root,
            self._music_unnumbered_single(release, paths),
        )

    @staticmethod
    def _game_files(meta: Meta) -> list[Path]:
        return [Path(str(item)) for item in meta.filelist or []]

    @staticmethod
    def _game_rars(files: list[Path]) -> list[Path]:
        return [
            item
            for item in files
            if item.suffix.lower() == ".rar"
            or re.search(r"\.r\d{2}$", item.name, re.IGNORECASE)
        ]

    @staticmethod
    def _game_prohibited_file(files: list[Path]) -> str:
        for item in files:
            if item.suffix.lower() in {".iso", ".zip", ".7z"}:
                return item.name
        return ""

    @staticmethod
    def _game_scene_metadata_allowed(meta: Meta) -> bool:
        if not meta.scene:
            return False
        if not str(meta.scene_nfo_file or "").strip():
            return False
        return not bool(str(meta.repack or "").strip())

    @classmethod
    def _game_scene_payload_allowed(cls, meta: Meta) -> bool:
        files = cls._game_files(meta)
        if cls._game_prohibited_file(files):
            return False
        if not cls._game_rars(files):
            return False
        return cls._game_scene_metadata_allowed(meta)

    @staticmethod
    def _game_instruction_text(meta: Meta) -> str:
        return " ".join(
            str(value or "")
            for value in (
                meta.description,
                meta.description_file_content,
                meta.description_link_content,
                meta.description_nfo_content,
            )
        )

    @classmethod
    def _game_has_instructions(cls, meta: Meta) -> bool:
        return bool(
            re.search(
                r"\b(?:install(?:ation)?|setup|usage|instructions?)\b",
                cls._game_instruction_text(meta),
                re.IGNORECASE,
            )
        )

    async def validate_game(self, meta: Meta) -> bool:
        if not self._game_scene_payload_allowed(meta):
            logger.info(
                f"{self.tracker}: [bold red]Games/Apps must be an original "
                "RAR'd scene release with its NFO, not a repack or ISO. "
                "Skipping upload."
            )
            return False
        if self._game_has_instructions(meta):
            return True
        return await self._missing_required(
            "installation and usage instructions", meta
        )

    async def _missing_required(self, field: str, meta: Meta) -> bool:
        _ = meta
        logger.info(
            f"{self.tracker}: [bold red]missing required {field}. Skipping upload.[/bold red]"
        )
        return False

    async def _confirm_or_skip(self, message: str, meta: Meta) -> bool:
        logger.info(f"{self.tracker}: [bold red]{message}[/bold red]")
        if meta.unattended:
            return bool(meta.unattended_confirm)
        return await self.common.prompt_user_for_confirmation(
            "Do you want to upload anyway?", meta
        )

    async def get_additional_data(self, meta: Meta) -> dict[str, Any]:
        return {
            "mod_queue_opt_in": await self.get_flag(meta, "modq"),
        }

    @classmethod
    def _single_foreign_audio_label(cls, only: str, original: str) -> str:
        if only == "english" and original:
            return "Dubbed"
        if original and only in cls._NORDIC_LANGUAGES:
            return f"{only.title()} Dubbed"
        return "SKIPPED"

    @classmethod
    def _single_audio_label(cls, audio: set[str], original: str) -> str:
        only = next(iter(audio))
        if only == original:
            return "SKIPPED"
        return cls._single_foreign_audio_label(only, original)

    @staticmethod
    def _english_original_pair_label(audio: set[str], original: str) -> str:
        if original != "english":
            return "Dual-Audio"
        other = next(iter(audio - {"english"}), "")
        return f"{other.title()} MULTi" if other else "SKIPPED"

    @classmethod
    def _original_audio_multi_label(
        cls, audio: set[str], original: str
    ) -> str:
        if "english" in audio and len(audio) == 2:
            return cls._english_original_pair_label(audio, original)
        if len(audio) >= 3:
            return "MULTi"
        other = next(iter(audio - {original}), "")
        return f"{other.title()} MULTi" if other else "SKIPPED"

    @staticmethod
    def _english_audio_multi_label(audio: set[str]) -> str:
        other = audio - {"english"}
        if len(other) == 1:
            return f"{next(iter(other)).title()} MULTi"
        return "MULTi"

    @classmethod
    def _other_audio_multi_label(cls, audio: set[str]) -> str:
        other = audio - cls._accepted_languages()
        if len(other) == 1 and len(audio) == 2:
            return f"{next(iter(other)).title()} MULTi"
        return "SKIPPED"

    @classmethod
    def _multi_audio_label(cls, audio: set[str], original: str) -> str:
        if original and original in audio:
            return cls._original_audio_multi_label(audio, original)
        if "english" in audio:
            return cls._english_audio_multi_label(audio)
        return cls._other_audio_multi_label(audio)

    @classmethod
    def _audio_label(cls, audio: set[str], original: str) -> str:
        if not audio:
            return "SKIPPED"
        if len(audio) == 1:
            return cls._single_audio_label(audio, original)
        return cls._multi_audio_label(audio, original)

    async def get_audio(self, meta: Meta) -> str:
        if not meta.language_checked:
            await languages_manager.process_desc_language(
                meta, tracker=self.tracker
            )
        if meta.is_disc:
            return "SKIPPED"
        return self._audio_label(
            self._languages(meta.audio_languages),
            self._normalise_language(meta.original_language),
        )

    def _special_category_name(self, meta: Meta) -> str | None:
        if meta.category == "MUSIC":
            return self._music_name(meta)
        if meta.category == "BOOK":
            return self._book_name(meta)
        return None

    @classmethod
    def _usable_scene_name(cls, meta: Meta) -> str:
        scene_name = str(meta.scene_name or "")
        if not scene_name or cls._is_local_path_name(scene_name):
            return ""
        return scene_name

    @staticmethod
    def _has_release_name_metadata(meta: Meta) -> bool:
        return bool(
            str(meta.title or "").strip() or str(meta.name or "").strip()
        )

    def _scene_name_override(self, meta: Meta) -> str | None:
        scene_name = self._usable_scene_name(meta)
        if not scene_name:
            return None
        if meta.scene:
            return scene_name
        if self._has_release_name_metadata(meta):
            return None
        return self._normalize_scene_name(scene_name)

    async def _base_upload_name(self, meta: Meta) -> str:
        name = str(meta.name or "").strip()
        if str(meta.type or "").strip():
            return await self._video_name(meta)
        if meta.category == "TV":
            return await self._tv_name(meta, name)
        return name

    @staticmethod
    def _name_year(meta: Meta) -> str:
        if meta.manual_year not in (None, 0):
            return str(meta.manual_year)
        return str(meta.year or "").strip()

    async def _final_media_name(self, meta: Meta) -> str:
        name = await self._base_upload_name(meta)
        if meta.category in {"TV", "MOVIE"} and not meta.scene:
            name = self._normalize_aka_year_order(
                name, meta.title, meta.aka, self._name_year(meta)
            )
        audio = await self.get_audio(meta)
        return self._apply_dub_element(name, audio)

    async def get_name(self, meta: Meta) -> dict[str, str]:
        special = self._special_category_name(meta)
        if special is not None:
            return {
                "name": self._ensure_group_tag(
                    special, meta.tag, preserve_if_scene=meta.scene
                )
            }
        scene_override = self._scene_name_override(meta)
        if scene_override is not None:
            return {
                "name": self._ensure_group_tag(
                    scene_override,
                    meta.tag,
                    preserve_if_scene=meta.scene,
                )
            }
        naming_meta = await self._canonical_tv_meta(meta)
        name = await self._final_media_name(naming_meta)
        return {"name": self._ensure_group_tag(name, naming_meta.tag)}

    @staticmethod
    def _should_canonicalize_tv(meta: Meta, title: str) -> bool:
        return bool(
            meta.category == "TV" and not meta.scene and title and meta.tmdb_id
        )

    @staticmethod
    def _replace_title_prefix(
        name: str, old_title: str, new_title: str
    ) -> str:
        if not name or not old_title or old_title == new_title:
            return name
        return re.sub(
            rf"^{re.escape(old_title)}(?=\s|$)",
            new_title,
            name,
            count=1,
            flags=re.IGNORECASE,
        )

    @staticmethod
    def _tv_results(payload: dict[str, Any]) -> list[dict[str, Any]]:
        raw_results = payload.get("results", [])
        if not isinstance(raw_results, list):
            return []
        return [
            cast(dict[str, Any], raw)
            for raw in raw_results
            if isinstance(raw, dict)
        ]

    @staticmethod
    def _tv_result_title(result: dict[str, Any]) -> str:
        name = result.get("name")
        if name:
            return str(name).strip()
        return str(result.get("original_name") or "").strip()

    @classmethod
    def _tv_result_title_for_id(
        cls, payload: dict[str, Any], tmdb_id: int | None
    ) -> str:
        target_id = str(tmdb_id or "")
        for result in cls._tv_results(payload):
            if str(result.get("id", "")) == target_id:
                return cls._tv_result_title(result)
        return ""

    @staticmethod
    def _canonical_or_current_title(canonical: str, current: str) -> str:
        return canonical if canonical else current

    async def _canonical_tv_title(self, meta: Meta) -> str:
        title = str(meta.title or "").strip()
        if not self._should_canonicalize_tv(meta, title):
            return title
        api_key = self._tmdb_api_key()
        if not api_key:
            return title
        payload = await self._tv_search_payload(title, api_key)
        if payload is None:
            return title
        canonical = self._tv_result_title_for_id(payload, meta.tmdb_id)
        return self._canonical_or_current_title(canonical, title)

    async def _canonical_tv_meta(self, meta: Meta) -> Meta:
        canonical_title = await self._canonical_tv_title(meta)
        current_title = str(meta.title or "").strip()
        if not canonical_title or canonical_title == current_title:
            return meta
        naming_meta = meta.copy()
        naming_meta.title = canonical_title
        naming_meta.name = self._replace_title_prefix(
            str(meta.name or ""), current_title, canonical_title
        )
        return naming_meta

    @staticmethod
    def _normalize_scene_name(scene_name: str) -> str:
        value = str(scene_name).strip()
        if not value:
            return ""

        prefix = value
        suffix = ""
        match = re.match(r"^(.+)-(?:[A-Za-z0-9][A-Za-z0-9_+]{1,})$", value)
        if match:
            prefix = match.group(1).strip()
            suffix = value[len(prefix) :].strip()

        encoded = prefix.replace("_", " ")
        encoded = re.sub(
            r"(?i)(?:(?:(?:x|h)\.[0-9]{3})|(?:[0-9]+\.[0-9]{1,2}\b))",
            lambda match: match.group(0).replace(".", "\x00"),
            encoded,
        )
        normalized = encoded.replace(".", " ")
        normalized = normalized.replace("\x00", ".")

        return (
            f"{normalized.strip()}{suffix}".strip()
            if suffix
            else normalized.strip()
        )

    @staticmethod
    def _normalized_group_tag(tag: str | None) -> str:
        cleaned = str(tag or "").strip()
        if not cleaned:
            return ""
        return cleaned if cleaned.startswith("-") else f"-{cleaned}"

    @staticmethod
    def _append_group_tag(name: str, normalized_tag: str) -> str:
        normalized_name = name.strip()
        if normalized_name.lower().endswith(normalized_tag.lower()):
            return name
        return f"{normalized_name}{normalized_tag}"

    @staticmethod
    def _ensure_group_tag(
        name: str, tag: str | None, preserve_if_scene: bool = False
    ) -> str:
        if preserve_if_scene or DarkPeers._has_group_in_name(name):
            return name
        normalized_tag = DarkPeers._normalized_group_tag(tag)
        if not normalized_tag:
            return name
        return DarkPeers._append_group_tag(name, normalized_tag)

    @staticmethod
    def _has_group_in_name(name: str) -> bool:
        match = re.search(
            r"-([A-Za-z0-9][A-Za-z0-9+_-]{1,})$", str(name).strip()
        )
        if not match:
            return False
        token = match.group(1)
        token_lower = token.lower()
        if re.fullmatch(r"\d+", token_lower):
            return False
        if token_lower in {
            "h264",
            "h265",
            "x264",
            "x265",
            "hevc",
            "avc",
            "ac3",
            "eac3",
            "dd",
            "dts",
            "opus",
            "aac",
            "mp3",
            "flac",
        }:
            return False
        if re.fullmatch(r"[xhx][.-]?\d{3,4}", token_lower):
            return False
        return not re.fullmatch(r"\d+p", token_lower)

    @staticmethod
    def _aka_year_values(
        title: str, aka: str, year: str
    ) -> tuple[str, str, str] | None:
        title_value = " ".join(str(title).split())
        aka_value = " ".join(str(aka).split())
        year_value = str(year).strip()
        if not title_value or not aka_value or not year_value:
            return None
        return title_value, aka_value, year_value

    @staticmethod
    def _aka_year_pattern(title: str, aka: str, year: str) -> str:
        return (
            rf"(?i)\b{re.escape(title)}\s+{re.escape(year)}\s+"
            rf"{re.escape(aka)}\b"
        )

    @classmethod
    def _normalize_aka_year_order(
        cls, name: str, title: str, aka: str, year: str
    ) -> str:
        if not name:
            return name
        values = cls._aka_year_values(title, aka, year)
        if values is None:
            return name
        title_value, aka_value, year_value = values
        normalized_name = " ".join(name.split())
        candidate = (
            normalized_name
            if normalized_name.casefold() == name.casefold()
            else name
        )
        pattern = cls._aka_year_pattern(title_value, aka_value, year_value)
        if not re.search(pattern, candidate):
            return candidate
        return re.sub(
            pattern,
            f"{title_value} {aka_value} {year_value}",
            candidate,
            count=1,
        )

    @staticmethod
    def _raw_video_year(meta: Meta) -> str:
        value = meta.manual_year if meta.manual_year else meta.year
        return str(value or "").strip()

    async def _tv_video_year(self, meta: Meta, year: str) -> str:
        if meta.category != "TV" or not year:
            return year
        return year if await self._tv_title_needs_year(meta) else ""

    async def _video_year(self, meta: Meta) -> str:
        if meta.no_year:
            return ""
        return await self._tv_video_year(meta, self._raw_video_year(meta))

    @staticmethod
    def _video_season_episode(meta: Meta) -> tuple[str, bool]:
        if meta.manual_date:
            return str(meta.manual_date), True
        if meta.no_season:
            return "", False
        return f"{meta.season or ''}{meta.episode or ''}", False

    @staticmethod
    def _strip_edition_token(
        edition: str, pattern: str, canonical: str | None = None
    ) -> tuple[str, str]:
        match = re.search(pattern, edition, re.IGNORECASE)
        if match is None:
            return edition, ""
        label = canonical if canonical is not None else match.group(0)
        remaining = (
            f"{edition[: match.start()]} {edition[match.end() :]}".strip()
        )
        return remaining, label

    @classmethod
    def _video_ratio(cls, edition: str) -> tuple[str, str]:
        for pattern, canonical in (
            (r"\bOpen Matte\b", "Open Matte"),
            (r"\bIMAX\b", "IMAX"),
            (r"\bMAR\b", "MAR"),
        ):
            remaining, label = cls._strip_edition_token(
                edition, pattern, canonical
            )
            if label:
                return remaining, label
        return edition, ""

    @classmethod
    def _video_cut(cls, edition: str) -> tuple[str, str]:
        patterns = (
            r"Director(?:'|\u2019)s Cut",
            r"Super Duper Cut",
            r"Special Edition",
            r"Extended",
            r"Unrated",
            r"Uncut",
        )
        for pattern in patterns:
            remaining, label = cls._strip_edition_token(
                edition, rf"\b{pattern}\b"
            )
            if label:
                return remaining, label
        return edition, ""

    @classmethod
    def _video_edition_parts(cls, meta: Meta) -> tuple[str, str, str, str]:
        edition = str(meta.manual_edition or meta.edition or "").strip()
        hybrid = (
            "Hybrid"
            if meta.webdv or re.search(r"\bHybrid\b", edition, re.IGNORECASE)
            else ""
        )
        edition = re.sub(
            r"\bHybrid\b", "", edition, flags=re.IGNORECASE
        ).strip()
        edition, ratio = cls._video_ratio(edition)
        edition, cut = cls._video_cut(edition)
        return " ".join(edition.split()), hybrid, ratio, cut

    @staticmethod
    def _video_resolution(meta: Meta) -> str:
        value = str(meta.resolution or "").strip()
        return "" if value.upper() == "OTHER" else value

    @staticmethod
    def _video_context(meta: Meta) -> str:
        return " ".join(
            (
                str(meta.name or ""),
                str(meta.basename_no_ext or ""),
                Path(str(meta.path or "")).name,
            )
        )

    @staticmethod
    def _video_type_name(release_type: str) -> str:
        return {
            "REMUX": "REMUX",
            "WEBDL": "WEB-DL",
            "WEBRIP": "WEBRip",
        }.get(release_type, "")

    @staticmethod
    def _ds4k_flag(
        release_type: str, full_disc_or_remux: bool, context: str
    ) -> str:
        eligible = release_type in {"WEBDL", "WEBRIP", "HDTV"}
        if full_disc_or_remux or not eligible:
            return ""
        return "DS4K" if re.search(r"\bDS4K\b", context, re.IGNORECASE) else ""

    @staticmethod
    def _hi10p_flag(context: str) -> str:
        return (
            "Hi10P" if re.search(r"\bHi10P\b", context, re.IGNORECASE) else ""
        )

    @staticmethod
    def _video_codec_name(meta: Meta, full_disc_or_remux: bool) -> str:
        value = (
            meta.video_codec
            if full_disc_or_remux
            else meta.video_encode or meta.video_codec or ""
        )
        return str(value).strip()

    @staticmethod
    def _video_prefix(
        meta: Meta,
        aka: str,
        year: str,
        season_episode: str,
        cut: str,
        ratio: str,
        hybrid: str,
        resolution: str,
    ) -> list[str]:
        return [
            str(meta.title or "").strip(),
            aka,
            year,
            season_episode,
            cut,
            ratio,
            hybrid,
            str(meta.repack or "").strip(),
            resolution,
        ]

    @staticmethod
    def _full_video_suffix(
        meta: Meta,
        edition: str,
        source: str,
        type_name: str,
        hi10p: str,
        video_codec: str,
    ) -> list[str]:
        return [
            edition,
            str(meta.region or "").strip(),
            str(meta.three_d or "").strip(),
            source,
            type_name,
            hi10p,
            str(meta.hdr or "").strip(),
            video_codec,
            str(meta.audio or "").strip(),
        ]

    @staticmethod
    def _file_video_suffix(
        meta: Meta,
        ds4k: str,
        edition: str,
        source: str,
        type_name: str,
        hi10p: str,
        video_codec: str,
    ) -> list[str]:
        return [
            ds4k,
            edition,
            str(meta.three_d or "").strip(),
            source,
            type_name,
            str(meta.audio or "").strip(),
            hi10p,
            str(meta.hdr or "").strip(),
            video_codec,
        ]

    async def _video_name_time_parts(self, meta: Meta) -> tuple[str, str]:
        year = await self._video_year(meta)
        season_episode, manual_date = self._video_season_episode(meta)
        return ("" if manual_date else year), season_episode

    @classmethod
    def _video_name_resolution(cls, meta: Meta, source: str) -> str:
        if "DVD" in source.upper():
            return ""
        return cls._video_resolution(meta)

    @classmethod
    def _video_name_suffix(
        cls,
        meta: Meta,
        release_type: str,
        source: str,
        edition: str,
        context: str,
    ) -> list[str]:
        full = release_type in {"DISC", "REMUX"}
        hi10p = cls._hi10p_flag(context)
        codec = cls._video_codec_name(meta, full)
        type_name = cls._video_type_name(release_type)
        if full:
            return cls._full_video_suffix(
                meta, edition, source, type_name, hi10p, codec
            )
        return cls._file_video_suffix(
            meta,
            cls._ds4k_flag(release_type, full, context),
            edition,
            source,
            type_name,
            hi10p,
            codec,
        )

    @staticmethod
    def _render_video_name(parts: list[str], tag: str | None) -> str:
        name = " ".join(part for part in parts if part)
        return f"{' '.join(name.split())}{str(tag or '').strip()}"

    async def _video_name(self, meta: Meta) -> str:
        release_type = self._release_type(meta)
        year, season_episode = await self._video_name_time_parts(meta)
        aka = "" if meta.no_aka else str(meta.aka or "").strip()
        edition, hybrid, ratio, cut = self._video_edition_parts(meta)
        source = self._video_source(meta, release_type)
        context = self._video_context(meta)
        prefix = self._video_prefix(
            meta,
            aka,
            year,
            season_episode,
            cut,
            ratio,
            hybrid,
            self._video_name_resolution(meta, source),
        )
        suffix = self._video_name_suffix(
            meta, release_type, source, edition, context
        )
        return self._render_video_name([*prefix, *suffix], meta.tag)

    @staticmethod
    def _web_video_source(meta: Meta, source: str) -> str:
        service = str(meta.service or "").strip()
        if service:
            return service
        return "" if source.upper() == "WEB" else source

    @staticmethod
    def _dvdrip_video_source(source: str) -> str:
        upper = source.upper()
        if "NTSC" in upper:
            standard = "NTSC"
        elif "PAL" in upper:
            standard = "PAL"
        else:
            standard = ""
        return " ".join(part for part in (standard, "DVDRip") if part)

    @staticmethod
    def _bluray_disc_source(meta: Meta) -> str:
        return (
            "UHD Blu-ray"
            if str(meta.uhd or "").upper() == "UHD"
            else "Blu-ray"
        )

    @staticmethod
    def _dvd_disc_source(meta: Meta, source: str) -> str:
        size = str(meta.dvd_size or "").strip()
        return f"{source}{size.replace('DVD', '')}" if size else source

    @classmethod
    def _disc_video_source(cls, meta: Meta, source: str) -> str:
        if source in {"BluRay", "Blu-ray"}:
            return cls._bluray_disc_source(meta)
        if source in {"PAL DVD", "NTSC DVD"}:
            return cls._dvd_disc_source(meta, source)
        return source

    @staticmethod
    def _remux_video_source(meta: Meta, source: str) -> str:
        if source not in {"Blu-ray", "BluRay"}:
            return source
        return (
            "UHD BluRay" if str(meta.uhd or "").upper() == "UHD" else "BluRay"
        )

    @classmethod
    def _video_source(cls, meta: Meta, release_type: str) -> str:
        source = str(meta.source or "").strip()
        source_type = (
            "WEB" if release_type in {"WEBDL", "WEBRIP"} else release_type
        )
        handlers = {
            "WEB": lambda: cls._web_video_source(meta, source),
            "DVDRIP": lambda: cls._dvdrip_video_source(source),
            "DISC": lambda: cls._disc_video_source(meta, source),
            "REMUX": lambda: cls._remux_video_source(meta, source),
        }
        handler = handlers.get(source_type)
        return handler() if handler is not None else source

    @staticmethod
    def _replace_name_span(
        name: str, start: int, end: int, replacement: str
    ) -> str:
        value = f"{name[:start]}{replacement}{name[end:]}"
        return " ".join(value.split())

    @staticmethod
    def _dub_replacement(audio: str) -> str:
        return f" {audio}" if audio else ""

    @classmethod
    def _dub_span(cls, name: str) -> tuple[int, int] | None:
        existing = cls._DUB_ELEMENT_PATTERN.search(name)
        if existing is not None:
            return existing.start(), existing.end()
        codec = cls._AUDIO_CODEC_PATTERN.search(name)
        return (codec.start(), codec.start()) if codec is not None else None

    @classmethod
    def _apply_dub_element(cls, name: str, audio: str) -> str:
        normalized = " ".join(name.split())
        if audio == "SKIPPED":
            return normalized
        replacement = cls._dub_replacement(audio)
        if not replacement:
            return normalized
        span = cls._dub_span(name)
        if span is None:
            return normalized
        return cls._replace_name_span(name, span[0], span[1], replacement)

    async def _tv_name(self, meta: Meta, name: str) -> str:
        title = str(meta.title or "").strip()
        year = str(meta.year or "").strip()
        if year and not await self._tv_title_needs_year(meta):
            name = re.sub(
                rf"^({re.escape(title)})\s+{re.escape(year)}(?=\s|$)",
                r"\1",
                name,
                count=1,
                flags=re.IGNORECASE,
            )
        return " ".join(name.split())

    def _tmdb_api_key(self) -> str:
        default = self.config.get("DEFAULT", {})
        return str(
            default.get("tmdb_api", "") if isinstance(default, dict) else ""
        ).strip()

    async def _tv_search_payload(
        self, title: str, api_key: str
    ) -> dict[str, Any] | None:
        try:
            logger.info(
                f"{self.tracker}: Checking if TMDb has multiple shows with "
                f"the title '{title}'..."
            )
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://api.themoviedb.org/3/search/tv",
                    params={
                        "api_key": api_key,
                        "query": title,
                        "language": "en-US",
                        "include_adult": "true",
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError, ValueError, TypeError:
            return None
        return (
            cast(dict[str, Any], payload) if isinstance(payload, dict) else {}
        )

    @staticmethod
    def _normalized_title_key(value: object) -> str:
        return " ".join(str(value or "").casefold().split())

    @classmethod
    def _tv_result_id_if_match(cls, raw: object, title_key: str) -> str:
        if not isinstance(raw, dict):
            return ""
        result = cast(dict[str, Any], raw)
        names = (result.get("name"), result.get("original_name"))
        normalized_names = {cls._normalized_title_key(name) for name in names}
        if title_key not in normalized_names:
            return ""
        return str(result.get("id", ""))

    @classmethod
    def _matching_tv_ids(cls, payload: dict[str, Any], title: str) -> set[str]:
        results_raw = payload.get("results", [])
        results = results_raw if isinstance(results_raw, list) else []
        title_key = cls._normalized_title_key(title)
        matching = {
            cls._tv_result_id_if_match(raw, title_key) for raw in results
        }
        matching.discard("")
        return matching

    @staticmethod
    def _tv_ids_need_year(ids: set[str], current_id: str) -> bool:
        return bool(ids - {current_id}) if current_id else len(ids) > 1

    async def _tv_title_with_api_needs_year(
        self, meta: Meta, title: str, api_key: str
    ) -> bool:
        payload = await self._tv_search_payload(title, api_key)
        if payload is None:
            return True
        ids = self._matching_tv_ids(payload, title)
        return self._tv_ids_need_year(ids, str(meta.tmdb_id or ""))

    async def _tv_title_needs_year(self, meta: Meta) -> bool:
        title = str(meta.title or "").strip()
        if not title:
            return False
        api_key = self._tmdb_api_key()
        if not api_key:
            return True
        return await self._tv_title_with_api_needs_year(meta, title, api_key)

    @classmethod
    def _release_field(
        cls, release: dict[str, Any], name: str, default: Any = ""
    ) -> Any:
        """Read a value from the serialized music release model."""
        fields = release.get("fields") if isinstance(release, dict) else {}
        value = fields.get(name) if isinstance(fields, dict) else {}
        return (
            value.get("value", default) if isinstance(value, dict) else default
        )

    @staticmethod
    def _music_release_data(meta: Meta) -> dict[str, Any]:
        return (
            meta.music_release if isinstance(meta.music_release, dict) else {}
        )

    @staticmethod
    def _first_music_track(release: dict[str, Any]) -> dict[str, Any]:
        raw = release.get("tracks")
        tracks = raw if isinstance(raw, list) else []
        if tracks and isinstance(tracks[0], dict):
            return cast(dict[str, Any], tracks[0])
        return {}

    @staticmethod
    def _music_codec(meta: Meta, track: dict[str, Any]) -> str:
        value = (
            track.get("codec")
            or track.get("format")
            or meta.format
            or meta.type
        )
        return str(value).upper().strip()

    @classmethod
    def _lossless_music_detail(
        cls, release: dict[str, Any], track: dict[str, Any]
    ) -> str:
        depth = track.get("bit_depth") or cls._release_field(
            release, "nfo_bit_depth"
        )
        rate = track.get("sample_rate") or cls._release_field(
            release, "nfo_sample_rate"
        )
        if depth is None or rate is None:
            return ""
        with suppress(TypeError, ValueError):
            return f"{int(depth)}-{int(rate) / 1000:g}"
        return ""

    @staticmethod
    def _lossy_music_bitrate(meta: Meta, track: dict[str, Any]) -> str:
        bitrate = track.get("bitrate") or meta.audio_bitrate
        if bitrate is None:
            return ""
        with suppress(TypeError, ValueError):
            value = int(bitrate)
            return str(value // 1000 if value >= 1000 else value)
        return ""

    @classmethod
    def _music_format_details(
        cls,
        meta: Meta,
        release: dict[str, Any],
        track: dict[str, Any],
        codec: str,
    ) -> list[str]:
        if codec in {"FLAC", "ALAC", "PCM"}:
            return [cls._lossless_music_detail(release, track)]
        if codec in {"MP3", "AAC", "OPUS", "VORBIS"}:
            return [
                cls._lossy_music_bitrate(meta, track),
                str(track.get("bitrate_mode") or "").upper().strip(),
            ]
        return []

    @classmethod
    def _music_format_parts(
        cls,
        meta: Meta,
        release: dict[str, Any],
        track: dict[str, Any],
    ) -> list[str]:
        media = str(cls._release_field(release, "media", meta.source)).strip()
        codec = cls._music_codec(meta, track)
        details = cls._music_format_details(meta, release, track, codec)
        return [part for part in (media, codec, *details) if part]

    @classmethod
    def _music_title_parts(
        cls, meta: Meta, release: dict[str, Any]
    ) -> tuple[str, str, str]:
        artist = str(
            cls._release_field(release, "artist", meta.artist)
        ).strip()
        album = str(cls._release_field(release, "album", meta.title)).strip()
        year = str(
            cls._release_field(
                release,
                "release_year",
                cls._release_field(release, "year", meta.year or ""),
            )
        ).strip()
        return artist, album, year

    @classmethod
    def _music_format_name(cls, meta: Meta, release: dict[str, Any]) -> str:
        track = cls._first_music_track(release)
        value = " ".join(cls._music_format_parts(meta, release, track))
        release_type = (
            str(cls._release_field(release, "release_type", ""))
            .strip()
            .casefold()
        )
        return f"{value} Single".strip() if release_type == "single" else value

    @staticmethod
    def _render_music_title(artist: str, album: str, year: str) -> str:
        title = " - ".join(part for part in (artist, album) if part)
        if not year:
            return title
        return f"{title} ({year})" if title else f"({year})"

    @classmethod
    def _music_name(cls, meta: Meta) -> str:
        """Format music as ``Artist - Album (Year) - Format`` for DarkPeers."""
        release = cls._music_release_data(meta)
        artist, album, year = cls._music_title_parts(meta, release)
        title = cls._render_music_title(artist, album, year)
        format_name = cls._music_format_name(meta, release)
        return f"{title} - {format_name}" if format_name else title

    @staticmethod
    def _book_title(meta: Meta, author: str) -> str:
        title = str(meta.title or "").strip()
        if not author:
            return title
        return re.sub(
            rf"^{re.escape(author)}\s*[-:]+\s*",
            "",
            title,
            flags=re.IGNORECASE,
        ).strip()

    @staticmethod
    def _book_base_parts(author: str, title: str, year: str) -> list[str]:
        separator = "-" if author and title else ""
        return [part for part in (author, separator, title, year) if part]

    @staticmethod
    def _book_edition_name(meta: Meta) -> str:
        edition = str(meta.manual_edition or meta.edition or "").strip()
        if not edition or re.search(
            r"\b(?:1st|first)\b", edition, re.IGNORECASE
        ):
            return ""
        return edition

    @staticmethod
    def _audiobook_source_name(meta: Meta) -> tuple[str, str]:
        manual_source = str(meta.manual_source or "").strip().upper()
        source_name = {
            "CD": "CD",
            "OVERDRIVE": "Overdrive",
            "HOOPLA": "Hoopla",
            "WEB": "Web",
            "OTHER": "Other",
        }.get(manual_source, "")
        return manual_source, source_name

    @staticmethod
    def _insert_audiobook_source(parts: list[str], source_name: str) -> None:
        if source_name:
            parts.insert(-1, source_name)

    @staticmethod
    def _append_audiobook_bitrate(
        parts: list[str], format_name: str, bitrate: object
    ) -> None:
        if format_name in {"MP3", "AAC", "OPUS", "VORBIS"} and bitrate:
            parts.append(str(bitrate))

    @staticmethod
    def _append_identifier(parts: list[str], identifier: str) -> None:
        if identifier:
            parts.append(identifier)

    @classmethod
    def _audiobook_name_parts(
        cls,
        meta: Meta,
        parts: list[str],
        format_name: str,
        identifier: str,
    ) -> list[str]:
        manual_source, source_name = cls._audiobook_source_name(meta)
        cls._insert_audiobook_source(parts, source_name)
        cls._append_audiobook_bitrate(
            parts, format_name, meta.audiobook_bitrate
        )
        cls._append_identifier(parts, identifier)
        if manual_source == "RETAIL":
            parts.append("Retail")
        return parts

    @staticmethod
    def _ebook_source_label(meta: Meta) -> str:
        source = str(meta.manual_source or meta.source or "").upper().strip()
        return {"RETAIL": "Retail", "SCAN": "Scan"}.get(source, "")

    @classmethod
    def _ebook_name_parts(
        cls, meta: Meta, parts: list[str], identifier: str
    ) -> list[str]:
        cls._append_identifier(parts, identifier)
        source_label = cls._ebook_source_label(meta)
        if source_label:
            parts.append(source_label)
        if meta.ocr:
            parts.append("OCR")
        return parts

    @classmethod
    def _append_book_edition(cls, meta: Meta, parts: list[str]) -> None:
        if meta.audiobook:
            return
        edition = cls._book_edition_name(meta)
        if edition:
            parts.append(edition)

    @staticmethod
    def _append_book_format(parts: list[str], format_name: str) -> None:
        if format_name:
            parts.append(format_name)

    @classmethod
    def _book_common_name_parts(
        cls, meta: Meta, format_name: str
    ) -> list[str]:
        author = str(meta.author or meta.book_author or "").strip()
        title = cls._book_title(meta, author)
        parts = cls._book_base_parts(
            author, title, str(meta.year or "").strip()
        )
        cls._append_book_edition(meta, parts)
        cls._append_book_format(parts, format_name)
        return parts

    @classmethod
    def _book_name(cls, meta: Meta) -> str:
        """Format eBooks and audiobooks according to DarkPeers' book rules."""
        format_name = cls._book_format(meta)
        identifier = cls._book_identifier(meta)
        parts = cls._book_common_name_parts(meta, format_name)
        if meta.audiobook:
            parts = cls._audiobook_name_parts(
                meta, parts, format_name, identifier
            )
        else:
            parts = cls._ebook_name_parts(meta, parts, identifier)
        return " ".join(parts)

    async def get_category_id(
        self,
        meta: Meta,
        category: str = "",
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        category_id = {
            "MOVIE": "1",
            "TV": "2",
            "BOOK": "8",
            "GAME": "4",
            "MUSIC": "3",
        }
        if mapping_only:
            return category_id
        if reverse:
            return {v: k for k, v in category_id.items()}
        return {"category_id": category_id.get(category or meta.category, "0")}

    @staticmethod
    def _type_id_mapping() -> dict[str, str]:
        return {
            "DISC": "1",
            "REMUX": "2",
            "WEBDL": "4",
            "WEBRIP": "5",
            "HDTV": "6",
            "ENCODE": "3",
            "DVDRIP": "3",
            "AUDIOBOOK": "15",
            "COMIC": "17",
            "EBOOK": "18",
            "PC": "9",
            "LINUX": "14",
            "MAC": "11",
            "CONSOLE": "10",
            "FLAC": "8",
            "MP3": "7",
        }

    @staticmethod
    def _explicit_book_type_map() -> dict[str, str]:
        mapping = dict.fromkeys(DarkPeers._BOOK_FORMATS, "EBOOK")
        mapping.update(
            dict.fromkeys(DarkPeers._AUDIOBOOK_FORMATS, "AUDIOBOOK")
        )
        mapping.update(
            {
                "CBR": "COMIC",
                "CBZ": "COMIC",
                "AZW": "EBOOK",
                "HTM": "EBOOK",
                "M4A": "AUDIOBOOK",
                "OGG": "AUDIOBOOK",
                "WAV": "AUDIOBOOK",
            }
        )
        return mapping

    @classmethod
    def _explicit_book_type(cls, value: str) -> str:
        upper = value.upper()
        return cls._explicit_book_type_map().get(upper, upper)

    @staticmethod
    def _book_meta_type(meta: Meta, meta_type: str) -> str:
        if meta.audiobook:
            return "AUDIOBOOK"
        if meta.comic or meta_type in {"CBR", "CBZ"}:
            return "COMIC"
        return "EBOOK"

    @staticmethod
    def _game_meta_type(meta: Meta) -> str:
        return "CONSOLE" if meta.console_game else meta.platform.upper()

    @classmethod
    def _resolved_meta_type(cls, meta: Meta) -> str:
        meta_type = str(meta.type or "").upper()
        handlers = {
            "BOOK": lambda: cls._book_meta_type(meta, meta_type),
            "GAME": lambda: cls._game_meta_type(meta),
            "MUSIC": lambda: meta.format.upper(),
        }
        handler = handlers.get(meta.category)
        return handler() if handler is not None else meta_type

    @classmethod
    def _book_type_id(cls, mapping: dict[str, str], value: str) -> str:
        normalized = cls._explicit_book_type(value)
        return mapping.get(normalized, mapping.get(value, "0"))

    @classmethod
    def _selected_type_id(
        cls,
        mapping: dict[str, str],
        meta: Meta,
        explicit_type: str,
    ) -> str:
        if meta.category == "BOOK" and explicit_type:
            return cls._book_type_id(mapping, explicit_type)
        return mapping.get(cls._resolved_meta_type(meta), "0")

    async def get_type_id(
        self,
        meta: Meta,
        type: str = "",
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        type_id = self._type_id_mapping()
        if mapping_only:
            return type_id
        if reverse:
            return {value: key for key, value in type_id.items()}
        return {"type_id": self._selected_type_id(type_id, meta, type)}
