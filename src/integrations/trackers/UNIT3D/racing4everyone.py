# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
from typing import Any, cast

import httpx

from src.domain_models.release import Meta
from src.integrations.trackers.common import Common
from src.integrations.trackers.UNIT3D import UNIT3D

Config = dict[str, Any]


class Racing4Everyone(UNIT3D):
    """
    RACING4EVERYONE (R4E) is a Private Torrent Tracker for RACING
    """

    tracker = "RACING4EVERYONE"
    display_name = "Racing4Everyone"
    allows_bloated_audio = True
    base_url = "https://racing4everyone.eu"
    banned_groups: tuple[str, ...] = ()
    id_url = f"{base_url}/api/torrents/"
    upload_url = f"{base_url}/api/torrents/upload"
    search_url = f"{base_url}/api/torrents/filter"
    torrent_url = f"{base_url}/torrents/"
    supported_categories = ("TV", "MOVIE")

    def __init__(self, config: Config) -> None:
        super().__init__(config, tracker_name="RACING4EVERYONE")
        self.config: Config = config
        self.common = Common(config)

    @staticmethod
    def _genre_ids(meta: Meta) -> set[str]:
        if not meta.genre_ids:
            return set()
        return {
            genre_id.strip()
            for genre_id in str(meta.genre_ids).split(",")
            if genre_id.strip()
        }

    @staticmethod
    def _category_value(category: str, documentary: bool) -> str:
        mapping = {
            "MOVIE": ("70", "66"),
            "TV": ("79", "2"),
        }
        values = mapping.get(category)
        if values is None:
            return "24"
        return values[1] if documentary else values[0]

    async def get_category_id(
        self,
        meta: Meta,
        category: str | None = None,
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        _ = (category, reverse, mapping_only)
        documentary = "99" in self._genre_ids(meta)
        return {
            "category_id": self._category_value(meta.category, documentary)
        }

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
        }

    @staticmethod
    def _selected_type(meta: Meta, explicit_type: str | None) -> str:
        if explicit_type is not None and explicit_type != "":
            return explicit_type
        return str(meta.type)

    async def get_type_id(
        self,
        meta: Meta,
        type: str | None = None,
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        type_id = self._type_mapping()
        if mapping_only:
            return type_id
        if reverse:
            return {value: key for key, value in type_id.items()}
        selected = self._selected_type(meta, type)
        return {"type_id": type_id.get(selected, "0")}

    async def get_personal_release(self, meta: Meta) -> dict[str, str]:
        _ = meta
        return {}

    async def get_internal(self, meta: Meta) -> dict[str, str]:
        _ = meta
        return {}

    async def get_featured(self, meta: Meta) -> dict[str, str]:
        _ = meta
        return {}

    async def get_free(self, meta: Meta) -> dict[str, str]:
        _ = meta
        return {}

    async def get_doubleup(self, meta: Meta) -> dict[str, str]:
        _ = meta
        return {}

    async def get_sticky(self, meta: Meta) -> dict[str, str]:
        _ = meta
        return {}

    @staticmethod
    def _resolution_mapping() -> dict[str, str]:
        return {
            "8640p": "2160p",
            "4320p": "2160p",
            "2160p": "2160p",
            "1440p": "1080p",
            "1080p": "1080p",
            "1080i": "1080i",
            "720p": "720p",
            "576p": "SD",
            "576i": "SD",
            "480p": "SD",
            "480i": "SD",
        }

    @staticmethod
    def _selected_resolution(meta: Meta, resolution: str | None) -> str:
        if resolution is not None and resolution != "":
            return resolution
        return str(meta.resolution)

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
        selected = self._selected_resolution(meta, resolution)
        return {"resolution_id": resolution_id.get(selected, "SD")}

    async def search_existing(self, meta: Meta) -> list[dict[str, Any]]:
        dupes: list[dict[str, Any]] = []
        url = self.search_url
        params: dict[str, Any] = {
            "api_token": str(
                self.config["TRACKERS"][self.tracker]["api_key"]
            ).strip(),
            "tmdb": meta.tmdb,
            "categories[]": (await self.get_category_id(meta))["category_id"],
            "types[]": (await self.get_type_id(meta))["type_id"],
            "name": "",
        }
        if meta.category == "TV":
            params["name"] = f"{meta.season}"
        if meta.edition != "":
            params["name"] = str(params["name"]) + meta.edition
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url=url, params=params)
            response.raise_for_status()
            data = cast(dict[str, Any], response.json())
            items = cast(list[dict[str, Any]], data.get("data", []))
            for each in items:
                attributes = cast(dict[str, Any], each.get("attributes", {}))
                result_name = str(attributes.get("name", ""))
                dupes.append(
                    {
                        "name": result_name,
                        "files": result_name,
                        "size": 0,
                        "link": "",
                        "file_count": 0,
                        "download": "",
                    }
                )

        return dupes
