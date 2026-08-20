# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import random
import re
from pathlib import Path
from typing import Any, Literal, cast

import aiofiles
import certifi
import cli_ui
import pycountry
import requests
from babel import Locale
from babel.core import UnknownLocaleError

from src.domain_models.release import Meta
from src.integrations.media.audio import AudioManager
from src.integrations.media.language_adapter import languages_manager
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.common import Common
from src.integrations.trackers.UNIT3D import UNIT3D

_shri_session_data: dict[str, dict[str, str | None]] = {}
ITALIAN_LANGS = {"it", "ita", "italian"}


class ShareIsland(UNIT3D):
    """
    ShareIsland is a ITALIAN Private Torrent Tracker for MOVIES / TV / GENERAL
    """

    tracker = "SHAREISLAND"
    display_name = "ShareIsland"
    base_url = "https://shareisland.org"
    banned_groups: tuple[str, ...] = ()
    id_url = f"{base_url}/api/torrents/"
    upload_url = f"{base_url}/api/torrents/upload"
    search_url = f"{base_url}/api/torrents/filter"
    requests_url = f"{base_url}/api/requests/filter"
    torrent_url = f"{base_url}/torrents/"
    allows_bloated_audio = True
    supported_categories = ("TV", "MOVIE")

    # Pre-compile regex patterns for performance
    INVALID_TAG_PATTERN = re.compile(r"-(nogrp|nogroup|unknown|unk)", re.IGNORECASE)
    WHITESPACE_PATTERN = re.compile(r"\s{2,}")
    MARKER_PATTERN = re.compile(r"\b(UNTOUCHED|VU1080|VU720|VU)\b", re.IGNORECASE)

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config, tracker_name="SHAREISLAND")
        self.config = config
        self.common = Common(config)
        self.audio_manager = AudioManager(config)

    def _get_language_code(self, track_or_string: Any) -> str:
        """Extract and normalize language to ISO alpha-2 code."""
        language = self._raw_language(track_or_string)
        if not language:
            return ""
        normalized = self._language_base(language)
        if len(normalized) == 2:
            return normalized
        return self._resolved_alpha2(normalized)

    @staticmethod
    def _raw_language(value: Any) -> str:
        if not isinstance(value, dict):
            return str(value or "")
        language = value.get("Language", "")
        if isinstance(language, dict):
            language = language.get("String", "")
        return str(language or "")

    @staticmethod
    def _language_base(value: str) -> str:
        return value.casefold().split("-", 1)[0]

    @staticmethod
    def _resolved_alpha2(value: str) -> str:
        try:
            language = pycountry.languages.get(name=value.title())
            if language is None:
                language = pycountry.languages.get(alpha_2=value)
            if language is None:
                language = pycountry.languages.get(alpha_3=value)
            alpha2 = getattr(language, "alpha_2", None)
            return str(alpha2).lower() if alpha2 else value
        except AttributeError, KeyError, LookupError:
            return value

    async def get_additional_data(self, meta: Meta) -> dict[str, Any]:
        """Get additional tracker-specific upload data"""
        return {"mod_queue_opt_in": await self.get_flag(meta, "modq")}

    async def get_name(self, meta: Meta) -> dict[str, str]:
        """Build a ShareIsland release name from normalized metadata."""
        await self._ensure_language_metadata(meta)
        title = self._selected_title(meta)
        audio = await self._get_best_italian_audio_format(meta)
        languages = self._audio_language_tag(meta)
        release_type = self.get_effective_type(meta)
        source = self._normalized_source(meta, release_type)
        extras = self._name_extras(meta, title)
        name = self._name_for_type(meta, title, audio, languages, release_type, source, extras)
        return {"name": self._finalized_name(meta, name)}

    async def _ensure_language_metadata(self, meta: Meta) -> None:
        if not meta.language_checked:
            await languages_manager.process_desc_language(meta, tracker=self.tracker)

    def _selected_title(self, meta: Meta) -> str:
        italian = self._get_italian_title(meta.imdb_info)
        use_italian = bool(self.config["TRACKERS"][self.tracker].get("use_italian_title", False))
        return italian if italian and use_italian else str(meta.title)

    def _audio_language_tag(self, meta: Meta) -> str:
        languages = self._normalized_audio_languages(meta.audio_languages)
        count = len(languages)
        if count == 0:
            return ""
        if count == 1:
            return languages[0]
        if count == 2:
            return self._two_language_tag(languages)
        return "ITA - MULTI" if "ITA" in languages else "MULTI"

    def _normalized_audio_languages(self, value: Any) -> list[str]:
        values = value if isinstance(value, list) else []
        normalized = [self._get_language_name(str(language)) for language in values]
        return list(dict.fromkeys(language for language in normalized if language))

    @staticmethod
    def _two_language_tag(languages: list[str]) -> str:
        if "ITA" not in languages:
            return " - ".join(languages)
        other = next((language for language in languages if language != "ITA"), "")
        return f"ITA - {other}" if other else "ITA"

    @staticmethod
    def _source_value(meta: Meta) -> str:
        source = meta.source
        if isinstance(source, list):
            return str(source[0]) if source else ""
        return str(source or "")

    @classmethod
    def _normalized_source(cls, meta: Meta, release_type: str) -> str:
        source = cls._source_value(meta)
        return source.replace("Blu-ray", "BluRay") if release_type != "DISC" else source

    @classmethod
    def _name_extras(cls, meta: Meta, title: str) -> dict[str, str]:
        return {
            "year": cls._year_text(meta.year),
            "season": cls._text(meta.season),
            "episode": cls._text(meta.episode),
            "episode_title": cls._text(meta.episode_title),
            "part": cls._text(meta.part),
            "edition": cls._text(meta.edition),
            "hdr": cls._text(meta.hdr),
            "uhd": cls._text(meta.uhd),
            "three_d": cls._text(meta.three_d),
            "hybrid": cls._hybrid_marker(meta, title),
            "repack": cls._text(meta.repack).strip(),
            "resolution": cls._text(meta.resolution),
        }

    @staticmethod
    def _text(value: Any) -> str:
        return "" if value is None else str(value)

    @staticmethod
    def _year_text(value: Any) -> str:
        return "" if value is None else str(value)

    @staticmethod
    def _hybrid_marker(meta: Meta, title: str) -> str:
        if meta.edition:
            return ""
        source_is_hybrid = bool(meta.webdv) or isinstance(meta.source, list)
        return "Hybrid" if source_is_hybrid and "HYBRID" not in title.upper() else ""

    def _name_for_type(
        self,
        meta: Meta,
        title: str,
        audio: str,
        languages: str,
        release_type: str,
        source: str,
        extras: dict[str, str],
    ) -> str:
        known = self._known_type_name(meta, title, audio, languages, release_type, source, extras)
        if known is not None:
            return known
        return str(meta.name or "").replace("Dual-Audio", "").strip()

    def _known_type_name(
        self,
        meta: Meta,
        title: str,
        audio: str,
        languages: str,
        release_type: str,
        source: str,
        extras: dict[str, str],
    ) -> str | None:
        if release_type == "DISC":
            return self._disc_name(meta, title, audio, source, extras)
        if release_type == "REMUX":
            return self._remux_name(meta, title, audio, languages, source, extras)
        return self._encoded_type_name(meta, title, audio, languages, release_type, source, extras)

    def _encoded_type_name(
        self,
        meta: Meta,
        title: str,
        audio: str,
        languages: str,
        release_type: str,
        source: str,
        extras: dict[str, str],
    ) -> str | None:
        if release_type in {"DVDRIP", "BRRIP"}:
            return self._rip_name(meta, title, audio, languages, release_type, extras)
        if release_type in {"ENCODE", "HDTV"}:
            return self._encode_name(meta, title, audio, languages, source, extras)
        if release_type in {"WEBDL", "WEBRIP"}:
            return self._web_name(meta, title, audio, languages, release_type, extras)
        return None

    def _disc_name(self, meta: Meta, title: str, audio: str, source: str, extra: dict[str, str]) -> str:
        region = self._disc_region(meta)
        if meta.is_disc == "DVD":
            return self._dvd_name(meta, title, audio, source, region, extra)
        if meta.is_disc == "HDDVD":
            return self._hddvd_name(meta, title, audio, source, region, extra)
        return self._bdmv_name(meta, title, audio, source, region, extra)

    @staticmethod
    def _disc_region(meta: Meta) -> str:
        stored = _shri_session_data.get(meta.uuid, {}).get("_shri_region_name")
        return str(stored if stored else meta.region or "")

    def _dvd_name(self, meta: Meta, title: str, audio: str, source: str, region: str, extra: dict[str, str]) -> str:
        return self._join_name(
            title,
            extra["year"],
            extra["season"] + extra["episode"],
            extra["three_d"],
            extra["edition"],
            extra["repack"],
            extra["resolution"],
            region,
            source,
            self._text(meta.dvd_size),
            audio,
        )

    def _hddvd_name(self, meta: Meta, title: str, audio: str, source: str, region: str, extra: dict[str, str]) -> str:
        return self._join_name(title, extra["year"], extra["edition"], extra["repack"], extra["resolution"], region, source, self._text(meta.video_codec), audio)

    def _bdmv_name(self, meta: Meta, title: str, audio: str, source: str, region: str, extra: dict[str, str]) -> str:
        return self._join_name(
            title,
            extra["year"],
            extra["season"] + extra["episode"],
            extra["three_d"],
            extra["edition"],
            extra["hybrid"],
            extra["repack"],
            extra["resolution"],
            region,
            extra["uhd"],
            source,
            extra["hdr"],
            self._text(meta.video_codec),
            audio,
        )

    def _remux_name(self, meta: Meta, title: str, audio: str, languages: str, source: str, extra: dict[str, str]) -> str:
        return self._join_name(
            title,
            extra["year"],
            extra["season"] + extra["episode"],
            extra["episode_title"],
            extra["part"],
            extra["three_d"],
            languages,
            extra["edition"],
            extra["hybrid"],
            extra["repack"],
            extra["resolution"],
            extra["uhd"],
            source,
            "REMUX",
            extra["hdr"],
            str(meta.video_codec or ""),
            audio,
        )

    def _rip_name(self, meta: Meta, title: str, audio: str, languages: str, release_type: str, extra: dict[str, str]) -> str:
        type_name = "DVDRip" if release_type == "DVDRIP" else "BRRip"
        return self._join_name(
            title,
            extra["year"],
            extra["season"],
            languages,
            extra["edition"],
            extra["hybrid"],
            extra["repack"],
            extra["resolution"],
            type_name,
            audio,
            extra["hdr"],
            str(meta.video_encode or ""),
        )

    def _encode_name(self, meta: Meta, title: str, audio: str, languages: str, source: str, extra: dict[str, str]) -> str:
        return self._join_name(
            title,
            extra["year"],
            extra["season"] + extra["episode"],
            extra["episode_title"],
            extra["part"],
            languages,
            extra["edition"],
            extra["hybrid"],
            extra["repack"],
            extra["resolution"],
            extra["uhd"],
            source,
            audio,
            extra["hdr"],
            str(meta.video_encode or ""),
        )

    def _web_name(self, meta: Meta, title: str, audio: str, languages: str, release_type: str, extra: dict[str, str]) -> str:
        type_name = "WEB-DL" if release_type == "WEBDL" else "WEBRip"
        return self._join_name(
            title,
            extra["year"],
            extra["season"] + extra["episode"],
            extra["episode_title"],
            extra["part"],
            languages,
            extra["edition"],
            extra["hybrid"],
            extra["repack"],
            extra["resolution"],
            extra["uhd"],
            str(meta.service or ""),
            type_name,
            audio,
            extra["hdr"],
            str(meta.video_encode or ""),
        )

    @staticmethod
    def _join_name(*parts: str) -> str:
        return " ".join(part for part in parts if part)

    def _finalized_name(self, meta: Meta, value: str) -> str:
        name = self.WHITESPACE_PATTERN.sub(" ", value).strip()
        if not name:
            name = str(meta.name or "UNKNOWN")
        tag = self._extract_clean_release_group(meta)
        return f"{name}-{tag}" if tag else name

    def _extract_clean_release_group(self, meta: Meta) -> str:
        """Extract a valid release-group marker accepted by ShareIsland."""
        explicit = self._explicit_release_group(meta.tag)
        if explicit:
            return explicit
        candidate = self._basename_release_group(meta)
        return candidate if self._valid_marker_group(candidate) else "NoGroup"

    def _explicit_release_group(self, value: Any) -> str:
        tag = str(value).strip().lstrip("-") if isinstance(value, str) else ""
        if not tag or " " in tag or self.INVALID_TAG_PATTERN.search(tag):
            return ""
        return tag

    def _basename_release_group(self, meta: Meta) -> str:
        basename = self._basename_without_extension(meta)
        parts = re.split(r"[-.]", basename)
        candidate = parts[-1].strip()
        return candidate.split()[-1] if " " in candidate else candidate

    def _basename_without_extension(self, meta: Meta) -> str:
        basename = self.get_basename(meta)
        extension = self._mediainfo_extension(meta)
        suffix = f".{extension}" if extension else ""
        return basename[: -len(suffix)] if suffix and basename.endswith(suffix) else basename

    @staticmethod
    def _mediainfo_extension(meta: Meta) -> str:
        try:
            return str(meta.mediainfo["media"]["track"][0].get("FileExtension", ""))
        except KeyError, IndexError, TypeError, AttributeError:
            return ""

    def _valid_marker_group(self, candidate: str) -> bool:
        if not self._valid_group_shape(candidate):
            return False
        return self.MARKER_PATTERN.search(candidate) is not None

    @staticmethod
    def _valid_group_shape(candidate: str) -> bool:
        return bool(candidate and len(candidate) <= 30 and candidate.replace("_", "").isalnum())

    async def get_type_id(
        self,
        meta: Meta,
        type: str | None = None,
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        """Map release type to ShareIsland type IDs"""
        type_mapping = {
            "DISC": "26",
            "REMUX": "7",
            "WEBDL": "27",
            "WEBRIP": "51",
            "HDTV": "33",
            "ENCODE": "15",
            "DVDRIP": "15",
            "BRRIP": "15",
        }

        if mapping_only:
            return type_mapping

        if reverse:
            return {v: k for k, v in type_mapping.items()}
        if type is not None:
            return {"type_id": type_mapping.get(type, "0")}
        effective_type = self.get_effective_type(meta)
        type_id = type_mapping.get(effective_type, "0")
        return {"type_id": type_id}

    async def get_additional_checks(self, meta: Meta) -> Literal[True]:
        """Validate required disc region/distributor metadata."""
        if meta.is_disc in {"DVD", "HDDVD"}:
            await self._validate_disc_metadata(meta)
        return await super().get_additional_checks(meta)  # type: ignore[return-value]

    async def _validate_disc_metadata(self, meta: Meta) -> None:
        region_name = await self._required_region_name(meta)
        region_id = await self._validated_region_id(region_name)
        distributor_name = self._distributor_name(meta)
        distributor_id = await self._optional_distributor_id(distributor_name)
        _shri_session_data[meta.uuid] = {
            "_shri_region_id": region_id,
            "_shri_region_name": region_name,
            "_shri_distributor_id": distributor_id,
        }

    async def _required_region_name(self, meta: Meta) -> str:
        region = str(meta.region or "").strip().upper()
        if region:
            return region
        if meta.unattended and not meta.unattended_confirm:
            self._raise_missing_region()
        return self._prompt_region()

    def _prompt_region(self) -> str:
        while True:
            value = cli_ui.ask_string("ShareIsland: Region code not found for disc. Please enter it manually (mandatory): ")
            region = str(value or "").strip().upper()
            if region:
                return region
            logger.info(f"{self.tracker}: Region code is required.", extra={"markup": False})

    @staticmethod
    def _raise_missing_region() -> None:
        cli_ui.error("Region required; skipping ShareIsland.")
        raise ValueError("Region required for disc upload")

    async def _validated_region_id(self, region_name: str) -> str:
        region_id = await self.common.unit3d_region_ids(region_name)
        if region_id:
            return str(region_id)
        cli_ui.error(f"Invalid region code '{region_name}'; skipping ShareIsland.")
        raise ValueError(f"Invalid region code: {region_name}")

    @staticmethod
    def _distributor_name(meta: Meta) -> str:
        existing = str(meta.distributor or "").strip().upper()
        if existing or meta.unattended:
            return existing
        value = cli_ui.ask_string("ShareIsland: Distributor (optional, Enter to skip): ")
        return str(value or "").strip().upper()

    async def _optional_distributor_id(self, name: str) -> str | None:
        if not name:
            return None
        value = await self.common.unit3d_distributor_ids(name)
        return str(value) if value else None

    async def get_region_id(self, meta: Meta) -> dict[str, Any]:
        """Override to use validated region ID stored in meta"""
        data = _shri_session_data.get(meta.uuid, {})
        region_id = data.get("_shri_region_id")
        if region_id:
            return {"region_id": region_id}
        return cast(dict[str, Any], await super().get_region_id(meta))

    async def get_distributor_id(self, meta: Meta) -> dict[str, Any]:
        """Override to use validated distributor ID stored in meta"""
        data = _shri_session_data.get(meta.uuid, {})
        distributor_id = data.get("_shri_distributor_id")
        if distributor_id:
            return {"distributor_id": distributor_id}
        return cast(dict[str, Any], await super().get_distributor_id(meta))

    def get_basename(self, meta: Meta) -> str:
        """Extract basename from first file in filelist or path"""
        path_value = next(iter(meta.filelist), meta.path)
        path = path_value if isinstance(path_value, str) else ""
        return Path(path).name

    def _detect_type_from_technical_analysis(self, meta: Meta) -> str:
        """Unified type detection: filename markers + MediaInfo analysis"""
        # Priority 1: Explicit REMUX markers (filename check FIRST)
        if self._has_remux_marker(meta):
            return "REMUX"
        # Priority 2: Base type from upstream
        base_type = meta.type if meta.type is not None else "ENCODE"
        if isinstance(base_type, str) and base_type in ("DISC", "DVDRIP", "BRRIP"):
            return base_type
        # Priority 3: Technical mediainfo analysis
        return self._analyze_encode_type(meta)

    def _has_remux_marker(self, meta: Meta) -> bool:
        filename = Path(self.get_basename(meta)).stem.casefold()
        if "remux" in filename or self.MARKER_PATTERN.search(filename):
            return True
        return self._makemkv_without_encoding(meta)

    @classmethod
    def _makemkv_without_encoding(cls, meta: Meta) -> bool:
        tracks = cls._mediainfo_tracks(meta)
        if not tracks or not cls._mentions_makemkv(tracks[0]):
            return False
        return cls._video_has_no_encoding_settings(tracks)

    @staticmethod
    def _video_has_no_encoding_settings(tracks: list[dict[str, Any]]) -> bool:
        video = next((track for track in tracks if track.get("@type") == "Video"), {})
        settings = video.get("Encoded_Library_Settings")
        return not settings or isinstance(settings, dict)

    @staticmethod
    def _mentions_makemkv(general: dict[str, Any]) -> bool:
        app = str(general.get("Encoded_Application", "")).casefold()
        library = str(general.get("Encoded_Library", "")).casefold()
        return "makemkv" in app or "makemkv" in library

    @classmethod
    def _mediainfo_tracks(cls, meta: Meta) -> list[dict[str, Any]]:
        media = cls._media_mapping(meta.mediainfo)
        return cls._mapping_tracks(media.get("track", []))

    @staticmethod
    def _media_mapping(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        media = value.get("media", {})
        return cast(dict[str, Any], media) if isinstance(media, dict) else {}

    @staticmethod
    def _mapping_tracks(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [cast(dict[str, Any], item) for item in value if isinstance(item, dict)]

    def _analyze_encode_type(self, meta: Meta) -> str:
        """Detect release type from normalized MediaInfo evidence."""
        general, video = self._analysis_tracks(meta)
        sources = self._analysis_sources(meta.source)
        settings_present, settings = self._encoding_field(video, "Encoded_Library_Settings")
        library_present, library = self._encoding_field(video, "Encoded_Library")
        service = str(meta.service or "").upper()
        for detected in (
            self._streaming_dv_type(general, video, settings),
            self._crf_type(settings, sources),
            self._service_fingerprint_type(service, video, settings, library, library_present),
            self._bluray_encode_type(general, video, sources, settings_present, library_present),
            self._web_source_type(general, sources),
            self._source_fallback_type(sources, service, settings_present, library_present),
        ):
            if detected is not None:
                return detected
        return self._fallback_type(meta)

    @classmethod
    def _analysis_tracks(cls, meta: Meta) -> tuple[dict[str, Any], dict[str, Any]]:
        tracks = cls._mediainfo_tracks(meta)
        general = tracks[0] if tracks else {}
        video = tracks[1] if len(tracks) > 1 else {}
        return general, video

    @staticmethod
    def _analysis_sources(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).upper() for item in value]
        return [str(value).upper()] if value else []

    @staticmethod
    def _encoding_field(video: dict[str, Any], key: str) -> tuple[bool, str]:
        raw = video.get(key, "")
        present = bool(raw) and not isinstance(raw, dict)
        return present, str(raw).lower() if present else ""

    @staticmethod
    def _has_encoding_tools(general: dict[str, Any], tools: tuple[str, ...]) -> bool:
        app = str(general.get("Encoded_Application", "")).lower()
        extra = general.get("extra", {})
        frontend = str(extra.get("Writing_frontend", "")).lower() if isinstance(extra, dict) else ""
        combined = f"{app} {frontend}"
        return any(tool in combined for tool in tools)

    @classmethod
    def _streaming_dv_type(cls, general: dict[str, Any], video: dict[str, Any], settings: str) -> str | None:
        profile = str(video.get("HDR_Format_Profile", ""))
        streaming_dv = any(marker in profile for marker in ("dvhe.05", "dvhe.07", "dvhe.08"))
        if not streaming_dv or settings:
            return None
        tools = ("handbrake", "staxrip", "megatagger")
        return None if cls._has_encoding_tools(general, tools) else "WEBDL"

    @classmethod
    def _crf_type(cls, settings: str, sources: list[str]) -> str | None:
        if "crf=" not in settings:
            return None
        return "WEBRIP" if cls._has_web_source(sources) else "ENCODE"

    @classmethod
    def _service_fingerprint_type(
        cls,
        service: str,
        video: dict[str, Any],
        settings: str,
        library: str,
        library_present: bool,
    ) -> str | None:
        crunchyroll = cls._crunchyroll_type(service, settings, library, library_present)
        if crunchyroll is not None:
            return crunchyroll
        return "WEBDL" if cls._is_netflix_fingerprint(video, settings, library) else None

    @classmethod
    def _crunchyroll_type(cls, service: str, settings: str, library: str, library_present: bool) -> str | None:
        if service != "CR":
            return None
        library_type = cls._crunchyroll_library_type(library, library_present)
        if library_type is not None:
            return library_type
        return "WEBDL" if cls._crunchyroll_bitrate_setting(settings) else None

    @staticmethod
    def _crunchyroll_library_type(library: str, library_present: bool) -> str | None:
        if "core 142" in library:
            return "WEBDL"
        if not library_present:
            return None
        core = re.search(r"core (\d+)", library)
        return "WEBRIP" if core and int(core.group(1)) >= 152 else None

    @staticmethod
    def _crunchyroll_bitrate_setting(settings: str) -> bool:
        return bool(settings and "bitrate=" in settings)

    @staticmethod
    def _is_netflix_fingerprint(video: dict[str, Any], settings: str, library: str) -> bool:
        profile = str(video.get("Format_Profile", ""))
        known_core = "core 118" in library or "core 148" in library
        return "Main@L4.0" in profile and "rc=2pass" in settings and known_core

    @classmethod
    def _bluray_encode_type(
        cls,
        general: dict[str, Any],
        video: dict[str, Any],
        sources: list[str],
        settings_present: bool,
        library_present: bool,
    ) -> str | None:
        if not cls._has_bluray_source(sources):
            return None
        return "ENCODE" if cls._bluray_encode_evidence(general, video, settings_present, library_present) else None

    @classmethod
    def _bluray_encode_evidence(
        cls,
        general: dict[str, Any],
        video: dict[str, Any],
        settings_present: bool,
        library_present: bool,
    ) -> bool:
        if isinstance(video.get("BitDepth"), dict):
            return True
        if settings_present or library_present:
            return True
        tools = ("x264", "x265", "handbrake", "staxrip", "megatagger")
        return cls._has_encoding_tools(general, tools)

    @classmethod
    def _web_source_type(cls, general: dict[str, Any], sources: list[str]) -> str | None:
        if not cls._has_web_source(sources):
            return None
        tools = ("handbrake", "staxrip", "megatagger")
        return "WEBRIP" if cls._has_encoding_tools(general, tools) else "WEBDL"

    @classmethod
    def _source_fallback_type(
        cls,
        sources: list[str],
        service: str,
        settings_present: bool,
        library_present: bool,
    ) -> str | None:
        if cls._has_streaming_service(service):
            return "WEBDL"
        return cls._disc_fallback_type(sources, settings_present, library_present)

    @staticmethod
    def _has_streaming_service(service: str) -> bool:
        return bool(service and service != "NONE")

    @classmethod
    def _disc_fallback_type(cls, sources: list[str], settings_present: bool, library_present: bool) -> str | None:
        if cls._has_disc_source(sources):
            return "REMUX"
        dvd_remux = cls._has_dvd_source(sources) and not settings_present and not library_present
        return "REMUX" if dvd_remux else None

    @staticmethod
    def _has_web_source(sources: list[str]) -> bool:
        return any("WEB" in source for source in sources)

    @staticmethod
    def _has_bluray_source(sources: list[str]) -> bool:
        return any(source in {"BLURAY", "BLU-RAY"} for source in sources)

    @staticmethod
    def _has_disc_source(sources: list[str]) -> bool:
        return any(source in {"BLURAY", "BLU-RAY", "HDDVD"} for source in sources)

    @staticmethod
    def _has_dvd_source(sources: list[str]) -> bool:
        return any(source in {"NTSC", "PAL", "NTSC DVD", "PAL DVD", "DVD"} for source in sources)

    @staticmethod
    def _fallback_type(meta: Meta) -> str:
        value = meta.type if meta.type is not None else "ENCODE"
        return value if isinstance(value, str) else "ENCODE"

    def get_effective_type(self, meta: Meta) -> str:
        """
        Determine effective type with priority hierarchy:
        1. Technical analysis (REMUX/ENCODE/WEB-DL/WEBRip detection)
        2. Base type from meta
        """
        return self._detect_type_from_technical_analysis(meta)

    def _get_italian_title(self, imdb_info: dict[str, Any]) -> str | None:
        """Extract Italian IMDb AKA, preferring the country match."""
        akas = self._aka_entries(imdb_info.get("akas", []))
        country = self._aka_title(akas, "country")
        return country if country is not None else self._aka_title(akas, "language")

    @staticmethod
    def _aka_entries(value: Any) -> list[dict[str, Any]]:
        values = value if isinstance(value, list) else []
        return [cast(dict[str, Any], item) for item in values if isinstance(item, dict)]

    @staticmethod
    def _aka_title(akas: list[dict[str, Any]], field: str) -> str | None:
        for aka in akas:
            if aka.get(field) != "Italy" or aka.get("attributes"):
                continue
            title = aka.get("title")
            return title if isinstance(title, str) else None
        return None

    def _get_language_name(self, iso_code: str) -> str:
        """Convert language input to uppercase ISO alpha-3 when possible."""
        if not iso_code:
            return ""
        language = self._pycountry_language(iso_code)
        alpha3 = getattr(language, "alpha_3", None)
        return str(alpha3).upper() if alpha3 else iso_code.upper()

    @staticmethod
    def _pycountry_language(value: str) -> Any:
        lowered = value.lower()
        language = pycountry.languages.get(alpha_2=lowered)
        if language is None:
            language = pycountry.languages.get(alpha_3=lowered)
        if language is None:
            language = pycountry.languages.get(name=value.title())
        return language

    def _get_italian_language_name(self, iso_code: str) -> str:
        """Convert ISO language code to Italian language name using Babel"""
        if not iso_code:
            return ""

        try:
            locale = Locale.parse(iso_code.lower())
            italian_name = locale.get_display_name("it")
            if isinstance(italian_name, str) and italian_name:
                return italian_name.title()
            return self._get_language_name(iso_code).title()
        except ValueError, AttributeError, KeyError, UnknownLocaleError:
            return self._get_language_name(iso_code).title()

    async def _get_best_italian_audio_format(self, meta: Meta) -> str:
        """Select and format the best Italian audio track."""
        bdinfo_tracks = self._bdinfo_italian_tracks(meta)
        if bdinfo_tracks:
            return await self._formatted_bdinfo_audio(meta, bdinfo_tracks)
        mediainfo_tracks = self._mediainfo_italian_tracks(meta)
        if mediainfo_tracks:
            return await self._formatted_mediainfo_audio(meta, mediainfo_tracks)
        return self._clean_audio_string(meta.audio)

    @staticmethod
    def _clean_audio_string(value: Any) -> str:
        audio = value if isinstance(value, str) else ""
        cleaned = audio.replace("Dual-Audio", "").replace("Dubbed", "")
        return re.sub(r"\s*-[A-Z]{3}(-[A-Z]{3})*$", "", cleaned).strip()

    @staticmethod
    def _bdinfo_audio_tracks(meta: Meta) -> list[dict[str, Any]]:
        bdinfo = meta.bdinfo if isinstance(meta.bdinfo, dict) else {}
        audio = bdinfo.get("audio", [])
        values = audio if isinstance(audio, list) else []
        return [cast(dict[str, Any], track) for track in values if isinstance(track, dict)]

    @classmethod
    def _bdinfo_italian_tracks(cls, meta: Meta) -> list[dict[str, Any]]:
        return [track for track in cls._bdinfo_audio_tracks(meta) if str(track.get("language", "")).lower() in ITALIAN_LANGS]

    def _mediainfo_italian_tracks(self, meta: Meta) -> list[dict[str, Any]]:
        tracks = self._mediainfo_tracks(meta)
        return [track for track in tracks[1:] if self._is_italian_audio_track(track)]

    def _is_italian_audio_track(self, track: dict[str, Any]) -> bool:
        if track.get("@type") != "Audio":
            return False
        if "commentary" in str(track.get("Title", "")).lower():
            return False
        return self._get_language_code(track) in ITALIAN_LANGS

    @classmethod
    def _bdinfo_audio_quality(cls, track: dict[str, Any]) -> tuple[bool, int, bool, int]:
        codec = str(track.get("codec", "")).lower()
        lossless = any(marker in codec for marker in ("truehd", "dts-hd ma", "flac", "pcm"))
        channels = cls._whole_channels(track.get("channels", "2.0"))
        atmos = "atmos" in str(track.get("atmos_why_you_be_like_this", "")).lower()
        bitrate = cls._digits_value(track.get("bitrate", "0"))
        return lossless, channels, atmos, bitrate

    @classmethod
    def _mediainfo_audio_quality(cls, track: dict[str, Any]) -> tuple[bool, int, bool, int]:
        lossless = track.get("Compression_Mode") == "Lossless"
        channels = cls._whole_channels(track.get("Channels", 2))
        atmos = cls._mediainfo_atmos(track)
        bitrate = cls._safe_bitrate(track.get("BitRate", 0))
        return lossless, channels, atmos, bitrate

    @staticmethod
    def _whole_channels(value: Any) -> int:
        try:
            return int(float(str(value).split(".", 1)[0]))
        except TypeError, ValueError:
            return 0

    @staticmethod
    def _digits_value(value: Any) -> int:
        match = re.search(r"(\d+)", str(value))
        return int(match.group(1)) if match else 0

    @staticmethod
    def _mediainfo_atmos(track: dict[str, Any]) -> bool:
        additional = str(track.get("Format_AdditionalFeatures", ""))
        commercial = str(track.get("Format_Commercial", ""))
        return "JOC" in additional or "Atmos" in commercial

    @staticmethod
    def _safe_bitrate(value: Any) -> int:
        try:
            return int(value) if value else 0
        except (ValueError, TypeError) as error:
            cli_ui.warning(f"Invalid BitRate value in audio track: {value}\nUsing 0 as default. Error: {error}.")
            return 0

    async def _formatted_bdinfo_audio(self, meta: Meta, tracks: list[dict[str, Any]]) -> str:
        best = max(tracks, key=self._bdinfo_audio_quality)
        audio, _, _ = await self.audio_manager.get_audio_v2({}, meta, {"audio": [best]})
        return self._clean_audio_string(audio)

    async def _formatted_mediainfo_audio(self, meta: Meta, tracks: list[dict[str, Any]]) -> str:
        all_tracks = self._mediainfo_tracks(meta)
        general = all_tracks[0] if all_tracks else {}
        best = max(tracks, key=self._mediainfo_audio_quality)
        audio, _, _ = await self.audio_manager.get_audio_v2({"media": {"track": [general, best]}}, meta, None)
        return self._clean_audio_string(audio)

    async def get_description(self, meta: Meta, is_test: bool = False) -> dict[str, str]:
        """Generate Italian BBCode description for ShareIsland."""
        title = self._description_title(meta)
        category = str(meta.category or "MOVIE")
        info_line = await self._description_info_line(meta)
        summary, logo_url = await self._fetch_tmdb_italian(meta)
        screens = await self._format_screens_italian(meta)
        synthetic = await self._get_synthetic_mediainfo(meta)
        bbcode = self._build_bbcode(title, info_line, logo_url, summary, screens, synthetic, category, meta)
        bbcode = self._apply_custom_description_header(bbcode)
        if not is_test:
            await self._write_description(meta, bbcode)
        return {"description": bbcode}

    def _description_title(self, meta: Meta) -> str:
        italian = self._get_italian_title(meta.imdb_info)
        if italian:
            return italian
        return str(meta.title) if meta.title is not None else "Unknown"

    async def _description_info_line(self, meta: Meta) -> str:
        parts = self._technical_info_parts(meta)
        audio = await self._get_best_italian_audio_format(meta)
        if audio:
            parts.append(audio)
        language = self._description_language(meta)
        if language:
            parts.append(language)
        return " ".join(parts)

    def _technical_info_parts(self, meta: Meta) -> list[str]:
        values = (
            str(meta.resolution or ""),
            self._description_source(meta),
            self._description_codec(meta.video_codec),
            self._description_hdr(meta.hdr),
        )
        return [value for value in values if value]

    @staticmethod
    def _description_hdr(value: Any) -> str:
        text = str(value or "")
        return "" if text == "SDR" else text

    @classmethod
    def _description_source(cls, meta: Meta) -> str:
        source = cls._source_value(meta)
        return source.replace("Blu-ray", "BluRay").replace("Web", "WEB-DL")

    @classmethod
    def _description_codec(cls, value: Any) -> str:
        codec = str(value or "")
        mapped = cls._codec_alias(codec)
        return mapped if mapped else codec

    @staticmethod
    def _codec_alias(codec: str) -> str:
        aliases = (("HEVC", "x265"), ("H.265", "x265"), ("AVC", "x264"), ("H.264", "x264"))
        return next((alias for marker, alias in aliases if marker in codec), "")

    def _description_language(self, meta: Meta) -> str:
        languages = self._description_languages(meta.audio_languages)
        preferred = self._preferred_description_language(languages)
        return preferred if preferred else self._first_description_language(languages)

    def _description_languages(self, value: Any) -> list[str]:
        values = value if isinstance(value, list) else []
        names = [self._get_italian_language_name(self._get_language_code(item)) for item in values]
        return [name for name in names if name]

    @staticmethod
    def _preferred_description_language(languages: list[str]) -> str:
        for preferred in ("Italiano", "Inglese"):
            if preferred in languages:
                return preferred
        return ""

    @staticmethod
    def _first_description_language(languages: list[str]) -> str:
        return languages[0].title() if languages else ""

    def _apply_custom_description_header(self, bbcode: str) -> str:
        default = self.config.get("DEFAULT", {})
        header = default.get("custom_description_header", "") if isinstance(default, dict) else ""
        return bbcode.replace("[code]\n", f"[code]\n{header}\n\n") if header else bbcode

    async def _write_description(self, meta: Meta, bbcode: str) -> None:
        path = Path(meta.base_dir) / "tmp" / meta.uuid / f"[{self.tracker}]DESCRIPTION.txt"
        async with aiofiles.open(path, "w", encoding="utf-8") as handle:
            await handle.write(bbcode)

    async def _fetch_tmdb_italian(self, meta: Meta) -> tuple[str, str]:
        """Fetch Italian overview and best available logo from TMDb."""
        if not meta.tmdb:
            return "Riassunto non disponibile.", ""
        try:
            data = await self._tmdb_media_payload(meta)
            summary = self._tmdb_summary(data)
            logo = await self._tmdb_logo(meta, data)
            return summary, logo
        except Exception as error:
            logger.info(f"{self.tracker}: [DEBUG] TMDb fetch error: {error}", extra={"markup": False})
            return "Riassunto non disponibile.", ""

    def _tmdb_api_key(self) -> str:
        default = self.config.get("DEFAULT", {})
        return str(default.get("tmdb_api", "N/A")) if isinstance(default, dict) else "N/A"

    @staticmethod
    def _tmdb_media_type(meta: Meta) -> str:
        return "tv" if meta.category == "TV" else "movie"

    async def _tmdb_media_payload(self, meta: Meta) -> dict[str, Any]:
        media_type = self._tmdb_media_type(meta)
        url = f"https://api.themoviedb.org/3/{media_type}/{meta.tmdb}"
        response = await self._tmdb_get(url, {"api_key": self._tmdb_api_key(), "language": "it-IT"})
        if response.status_code != 200:
            return {}
        payload = response.json()
        return cast(dict[str, Any], payload) if isinstance(payload, dict) else {}

    @staticmethod
    async def _tmdb_get(url: str, params: dict[str, str]) -> Any:
        response = await asyncio.to_thread(requests.get, url, params=params, timeout=5, verify=certifi.where())
        response.encoding = "utf-8"
        return response

    @staticmethod
    def _tmdb_summary(data: dict[str, Any]) -> str:
        raw = str(data.get("overview") or "Riassunto non disponibile.")
        return " ".join(raw.split())

    async def _tmdb_logo(self, meta: Meta, _data: dict[str, Any]) -> str:
        if meta.tmdb_logo:
            return f"https://image.tmdb.org/t/p/w300/{meta.tmdb_logo}"
        logos = await self._tmdb_logo_entries(meta)
        path = self._preferred_logo_path(logos)
        return f"https://image.tmdb.org/t/p/w300{path}" if path else ""

    async def _tmdb_logo_entries(self, meta: Meta) -> list[dict[str, Any]]:
        media_type = self._tmdb_media_type(meta)
        url = f"https://api.themoviedb.org/3/{media_type}/{meta.tmdb}/images"
        response = await self._tmdb_get(url, {"api_key": self._tmdb_api_key()})
        if response.status_code != 200:
            return []
        return self._logo_entries_from_payload(response.json())

    @classmethod
    def _logo_entries_from_payload(cls, payload: Any) -> list[dict[str, Any]]:
        data = cls._payload_mapping(payload)
        return cls._mapping_items(data.get("logos", []))

    @staticmethod
    def _payload_mapping(payload: Any) -> dict[str, Any]:
        return cast(dict[str, Any], payload) if isinstance(payload, dict) else {}

    @staticmethod
    def _mapping_items(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [cast(dict[str, Any], item) for item in value if isinstance(item, dict)]

    @classmethod
    def _preferred_logo_path(cls, logos: list[dict[str, Any]]) -> str:
        italian = cls._logo_path_for_language(logos, "it")
        if italian:
            return italian
        english = cls._logo_path_for_language(logos, "en")
        if english:
            return english
        return cls._first_logo_path(logos)

    @staticmethod
    def _logo_path_for_language(logos: list[dict[str, Any]], language: str) -> str:
        return next((str(logo.get("file_path") or "") for logo in logos if logo.get("iso_639_1") == language and logo.get("file_path")), "")

    @staticmethod
    def _first_logo_path(logos: list[dict[str, Any]]) -> str:
        return next((str(logo.get("file_path") or "") for logo in logos if logo.get("file_path")), "")

    async def _format_screens_italian(self, meta: Meta) -> str:
        """Format up to six screenshots in a two-column grid."""
        links = self._screen_links(meta.image_list)
        if not links:
            return "[center]Nessuno screenshot disponibile[/center]"
        return f"[center]{self._screen_rows(links)}[/center]"

    @classmethod
    def _screen_links(cls, value: Any) -> list[str]:
        images = value if isinstance(value, list) else []
        return [link for image in images[:6] if (link := cls._screen_link(image))]

    @staticmethod
    def _screen_link(image: Any) -> str:
        if not isinstance(image, dict):
            return ""
        raw_url = str(image.get("raw_url") or "")
        if not raw_url:
            return ""
        web_url = str(image.get("web_url") or raw_url)
        return f"[url={web_url}][img=350]{raw_url}[/img][/url]"

    @staticmethod
    def _screen_rows(links: list[str]) -> str:
        rows = [" ".join(links[index : index + 2]) + " \n" for index in range(0, len(links), 2)]
        return "".join(rows)

    async def _get_synthetic_mediainfo(self, meta: Meta) -> dict[str, Any] | None:
        """Extract a compact Italian MediaInfo summary from normalized tracks."""
        try:
            tracks = self._mediainfo_tracks(meta)
            general = self._first_track(tracks, "General")
            video = self._first_track(tracks, "Video")
            audio = self._preferred_audio_track(tracks)
            texts = self._tracks_of_type(tracks, "Text")
            result = self._synthetic_general_info(meta, general)
            result.update(self._synthetic_video_info(meta, video))
            result.update(self._synthetic_audio_info(audio))
            result["subs"] = self._synthetic_subtitles(texts)
            return result
        except Exception as error:
            logger.info(f"{self.tracker}: [DEBUG] Mediainfo extraction error: {error}", extra={"markup": False})
            return None

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        if isinstance(value, dict):
            return default
        try:
            return int(value)
        except ValueError, TypeError:
            return default

    @staticmethod
    def _first_track(tracks: list[dict[str, Any]], track_type: str) -> dict[str, Any]:
        return next((track for track in tracks if track.get("@type") == track_type), {})

    @staticmethod
    def _tracks_of_type(tracks: list[dict[str, Any]], track_type: str) -> list[dict[str, Any]]:
        return [track for track in tracks if track.get("@type") == track_type]

    def _preferred_audio_track(self, tracks: list[dict[str, Any]]) -> dict[str, Any]:
        audio = self._tracks_of_type(tracks, "Audio")
        italian = next((track for track in audio if self._get_language_code(track) == "it"), None)
        return italian if italian is not None else (audio[0] if audio else {})

    def _synthetic_general_info(self, meta: Meta, general: dict[str, Any]) -> dict[str, Any]:
        duration = self._synthetic_duration(general.get("Duration", 0))
        return {
            "fn": self._synthetic_filename(meta, general),
            "size": f"{self._safe_int(general.get('FileSize', 0)) / (1024**3):.1f} GiB",
            "dur": duration,
            "total_br": f"{self._safe_int(general.get('OverallBitRate', 0)) / 1_000_000:.1f} Mb/s",
            "chap": "Si" if self._safe_int(general.get("MenuCount", 0)) > 0 else "No",
        }

    @staticmethod
    def _synthetic_filename(meta: Meta, general: dict[str, Any]) -> str:
        files = meta.filelist if isinstance(meta.filelist, list) else []
        return Path(str(files[0])).name if files else str(general.get("FileName", "file.mkv"))

    @classmethod
    def _synthetic_duration(cls, value: Any) -> str:
        seconds = cls._safe_float(value)
        hours = cls._safe_int(seconds // 3600)
        minutes = cls._safe_int((seconds % 3600) // 60)
        return f"{hours} h {minutes} min" if hours > 0 else f"{minutes} min"

    @staticmethod
    def _safe_float(value: Any) -> float:
        try:
            return float(value or 0)
        except TypeError, ValueError:
            return 0.0

    def _synthetic_video_info(self, meta: Meta, video: dict[str, Any]) -> dict[str, Any]:
        video_format = str(video.get("Format", "N/A"))
        return {
            "vid_format": video_format,
            "codec": self._synthetic_video_codec(video_format),
            "depth": f"{video.get('BitDepth', 10)} bits",
            "vid_br": f"{self._safe_int(video.get('BitRate', 0)) / 1_000_000:.1f} Mb/s",
            "res": meta.resolution if meta.resolution is not None else "N/A",
            "asp": self._synthetic_aspect_ratio(video.get("DisplayAspectRatio")),
        }

    @staticmethod
    def _synthetic_video_codec(value: str) -> str:
        upper = value.upper()
        aliases = (("HEVC", "x265"), ("AVC", "x264"), ("H.264", "x264"), ("MPEG VIDEO", "MPEG-2"), ("MPEG-2", "MPEG-2"), ("VC-1", "VC-1"), ("VC1", "VC-1"))
        return next((alias for marker, alias in aliases if marker in upper), value)

    @classmethod
    def _synthetic_aspect_ratio(cls, value: Any) -> str:
        aspect = cls._safe_float(value)
        for low, high, label in ((1.77, 1.79, "16:9"), (1.32, 1.34, "4:3"), (2.35, 2.45, "2.39:1")):
            if low <= aspect <= high:
                return label
        return f"{aspect:.2f}:1" if aspect else "N/A"

    def _synthetic_audio_info(self, audio: dict[str, Any]) -> dict[str, Any]:
        audio_format = str(audio.get("Format", "N/A")) if audio else "N/A"
        return {
            "aud_format": audio_format,
            "aud_name": self._synthetic_audio_name(audio, audio_format),
            "ch": self._synthetic_channels(audio.get("Channels", "2") if audio else "2"),
            "aud_br": f"{self._safe_int(audio.get('BitRate', 0)) / 1000:.0f} kb/s" if audio else "0 kb/s",
            "lang": self._synthetic_audio_language(audio),
        }

    def _synthetic_audio_name(self, audio: dict[str, Any], fallback: str) -> str:
        if not audio:
            return fallback
        commercial = self._string_audio_field(audio.get("Format_Commercial_IfAny"))
        if commercial:
            return commercial
        title = self._string_audio_field(audio.get("Title"))
        if title:
            return title
        return self._audio_format_details(audio)[1]

    @staticmethod
    def _string_audio_field(value: Any) -> str:
        return value if isinstance(value, str) else ""

    @staticmethod
    def _audio_format_details(audio: dict[str, Any]) -> tuple[str, str]:
        if not audio:
            return "AAC", "AAC"
        raw = str(audio.get("Format", "AAC"))
        if raw == "MLP FBA" and "atmos" in str(audio.get("Format_Commercial_IfAny", "")).lower():
            return "TrueHD Atmos", "Dolby TrueHD with Atmos"
        mapping = {
            "E-AC-3": ("DDP", "Dolby Digital Plus"),
            "AC-3": ("DD", "Dolby Digital"),
            "TrueHD": ("TrueHD", "Dolby TrueHD"),
            "MLP FBA": ("TrueHD", "Dolby TrueHD"),
            "DTS-HD MA": ("DTS-HD MA", "DTS-HD Master Audio"),
            "AAC": ("AAC", "Advanced Audio Codec"),
        }
        return mapping.get(raw, (raw, raw))

    @staticmethod
    def _synthetic_channels(value: Any) -> str:
        return {"2": "2.0", "6": "5.1", "8": "7.1"}.get(str(value), str(value))

    def _synthetic_audio_language(self, audio: dict[str, Any]) -> str:
        if not audio:
            return "Inglese"
        code = self._get_language_code(audio)
        return self._get_italian_language_name(code) if code else "Inglese"

    def _synthetic_subtitles(self, tracks: list[dict[str, Any]]) -> str:
        languages = {name for track in tracks if (name := self._subtitle_language_name(track))}
        return ", ".join(sorted(languages)) if languages else "Assenti"

    def _subtitle_language_name(self, track: dict[str, Any]) -> str:
        code = self._get_language_code(track)
        if not code:
            return ""
        name = self._get_italian_language_name(code)
        return name.title() if name else ""

    def _strip_bbcode(self, text: str) -> str:
        """Remove BBCode tags from text, keeping only plain content"""
        pattern = re.compile(r"\[/?[^\]]+\]")
        return pattern.sub("", text).strip()

    def _build_bbcode(
        self,
        title: str,
        info_line: str,
        logo_url: str,
        summary: str,
        screens: str,
        synthetic_mi: dict[str, Any] | None,
        category: str,
        meta: Meta,
    ) -> str:
        """Build the ShareIsland Italian BBCode template."""
        category_header = self._category_header(category, meta)
        release_group = self._release_group(meta)
        release_notes = self._release_notes_section(release_group, self._tonemapped_text(meta))
        shoutouts = self._shoutouts(release_group)
        logo_section = self._logo_section(logo_url)
        links_section = self._links_section(meta, category)
        mediainfo_section = self._mediainfo_section(synthetic_mi)
        return self._bbcode_template(
            title,
            info_line,
            logo_section,
            summary,
            screens,
            links_section,
            mediainfo_section,
            release_notes,
            shoutouts,
            category_header,
            str(meta.ua_signature),
        )

    @staticmethod
    def _category_header(category: str, meta: Meta) -> str:
        if category != "TV":
            return "--- FILM ---"
        return "--- SERIE TV (STAGIONE) ---" if meta.tv_pack == 1 else "--- SERIE TV (EPISODIO) ---"

    @staticmethod
    def _release_group(meta: Meta) -> str:
        return str(meta.tag or "").lstrip("-").strip()

    def _tonemapped_text(self, meta: Meta) -> str:
        if not meta.tonemapped:
            return ""
        default = self.config.get("DEFAULT", {})
        header = default.get("tonemapped_header", "") if isinstance(default, dict) else ""
        return self._strip_bbcode(str(header)) if header else ""

    @staticmethod
    def _release_notes_section(release_group: str, tonemapped_text: str) -> str:
        island = release_group.casefold() == "island"
        base = (
            "Release ShareIsland 🏴‍☠️\nFalla girare, condividila e contribuisci a mantenerla viva restando in seed il più possibile.\nGrazie per il supporto!"
            if island
            else "Nulla da aggiungere."
        )
        content = f"{base}\n{tonemapped_text}" if island and tonemapped_text else (tonemapped_text or base)
        return f"[size=13][b][color=#e8024b]--- RELEASE NOTES ---[/color][/b][/size]\n[size=11][color=#FFFFFF]{content}[/color][/size]"

    @classmethod
    def _shoutouts(cls, release_group: str) -> str:
        if cls._generic_release_group(release_group):
            return f"SHOUTOUTS : {random.choice(cls._pirate_shouts())}"  # nosec B311  # noqa: S311
        return f"SHOUTOUTS : {release_group}"

    @staticmethod
    def _generic_release_group(release_group: str) -> bool:
        return not release_group or release_group.casefold() in {"nogroup", "nogrp", "unknown", "unk"}

    @staticmethod
    def _pirate_shouts() -> tuple[str, ...]:
        return (
            "The Scene never dies",
            "Arrr! Powered by Rum & Bandwidth",
            "Seed or walk the plank!",
            "Released by Nobody — claimed by Everybody",
            "From the depths of the digital seas",
            "Where bits are free and rum flows endlessly",
            "Pirates don't ask, they share",
            "For the glory of the Scene!",
            "Scene is the paradise",
        )

    @staticmethod
    def _logo_section(logo_url: str) -> str:
        return f"[center][img=250]{logo_url}[/img][/center]\n" if logo_url else ""

    @classmethod
    def _links_section(cls, meta: Meta, category: str) -> str:
        links = cls._metadata_link_lines(meta, category)
        if not links:
            return ""
        return "\n[size=13][b][color=#e8024b]--- LINKS ---[/color][/b][/size]\n" + "".join(links) + "\n"

    @staticmethod
    def _metadata_link_lines(meta: Meta, category: str) -> list[str]:
        lines: list[str] = []
        if meta.imdb:
            lines.append(f"[size=11][color=#FFFFFF]IMDb: https://www.imdb.com/title/tt{meta.imdb}/[/color][/size]\n")
        if meta.tmdb:
            media_type = "tv" if category == "TV" else "movie"
            lines.append(f"[size=11][color=#FFFFFF]TMDb: https://www.themoviedb.org/{media_type}/{meta.tmdb}[/color][/size]\n")
        return lines

    @staticmethod
    def _mediainfo_section(synthetic: dict[str, Any] | None) -> str:
        if not synthetic:
            return ""
        return f"""[size=13][b][color=#da8d49]INFO GENERALI[/color][/b][/size]
[size=11][color=#FFFFFF]Nome File       : {synthetic["fn"]}[/color][/size]
[size=11][color=#FFFFFF]Dimensioni File : {synthetic["size"]}[/color][/size]
[size=11][color=#FFFFFF]Durata          : {synthetic["dur"]}[/color][/size]
[size=11][color=#FFFFFF]Bitrate Totale  : {synthetic["total_br"]}[/color][/size]
[size=11][color=#FFFFFF]Capitoli        : {synthetic["chap"]}[/color][/size]

[size=13][b][color=#da8d49]VIDEO[/color][/b][/size]
[size=11][color=#FFFFFF]Formato         : {synthetic["vid_format"]}[/color][/size]
[size=11][color=#FFFFFF]Compressore     : {synthetic["codec"]}[/color][/size]
[size=11][color=#FFFFFF]Profondità Bit  : {synthetic["depth"]}[/color][/size]
[size=11][color=#FFFFFF]Bitrate         : {synthetic["vid_br"]}[/color][/size]
[size=11][color=#FFFFFF]Risoluzione     : {synthetic["res"]}[/color][/size]
[size=11][color=#FFFFFF]Rapporto        : {synthetic["asp"]}[/color][/size]

[size=13][b][color=#da8d49]AUDIO[/color][/b][/size]
[size=11][color=#FFFFFF]Formato         : {synthetic["aud_format"]}[/color][/size]
[size=11][color=#FFFFFF]Nome            : {synthetic["aud_name"]}[/color][/size]
[size=11][color=#FFFFFF]Canali          : {synthetic["ch"]}[/color][/size]
[size=11][color=#FFFFFF]Bitrate         : {synthetic["aud_br"]}[/color][/size]
[size=11][color=#FFFFFF]Lingua          : {synthetic["lang"]}[/color][/size]

[size=13][b][color=#da8d49]SOTTOTITOLI[/color][/b][/size]
[size=11][color=#FFFFFF]{synthetic["subs"]}[/color][/size]

"""

    @staticmethod
    def _bbcode_template(
        title: str,
        info_line: str,
        logo_section: str,
        summary: str,
        screens: str,
        links_section: str,
        mediainfo_section: str,
        release_notes: str,
        shoutouts: str,
        category_header: str,
        signature: str,
    ) -> str:
        return f"""[code]
{logo_section}[center][size=13][b][color=#e8024b]{category_header}[/color][/b][/size][/center]
[center][size=13][b][color=#ffffff]{title}[/color][/b][/size][/center]
[center][size=13][color=#ffffff]{info_line}[/color][/size][/center]

[center][size=13][b][color=#e8024b]--- RIASSUNTO ---[/color][/b][/size][/center]
{summary}

[center][size=13][b][color=#e8024b]--- SCREENS ---[/color][/b][/size][/center]
{screens}
{links_section}{mediainfo_section}{release_notes}

[size=13][b][color=#e8024b]--- SHOUTOUTS ---[/color][/b][/size]
[size=11][color=#FFFFFF]{shoutouts}[/color][/size]

[size=13][color=#0592a3][size=16][b]BUON DOWNLOAD![/b][/size][/color][/size]

[right][size=8]{signature}[/size][/right]
[/code]"""
