# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from typing import Any

from src.domain_models.release import Meta
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.common import Common
from src.integrations.trackers.UNIT3D import UNIT3D

Config = dict[str, Any]


class RetroMoviesClub(UNIT3D):
    """Retro Movies Club (RMC) tracker adapter."""

    tracker = "RETROMOVIESCLUB"
    display_name = "RetroMoviesClub"
    base_url = "https://retro-movies.club"
    banned_groups = (
        "[Oj]",
        "3LTON",
        "4yEo",
        "ADE",
        "AFG",
        "AniHLS",
        "AnimeRG",
        "AniURL",
        "AROMA",
        "aXXo",
        "CM8",
        "CrEwSaDe",
        "DeadFish",
        "DNL",
        "ELiTE",
        "eSc",
        "FaNGDiNG0",
        "FGT",
        "Flights",
        "FRDS",
        "FUM",
        "GalaxyRG",
        "HAiKU",
        "HDS",
        "HDTime",
        "INFINITY",
        "ION10",
        "iPlanet",
        "JIVE",
        "KiNGDOM",
        "LAMA",
        "Leffe",
        "LOAD",
        "mHD",
        "nHD",
        "NOIVTC",
        "nSD",
        "PiRaTeS",
        "RARBG",
        "RDN",
        "REsuRRecTioN",
        "RMTeam",
        "SANTi",
        "SicFoI",
        "SPASM",
        "STUTTERSHIT",
        "Telly",
        "TM",
        "UPiNSMOKE",
        "WAF",
        "xRed",
        "XS",
        "YELLO",
        "YIFY",
        "YTS",
        "ZKBL",
        "ZmN",
    )
    id_url = f"{base_url}/api/torrents/"
    upload_url = f"{base_url}/api/torrents/upload"
    search_url = f"{base_url}/api/torrents/filter"
    torrent_url = f"{base_url}/torrents/"
    supported_categories = ("MOVIE",)
    tracker_urls = ("retro-movies.club",)

    def __init__(self, config: Config) -> None:
        super().__init__(config, tracker_name=self.tracker)
        self.config = config
        self.common = Common(config)

    async def get_category_id(
        self,
        meta: Meta,
        category: str = "",
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        category_id = {"MOVIE": "1"}
        if mapping_only:
            return category_id
        if reverse:
            return {value: key for key, value in category_id.items()}
        category_value = category or meta.category
        return {"category_id": category_id.get(category_value, "0")}

    @staticmethod
    def _type_mapping() -> dict[str, str]:
        return {
            "BDMV": "1",
            "REMUX_BLURAY": "2",
            "DVD": "3",
            "REMUX_DVD": "4",
            "ENCODE": "5",
            "DVDRIP": "6",
            "WEBDL": "7",
            "WEBRIP": "8",
            "UHDTV": "9",
            "HDTV": "10",
            "TV_SD": "11",
        }

    @staticmethod
    def _type_context(
        meta: Meta, explicit_type: str
    ) -> tuple[str, str, str, str]:
        return (
            str(meta.source or "").upper(),
            str(meta.is_disc or "").upper(),
            meta.category.upper(),
            (explicit_type or str(meta.type or "")).upper(),
        )

    @staticmethod
    def _is_bluray_remux(source: str, type_value: str) -> bool:
        return type_value == "REMUX" and source in {"BLURAY", "BLU-RAY"}

    @staticmethod
    def _is_dvd_remux(source: str, type_value: str) -> bool:
        return type_value == "REMUX" and source in {
            "DVD",
            "PAL DVD",
            "NTSC DVD",
        }

    @classmethod
    def _disc_type_key(
        cls, source: str, is_disc: str, type_value: str
    ) -> str | None:
        if is_disc == "BDMV":
            return "BDMV"
        if cls._is_bluray_remux(source, type_value):
            return "REMUX_BLURAY"
        if is_disc == "DVD":
            return "DVD"
        if cls._is_dvd_remux(source, type_value):
            return "REMUX_DVD"
        return None

    @staticmethod
    def _direct_stream_type_key(type_value: str) -> str | None:
        return {
            "ENCODE": "ENCODE",
            "DVDRIP": "DVDRIP",
            "WEBDL": "WEBDL",
        }.get(type_value)

    @staticmethod
    def _is_web_release(source: str, type_value: str) -> bool:
        return type_value == "WEBRIP" or source == "WEB"

    @staticmethod
    def _tv_sd_type_key(category: str, sd: int) -> str | None:
        return "TV_SD" if category == "TV" and sd == 1 else None

    @classmethod
    def _stream_type_key(
        cls,
        source: str,
        category: str,
        type_value: str,
        sd: int,
    ) -> str | None:
        direct = cls._direct_stream_type_key(type_value)
        if direct is not None:
            return direct
        if cls._is_web_release(source, type_value):
            return "WEBRIP"
        if source == "UHDTV":
            return "UHDTV"
        if type_value == "HDTV":
            return "HDTV"
        return cls._tv_sd_type_key(category, sd)

    @classmethod
    def _type_key(cls, meta: Meta, explicit_type: str) -> str | None:
        source, is_disc, category, type_value = cls._type_context(
            meta, explicit_type
        )
        disc_key = cls._disc_type_key(source, is_disc, type_value)
        if disc_key is not None:
            return disc_key
        return cls._stream_type_key(source, category, type_value, meta.sd)

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
        key = self._type_key(meta, type)
        return {"type_id": type_id.get(key or "", "0")}

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
            "1440p": "3",
            "1080p": "3",
            "1080i": "4",
            "720p": "5",
            "576p": "6",
            "576i": "7",
            "480p": "8",
            "480i": "9",
        }
        if mapping_only:
            return resolution_id
        if reverse:
            return {value: key for key, value in resolution_id.items()}
        resolution_value = resolution or meta.resolution
        return {"resolution_id": resolution_id.get(resolution_value, "11")}

    async def get_additional_checks(self, meta: Meta) -> bool:
        if meta.category != "MOVIE":
            logger.info(
                f"{self.tracker}: [bold red]Only movies are allowed.[/bold red]"
            )
            return False
        if meta.year is not None and meta.year > 2000:
            logger.info(
                f"{self.tracker}: [bold red]Only movies released in 2000 or earlier are allowed.[/bold red]"
            )
            return False
        return True

    async def get_additional_data(self, meta: Meta) -> dict[str, str]:
        return {"mod_queue_opt_in": await self.get_flag(meta, "modq")}

    async def get_name(self, meta: Meta) -> dict[str, str]:
        name = meta.name or ""
        aka = meta.aka.strip()
        if aka:
            name = name.replace(f" {aka} ", " ")
        name = re.sub(r"[^A-Za-z0-9 ._+-]+", "", name)
        return {"name": re.sub(r"\s+", " ", name).strip()}
