# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from pathlib import Path
from typing import Any, cast

import cli_ui

from src.domain_models.release import Meta
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.common import Common
from src.integrations.trackers.UNIT3D import UNIT3D

Config = dict[str, Any]

CATEGORY_IDS = {
    "MOVIE": "1",
    "TV": "2",
    "GAME": "3",
    "MUSIC": "5",
    "EBOOK": "7",
    "BOOK": "7",
    "AUDIOBOOK": "9",
}
TYPE_IDS = {
    "DISC": "1",
    "REMUX": "2",
    "ENCODE": "3",
    "DVDRIP": "3",
    "WEBDL": "4",
    "WEBRIP": "5",
    "HDTV": "6",
    "FLAC": "11",
    "FLAC PACK": "30",
    "FLAC_PACK": "30",
    "MP3": "13",
    "MP3 PACK": "31",
    "MP3_PACK": "31",
    "KARAOKE": "43",
    "MUSIC VIDEO": "55",
    "MUSIC VIDEOS": "55",
    "SAMPLES & SFX": "48",
    "SAMPLES_AND_SFX": "48",
    "BOOK": "20",
    "COMIC": "40",
    "DOCUMENT": "49",
    "MAGAZINE": "41",
    "NEWSPAPER": "42",
    "NES": "45",
    "NINTENDO SWITCH": "15",
    "SWITCH": "15",
    "PS1": "50",
    "PS2": "51",
    "PS3": "52",
    "PS4": "28",
    "WII": "44",
    "XBOX": "35",
    "XBOX 360": "53",
    "XBOX ONE": "54",
    "OTHER": "17",
}
SPORT_PATTERNS = (
    r"EFL.*",
    r".*mlb.*",
    r".*formula1.*",
    r".*nascar.*",
    r".*nfl.*",
    r".*wrc.*",
    r".*wwe.*",
    r".*fifa.*",
    r".*boxing.*",
    r".*rally.*",
    r".*ufc.*",
    r".*ppv.*",
    r".*uefa.*",
    r".*nhl.*",
    r".*nba.*",
    r".*motogp.*",
    r".*moto2.*",
    r".*moto3.*",
    r".*gamenight.*",
    r".*darksport.*",
    r".*overtake.*",
)
GAME_PLATFORM_TYPES: tuple[tuple[str, str], ...] = (
    ("XBOX 360", "XBOX 360"),
    ("XBOX ONE", "XBOX ONE"),
    ("XBOX", "XBOX"),
    ("PLAYSTATION 4", "PS4"),
    ("PS4", "PS4"),
    ("PLAYSTATION 3", "PS3"),
    ("PS3", "PS3"),
    ("PLAYSTATION 2", "PS2"),
    ("PS2", "PS2"),
    ("PLAYSTATION", "PS1"),
    ("PS1", "PS1"),
    ("SWITCH", "SWITCH"),
    ("WII", "WII"),
    ("NES", "NES"),
)
VIDEO_EXTENSIONS = frozenset({".mkv", ".mp4", ".avi", ".ts"})
ALLOWED_HD_RESOLUTIONS = frozenset(
    {"8640p", "4320p", "2160p", "1440p", "1080p", "1080i"}
)
DISALLOWED_KEYWORDS = frozenset({"xxx", "erotic", "porn"})
DISALLOWED_GENRES = frozenset({"adult", "erotica"})


