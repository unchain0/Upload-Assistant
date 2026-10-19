# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
from typing import Any

from src.domain_models.release import Meta
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.common import Common
from src.integrations.trackers.UNIT3D import UNIT3D

Config = dict[str, Any]

CATEGORY_IDS = {
    "MOVIE": "1",
    "TV": "2",
    "GAME": "4",
    "MUSIC": "5",
    "BOOK": "6",
    "AUDIOBOOK": "7",
    "ANIME": "11",
}
CATEGORY_REVERSE = {
    "1": "MOVIE",
    "2": "TV",
    "4": "GAME",
    "5": "MUSIC",
    "6": "BOOK",
    "7": "AUDIOBOOK",
    "11": "ANIME",
}
TYPE_IDS = {
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
    "MAC": "12",
    "WINDOWS": "13",
    "BLURAY": "14",
    "ANDET": "15",
    "OTHER": "15",
    "XVID": "16",
    "MP4": "17",
    "DVDRIP": "18",
    "UHD": "19",
    "M4A": "20",
    "WAV": "21",
    "WAW": "21",
    "WMA": "22",
    "3D": "23",
    "ANDROID": "24",
    "IOS": "25",
    "H.264": "26",
    "H264": "26",
    "X264": "27",
    "PDF": "28",
    "EPUB": "29",
    "BOXSET": "30",
    "CAM": "31",
    "TS": "31",
    "CONSOLE": "32",
    "4K": "33",
    "VR": "34",
    "PODCAST": "35",
    "X265": "36",
    "H265": "36",
    "HEVC": "36",
    "VC1": "37",
    "SUBS": "38",
}
TYPE_REVERSE = {
    "1": "DISC",
    "2": "REMUX",
    "3": "ENCODE",
    "4": "WEBDL",
    "5": "WEBRIP",
    "6": "HDTV",
    "7": "FLAC",
    "8": "ALAC",
    "9": "AC3",
    "10": "AAC",
    "11": "MP3",
    "12": "MAC",
    "13": "WINDOWS",
    "14": "BLURAY",
    "15": "OTHER",
    "16": "XVID",
    "17": "MP4",
    "18": "DVDRIP",
    "19": "UHD",
    "20": "M4A",
    "21": "WAV",
    "22": "WMA",
    "23": "3D",
    "24": "ANDROID",
    "25": "IOS",
    "26": "H264",
    "27": "X264",
    "28": "PDF",
    "29": "EPUB",
    "30": "BOXSET",
    "31": "CAM",
    "32": "CONSOLE",
    "33": "4K",
    "34": "VR",
    "35": "PODCAST",
    "36": "X265",
    "37": "VC1",
    "38": "SUBS",
}
RESOLUTION_IDS = {
    "4320p": "1",
    "2160p": "2",
    "1080p": "3",
    "1080i": "4",
    "720p": "5",
    "576p": "6",
    "576i": "7",
    "480p": "8",
    "480i": "9",
    "8640p": "10",
    "1440p": "10",
    "OTHER": "10",
}
RESOLUTION_REVERSE = {
    "1": "4320p",
    "2": "2160p",
    "3": "1080p",
    "4": "1080i",
    "5": "720p",
    "6": "576p",
    "7": "576i",
    "8": "480p",
    "9": "480i",
    "10": "OTHER",
    "11": "OTHER",
}
GAME_PLATFORM_TYPES = (
    ("ANDROID", "ANDROID"),
    ("IOS", "IOS"),
    ("IPHONE", "IOS"),
    ("IPAD", "IOS"),
    ("MAC", "MAC"),
    ("WINDOWS", "WINDOWS"),
)


