# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
from pathlib import Path
from typing import Any

from src.domain_models.release import Meta
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.common import Common
from src.integrations.trackers.description_builder import DescriptionBuilder
from src.integrations.trackers.UNIT3D import UNIT3D
from src.integrations.trackers.UNIT3D.capybarabr import CapybaraBR

Config = dict[str, Any]

CATEGORY_IDS = {
    "MOVIE": "1",
    "TV": "2",
    "ANIME": "3",
    "CURSOS": "4",
    "GAME": "5",
    "LIVROS": "6",
    "HQS_E_MANGAS": "7",
    "AUDIOBOOK": "8",
    "PROGRAMAS": "9",
    "MATERIAIS_DE_APOIO": "10",
    "DIVERSOS": "11",
    "MUSIC": "12",
}
_NINTENDO_TERM = bytes([110, 105, 110, 116, 101, 110, 100, 111]).decode()
TYPE_IDS = {
    "DISC": "1",
    "REMUX": "2",
    "ENCODE": "3",
    "DVDRIP": "3",
    "WEBDL": "4",
    "WEBRIP": "5",
    "HDTV": "6",
    "AZW3": "57",
    "CBR": "58",
    "CBZ": "59",
    "MOBI": "60",
    "PDF": "61",
    "EPUB": "62",
    "KFX": "63",
    "MP3": "67",
    "FLAC": "78",
    "OTHER": "68",
    "PC": "50",
    "EMULADORES_E_ROMS": "51",
    "PLAYSTATION": "52",
    "XBOX": "53",
    _NINTENDO_TERM.upper(): "54",
    "MOBILE": "55",
    "OUTRO": "76",
}
GAME_PLATFORM_TYPES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("playstation", "ps5", "ps4", "ps3", "ps2", "ps1", "psp", "vita"), "52"),
    (("xbox",), "53"),
    ((_NINTENDO_TERM, "switch", "wii", "3ds", "nds", "ds"), "54"),
    (("android", "ios", "mobile"), "55"),
    (("emulador", "rom", "emulator"), "51"),
)
RESOLUTION_IDS = {
    "4320p": "1",
    "2160p": "2",
    "1080p": "3",
    "720p": "5",
    "480p": "8",
}


