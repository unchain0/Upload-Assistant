# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from typing import Any, cast

from src.domain_models.release import Meta
from src.integrations.image_hosts.rehosting import (
    ImageHostPolicy,
    RehostImagesManager,
)
from src.integrations.media.language_adapter import languages_manager
from src.integrations.trackers.common import Common
from src.integrations.trackers.UNIT3D import UNIT3D

Config = dict[str, Any]


class OnlyEncodes(UNIT3D):
    """
    OnlyEncodes+ is a Private Tracker for MOVIES / TV
    """

    tracker = "ONLYENCODES"
    display_name = "OnlyEncodes"
    allows_bloated_audio = True
    base_url = "https://onlyencodes.cc"
    approved_image_hosts = (
        "imgbox",
        "imgbb",
        "onlyimage",
        "ptscreens",
        "passtheimage",
    )
    image_host_policy = ImageHostPolicy(
        {
            "ibb.co": "imgbb",
            "imgbox.com": "imgbox",
            "onlyimage.org": "onlyimage",
            "imagebam.com": "bam",
            "ptscreens.com": "ptscreens",
            "img.passtheima.ge": "passtheimage",
        },
        approved_image_hosts,
    )
    banned_groups = (
        "[Oj]",
        "$andra",
        "0neshot",
        "3LT0N",
        "4K4U",
        "4yEo",
        "AFG",
        "AkihitoSubs",
        "Alcaide_Kira",
        "AniHLS",
        "Anime Time",
        "AnimeRG",
        "AniURL",
        "AOC",
        "AR",
        "AROMA",
        "ASW",
        "aXXo",
        "BakedFish",
        "BiTOR",
        "bonkai",
        "BRrip",
        "C4K",
        "Cleo",
        "CM8",
        "core",
        "CrEwSaDe",
        "d3g",
        "DDR",
        "DE3PM",
        "DeadFish",
        "DeeJayAhmed",
        "DNL",
        "ELiTE",
        "EMBER",
        "eSc",
        "EVO",
        "EZTV",
        "FaNGDiNG0",
        "fenix",
        "FGT",
        "FRDS",
        "FROZEN",
        "FUM",
        "GalaxyRG",
        "GalaxyRG265",
        "GalaxyTV",
        "GERMini",
        "Grym",
        "GrymLegacy",
        "HAiKU",
        "HD2DVD",
        "HDTime",
        "Hi10",
        "HiQVE",
        "ION10",
        "iPlanet",
        "iVy",
        "JacobSwaggedUp",
        "JIVE",
        "Judas",
        "KiNGDOM",
        "LAMA",
        "Leffe",
        "LiGaS",
        "LOAD",
        "LycanHD",
        "MeGusta",
        "MezRips",
        "mHD",
        "Mr.Deadpool",
        "mSD",
        "NemDiggers",
        "neoHEVC",
        "NeXus",
        "NhaNc3",
        "nHD",
        "nikt0",
        "NOIVTC",
        "nSD",
        "pahe.in",
        "PlaySD",
        "playXD",
        "PRODJi",
        "project-gxs",
        "ProRes",
        "PSA",
        "QaS",
        "Ranger",
        "RAPiDCOWS",
        "RARBG",
        "Raze",
        "RCDiVX",
        "RDN",
        "Reaktor",
        "REsuRRecTioN",
        "RMTeam",
        "ROBOTS",
        "rubix",
        "SANTi",
        "SHUTTERSHIT",
        "SM737",
        "SpaceFish",
        "SPASM",
        "SSA",
        "TBS",
        "Telly",
        "Tenrai-Sensei",
        "TERMiNAL",
        "TGx",
        "TM",
        "topaz",
        "ToVaR",
        "TSP",
        "TSPxL",
        "UnKn0wn",
        "URANiME",
        "UTR",
        "VipapkSudios",
        "ViSION",
        "WAF",
        "Wardevil",
        "x0r",
        "xRed",
        "XS",
        "YakuboEncodes",
        "YAWNiX",
        "YAWNTiC",
        "YIFY",
        "YTS",
        "YuiSubs",
        "ZKBL",
        "ZmN",
        "ZMNT",
    )
    id_url = f"{base_url}/api/torrents/"
    upload_url = f"{base_url}/api/torrents/upload"
    search_url = f"{base_url}/api/torrents/filter"
    torrent_url = f"{base_url}/torrents/"
    supported_categories = ("TV", "MOVIE")
    tracker_urls = ("https://onlyencodes.cc",)

    def __init__(self, config: Config) -> None:
        super().__init__(config, tracker_name="ONLYENCODES")
        self.config: Config = config
        self.common = Common(config)
        self.rehost_images_manager = RehostImagesManager(config)

    async def get_additional_checks(self, meta: Meta) -> bool:
        if not await self.common.check_and_confirm_adult_media_upload(
            meta, self.tracker
        ):
            return False

        return not (
            meta.is_disc != "BDMV"
            and not await self.common.check_language_requirements(
                meta,
                self.tracker,
                languages_to_check=["english"],
                check_audio=True,
                check_subtitle=True,
            )
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
    def _should_replace_imdb_year(
        meta: Meta, imdb_year: str, year: str
    ) -> bool:
        if meta.category == "TV" or not imdb_year.strip():
            return False
        return bool(year.strip()) and imdb_year != year

    @classmethod
    def _apply_imdb_year(cls, name: str, meta: Meta, imdb_year: str) -> str:
        year = str(meta.year) if meta.year is not None else ""
        if not cls._should_replace_imdb_year(meta, imdb_year, year):
            return name
        return name.replace(year, imdb_year, 1)

    @staticmethod
    def _apply_dvdrip_name(name: str, meta: Meta) -> str:
        if str(meta.type) != "DVDRIP":
            return name
        resolution = meta.resolution
        source = str(meta.source)
        audio = meta.audio
        if meta.category == "MOVIE":
            name = name.replace(
                f"{source}{meta.video_encode}", f"{resolution}", 1
            )
            return name.replace(audio, f"{audio}{meta.video_encode}", 1)
        name = name.replace(source, f"{resolution}", 1)
        return name.replace(
            str(meta.video_codec), f"{audio} {meta.video_codec}", 1
        )

    @staticmethod
    def _audio_languages(meta: Meta) -> list[str]:
        value = meta.audio_languages
        if isinstance(value, list):
            return cast(list[str], value)
        return []

    async def _apply_foreign_language(self, name: str, meta: Meta) -> str:
        if not meta.audio_languages:
            await languages_manager.process_desc_language(
                meta, tracker=self.tracker
            )
            return name
        audio_languages = self._audio_languages(meta)
        if not audio_languages or meta.is_disc == "BDMV":
            return name
        if await languages_manager.has_english_language(audio_languages):
            return name
        foreign_lang = str(audio_languages[0]).upper()
        return name.replace(
            str(meta.resolution), f"{foreign_lang} {meta.resolution}", 1
        )

    @staticmethod
    def _scale_marker(meta: Meta) -> str:
        basename = meta.basename_no_ext.upper()
        if "DS4K" in basename:
            return "DS4K"
        if "RM4K" in basename:
            return "RM4K"
        return ""

    @classmethod
    def _apply_scale(cls, name: str, meta: Meta) -> str:
        scale = cls._scale_marker(meta)
        if str(meta.type) not in {"ENCODE", "WEBDL", "WEBRIP"} or not scale:
            return name
        return name.replace(str(meta.resolution), scale, 1)

    @staticmethod
    def _needs_nogroup(tag: str) -> bool:
        if not tag:
            return True
        tag_lower = tag.lower()
        return any(
            marker in tag_lower
            for marker in ("nogrp", "nogroup", "unknown", "-unk-")
        )

    @classmethod
    def _apply_group_tag(cls, name: str, meta: Meta) -> str:
        tag = meta.tag or ""
        if not cls._needs_nogroup(tag):
            return name
        for invalid_tag in ("nogrp", "nogroup", "unknown", "-unk-"):
            name = re.sub(f"-{invalid_tag}", "", name, flags=re.IGNORECASE)
        return f"{name}-NOGRP"

    async def get_name(self, meta: Meta) -> dict[str, str]:
        imdb_name, imdb_year, imdb_aka = self._imdb_values(meta)
        name = self._apply_imdb_title(meta.name, meta, imdb_name, imdb_aka)
        name = self._apply_imdb_year(name, meta, imdb_year)
        name = self._apply_dvdrip_name(name, meta)
        name = await self._apply_foreign_language(name, meta)
        name = self._apply_scale(name, meta)
        return {"name": self._apply_group_tag(name, meta)}

    @staticmethod
    def _type_mapping() -> dict[str, str]:
        return {
            "DISC": "19",
            "REMUX": "20",
            "WEBDL": "21",
            "WEBRIP": "16",
            "ENCODE": "16",
            "DVDRIP": "16",
        }

    @staticmethod
    def _selected_type(meta: Meta, requested_type: str | None) -> str:
        selected = (
            requested_type
            if requested_type is not None and requested_type != ""
            else meta.type
        )
        normalized = str(selected).upper()
        return "ENCODE" if normalized == "DVDRIP" else normalized

    @staticmethod
    def _codec_type_id(
        type_value: str, video_codec: str, default_id: str
    ) -> str:
        if type_value not in {"WEBRIP", "ENCODE"}:
            return default_id
        return {"HEVC": "10", "AV1": "14", "AVC": "15"}.get(
            video_codec, default_id
        )

    async def get_type_id(
        self,
        meta: Meta,
        type: str | None = None,
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        mapping = self._type_mapping()
        if mapping_only:
            return mapping
        if reverse:
            return {value: key for key, value in mapping.items()}
        type_value = self._selected_type(meta, type)
        default_id = mapping.get(type_value, "16")
        video_codec = (
            meta.video_codec if meta.video_codec is not None else "N/A"
        )
        return {
            "type_id": self._codec_type_id(type_value, video_codec, default_id)
        }
