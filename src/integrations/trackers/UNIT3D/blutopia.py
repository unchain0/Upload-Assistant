# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
from typing import Any, cast

import cli_ui

from src.domain_models.release import Meta
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.common import Common
from src.integrations.trackers.UNIT3D import UNIT3D

CATEGORY_IDS = {"MOVIE": "1", "TV": "2", "FANRES": "3"}
TYPE_IDS = {
    "DISC": "1",
    "REMUX": "3",
    "WEBDL": "4",
    "WEBRIP": "5",
    "HDTV": "6",
    "ENCODE": "12",
}
RESOLUTION_IDS = {
    "8640p": "10",
    "4320p": "11",
    "2160p": "1",
    "1440p": "2",
    "1080p": "2",
    "1080i": "3",
    "720p": "5",
    "576p": "6",
    "576i": "7",
    "480p": "8",
    "480i": "9",
}
RAW_ONLY_GROUPS = frozenset({"AOC", "CMRG", "EVO", "TERMiNAL", "ViSION"})


class Blutopia(UNIT3D):
    """
    Blutopia (BLU) is a Private Torrent Tracker for HD MOVIES / TV
    """

    tracker = "BLUTOPIA"
    display_name = "Blutopia"
    base_url = "https://blutopia.cc"
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
        "B3LLUM",
        "BHDStudio",
        "Brrip",
        "CHD",
        "CM8",
        "CrEwSaDe",
        "d3g",
        "DeadFish",
        "DNL",
        "DTLegacy",
        "ELiTE",
        "eSc",
        "EZTV.RE",
        "EZTV",
        "F13",
        "FaNGDiNG0",
        "FGT",
        "Flights",
        "flower",
        "FRDS",
        "FUM",
        "HAiKU",
        "hallowed",
        "HD2DVD",
        "HDS",
        "HDTime",
        "Hi10",
        "ION10",
        "iPlanet",
        "JIVE",
        "KiNGDOM",
        "LAMA",
        "Leffe",
        "LEGi0N",
        "LOAD",
        "MeGusta",
        "mHD",
        "mSD",
        "NhaNc3",
        "nHD",
        "nikt0",
        "NOIVTC",
        "nSD",
        "OFT",
        "PiRaTeS",
        "playBD",
        "PlaySD",
        "playXD",
        "PRODJi",
        "RAPiDCOWS",
        "RARBG",
        "RDN",
        "REsuRRecTioN",
        "RetroPeeps",
        "RMTeam",
        "SANTi",
        "SasukeducK",
        "SicFoI",
        "SPASM",
        "SPDVD",
        "STUTTERSHIT",
        "Telly",
        "TheFarm",
        "TM",
        "TRiToN",
        "UPiNSMOKE",
        "URANiME",
        "VN_Foxcore",
        "WAF",
        "WKS",
        "x0r",
        "xRed",
        "XS",
        "YIFY",
        "ZKBL",
        "ZmN",
        "ZMNT",
    )
    id_url = f"{base_url}/api/torrents/"
    upload_url = f"{base_url}/api/torrents/upload"
    search_url = f"{base_url}/api/torrents/filter"
    requests_url = f"{base_url}/api/requests/filter"
    torrent_url = f"{base_url}/torrents/"
    supported_categories = ("TV", "MOVIE")
    tracker_urls = ("https://blutopia.cc",)
    allowed_bloated_audio_languages = ("en",)

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config, tracker_name="BLUTOPIA")
        self.config = config
        self.common = Common(config)

    @staticmethod
    def _interactive(meta: Meta) -> bool:
        return not meta.unattended or meta.unattended_confirm

    @staticmethod
    def _allows_mp4(meta: Meta, type_name: str) -> bool:
        return (
            type_name in {"WEBDL", "HDTV"}
            and "DV" in meta.hdr
            and "HDR" not in meta.hdr
        )

    @classmethod
    def _allowed_containers(cls, meta: Meta) -> list[str]:
        type_name = "" if not meta.type else meta.type.upper()
        allowed = ["mkv"]
        if type_name == "HDTV":
            allowed.append("ts")
        if cls._allows_mp4(meta, type_name):
            allowed.append("mp4")
        return allowed

    def _container_check(self, meta: Meta) -> bool:
        if meta.is_disc:
            return True
        allowed = self._allowed_containers(meta)
        if meta.container.lower() in allowed:
            return True
        logger.info(
            f"{self.tracker}: [bold red]For this release, {self.tracker} requires one of the following containers: {', '.join(a.upper() for a in allowed)}[/bold red]"
        )
        return False

    @staticmethod
    def _needs_derived_layer_prompt(meta: Meta) -> bool:
        return (
            meta.type in {"ENCODE", "REMUX"}
            and "HDR" in meta.hdr
            and "DV" in meta.hdr
            and Blutopia._interactive(meta)
        )

    def _derived_layer_check(self, meta: Meta) -> bool:
        if not self._needs_derived_layer_prompt(meta):
            return True
        logger.info(
            f"{self.tracker}: [bold red]Releases using a Dolby Vision layer from a different source have specific description requirements.[/bold red]"
        )
        logger.info(
            f"{self.tracker}: [bold red]See rule 12.5. You must have a correct pre-formatted description if this release has a derived layer[/bold red]"
        )
        if not cli_ui.ask_yes_no(
            "Do you want to upload anyway?", default=False
        ):
            return False
        if cli_ui.ask_yes_no(
            "Is this a derived layer release?", default=False
        ):
            meta.tracker_status[self.tracker]["other"] = True
        return True

    @staticmethod
    def _raw_only_group(meta: Meta) -> bool:
        return (
            meta.type != "WEBDL"
            and not meta.is_disc
            and meta.tag in RAW_ONLY_GROUPS
        )

    def _raw_group_check(self, meta: Meta) -> bool:
        if not self._raw_only_group(meta):
            return True
        if not self._interactive(meta):
            return False
        logger.info(
            f"{self.tracker}: [bold red]Group {meta.tag} is only allowed for raw type content[/bold red]"
        )
        return cli_ui.ask_yes_no(
            "Do you want to upload anyway?", default=False
        )

    def _mediainfo_check(self, meta: Meta) -> bool:
        if meta.valid_mi_settings:
            return True
        logger.info(
            f"{self.tracker}: [bold red]No encoding settings in mediainfo, skipping {self.tracker} upload.[/bold red]"
        )
        return False

    async def get_additional_checks(self, meta: Meta) -> bool:
        if not self._container_check(meta):
            return False
        if not self._derived_layer_check(meta):
            return False
        if not self._raw_group_check(meta):
            return False
        return self._mediainfo_check(meta)

    @staticmethod
    def _strip_episode_title(name: str, meta: Meta) -> str:
        if meta.category != "TV" or not meta.episode_title:
            return name
        return name.replace(
            f"{meta.episode_title} {meta.resolution}", str(meta.resolution), 1
        )

    @staticmethod
    def _imdb_values(meta: Meta) -> tuple[str, str, str]:
        imdb_info = cast(dict[str, Any], meta.imdb_info)
        return (
            str(imdb_info.get("title", "")),
            str(imdb_info.get("year", "")),
            str(imdb_info.get("aka", "")),
        )

    @staticmethod
    def _valid_imdb_aka(meta: Meta, imdb_name: str, imdb_aka: str) -> bool:
        return (
            bool(imdb_aka.strip())
            and imdb_aka != imdb_name
            and not meta.no_aka
        )

    @classmethod
    def _apply_imdb_title(
        cls, name: str, meta: Meta, imdb_name: str, imdb_aka: str
    ) -> str:
        if not imdb_name.strip():
            return name
        if meta.aka:
            name = name.replace(f"{meta.aka} ", "", 1)
        name = name.replace(str(meta.title), imdb_name, 1)
        if cls._valid_imdb_aka(meta, imdb_name, imdb_aka):
            return name.replace(imdb_name, f"{imdb_name} AKA {imdb_aka}", 1)
        return name

    @staticmethod
    def _should_replace_year(meta: Meta, imdb_year: str, year: str) -> bool:
        if meta.category == "TV" or not imdb_year.strip():
            return False
        return bool(year.strip()) and imdb_year != year

    @classmethod
    def _apply_imdb_year(cls, name: str, meta: Meta, imdb_year: str) -> str:
        year = str(meta.year) if meta.year is not None else ""
        if not cls._should_replace_year(meta, imdb_year, year):
            return name
        return name.replace(year, imdb_year, 1)

    @staticmethod
    def _apply_webdv(name: str, meta: Meta) -> str:
        return name.replace("HYBRID ", "", 1) if meta.webdv else name

    def _apply_derived_marker(self, name: str, meta: Meta) -> str:
        tracker_status = meta.tracker_status.get(self.tracker, {})
        if not tracker_status.get("other", False):
            return name
        return name.replace(
            str(meta.resolution), f"{meta.resolution} DVP5/DVP8", 1
        )

    async def get_name(self, meta: Meta) -> dict[str, str]:
        imdb_name, imdb_year, imdb_aka = self._imdb_values(meta)
        name = self._strip_episode_title(meta.name, meta)
        name = self._apply_imdb_title(name, meta, imdb_name, imdb_aka)
        name = self._apply_imdb_year(name, meta, imdb_year)
        name = self._apply_webdv(name, meta)
        return {"name": self._apply_derived_marker(name, meta)}

    async def get_additional_data(self, meta: Meta) -> dict[str, Any]:
        return {
            "mod_queue_opt_in": await self.get_flag(meta, "modq"),
        }

    @staticmethod
    def _mapping_result(
        mapping: dict[str, str], mapping_only: bool, reverse: bool
    ) -> dict[str, str] | None:
        if mapping_only:
            return mapping
        if reverse:
            return {value: key for key, value in mapping.items()}
        return None

    def _is_fanres(self, meta: Meta) -> bool:
        if meta.category == "MOVIE" and "FANRES" in meta.edition:
            return True
        return bool(meta.tracker_status[self.tracker].get("other", False))

    async def get_category_id(
        self,
        meta: Meta,
        category: str | None = None,
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        if self._is_fanres(meta):
            return {"category_id": "3"}
        mapping = self._mapping_result(CATEGORY_IDS, mapping_only, reverse)
        if mapping is not None:
            return mapping
        resolved = category if category is not None else meta.category
        return {"category_id": CATEGORY_IDS.get(resolved, "0")}

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
        resolved = type if type is not None else (meta.type or "")
        return {"type_id": TYPE_IDS.get(resolved, "0")}

    async def get_resolution_id(
        self,
        meta: Meta,
        resolution: str | None = None,
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        if mapping_only:
            return RESOLUTION_IDS
        if reverse:
            return {value: key for key, value in RESOLUTION_IDS.items()}
        resolved = resolution if resolution is not None else meta.resolution
        return {"resolution_id": RESOLUTION_IDS.get(resolved, "10")}