class Samaritano(UNIT3D):
    """
    SAMARITANO is a BRAZILIAN Private tracker for MOVIES / TV / GENERAL
    """

    tracker = "SAMARITANO"
    display_name = "Samaritano"
    base_url = "https://samaritano.cc"
    banned_groups = ()
    id_url = f"{base_url}/api/torrents/"
    upload_url = f"{base_url}/api/torrents/upload"
    search_url = f"{base_url}/api/torrents/filter"
    torrent_url = f"{base_url}/torrents/"
    requests_url = f"{base_url}/api/requests/filter"
    supported_categories = ("TV", "MOVIE", "BOOK", "GAME")
    tracker_urls = ("https://samaritano.cc",)
    allows_bloated_audio = True
    _TV_ENDED_STATUSES: frozenset[str] = frozenset(
        {"ended", "canceled", "cancelled", "finished", "completed"}
    )
    _TV_ONGOING_STATUSES: frozenset[str] = frozenset(
        {"returning", "continuing", "in production", "upcoming", "ongoing"}
    )
    _VIDEO_EXTENSIONS: tuple[str, ...] = (
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
    )

    def __init__(self, config: Config) -> None:
        super().__init__(config, tracker_name="SAMARITANO")
        self.config: Config = config
        self.common = Common(config)

    @staticmethod
    def _video_file_count(filelist: list[Any]) -> int:
        return sum(
            1
            for item in filelist
            if Path(str(item)).suffix.lower() in Samaritano._VIDEO_EXTENSIONS
        )

    async def get_resolution_id(
        self,
        meta: Meta,
        resolution: str = "",
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        if mapping_only:
            return RESOLUTION_IDS
        if reverse:
            return {value: key for key, value in RESOLUTION_IDS.items()}
        resolved = resolution if resolution else meta.resolution
        return {"resolution_id": RESOLUTION_IDS.get(resolved, "10")}

    async def get_name(self, meta: Meta) -> dict[str, str]:
        if meta.software:
            return {"name": meta.name}
        cbr = CapybaraBR(self.config)
        cbr.tracker = self.tracker
        return await cbr.get_name(meta)

    @staticmethod
    def _selected_category(meta: Meta, category: str | None) -> str:
        if category is not None and category != "":
            return category
        return meta.category

    @staticmethod
    def _anime_category(meta: Meta, category: str) -> str:
        if meta.anime is True and category == "TV":
            return "ANIME"
        return category

    @staticmethod
    def _book_category(meta: Meta, category: str) -> str:
        if category != "BOOK":
            return category
        if meta.audiobook:
            return "AUDIOBOOK"
        if meta.comic or meta.manga:
            return "HQS_E_MANGAS"
        return "LIVROS"

    @staticmethod
    def _software_category(meta: Meta, category: str) -> str:
        if category == "GAME" and meta.software:
            return "PROGRAMAS"
        return category

    @classmethod
    def _resolved_category(cls, meta: Meta, category: str | None) -> str:
        resolved = cls._selected_category(meta, category)
        resolved = cls._anime_category(meta, resolved)
        resolved = cls._book_category(meta, resolved)
        return cls._software_category(meta, resolved)

    async def get_category_id(
        self,
        meta: Meta,
        category: str | None = None,
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        if mapping_only:
            return CATEGORY_IDS
        if reverse:
            return {value: key for key, value in CATEGORY_IDS.items()}
        resolved = self._resolved_category(meta, category)
        return {"category_id": CATEGORY_IDS.get(resolved, "0")}

    @staticmethod
    def _normalize_type(value: object) -> str:
        return str(value or "").upper().strip().lstrip(".")

    @classmethod
    def _selected_type(cls, meta: Meta, release_type: str | None) -> str:
        if release_type is not None and release_type != "":
            return cls._normalize_type(release_type)
        return cls._normalize_type(meta.type)

    @staticmethod
    def _software_type(meta: Meta) -> str:
        platform_name = str(meta.platform or "").upper()
        if platform_name in {"PC", "WINDOWS"}:
            return TYPE_IDS["PC"]
        return TYPE_IDS["OUTRO"]

    @staticmethod
    def _platform_type(platform: str) -> str:
        normalized = platform.lower()
        for markers, type_id in GAME_PLATFORM_TYPES:
            if any(marker in normalized for marker in markers):
                return type_id
        return TYPE_IDS["PC"]

    @staticmethod
    def _is_game_type(meta: Meta, resolved_type: str) -> bool:
        if resolved_type == "GAME":
            return True
        return meta.category == "GAME" and resolved_type not in TYPE_IDS

    @classmethod
    def _regular_type(cls, meta: Meta, resolved_type: str) -> str:
        if cls._is_game_type(meta, resolved_type):
            return cls._platform_type(meta.platform)
        value = TYPE_IDS.get(resolved_type, "0")
        if meta.category == "BOOK" and value == "0":
            return TYPE_IDS["OTHER"]
        return value

    async def get_type_id(
        self,
        meta: Meta,
        type: str | None = None,
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        if mapping_only:
            return TYPE_IDS
        if reverse:
            return {value: key for key, value in TYPE_IDS.items()}
        if meta.software:
            return {"type_id": self._software_type(meta)}
        resolved_type = self._selected_type(meta, type)
        return {"type_id": self._regular_type(meta, resolved_type)}

    async def get_additional_data(self, meta: Meta) -> dict[str, Any]:
        return {
            "mod_queue_opt_in": await self.get_flag(meta, "modq"),
        }

    @staticmethod
    def _normalized_filelist(
        raw_filelist: list[Any] | tuple[Any, ...] | set[Any],
    ) -> list[Any]:
        return [item for item in raw_filelist if str(item).strip() != ""]

    async def _movie_checks(self, meta: Meta, filelist: list[Any]) -> bool:
        if self._video_file_count(filelist) > 1:
            logger.info(
                f"{self.tracker}: [bold red]Movie uploads must contain only one video file.[/bold red]"
            )
            return False
        return await self.common.check_portuguese_video_requirements(
            meta, self.tracker
        )

    async def _tv_pack_checks(self, meta: Meta) -> bool:
        ended = self.common.is_tv_series_ended(
            meta,
            self._TV_ENDED_STATUSES,
            self._TV_ONGOING_STATUSES,
        )
        if ended is not True:
            logger.info(
                f"{self.tracker}: [bold red]TV season packs are allowed only for ended series.[/bold red]"
            )
            return False
        return await self.common.check_portuguese_video_requirements(
            meta, self.tracker
        )

    async def _tv_checks(self, meta: Meta, filelist: list[Any]) -> bool:
        seasons = self.common.extract_tv_seasons(filelist)
        episode_count = self.common.count_tv_episodes(filelist)
        if len(seasons) > 1:
            logger.info(
                f"{self.tracker}: [bold red]TV uploads must contain only one season.[/bold red]"
            )
            return False
        if meta.tv_pack:
            return await self._tv_pack_checks(meta)
        if episode_count > 1:
            logger.info(
                f"{self.tracker}: [bold red]Non-pack TV uploads must contain only one episode.[/bold red]"
            )
            return False
        return await self.common.check_portuguese_video_requirements(
            meta, self.tracker
        )

    def _validated_filelist(self, meta: Meta) -> list[Any] | None:
        raw_filelist = [] if meta.filelist is None else meta.filelist
        if isinstance(raw_filelist, (list, tuple, set)):
            return self._normalized_filelist(raw_filelist)
        logger.info(
            f"{self.tracker}: [bold red]File list metadata is invalid.[/bold red]"
        )
        return None

    async def get_additional_checks(self, meta: Meta) -> bool:
        filelist = self._validated_filelist(meta)
        if filelist is None:
            return False
        if meta.category == "BOOK":
            return True
        if meta.category == "MOVIE":
            return await self._movie_checks(meta, filelist)
        if meta.category == "TV":
            return await self._tv_checks(meta, filelist)
        return await self.common.check_portuguese_video_requirements(
            meta, self.tracker
        )

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
