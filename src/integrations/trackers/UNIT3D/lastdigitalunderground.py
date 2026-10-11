# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from typing import Any

import langcodes
from rich.markup import escape

from src.domain_models.release import Meta
from src.integrations.media.language_adapter import languages_manager
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.common import Common
from src.integrations.trackers.UNIT3D import UNIT3D

Config = dict[str, Any]


class LastDigitalUnderground(UNIT3D):
    """
    Last Digital Underground (LDU) is a Private Torrent Tracker for MOVIES / TV / GENERAL
    """

    tracker = "LASTDIGITALUNDERGROUND"
    display_name = "LastDigitalUnderground"
    allows_bloated_audio = True
    base_url = "https://theldu.to"
    banned_groups = ()
    id_url = f"{base_url}/api/torrents/"
    upload_url = f"{base_url}/api/torrents/upload"
    search_url = f"{base_url}/api/torrents/filter"
    torrent_url = f"{base_url}/torrents/"
    supported_categories = ("TV", "MOVIE", "BOOK")
    tracker_urls = ("theldu.to",)

    def __init__(self, config: Config) -> None:
        super().__init__(config, tracker_name="LASTDIGITALUNDERGROUND")
        self.config: Config = config
        self.common = Common(config)

    async def get_category_id(
        self,
        meta: Meta,
        category: str | None = None,
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        mapping = self._category_mapping()
        mode = self._mapping_mode(
            mapping, reverse=reverse, mapping_only=mapping_only
        )
        if mode is not None:
            return mode
        genres = self._genre_text(meta)
        if meta.category == "MOVIE":
            return {"category_id": await self._movie_category(meta, genres)}
        if meta.category == "TV":
            return {"category_id": await self._tv_category(meta, genres)}
        resolved = self._resolved_base_category(meta, category, mapping)
        return {
            "category_id": await self._non_video_category(
                meta, genres, resolved
            )
        }

    @staticmethod
    def _category_mapping() -> dict[str, str]:
        return {
            "MOVIE": "1",
            "TV": "2",
            "Anime": "8",
            "FANRES": "12",
            "MUSIC": "3",
            "EBOOK": "7",
            "AUDIOBOOK": "34",
        }

    @staticmethod
    def _mapping_mode(
        mapping: dict[str, str], *, reverse: bool, mapping_only: bool
    ) -> dict[str, str] | None:
        if mapping_only:
            return mapping
        return (
            {value: key for key, value in mapping.items()} if reverse else None
        )

    @staticmethod
    def _resolved_base_category(
        meta: Meta, category: str | None, mapping: dict[str, str]
    ) -> str:
        resolved = category if category is not None else meta.category
        if resolved == "BOOK":
            resolved = "AUDIOBOOK" if meta.audiobook else "EBOOK"
        return mapping.get(resolved, "0")

    @classmethod
    def _genre_text(cls, meta: Meta) -> str:
        keywords = ", ".join(cls._string_list(meta.keywords))
        return f"{keywords} {meta.combined_genres}".casefold()

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        return [str(item) for item in value] if isinstance(value, list) else []

    async def _non_video_category(
        self, meta: Meta, genres: str, base: str
    ) -> str:
        if "hentai" in genres:
            return "10"
        if self._contains_adult_keyword(genres):
            has_english_subs = await languages_manager.has_english_language(
                meta.subtitle_languages or []
            )
            return "6" if has_english_subs else "45"
        return base

    @staticmethod
    def _contains_adult_keyword(genres: str) -> bool:
        terms = ("xxx", "erotic", "porn", "adult", "orgy")
        return any(
            re.search(
                rf"(^|,\s*){re.escape(term)}(\s*,|$)", genres, re.IGNORECASE
            )
            for term in terms
        )

    async def _movie_category(self, meta: Meta, genres: str) -> str:
        static = self._movie_static_category(meta, genres)
        if static:
            return static
        if not await self._has_english_audio_or_subs(meta):
            return "22"
        return "27" if "dubbed" in meta.audio.casefold() else "1"

    def _movie_static_category(self, meta: Meta, genres: str) -> str:
        rules = (
            (self._is_3d(meta), "21"),
            (self._is_fanedit(meta), "12"),
            (self._is_anime(meta), "8"),
            (self._is_silent(meta), "18"),
            ("musical" in genres, "25"),
            (self._is_holiday(genres), "24"),
            ("documentary" in genres, "17"),
            (self._is_standup(genres), "20"),
            (self._is_short_film(meta, genres), "19"),
        )
        return next((category for matched, category in rules if matched), "")

    @staticmethod
    def _is_3d(meta: Meta) -> bool:
        return bool(meta.three_d) or "3d" in str(meta.edition or "").casefold()

    @staticmethod
    def _is_fanedit(meta: Meta) -> bool:
        edition = str(meta.edition or "").casefold()
        return "fanedit" in edition or "fanres" in edition

    @staticmethod
    def _is_anime(meta: Meta) -> bool:
        return bool(meta.anime) or meta.mal_id != 0

    @classmethod
    def _is_silent(cls, meta: Meta) -> bool:
        return bool(meta.silent) or any(
            "silent film" in mix.casefold() for mix in cls._sound_mixes(meta)
        )

    @staticmethod
    def _sound_mixes(meta: Meta) -> list[str]:
        if not isinstance(meta.imdb_info, dict):
            return []
        values = meta.imdb_info.get("sound_mixes", [])
        return (
            [str(item) for item in values if isinstance(item, str)]
            if isinstance(values, list)
            else []
        )

    @staticmethod
    def _is_holiday(genres: str) -> bool:
        return any(
            term in genres
            for term in (
                "holiday",
                "easter",
                "christmas",
                "halloween",
                "thanksgiving",
            )
        )

    @staticmethod
    def _is_standup(genres: str) -> bool:
        return "stand-up" in genres or "standup" in genres

    @classmethod
    def _is_short_film(cls, meta: Meta, genres: str) -> bool:
        if "short film" in genres:
            return True
        return cls._runtime_minutes(meta) < 5

    @staticmethod
    def _runtime_minutes(meta: Meta) -> int:
        if not isinstance(meta.imdb_info, dict):
            return 0
        try:
            return int(meta.imdb_info.get("runtime", 0) or 0)
        except TypeError, ValueError:
            return 0

    async def _tv_category(self, meta: Meta, genres: str) -> str:
        static = self._tv_static_category(meta, genres)
        if static:
            return static
        if not await self._has_english_audio_or_subs(meta):
            return "29"
        if meta.tv_pack:
            return "2"
        return "31" if "dubbed" in meta.audio.casefold() else "41"

    @staticmethod
    def _tv_static_category(meta: Meta, genres: str) -> str:
        if meta.anime or meta.mal_id != 0:
            return "9"
        return "40" if "documentary" in genres else ""

    @staticmethod
    async def _has_english_audio_or_subs(meta: Meta) -> bool:
        if await languages_manager.has_english_language(
            meta.audio_languages or []
        ):
            return True
        return await languages_manager.has_english_language(
            meta.subtitle_languages or []
        )

    async def get_type_id(
        self,
        meta: Meta,
        type: str | None = None,
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        mapping = self._type_mapping()
        mode = self._mapping_mode(
            mapping, reverse=reverse, mapping_only=mapping_only
        )
        if mode is not None:
            return mode
        resolved = self._normalized_type(
            type if type is not None else meta.type
        )
        value = self._base_type_id(meta, resolved, mapping)
        return {"type_id": "16" if self._is_fanedit(meta) else value}

    @staticmethod
    def _type_mapping() -> dict[str, str]:
        return {
            "DISC": "1",
            "REMUX": "2",
            "ENCODE": "3",
            "WEBDL": "4",
            "WEBRIP": "5",
            "HDTV": "6",
            "FLAC": "7",
            "ALAC": "8",
            "AC3": "9",
            "AAC": "10",
            "MP3": "11",
            "OTHER": "14",
            "EPUB": "17",
            "CBR": "18",
            "CBZ": "19",
            "CB7": "20",
            "CBT": "21",
            "CBA": "22",
            "PDP": "23",
            "AZW": "24",
            "AZW3": "25",
            "PDF": "26",
        }

    @staticmethod
    def _normalized_type(value: Any) -> str:
        return str(value or "").upper().lstrip(".")

    @staticmethod
    def _base_type_id(
        meta: Meta, resolved: str, mapping: dict[str, str]
    ) -> str:
        if meta.category == "BOOK" and resolved not in mapping:
            return "14"
        return mapping.get(resolved, "0")

    async def get_name(self, meta: Meta) -> dict[str, str]:
        category_id = (await self.get_category_id(meta))["category_id"]
        audio_iso, non_english_audio = await self._audio_language(meta)
        subtitle_label = self._subtitle_label(meta)
        name = self._language_decorated_name(
            meta.name,
            category_id,
            non_english_original=self._non_english_original(meta),
            non_english_audio=non_english_audio,
            audio_iso=audio_iso,
            subtitle_label=subtitle_label,
        )
        return {"name": name}

    @staticmethod
    def _non_english_original(meta: Meta) -> bool:
        return str(meta.original_language or "").casefold() != "en"

    async def _audio_language(self, meta: Meta) -> tuple[str, bool]:
        language = self._first_nonempty(
            self._string_list(meta.audio_languages)
        )
        if not language:
            return "", False
        iso = self._alpha3(language, "audio")
        if not iso:
            return "", False
        non_english = not await languages_manager.has_english_language(
            language
        )
        return iso, non_english

    def _subtitle_label(self, meta: Meta) -> str:
        if meta.no_subs:
            return "NoSubs"
        language = self._first_nonempty(
            self._string_list(meta.subtitle_languages)
        )
        iso = self._alpha3(language, "subtitle") if language else ""
        return f"Subs {iso}" if iso else ""

    @staticmethod
    def _first_nonempty(values: list[str]) -> str:
        return next((value.strip() for value in values if value.strip()), "")

    def _alpha3(self, language: str, kind: str) -> str:
        try:
            return langcodes.find(language).to_alpha3().upper()
        except (LookupError, AttributeError, ValueError) as error:
            logger.info(
                f"{self.tracker}: [bold red]Error extracting {kind} language: {escape(str(error))}[/bold red]"
            )
            return ""

    @classmethod
    def _language_decorated_name(
        cls,
        name: str,
        category_id: str,
        *,
        non_english_original: bool,
        non_english_audio: bool,
        audio_iso: str,
        subtitle_label: str,
    ) -> str:
        silent_name = cls._silent_subtitle_name(
            name, category_id, subtitle_label
        )
        if silent_name is not None:
            return silent_name
        if not cls._needs_language_decoration(
            non_english_original, non_english_audio
        ):
            return name
        return cls._append_language_parts(name, audio_iso, subtitle_label)

    @staticmethod
    def _silent_subtitle_name(
        name: str, category_id: str, subtitle_label: str
    ) -> str | None:
        if category_id != "18" or not subtitle_label:
            return None
        return f"{name} [{subtitle_label}]"

    @staticmethod
    def _needs_language_decoration(
        non_english_original: bool, non_english_audio: bool
    ) -> bool:
        return non_english_original or non_english_audio

    @classmethod
    def _append_language_parts(
        cls, name: str, audio_iso: str, subtitle_label: str
    ) -> str:
        parts = cls._language_parts(audio_iso, subtitle_label)
        return f"{name} {' '.join(parts)}" if parts else name

    @staticmethod
    def _language_parts(audio_iso: str, subtitle_label: str) -> list[str]:
        parts: list[str] = []
        if audio_iso:
            parts.append(f"[{audio_iso}]")
        if subtitle_label:
            parts.append(f"[{subtitle_label}]")
        return parts