class PeerGarden(UNIT3D):
    """
    PeerGarden is a UNIT3D-based tracker
    """

    tracker = "PEERGARDEN"
    display_name = "PeerGarden"
    base_url = "https://peergarden.org"
    banned_groups: tuple[str, ...] = ()
    id_url = f"{base_url}/api/torrents/"
    upload_url = f"{base_url}/api/torrents/upload"
    requests_url = f"{base_url}/api/requests/filter"
    search_url = f"{base_url}/api/torrents/filter"
    torrent_url = f"{base_url}/torrents/"
    supported_categories = (
        "TV",
        "MOVIE",
        "GAME",
        "BOOK",
        "MUSIC",
    )
    tracker_urls = ("peergarden",)
    allows_dupes = True
    exact_match_only = True
    required_book_fields = ("title",)
    requires_book_cover = False

    def __init__(self, config: Config) -> None:
        """Initialize the PeerGarden tracker adapter."""
        super().__init__(config, tracker_name="PEERGARDEN")
        self.config = config
        self.common = Common(config)

    @staticmethod
    def _book_category(meta: Meta, category: str) -> str:
        if category != "BOOK":
            return category
        return "AUDIOBOOK" if meta.audiobook else "BOOK"

    @staticmethod
    def _anime_category(meta: Meta, category: str) -> str:
        if meta.anime and category == "TV":
            return "ANIME"
        return category

    @classmethod
    def _resolved_category(cls, meta: Meta, category: str) -> str:
        resolved = category if category else meta.category
        resolved = cls._book_category(meta, resolved)
        return cls._anime_category(meta, resolved)

    async def get_category_id(
        self,
        meta: Meta,
        category: str = "",
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        """Resolve Upload Assistant categories to PeerGarden category IDs."""
        if mapping_only:
            return CATEGORY_IDS
        if reverse:
            return CATEGORY_REVERSE
        resolved = self._resolved_category(meta, category)
        return {"category_id": CATEGORY_IDS.get(resolved, "0")}

    @staticmethod
    def _normalize(value: object) -> str:
        """Normalize tracker mapping inputs for lookup."""
        return str(value or "").upper().strip().lstrip(".")

    @classmethod
    def _game_type(cls, meta: Meta) -> str:
        if meta.console_game:
            return "CONSOLE"
        platform = cls._normalize(meta.platform)
        for marker, resolved in GAME_PLATFORM_TYPES:
            if marker in platform:
                return resolved
        if platform == "PC":
            return "WINDOWS"
        return "OTHER"

    @classmethod
    def _resolved_type(cls, meta: Meta) -> str:
        category = cls._normalize(meta.category)
        if category == "MUSIC":
            return cls._normalize(meta.format)
        if category == "GAME":
            return cls._game_type(meta)
        resolved = cls._normalize(meta.type)
        if category == "BOOK" and resolved not in TYPE_IDS:
            return "OTHER"
        return resolved

    async def get_type_id(
        self,
        meta: Meta,
        type: str | None = None,
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        """Resolve Upload Assistant release types to PeerGarden type IDs."""
        if mapping_only:
            return TYPE_IDS
        if reverse:
            return TYPE_REVERSE
        if type is not None and type.strip():
            return {"type_id": TYPE_IDS.get(self._normalize(type), "15")}
        resolved_type = self._resolved_type(meta)
        return {"type_id": TYPE_IDS.get(resolved_type, "15")}

    @staticmethod
    def _resolved_resolution(meta: Meta, resolution: str) -> str:
        value: object = resolution if resolution else meta.resolution
        if isinstance(value, str):
            return value.strip().lower()
        return str(value or "")

    async def get_resolution_id(
        self,
        meta: Meta,
        resolution: str = "",
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        """Resolve video resolutions to PeerGarden resolution IDs."""
        if mapping_only:
            return RESOLUTION_IDS
        if reverse:
            return RESOLUTION_REVERSE
        resolved = self._resolved_resolution(meta, resolution)
        return {"resolution_id": RESOLUTION_IDS.get(resolved, "10")}

    async def get_additional_data(self, meta: Meta) -> dict[str, Any]:
        """Build PeerGarden-specific upload flags."""
        return {
            "mod_queue_opt_in": await self.get_flag(meta, "modq"),
        }

    async def get_additional_checks(self, meta: Meta) -> bool:
        if meta.software:
            logger.info(
                f"{self.tracker}: [yellow]Software uploads are not mapped to a dedicated tracker category. Skipping upload.[/yellow]"
            )
            return False
        return True

    async def get_data(self, meta: Meta) -> dict[str, Any]:
        """Build PeerGarden-specific upload payload, filtering out prohibited fields."""
        data = await super().get_data(meta)

        # Pop prohibited administrative flags
        for field in ("free", "featured", "doubleup", "sticky"):
            data.pop(field, None)

        return data