class Seedpool(UNIT3D):
    """Seedpool UNIT3D tracker adapter."""

    tracker = "SEEDPOOL"
    display_name = "Seedpool"
    base_url = "https://seedpool.org"
    banned_groups = ()
    id_url = f"{base_url}/api/torrents/"
    upload_url = f"{base_url}/api/torrents/upload"
    search_url = f"{base_url}/api/torrents/filter"
    torrent_url = f"{base_url}/torrents/"
    supported_categories = ("TV", "MOVIE", "BOOK", "GAME", "MUSIC")
    tracker_urls = ("https://seedpool.org",)
    allows_bloated_audio = True

    def __init__(self, config: Config) -> None:
        super().__init__(config, tracker_name="SEEDPOOL")
        self.config = config
        self.common = Common(config)

    @staticmethod
    def _mapping_result(
        mapping: dict[str, str], mapping_only: bool, reverse: bool
    ) -> dict[str, str] | None:
        if mapping_only:
            return mapping
        if reverse:
            return {value: key for key, value in mapping.items()}
        return None

    @staticmethod
    def contains_sports_patterns(release_title: str) -> bool:
        return any(
            re.search(pattern, release_title, re.IGNORECASE)
            for pattern in SPORT_PATTERNS
        )

    @staticmethod
    def _book_category(meta: Meta, category_name: str) -> str:
        if category_name == "BOOK" and meta.audiobook:
            return "AUDIOBOOK"
        return category_name

    @staticmethod
    def _anime_category(meta: Meta, category_name: str) -> str:
        if (meta.mal_id or 0) != 0 and category_name == "TV":
            return "ANIME"
        return category_name

    def _sports_category(self, meta: Meta, category_name: str) -> str:
        if category_name not in {"MOVIE", "TV"}:
            return category_name
        if self.contains_sports_patterns(meta.name):
            return "SPORTS"
        return category_name

    def _resolved_category(self, meta: Meta, category: str | None) -> str:
        resolved = str(category or meta.category).upper()
        resolved = self._book_category(meta, resolved)
        resolved = self._anime_category(meta, resolved)
        return self._sports_category(meta, resolved)

    async def get_category_id(
        self,
        meta: Meta,
        category: str | None = None,
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        mapping = self._mapping_result(CATEGORY_IDS, mapping_only, reverse)
        if mapping is not None:
            return mapping
        resolved = self._resolved_category(meta, category)
        if resolved == "ANIME":
            return {"category_id": "6"}
        if resolved == "SPORTS":
            return {"category_id": "8"}
        return {"category_id": CATEGORY_IDS.get(resolved, "0")}

    @staticmethod
    def _normalize(value: object) -> str:
        return str(value or "").upper().strip().lstrip(".")

    @classmethod
    def _game_type(cls, meta: Meta) -> str:
        platform = cls._normalize(meta.platform)
        for marker, type_value in GAME_PLATFORM_TYPES:
            if marker in platform:
                return type_value
        return "OTHER"

    @classmethod
    def _book_type(cls, meta: Meta) -> str:
        if meta.audiobook:
            return cls._normalize(meta.format or meta.type)
        if meta.comic or cls._normalize(meta.type) in {"CBR", "CBZ"}:
            return "COMIC"
        return "BOOK"

    @classmethod
    def _resolved_type(cls, meta: Meta) -> str:
        if meta.category == "GAME":
            return cls._game_type(meta)
        if meta.category == "MUSIC":
            return cls._normalize(meta.format)
        if meta.category == "BOOK":
            return cls._book_type(meta)
        return cls._normalize(meta.type)

    @staticmethod
    def _fallback_type_id(meta: Meta) -> str:
        return "17" if meta.category in {"BOOK", "GAME", "MUSIC"} else "0"

    async def get_type_id(
        self,
        meta: Meta,
        type: str | None = None,
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        mapping = self._mapping_result(TYPE_IDS, mapping_only, reverse)
        if mapping is not None:
            return mapping
        if type:
            return {"type_id": TYPE_IDS.get(self._normalize(type), "0")}
        resolved = self._resolved_type(meta)
        return {
            "type_id": TYPE_IDS.get(resolved, self._fallback_type_id(meta))
        }

    @staticmethod
    def _scene_generated_name(meta: Meta) -> str:
        if meta.scene_name:
            return meta.scene_name
        return meta.basename_no_ext.replace(" ", ".")

    @staticmethod
    def _regular_generated_name(meta: Meta) -> str:
        if (meta.mal_id or 0) != 0:
            return meta.name.replace(" ", ".")
        return meta.basename_no_ext.replace(" ", ".")

    @classmethod
    def _generated_name(cls, meta: Meta) -> str:
        if meta.scene:
            return cls._scene_generated_name(meta)
        if bool(meta.is_disc):
            return meta.name.replace(" ", ".")
        return cls._regular_generated_name(meta)

    @staticmethod
    def _strip_video_extension(name: str) -> str:
        path = Path(name)
        if path.suffix.lower() not in VIDEO_EXTENSIONS:
            return name
        return path.stem.replace(" ", ".")

    async def get_name(self, meta: Meta) -> dict[str, str]:
        return {
            "name": self._strip_video_extension(self._generated_name(meta))
        }

    @staticmethod
    def _interactive(meta: Meta) -> bool:
        return not meta.unattended or meta.unattended_confirm

    def _resolution_check(self, meta: Meta) -> bool:
        if meta.category not in {"MOVIE", "TV"}:
            return True
        if meta.resolution in ALLOWED_HD_RESOLUTIONS:
            return True
        logger.info(
            f"{self.tracker}: [bold red]Only 1080 or higher resolutions allowed at {self.tracker}.[/bold red]"
        )
        if not self._interactive(meta):
            return False
        return cli_ui.ask_yes_no(
            "Do you want to upload anyway?", default=False
        )

    @staticmethod
    def _normalized_genres(meta: Meta) -> list[str]:
        value = meta.combined_genres
        if isinstance(value, str):
            return [
                genre.strip() for genre in value.split(",") if genre.strip()
            ]
        return [str(genre) for genre in cast(list[Any], value)]

    @staticmethod
    def _normalized_keywords(meta: Meta) -> list[str]:
        return [
            keyword.strip().lower()
            for keyword in meta.keywords
            if keyword.strip()
        ]

    @staticmethod
    def _has_disallowed_keyword(keywords: list[str]) -> bool:
        return any(keyword in DISALLOWED_KEYWORDS for keyword in keywords)

    @staticmethod
    def _has_disallowed_genre(genres: list[str]) -> bool:
        return any(genre.lower() in DISALLOWED_GENRES for genre in genres)

    @classmethod
    def _has_disallowed_content(cls, meta: Meta) -> bool:
        if cls._has_disallowed_keyword(cls._normalized_keywords(meta)):
            return True
        return cls._has_disallowed_genre(cls._normalized_genres(meta))

    def _content_check(self, meta: Meta) -> bool:
        if not self._has_disallowed_content(meta):
            return True
        if not self._interactive(meta):
            return False
        logger.info(
            f"{self.tracker}: [bold red]Porn/xxx is not allowed at {self.tracker}.[/bold red]"
        )
        return cli_ui.ask_yes_no(
            "Do you want to upload anyway?", default=False
        )

    async def get_additional_checks(self, meta: Meta) -> bool:
        if not self._resolution_check(meta):
            return False
        return self._content_check(meta)

    async def get_additional_data(self, meta: Meta) -> dict[str, Any]:
        return {"mod_queue_opt_in": await self.get_flag(meta, "modq")}
