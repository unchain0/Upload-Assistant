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
        resolution_id = {
            "4320p": "1",
            "2160p": "2",
            "1080p": "3",
            "720p": "5",
            "480p": "8",
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

    async def get_name(self, meta: Meta) -> dict[str, str]:
        if meta.software:
            return {"name": meta.name}
        cbr = CapybaraBR(self.config)
        cbr.tracker = self.tracker
        return await cbr.get_name(meta)

    async def get_category_id(
        self,
        meta: Meta,
        category: str | None = None,
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        cat_map = {
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
        if mapping_only:
            return cat_map
        if reverse:
            return {v: k for k, v in cat_map.items()}

        resolved_category = (
            category
            if category is not None and category != ""
            else meta.category
        )
        if meta.anime is True and resolved_category == "TV":
            resolved_category = "ANIME"

        if resolved_category == "BOOK":
            if meta.audiobook:
                resolved_category = "AUDIOBOOK"
            elif meta.comic or meta.manga:
                resolved_category = "HQS_E_MANGAS"
            else:
                resolved_category = "LIVROS"
        elif resolved_category == "GAME" and meta.software:
            resolved_category = "PROGRAMAS"

        category_id = cat_map.get(resolved_category, "0")
        return {"category_id": category_id}

    async def get_type_id(
        self,
        meta: Meta,
        type: str | None = None,
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        nin_term = (
            bytes([110, 105, 110, 116, 101, 110, 100, 111]).decode()
        ).upper()
        type_id = {
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
            f"{nin_term}": "54",
            "MOBILE": "55",
            "OUTRO": "76",
        }
        if mapping_only:
            return type_id
        if reverse:
            return {v: k for k, v in type_id.items()}

        resolved_type = type if type is not None and type != "" else meta.type
        if isinstance(resolved_type, str):
            resolved_type = resolved_type.upper().strip().lstrip(".")

        if meta.software:
            return {
                "type_id": type_id["PC"]
                if str(meta.platform or "").upper() in {"PC", "WINDOWS"}
                else type_id["OUTRO"]
            }

        if resolved_type == "GAME" or (
            meta.category == "GAME" and resolved_type not in type_id
        ):
            platform = meta.platform.lower()
            nin_term = bytes([110, 105, 110, 116, 101, 110, 100, 111]).decode()

            if any(
                word in platform
                for word in [
                    "playstation",
                    "ps5",
                    "ps4",
                    "ps3",
                    "ps2",
                    "ps1",
                    "psp",
                    "vita",
                ]
            ):
                val = "52"
            elif "xbox" in platform:
                val = "53"
            elif any(
                word in platform
                for word in [
                    f"{nin_term}",
                    "switch",
                    "wii",
                    "3ds",
                    "nds",
                    "ds",
                ]
            ):
                val = "54"
            elif any(
                word in platform for word in ["android", "ios", "mobile"]
            ):
                val = "55"
            elif any(
                word in platform for word in ["emulador", "rom", "emulator"]
            ):
                val = "51"
            else:
                val = "50"  # PC
        else:
            val = type_id.get(resolved_type or "", "0")
            if meta.category == "BOOK" and val == "0":
                val = "68"

        return {"type_id": val}

    async def get_additional_data(self, meta: Meta) -> dict[str, Any]:
        data: dict[str, Any] = {
            "mod_queue_opt_in": await self.get_flag(meta, "modq"),
        }

        return data

    async def get_additional_checks(self, meta: Meta) -> bool:
        raw_filelist = [] if meta.filelist is None else meta.filelist
        if not isinstance(raw_filelist, (list, tuple, set)):
            logger.info(
                f"{self.tracker}: [bold red]File list metadata is invalid.[/bold red]"
            )
            return False

        if meta.category == "BOOK":
            return True

        if meta.category == "MOVIE":
            filelist = [
                item for item in raw_filelist if str(item).strip() != ""
            ]
            if self._video_file_count(filelist) > 1:
                logger.info(
                    f"{self.tracker}: [bold red]Movie uploads must contain only one video file.[/bold red]"
                )
                return False
            return await self.common.check_portuguese_video_requirements(
                meta, self.tracker
            )

        if meta.category == "TV":
            filelist = [
                item for item in raw_filelist if str(item).strip() != ""
            ]
            seasons = self.common.extract_tv_seasons(filelist)
            episode_count = self.common.count_tv_episodes(filelist)

            if len(seasons) > 1:
                logger.info(
                    f"{self.tracker}: [bold red]TV uploads must contain only one season.[/bold red]"
                )
                return False

            if meta.tv_pack:
                if (
                    self.common.is_tv_series_ended(
                        meta,
                        self._TV_ENDED_STATUSES,
                        self._TV_ONGOING_STATUSES,
                    )
                    is not True
                ):
                    logger.info(
                        f"{self.tracker}: [bold red]TV season packs are allowed only for ended series.[/bold red]"
                    )
                    return False
                return await self.common.check_portuguese_video_requirements(
                    meta, self.tracker
                )

            if not meta.tv_pack and episode_count > 1:
                logger.info(
                    f"{self.tracker}: [bold red]Non-pack TV uploads must contain only one episode.[/bold red]"
                )
                return False

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
