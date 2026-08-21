# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from typing import Any

from src.domain_models.release import Meta
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.common import Common
from src.integrations.trackers.description_builder import DescriptionBuilder
from src.integrations.trackers.UNIT3D import UNIT3D


class CapybaraBR(UNIT3D):
    """
    CapybaraBR is a BRAZILIAN Private Torrent Tracker for MOVIES / TV / GENERAL
    """

    tracker = "CAPYBARABR"
    display_name = "CapybaraBR"
    base_url = "https://capybarabr.com"
    allows_bloated_audio = True
    banned_groups: tuple[str, ...] = ()
    banned_url = f"{base_url}/api/banned-groups"
    banned_groups_auth_mode = "api_token"
    banned_groups_response_key = "groups"
    id_url = f"{base_url}/api/torrents/"
    upload_url = f"{base_url}/api/torrents/upload"
    search_url = f"{base_url}/api/torrents/filter"
    torrent_url = f"{base_url}/torrents/"
    requests_url = f"{base_url}/api/requests/filter"
    pending_url = f"{base_url}/api/torrents/pending"
    supported_categories = ("TV", "MOVIE", "BOOK", "GAME")
    tracker_urls = ("capybarabr.com",)

    def __init__(self, config: dict[str, Any]):
        super().__init__(config, tracker_name="CAPYBARABR")
        self.config = config
        self.common = Common(config)

    async def get_category_id(
        self,
        meta: Meta,
        category: str = "",
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        mapping = self._category_mapping()
        mode = self._mapping_mode(
            mapping, reverse=reverse, mapping_only=mapping_only
        )
        if mode is not None:
            return mode
        resolved = self._resolved_category(meta, category)
        return {"category_id": mapping.get(resolved, "0")}

    @staticmethod
    def _category_mapping() -> dict[str, str]:
        return {
            "MOVIE": "1",
            "TV": "2",
            "ANIMES": "4",
            "BOOK": "11",
            "COMIC_MANGA": "10",
            "GAME": "5",
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

    @classmethod
    def _resolved_category(cls, meta: Meta, requested: str) -> str:
        resolved = cls._base_category(meta, requested)
        if cls._is_anime_tv(meta, resolved):
            return "ANIMES"
        return (
            "COMIC_MANGA"
            if cls._is_comic_category(meta, resolved)
            else resolved
        )

    @staticmethod
    def _base_category(meta: Meta, requested: str) -> str:
        return requested if requested else str(meta.category or "")

    @staticmethod
    def _is_anime_tv(meta: Meta, resolved: str) -> bool:
        return meta.anime is True and resolved == "TV"

    @classmethod
    def _is_comic_category(cls, meta: Meta, resolved: str) -> bool:
        return resolved == "BOOK" and cls._is_comic_book(meta)

    @staticmethod
    def _is_comic_book(meta: Meta) -> bool:
        return (
            str(meta.type).upper() in {"CBR", "CBZ"}
            or bool(meta.manga)
            or bool(meta.comic)
        )

    async def get_type_id(
        self,
        meta: Meta,
        type: str = "",
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        mapping = self._type_mapping()
        mode = self._mapping_mode(
            mapping, reverse=reverse, mapping_only=mapping_only
        )
        if mode is not None:
            return mode
        resolved = type or str(meta.type or "")
        if self._is_game_type(meta, resolved, mapping):
            return {"type_id": self._game_type_id(meta)}
        return {"type_id": mapping.get(resolved, "0")}

    @staticmethod
    def _type_mapping() -> dict[str, str]:
        console = (
            bytes([110, 105, 110, 116, 101, 110, 100, 111]).decode().upper()
        )
        return {
            "DISC": "1",
            "REMUX": "2",
            "ENCODE": "3",
            "DVDRIP": "3",
            "WEBDL": "4",
            "WEBRIP": "5",
            "HDTV": "6",
            "AZW3": "13",
            "CBR": "14",
            "CBZ": "15",
            "MOBI": "16",
            "PDF": "17",
            "EPUB": "18",
            "KFX": "19",
            "MP4": "21",
            "TS": "22",
            "MKV": "23",
            "MP3": "24",
            "M4B": "43",
            "FLAC": "43",
            "AAC": "43",
            "M4A": "43",
            "OGG": "43",
            "WAV": "43",
            "AUDIOBOOK": "24",
            "OUTROS": "43",
            "PC": "46",
            "PLAYSTATION": "48",
            "XBOX": "49",
            console: "50",
        }

    @staticmethod
    def _is_game_type(
        meta: Meta, resolved: str, mapping: dict[str, str]
    ) -> bool:
        return resolved == "GAME" or (
            meta.category == "GAME" and resolved not in mapping
        )

    @staticmethod
    def _game_type_id(meta: Meta) -> str:
        platform = str(meta.platform or "").casefold()
        console = bytes([110, 105, 110, 116, 101, 110, 100, 111]).decode()
        if CapybaraBR._contains_any(
            platform,
            ("playstation", "ps5", "ps4", "ps3", "ps2", "ps1", "psp", "vita"),
        ):
            return "48"
        if "xbox" in platform:
            return "49"
        if CapybaraBR._contains_any(
            platform, (console, "switch", "wii", "3ds", "nds", "ds")
        ):
            return "50"
        return "46"

    @staticmethod
    def _contains_any(value: str, needles: tuple[str, ...]) -> bool:
        return any(needle in value for needle in needles)

    async def get_resolution_id(
        self,
        meta: Meta,
        resolution: str = "",
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        resolution_id = {
            "4320p": "1",
            "2160p": "2",
            "1080p": "3",
            "1080i": "4",
            "720p": "5",
            "576p": "6",
            "576i": "7",
            "480p": "8",
            "480i": "9",
            "Other": "10",
        }

        if mapping_only:
            return resolution_id
        if reverse:
            return {v: k for k, v in resolution_id.items()}
        if resolution:
            return {"resolution_id": resolution_id.get(resolution, "10")}
        meta_resolution = meta.resolution
        resolved_id = resolution_id.get(meta_resolution, "10")
        return {"resolution_id": resolved_id}

    async def get_description(self, meta: Meta) -> dict[str, str]:
        signature = f"[right][url=https://github.com/wastaken7/Upload-Assistant][size=4]Compartilhado com {meta.ua_name} {meta.current_version} (fork)[/size][/url][/right]"
        return {
            "description": await DescriptionBuilder(
                self.tracker, self.config
            ).general_description_generator(
                meta,
                mediainfo=False,
                nfo=False,
                signature=signature,
            )
        }

    async def get_name(self, meta: Meta) -> dict[str, str]:
        name = self._name_for_category(meta, str(meta.category or ""))
        return {"name": re.sub(r"\s{2,}", " ", name)}

    def _name_for_category(self, meta: Meta, category: str) -> str:
        if category == "BOOK":
            return self._book_name(meta)
        if category == "GAME":
            return self._game_name(meta)
        if category in {"MOVIE", "TV"}:
            return self._video_name(meta)
        return str(meta.name or "")

    def _book_name(self, meta: Meta) -> str:
        title = self.common.portuguese_title_capitalization(meta.title)
        name = f"{title} - {meta.author} [{self._year_text(meta)}]{self._audiobook_suffix(meta)}"
        return f"{name}{self._book_language_suffix(meta)}"

    @staticmethod
    def _year_text(meta: Meta) -> str:
        return "" if meta.year is None else str(meta.year)

    @staticmethod
    def _audiobook_suffix(meta: Meta) -> str:
        return " [AUDIOBOOK]" if meta.audiobook else ""

    @staticmethod
    def _book_language_suffix(meta: Meta) -> str:
        language = str(meta.book_language_iso or "")
        if not language or language == "por":
            return ""
        return f" [{language.upper()}]"

    @staticmethod
    def _game_name(meta: Meta) -> str:
        tag = str(meta.tag or "").lstrip("-")
        language = CapybaraBR._game_language_tag(meta)
        subcategory = str(meta.game_subcategory or "").casefold()
        update = "Update" if subcategory == "update" else ""
        dlc = CapybaraBR._game_dlc_tag(subcategory)
        year = "" if meta.year is None else str(meta.year)
        return f"{meta.title} {update} {meta.game_version} {year} - {tag} {language}{dlc}"

    @classmethod
    def _game_language_tag(cls, meta: Meta) -> str:
        languages = cls._game_languages(meta)
        if cls._is_multilingual_portuguese(languages):
            return "[MULTI]"
        if cls._contains_language(languages, "ENGLISH"):
            return "[INGLÊS]"
        return f"[{str(meta.language or '').upper()}]"

    @staticmethod
    def _game_languages(meta: Meta) -> list[str]:
        return (
            [str(value) for value in meta.languages]
            if isinstance(meta.languages, list)
            else []
        )

    @classmethod
    def _is_multilingual_portuguese(cls, languages: list[str]) -> bool:
        if len(languages) <= 1:
            return False
        return cls._contains_language(languages, "PORTUGUESE")

    @staticmethod
    def _contains_language(languages: list[str], expected: str) -> bool:
        return any(expected in value.upper() for value in languages)

    @staticmethod
    def _game_dlc_tag(subcategory: str) -> str:
        if subcategory == "dlc":
            return " [DLC]"
        if subcategory == "full_game_dlc":
            return " [+DLC]"
        return ""

    def _video_name(self, meta: Meta) -> str:
        name = self._normalized_video_base(meta)
        name = self._localized_video_title(name, meta)
        if self.tracker == "CAPYBARABR" and meta.type == "DVDRIP":
            name = self._dvdrip_name(meta)
        name = self._apply_video_audio_tag(name, meta)
        return self._ensure_nogroup(name, meta.tag)

    @staticmethod
    def _normalized_video_base(meta: Meta) -> str:
        name = (
            str(meta.name or "")
            .replace("DD+ ", "DDP")
            .replace("DD ", "DD")
            .replace("AAC ", "AAC")
            .replace("FLAC ", "FLAC")
            .replace("Dubbed", "")
            .replace("Dual-Audio", "")
        )
        if meta.category in {"TV", "ANIMES"}:
            year = "" if meta.year is None else str(meta.year)
            if year:
                name = name.replace(f"({year})", "").replace(year, "").strip()
        return name

    @staticmethod
    def _localized_video_title(name: str, meta: Meta) -> str:
        if meta.original_language != "pt":
            return name.replace(str(meta.aka or ""), "")
        if not meta.aka:
            return name
        aka = str(meta.aka).replace("AKA", "").strip()
        return (
            name.replace(str(meta.aka), "")
            .replace(str(meta.title), aka)
            .strip()
        )

    @classmethod
    def _dvdrip_name(cls, meta: Meta) -> str:
        parts = (
            cls._dvdrip_title(meta),
            str(meta.year or ""),
            cls._episode_code(meta),
            str(meta.resolution or ""),
            "DVDRip",
            cls._normalized_audio(meta.audio),
            str(meta.video_encode or ""),
        )
        name = cls._compact_parts(parts)
        return f"{name}{meta.tag}" if meta.tag else name

    @staticmethod
    def _dvdrip_title(meta: Meta) -> str:
        if meta.original_language == "pt" and meta.aka:
            return str(meta.aka).replace("AKA", "").strip()
        return str(meta.title)

    @staticmethod
    def _episode_code(meta: Meta) -> str:
        return f"{meta.season}{meta.episode}" if meta.category == "TV" else ""

    @staticmethod
    def _normalized_audio(value: Any) -> str:
        return (
            str(value)
            .replace("DD+ ", "DDP")
            .replace("DD ", "DD")
            .replace("AAC ", "AAC")
            .replace("FLAC ", "FLAC")
        )

    @staticmethod
    def _compact_parts(parts: tuple[str, ...]) -> str:
        return " ".join(part for part in parts if part)

    def _apply_video_audio_tag(self, name: str, meta: Meta) -> str:
        if meta.is_disc:
            return name
        audio_tag = self._audio_tag(meta.audio_languages)
        if not audio_tag:
            return name
        return self._insert_audio_tag(name, audio_tag, meta)

    @classmethod
    def _audio_tag(cls, audio_languages: Any) -> str:
        languages = cls._unique_audio_languages(audio_languages)
        if not languages or not cls._has_portuguese(languages):
            return ""
        return cls._audio_count_tag(len(languages))

    @staticmethod
    def _unique_audio_languages(audio_languages: Any) -> set[Any]:
        if not audio_languages:
            return set()
        try:
            return set(audio_languages)
        except TypeError:
            return set()

    @staticmethod
    def _has_portuguese(languages: set[Any]) -> bool:
        return any(
            str(language).casefold() in {"portuguese", "português"}
            for language in languages
        )

    @staticmethod
    def _audio_count_tag(count: int) -> str:
        if count >= 3:
            return " MULTI"
        return " DUAL" if count == 2 else ""

    @classmethod
    def _insert_audio_tag(cls, name: str, audio_tag: str, meta: Meta) -> str:
        if "-" not in name:
            return f"{name}{audio_tag}"
        prefix, suffix = name.rsplit("-", 1)
        original_group = cls._source_group(meta)
        configured_group = str(meta.tag or "").lstrip("-")
        if (
            original_group
            and original_group.casefold() != configured_group.casefold()
        ):
            return f"{prefix}-{original_group}{audio_tag}-{suffix}"
        return f"{prefix}{audio_tag}-{suffix}"

    @staticmethod
    def _source_group(meta: Meta) -> str:
        for source in (meta.path, meta.uuid):
            match = re.search(
                r"-([^.-]+)\.(?:DUAL|MULTI)(?=-|\.|$)",
                str(source or ""),
                re.IGNORECASE,
            )
            if match:
                return match.group(1)
        return ""

    @classmethod
    def _ensure_nogroup(cls, name: str, tag: str | None) -> str:
        if cls._valid_group_tag(tag):
            return name
        return f"{cls._strip_invalid_group_tags(name)}-NoGroup"

    @staticmethod
    def _valid_group_tag(tag: str | None) -> bool:
        if not tag:
            return False
        lowered = str(tag).casefold()
        return not any(
            value in lowered
            for value in ("nogrp", "nogroup", "unknown", "-unk-")
        )

    @staticmethod
    def _strip_invalid_group_tags(name: str) -> str:
        result = name
        for value in ("nogrp", "nogroup", "unknown", "-unk-"):
            result = re.sub(f"-{value}", "", result, flags=re.IGNORECASE)
        return result

    async def get_additional_data(self, meta: Meta) -> dict[str, str]:
        return {
            "mod_queue_opt_in": await self.get_flag(meta, "modq"),
        }

    async def get_additional_checks(self, meta: Meta) -> bool:
        if self._missing_audiobook_narrator(meta):
            logger.info(
                f"{self.tracker}: [bold red]Narrator is required for audiobooks. Skipping upload...[/bold red]"
            )
            return False
        if meta.category not in {"MOVIE", "TV"}:
            return True
        return await self._video_additional_checks(meta)

    @staticmethod
    def _missing_audiobook_narrator(meta: Meta) -> bool:
        return (
            meta.category == "BOOK"
            and bool(meta.audiobook)
            and not bool(meta.narrator)
        )

    async def _video_additional_checks(self, meta: Meta) -> bool:
        release_type = str(meta.type or "").casefold()
        if release_type == "encode" and not meta.has_encode_settings:
            logger.info(
                f"{self.tracker}: [bold red]'Encode settings' field in the MediaInfo is required for encodes. Skipping upload...[/bold red]"
            )
            return False
        if self._remux_requires_bdinfo(meta, release_type):
            logger.info(
                f"{self.tracker}: [bold red]BDInfo is required for Blu-ray remuxes that include 'Encode settings' field in the MediaInfo. "
                "You can add BDInfo to the description using -df (path/to/file.txt) or -pb (Pastebin link). Skipping upload...[/bold red]"
            )
            return False
        return await self.common.check_portuguese_video_requirements(
            meta, self.tracker
        )

    def _remux_requires_bdinfo(self, meta: Meta, release_type: str) -> bool:
        if release_type != "remux" or str(
            meta.source or ""
        ).casefold() not in {"bluray", "blu-ray"}:
            return False
        if not meta.has_encode_settings:
            return False
        return not self.common.has_bdinfo(self._description_text(meta))

    @staticmethod
    def _description_text(meta: Meta) -> str:
        return f"{meta.description}\n{meta.description_link_content}\n{meta.description_file_content}"
