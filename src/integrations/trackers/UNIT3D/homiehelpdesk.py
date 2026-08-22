# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from typing import Any

from src.domain_models.release import Meta
from src.integrations.external_apis.music_sources import DiscogsEnricher
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.common import Common
from src.integrations.trackers.UNIT3D import UNIT3D

Config = dict[str, Any]


class HomieHelpDesk(UNIT3D):
    """
    HHD is a Private Torrent Tracker for MOVIES / TV / GAMES
    """

    tracker = "HOMIEHELPDESK"
    display_name = "HomieHelpDesk"
    allows_bloated_audio = True
    base_url = "https://homiehelpdesk.net"
    banned_groups = (
        "aXXo",
        "BONE",
        "BRrip",
        "CM8",
        "CrEwSaDe",
        "CTFOH",
        "d3g",
        "dAV1nci",
        "DNL",
        "EVO",
        "FaNGDiNG0",
        "GalaxyTV",
        "HD2DVD",
        "HDTime",
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
        "PRODJi",
        "PSA",
        "RARBG",
        "Rifftrax",
        "SANTi",
        "SasukeducK",
        "ShAaNiG",
        "Sicario",
        "STUTTERSHIT",
        "TGALAXY",
        "TORRENTGALAXY",
        "TSP",
        "TSPxL",
        "ViSION",
        "VXT",
        "WAF",
        "WKS",
        "x0r",
        "YAWNiX",
        "YIFY",
        "YTS",
    )
    id_url = f"{base_url}/api/torrents/"
    upload_url = f"{base_url}/api/torrents/upload"
    search_url = f"{base_url}/api/torrents/filter"
    requests_url = f"{base_url}/api/requests/filter"
    torrent_url = f"{base_url}/torrents/"
    supported_categories = ("TV", "MOVIE", "BOOK", "GAME", "MUSIC")
    tracker_urls = ("https://homiehelpdesk.net",)

    def __init__(self, config: Config) -> None:
        super().__init__(config, tracker_name="HOMIEHELPDESK")
        self.config: Config = config
        self.common = Common(config)

    async def get_additional_checks(self, meta: Meta) -> bool:
        if meta.type == "DVDRIP":
            logger.info(
                f"{self.tracker}: [bold red]DVDRIP uploads are not "
                f"allowed on {self.tracker}.[/bold red]"
            )
            return False

        if meta.category == "MUSIC" and not self._music_upload_data(meta):
            logger.info(
                f"{self.tracker}: [bold red]Music uploads require a valid "
                "MusicBrainz or Discogs ID.[/bold red]"
            )
            return False

        return True

    @staticmethod
    def _music_release(meta: Meta) -> dict[str, Any]:
        return (
            meta.music_release if isinstance(meta.music_release, dict) else {}
        )

    @classmethod
    def _music_external_ids(cls, meta: Meta) -> dict[str, Any]:
        external_ids = cls._music_release(meta).get("external_ids", {})
        return external_ids if isinstance(external_ids, dict) else {}

    @classmethod
    def _musicbrainz_reference(cls, meta: Meta) -> str:
        external_ids = cls._music_external_ids(meta)
        return cls._first_reference(
            (
                external_ids.get("musicbrainz_release"),
                external_ids.get("musicbrainz_release_group"),
            )
        )

    @staticmethod
    def _valid_musicbrainz_reference(value: str) -> bool:
        return bool(
            re.fullmatch(
                r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}",
                value,
                re.IGNORECASE,
            )
        )

    @staticmethod
    def _first_reference(values: tuple[object, ...]) -> str:
        for value in values:
            reference = str(value or "").strip()
            if reference:
                return reference
        return ""

    @classmethod
    def _discogs_reference(cls, meta: Meta) -> str:
        if not meta.music_discogs_enabled:
            return ""
        external_ids = cls._music_external_ids(meta)
        return cls._first_reference(
            (
                external_ids.get("discogs_release_url"),
                external_ids.get("discogs_release"),
                meta.music_discogs_release_id,
                meta.music_discogs_id,
                external_ids.get("discogs_master_url"),
                external_ids.get("discogs_master"),
                meta.music_discogs_master_id,
            )
        )

    @classmethod
    def _music_upload_data(cls, meta: Meta) -> dict[str, str]:
        """Build HomieHelpDesk's music-specific external-ID payload."""
        musicbrainz = cls._musicbrainz_reference(meta)
        if cls._valid_musicbrainz_reference(musicbrainz):
            return {
                "music_exists_on_musicbrainz": "1",
                "musicbrainz": musicbrainz,
            }
        discogs = cls._discogs_reference(meta)
        if DiscogsEnricher.parse_reference(discogs):
            return {
                "music_exists_on_discogs": "1",
                "discogs": discogs,
            }
        return {}

    async def get_additional_data(self, meta: Meta) -> dict[str, str]:
        if meta.category == "MUSIC":
            return self._music_upload_data(meta)
        return {}

    @staticmethod
    def _category_mapping() -> dict[str, str]:
        return {
            "MOVIE": "1",
            "TV": "2",
            "ANIME": "3",
            "MUSIC": "4",
            "GAME": "5",
            "APPS": "6",
            "BOOKS": "7",
            "AUDIOBOOK": "8",
            "MANGA": "9",
            "ADULT": "10",
            "COMICS": "11",
            "MAGAZINE": "12",
        }

    @staticmethod
    def _book_category(meta: Meta) -> str:
        if meta.audiobook:
            return "AUDIOBOOK"
        if meta.comic:
            return "COMICS"
        if meta.manga:
            return "MANGA"
        if meta.magazine:
            return "MAGAZINE"
        return "BOOKS"

    @classmethod
    def _resolved_category(cls, meta: Meta, category: str) -> str:
        selected = category if category else meta.category
        return cls._book_category(meta) if selected == "BOOK" else selected

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
        resolved = self._resolved_category(meta, category)
        return {"category_id": category_id.get(resolved, "0")}

    @staticmethod
    def _type_mapping() -> dict[str, str]:
        return {
            "DISC": "1",
            "REMUX": "2",
            "ENCODE": "3",
            "DVDRIP": "3",
            "WEBDL": "4",
            "WEBRIP": "5",
            "HDTV": "6",
            "FLAC": "7",
            "AAC": "8",
            "ALAC": "9",
            "M4A": "10",
            "M4B": "11",
            "MP4": "12",
            "MP3": "13",
            "ISO": "14",
            "APK": "15",
            "RAR": "16",
            "7Z": "17",
            "ROM": "18",
            "PDF": "19",
            "EPUB": "20",
            "MOBI": "21",
            "CBZ": "22",
            "CBR": "22",
            "OTHER": "23",
            "PC": "25",
            "WINDOWS": "25",
            "MAC": "26",
            "LINUX": "27",
            "CONSOLE": "28",
        }

    @staticmethod
    def _normalized_type(value: object) -> str:
        return str(value or "").upper()

    @classmethod
    def _music_type(cls, meta: Meta) -> str:
        fields = cls._music_release(meta).get("fields", {})
        fields = fields if isinstance(fields, dict) else {}
        music_format = fields.get("format", {})
        value = (
            music_format.get("value", meta.format)
            if isinstance(music_format, dict)
            else meta.format
        )
        return cls._normalized_type(value)

    @staticmethod
    def _game_type(meta: Meta) -> str:
        return "CONSOLE" if meta.console_game else meta.platform.upper()

    @staticmethod
    def _book_type(resolved: str, mapping: dict[str, str]) -> str:
        return resolved if resolved in mapping else "OTHER"

    @classmethod
    def _resolved_type(
        cls, meta: Meta, explicit_type: str, mapping: dict[str, str]
    ) -> str:
        resolved = cls._normalized_type(explicit_type or meta.type)
        if meta.category == "GAME":
            return cls._game_type(meta)
        if meta.category == "MUSIC":
            return cls._music_type(meta)
        if meta.category == "BOOK":
            return cls._book_type(resolved, mapping)
        return resolved

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
        resolved = self._resolved_type(meta, type, type_id)
        return {"type_id": type_id.get(resolved, "0")}

    @staticmethod
    def _resolution_mapping() -> dict[str, str]:
        return {
            "4320p": "1",
            "2160p": "2",
            "1440p": "3",
            "1080p": "3",
            "1080i": "4",
            "720p": "5",
            "576p": "6",
            "576i": "7",
            "480p": "8",
            "480i": "9",
            "Other": "10",
        }

    async def get_resolution_id(
        self,
        meta: Meta,
        resolution: str | None = None,
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        resolution_id = self._resolution_mapping()
        if mapping_only:
            return resolution_id
        if reverse:
            return {value: key for key, value in resolution_id.items()}
        selected = meta.resolution if resolution is None else resolution
        return {"resolution_id": resolution_id.get(selected, "10")}
