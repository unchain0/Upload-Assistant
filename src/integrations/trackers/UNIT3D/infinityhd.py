# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
from typing import Any, cast

import pycountry

from src.domain_models.release import Meta
from src.integrations.media.language_adapter import languages_manager
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.common import Common
from src.integrations.trackers.UNIT3D import UNIT3D

Config = dict[str, Any]


class InfinityHD(UNIT3D):
    """
    INFINITYHD is a Private Torrent Tracker for MOVIES / TV / ANIME
    """

    tracker = "INFINITYHD"
    display_name = "InfinityHD"
    allows_bloated_audio = True
    base_url = "https://infinityhd.net"
    banned_groups = ()
    id_url = f"{base_url}/api/torrents/"
    upload_url = f"{base_url}/api/torrents/upload"
    search_url = f"{base_url}/api/torrents/filter"
    requests_url = f"{base_url}/api/requests/filter"
    torrent_url = f"{base_url}/torrents/"
    supported_categories = ("TV", "MOVIE")
    tracker_urls = ("https://infinityhd.net",)

    def __init__(self, config: Config) -> None:
        super().__init__(config, tracker_name="INFINITYHD")
        self.config: Config = config
        self.common = Common(config)

    async def get_category_id(self, meta: Meta, category: str | None = None, reverse: bool = False, mapping_only: bool = False) -> dict[str, str]:
        mapping = self._category_mapping()
        anime_id = self._anime_category_id(meta)
        if anime_id:
            return {"category_id": anime_id}
        value = self._resolve_mapping(mapping, category, meta.category, reverse=reverse, mapping_only=mapping_only, default="0")
        return value if mapping_only or reverse else {"category_id": value["value"]}

    @staticmethod
    def _category_mapping() -> dict[str, str]:
        return {"MOVIE": "1", "TV": "2", "ANIME": "3", "ANIME MOVIE": "4"}

    @staticmethod
    def _anime_category_id(meta: Meta) -> str:
        if meta.anime is not True:
            return ""
        return {"TV": "3", "MOVIE": "4"}.get(meta.category, "")

    @staticmethod
    def _resolve_mapping(
        mapping: dict[str, str],
        requested: str | None,
        fallback: str,
        *,
        reverse: bool,
        mapping_only: bool,
        default: str,
    ) -> dict[str, str]:
        if mapping_only:
            return mapping
        if reverse:
            return {value: key for key, value in mapping.items()}
        selected = requested if requested is not None else fallback
        return {"value": mapping.get(selected, default)}

    async def get_resolution_id(self, meta: Meta, resolution: str | None = None, reverse: bool = False, mapping_only: bool = False) -> dict[str, str]:
        mapping = {"4320p": "1", "2160p": "2", "1440p": "3", "1080p": "3", "1080i": "4"}
        value = self._resolve_mapping(mapping, resolution, meta.resolution, reverse=reverse, mapping_only=mapping_only, default="10")
        return value if mapping_only or reverse else {"resolution_id": value["value"]}

    def _get_language_code(self, track_or_string: Any) -> str:
        """Extract and normalize language to ISO alpha-2 code."""
        language = self._language_value(track_or_string)
        if not language:
            return ""
        normalized = self._normalize_language_key(language)
        return normalized if len(normalized) == 2 else self._lookup_language_code(normalized)

    @staticmethod
    def _language_value(track_or_string: Any) -> str:
        if not isinstance(track_or_string, dict):
            return str(track_or_string)
        lang_value = cast(dict[str, Any], track_or_string).get("Language", "")
        if isinstance(lang_value, dict):
            return str(cast(dict[str, Any], lang_value).get("String", ""))
        return str(lang_value)

    @staticmethod
    def _normalize_language_key(language: str) -> str:
        normalized = language.casefold()
        return normalized.split("-", maxsplit=1)[0]

    @staticmethod
    def _lookup_language_code(language: str) -> str:
        try:
            lang_obj = pycountry.languages.get(name=language.title()) or pycountry.languages.get(alpha_2=language) or pycountry.languages.get(alpha_3=language)
            return lang_obj.alpha_2.lower() if lang_obj else language
        except AttributeError, KeyError, LookupError:
            return language

    def original_language_check(self, meta: Meta) -> bool:
        original_languages = self._original_language_codes(meta)
        if not original_languages:
            return False
        return any(self._track_matches_original(track, original_languages) for track in self._mediainfo_tracks(meta))

    @classmethod
    def _original_language_codes(cls, meta: Meta) -> set[str]:
        return {value.casefold() for value in cls._original_language_values(meta) if cls._nonempty_string(value)}

    @staticmethod
    def _original_language_values(meta: Meta) -> list[str]:
        values = meta.original_language or []
        if isinstance(values, str):
            return [values]
        return [str(value) for value in values]

    @staticmethod
    def _nonempty_string(value: Any) -> bool:
        return isinstance(value, str) and bool(value.strip())

    @staticmethod
    def _mediainfo_tracks(meta: Meta) -> list[Any]:
        if "mediainfo" not in meta or not isinstance(meta.mediainfo, dict):
            return []
        media = meta.mediainfo.get("media", {})
        if not isinstance(media, dict):
            return []
        tracks = media.get("track", [])
        return tracks if isinstance(tracks, list) else []

    def _track_matches_original(self, track: Any, original_languages: set[str]) -> bool:
        if not isinstance(track, dict) or track.get("@type") != "Audio":
            return False
        if "commentary" in str(track.get("Title", "")).casefold():
            return False
        language = self._get_language_code(track).casefold()
        return bool(language) and language in original_languages

    async def get_name(self, meta: Meta) -> dict[str, str]:
        if not meta.language_checked:
            await languages_manager.process_desc_language(meta, tracker=self.tracker)
        audio_languages = self._string_list(meta.audio_languages)
        name = meta.name
        if audio_languages and not await languages_manager.has_english_language(audio_languages):
            name = name.replace(meta.resolution, f"{audio_languages[0].upper()} {meta.resolution}", 1)
        return {"name": name}

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        return [str(item) for item in value] if isinstance(value, list) else []

    async def get_additional_checks(self, meta: Meta) -> bool:
        if not self._basic_policy_passes(meta):
            return False
        if meta.is_disc != "BDMV" and not await self._language_policy_passes(meta):
            return False
        return await self.common.check_and_confirm_adult_media_upload(meta, self.tracker)

    def _basic_policy_passes(self, meta: Meta) -> bool:
        if meta.resolution not in {"4320p", "2160p", "1440p", "1080p", "1080i"}:
            self._log_policy_failure(meta, f"Uploads must be at least 1080 resolution for {self.tracker}.")
            return False
        if not meta.valid_mi_settings:
            self._log_policy_failure(meta, "No encoding settings in mediainfo, skipping upload.")
            return False
        return True

    async def _language_policy_passes(self, meta: Meta) -> bool:
        if not meta.language_checked:
            await languages_manager.process_desc_language(meta, tracker=self.tracker)
        if await self._has_allowed_language(meta):
            return True
        self._log_policy_failure(meta, "requires at least one English audio or subtitle track or an original language audio track.")
        return False

    async def _has_allowed_language(self, meta: Meta) -> bool:
        if self.original_language_check(meta):
            return True
        if await languages_manager.has_english_language(self._string_list(meta.audio_languages) or ""):
            return True
        return await languages_manager.has_english_language(self._string_list(meta.subtitle_languages) or "")

    def _log_policy_failure(self, meta: Meta, message: str) -> None:
        if not meta.unattended or meta.debug:
            logger.info(f"{self.tracker}: [bold red]{message}[/bold red]")
