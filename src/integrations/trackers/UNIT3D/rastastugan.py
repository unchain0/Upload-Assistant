# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
from typing import Any

from src.domain_models.release import Meta
from src.integrations.external_apis.tmdb import TmdbManager
from src.integrations.trackers.common import Common
from src.integrations.trackers.UNIT3D import UNIT3D

Config = dict[str, Any]


class Rastastugan(UNIT3D):
    """
    Rastastugan is a NORDIC Private Torrent Tracker for MOVIES / TV / GENERAL
    """

    tracker = "RASTASTUGAN"
    display_name = "Rastastugan"
    allows_bloated_audio = True
    base_url = "https://rastastugan.org"
    banned_groups = (
        "GalaxyRG",
        "INFINITY",
        "LAMA",
        "MeGUSTA",
        "NAHOM",
        "RARBG",
        "YiFY",
        "YTS",
    )
    id_url = f"{base_url}/api/torrents/"
    upload_url = f"{base_url}/api/torrents/upload"
    search_url = f"{base_url}/api/torrents/filter"
    requests_url = f"{base_url}/api/requests/filter"
    torrent_url = f"{base_url}/torrents/"
    supported_categories = ("TV", "MOVIE", "BOOK", "GAME", "MUSIC")
    tracker_urls = ("https://rastastugan.org",)

    def __init__(self, config: Config) -> None:
        super().__init__(config, tracker_name="RASTASTUGAN")
        self.config: Config = config
        self.tmdb_manager = TmdbManager(config)
        self.common = Common(config)

    async def get_additional_checks(self, meta: Meta) -> bool:
        nordic_languages = [
            "danish",
            "swedish",
            "norwegian",
            "icelandic",
            "finnish",
            "english",
        ]
        return await self.common.check_language_requirements(
            meta,
            self.tracker,
            languages_to_check=nordic_languages,
            check_audio=True,
            check_subtitle=True,
        )

    @staticmethod
    def _category_mapping() -> dict[str, str]:
        return {
            "MOVIE": "1",
            "TV": "2",
            "MUSIC": "3",
            "AUDIOBOOK": "7",
            "BOOK": "8",
            "GAME": "5",
        }

    @staticmethod
    def _selected_category(meta: Meta, category: str) -> str:
        if category:
            return category
        return "AUDIOBOOK" if meta.audiobook else meta.category

    async def get_category_id(
        self,
        meta: Meta,
        category: str = "",
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        category_id = self._category_mapping()
        if mapping_only:
            return category_id
        if reverse:
            return {value: key for key, value in category_id.items()}
        selected = self._selected_category(meta, category)
        return {"category_id": category_id.get(selected, "0")}

    @staticmethod
    def _type_mapping() -> dict[str, str]:
        return {
            "DISC": "1",
            "REMUX": "2",
            "WEBDL": "4",
            "WEBRIP": "5",
            "HDTV": "6",
            "ENCODE": "3",
            "DVDRIP": "3",
            "CAM": "13",
            "FLAC": "7",
            "MP3": "8",
            "M4A": "14",
            "M4B": "20",
            "MAC": "9",
            "WINDOWS": "10",
            "CONSOLE": "11",
            "LINUX": "18",
            "EPUB": "15",
            "PDF": "16",
            "MOBI": "17",
            "STL": "21",
            "OTHER": "19",
        }

    @staticmethod
    def _normalized_type(value: object) -> str:
        return str(value or "").upper().strip().lstrip(".")

    @staticmethod
    def _windows_game_platform(platform: str) -> bool:
        return "windows" in platform or "pc" in platform

    @staticmethod
    def _named_game_platform_type_id(platform: str) -> str | None:
        if "mac" in platform:
            return "9"
        if "linux" in platform:
            return "18"
        return None

    @classmethod
    def _game_platform_type_id(cls, meta: Meta) -> str | None:
        platform = meta.platform.lower()
        named_type = cls._named_game_platform_type_id(platform)
        if named_type is not None:
            return named_type
        if cls._windows_game_platform(platform):
            return "10"
        return "11" if meta.console_game else None

    @classmethod
    def _game_type_id(
        cls, meta: Meta, type_id: dict[str, str], meta_type: str
    ) -> str:
        platform_type = cls._game_platform_type_id(meta)
        if platform_type is not None:
            return platform_type
        return type_id.get(meta_type, "19")

    @staticmethod
    def _book_type_id(resolved_id: str) -> str:
        return "19" if resolved_id == "0" else resolved_id

    @classmethod
    def _category_type_id(
        cls,
        meta: Meta,
        type_id: dict[str, str],
        meta_type: str,
    ) -> str:
        resolved_id = type_id.get(meta_type, "0")
        if meta.category == "MUSIC":
            return type_id.get(meta.format.upper(), "19")
        if meta.category == "GAME":
            return cls._game_type_id(meta, type_id, meta_type)
        if meta.category in ("BOOK", "AUDIOBOOK"):
            return cls._book_type_id(resolved_id)
        return resolved_id

    async def get_type_id(
        self,
        meta: Meta,
        type: str = "",
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        type_id = self._type_mapping()
        if mapping_only:
            return type_id
        if reverse:
            return {value: key for key, value in type_id.items()}
        if type:
            resolved_type = self._normalized_type(type)
            return {"type_id": type_id.get(resolved_type, "0")}
        meta_type = self._normalized_type(meta.type)
        return {"type_id": self._category_type_id(meta, type_id, meta_type)}
