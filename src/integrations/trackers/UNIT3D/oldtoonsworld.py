# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
from typing import Any

import cli_ui

from src.domain_models.release import Meta
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.common import Common
from src.integrations.trackers.UNIT3D import UNIT3D

Config = dict[str, Any]


class OldToonsWorld(UNIT3D):
    """
    Old Toons World (OTW) is a Private Torrent Tracker for ANIMATED MOVIES / TV
    """

    tracker = "OLDTOONSWORLD"
    display_name = "OldToonsWorld"
    allows_bloated_audio = True
    base_url = "https://oldtoons.world"
    banned_groups = (
        "[Oj]",
        "3LTON",
        "4f8c4100292",
        "4yEo",
        "ADE",
        "AFG",
        "AniHLS",
        "AnimeRG",
        "AniURL",
        "AROMA",
        "aXXo",
        "Azkars",
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
        "HD2DVD",
        "HDS",
        "HDTime",
        "Hi10",
        "INFINITY",
        "ION10",
        "iPlanet",
        "JIVE",
        "KiNGDOM",
        "LAMA",
        "Leffe",
        "LOAD",
        "mHD",
        "NhaNc3",
        "nHD",
        "NOIVTC",
        "nSD",
        "PiRaTeS",
        "PRODJi",
        "RAPiDCOWS",
        "RARBG",
        "RDN",
        "REsuRRecTioN",
        "RMTeam",
        "SANTi",
        "SicFoI",
        "SPASM",
        "STUTTERSHIT",
        "Sync0rdi",
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
    requests_url = f"{base_url}/api/requests/filter"
    torrent_url = f"{base_url}/torrents/"
    supported_categories = ("TV", "MOVIE")
    tracker_urls = ("oldtoons.world",)

    def __init__(self, config: Config) -> None:
        super().__init__(config, tracker_name="OLDTOONSWORLD")
        self.config: Config = config
        self.common = Common(config)

    async def get_additional_checks(self, meta: Meta) -> bool:
        genres = self._normalized_genres(meta.combined_genres)
        if not self._genre_policy_passes(meta, genres):
            return False
        if not self._adult_policy_passes(meta, genres):
            return False
        if not self._reality_policy_passes(meta, genres):
            return False
        return self._group_policy_passes(meta)

    @classmethod
    def _normalized_genres(cls, value: Any) -> list[str]:
        if isinstance(value, list):
            return cls._clean_strings(value)
        return cls._split_genre_string(value)

    @staticmethod
    def _clean_strings(values: list[Any]) -> list[str]:
        return [text for item in values if (text := str(item).strip())]

    @staticmethod
    def _split_genre_string(value: Any) -> list[str]:
        return [text for item in str(value or "").split(",") if (text := item.strip())]

    def _genre_policy_passes(self, meta: Meta, genres: list[str]) -> bool:
        if any(genre in genres for genre in ("Animation", "Family")):
            return True
        return self._confirm_policy_override(meta, "Genre does not match Animation or Family for OldToonsWorld.")

    def _adult_policy_passes(self, meta: Meta, genres: list[str]) -> bool:
        keywords = ("xxx", "erotic", "porn", "adult", "orgy", "hentai", "adult animation", "softcore")
        if not self._contains_metadata_keyword(meta, genres, keywords):
            return True
        return self._confirm_policy_override(meta, "Adult animation not allowed at OldToonsWorld.")

    def _reality_policy_passes(self, meta: Meta, genres: list[str]) -> bool:
        keywords = ("reality", "game show", "game-show", "reality tv", "reality television")
        if not self._contains_metadata_keyword(meta, genres, keywords):
            return True
        return self._confirm_policy_override(meta, "Reality / Game Show content not allowed at OldToonsWorld.")

    @classmethod
    def _contains_metadata_keyword(cls, meta: Meta, genres: list[str], keywords: tuple[str, ...]) -> bool:
        values = [*cls._string_values(meta.keywords), *genres]
        normalized = {value.casefold().strip() for value in values if value.strip()}
        return any(keyword.casefold() in normalized for keyword in keywords)

    @staticmethod
    def _string_values(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value]
        if not value:
            return []
        return [str(value)]

    def _group_policy_passes(self, meta: Meta) -> bool:
        if not self._restricted_group_requires_override(meta):
            return True
        return self._confirm_policy_override(meta, f"Group {meta.tag} is only allowed for raw type content at OldToonsWorld")

    @staticmethod
    def _restricted_group_requires_override(meta: Meta) -> bool:
        if meta.type == "WEBDL" or meta.is_disc:
            return False
        return meta.tag in {"CMRG", "EVO", "TERMiNAL", "ViSION"}

    def _confirm_policy_override(self, meta: Meta, message: str) -> bool:
        if meta.unattended and not meta.unattended_confirm:
            return False
        logger.info(f"{self.tracker}: [bold red]{message}[/bold red]")
        return bool(cli_ui.ask_yes_no("Do you want to upload anyway?", default=False))

    async def get_type_id(self, meta: Meta, type: str | None = None, reverse: bool = False, mapping_only: bool = False) -> dict[str, str]:
        mapping = self._type_mapping()
        mode_result = self._mapping_mode_result(mapping, reverse=reverse, mapping_only=mapping_only)
        if mode_result is not None:
            return mode_result
        return {"type_id": self._resolved_type_id(meta, type, mapping)}

    @staticmethod
    def _mapping_mode_result(mapping: dict[str, str], *, reverse: bool, mapping_only: bool) -> dict[str, str] | None:
        if mapping_only:
            return mapping
        if reverse:
            return {value: key for key, value in mapping.items()}
        return None

    @classmethod
    def _resolved_type_id(cls, meta: Meta, requested_type: str | None, mapping: dict[str, str]) -> str:
        disc_type = cls._disc_type_id(meta)
        if disc_type:
            return disc_type
        selected = requested_type or str(meta.type)
        return mapping.get(selected, "0")

    @staticmethod
    def _type_mapping() -> dict[str, str]:
        return {"DISC": "1", "REMUX": "2", "WEBDL": "4", "WEBRIP": "5", "HDTV": "6", "ENCODE": "3", "DVDRIP": "8"}

    @staticmethod
    def _disc_type_id(meta: Meta) -> str:
        if meta.is_disc == "BDMV":
            return "1"
        return "7" if meta.is_disc else ""

    async def get_name(self, meta: Meta) -> dict[str, str]:
        name = self._without_aka(meta.name, meta.aka)
        name = self._apply_dvd_name_details(name, meta)
        return {"name": self._apply_tv_year(name, meta)}

    @staticmethod
    def _without_aka(name: str, aka: str) -> str:
        return name.replace(f"{aka} ", "") if aka else name

    @classmethod
    def _apply_dvd_name_details(cls, name: str, meta: Meta) -> str:
        if not cls._uses_dvd_name_details(meta):
            return name
        source = str(meta.source)
        name = name.replace(source, f"{meta.resolution} {source}", 1)
        return name.replace(meta.audio, f"{meta.video_codec} {meta.audio}", 1)

    @staticmethod
    def _uses_dvd_name_details(meta: Meta) -> bool:
        if meta.is_disc == "DVD":
            return True
        return str(meta.type) == "REMUX" and str(meta.source) in {"PAL DVD", "NTSC DVD", "DVD"}

    @classmethod
    def _apply_tv_year(cls, name: str, meta: Meta) -> str:
        if str(meta.category) != "TV" or meta.no_year or meta.search_year:
            return name
        year = cls._tv_year(meta)
        if not year:
            return name
        return name.replace(meta.title, f"{meta.title} {year}", 1)

    @classmethod
    def _tv_year(cls, meta: Meta) -> str:
        tmdb_year = cls._numeric_year(meta.year)
        if tmdb_year:
            return tmdb_year
        candidates = [
            cls._numeric_year(cls._mapping_value(meta.imdb_info, "year")),
            cls._numeric_year(cls._mapping_value(meta.tvdb_episode_data, "series_year")),
        ]
        years = [int(value) for value in candidates if value]
        return str(min(years)) if years else ""

    @staticmethod
    def _mapping_value(value: Any, key: str) -> Any:
        return value.get(key) if isinstance(value, dict) else None

    @staticmethod
    def _numeric_year(value: Any) -> str:
        text = str(value or "")
        return text if text.isdigit() else ""

    async def get_additional_data(self, meta: Meta) -> dict[str, Any]:
        data: dict[str, Any] = {
            "mod_queue_opt_in": await self.get_flag(meta, "modq"),
        }

        return data
