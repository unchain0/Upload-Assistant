# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from typing import Any

from src.domain_models.release import Meta
from src.integrations.trackers.common import Common
from src.integrations.trackers.UNIT3D import UNIT3D

Config = dict[str, Any]


class ReelFlix(UNIT3D):
    """
    ReelFLiX (HD4Free, LegacyHD) is a Private Torrent Tracker for HD MOVIES
    """

    tracker = "REELFLIX"
    display_name = "ReelFlix"
    allows_bloated_audio = True
    base_url = "https://reelflix.cc"
    banned_groups = ()
    id_url = f"{base_url}/api/torrents/"
    upload_url = f"{base_url}/api/torrents/upload"
    search_url = f"{base_url}/api/torrents/filter"
    requests_url = f"{base_url}/api/requests/filter"
    torrent_url = f"{base_url}/torrents/"
    supported_categories = ("MOVIE",)
    tracker_urls = ("https://reelflix.xyz", "https://reelflix.cc")

    def __init__(self, config: Config) -> None:
        super().__init__(config, tracker_name="REELFLIX")
        self.config: Config = config
        self.common = Common(config)

    async def get_additional_checks(self, meta: Meta) -> bool:
        return await self.common.check_and_confirm_adult_media_upload(
            meta, self.tracker
        )

    @staticmethod
    def _invalid_group_tags() -> tuple[str, ...]:
        return ("nogrp", "nogroup", "unknown", "-unk-")

    @classmethod
    def _needs_no_group(cls, tag_value: str) -> bool:
        if not tag_value:
            return True
        tag_lower = tag_value.lower()
        return any(tag in tag_lower for tag in cls._invalid_group_tags())

    @classmethod
    def _without_invalid_group_tags(cls, name: str) -> str:
        for invalid_tag in cls._invalid_group_tags():
            name = re.sub(f"-{invalid_tag}", "", name, flags=re.IGNORECASE)
        return name

    async def get_name(self, meta: Meta) -> dict[str, str]:
        tag_value = meta.tag or ""
        if not self._needs_no_group(tag_value):
            return {"name": meta.name}
        name = self._without_invalid_group_tags(meta.name)
        return {"name": f"{name}-NoGroup"}

    async def get_type_id(
        self,
        meta: Meta,
        type: str | None = None,
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        type_id = {
            "DISC": "43",
            "REMUX": "40",
            "WEBDL": "42",
            "WEBRIP": "45",
            # 'FANRES': '6',
            "ENCODE": "41",
            "HDTV": "35",
        }
        if mapping_only:
            return type_id
        if reverse:
            return {v: k for k, v in type_id.items()}
        type_value = type if type is not None else str(meta.type)
        return {"type_id": type_id.get(type_value, "0")}

    async def get_resolution_id(
        self,
        meta: Meta,
        resolution: str | None = None,
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        resolution_id = {
            # '8640p':'10',
            "4320p": "1",
            "2160p": "2",
            # '1440p' : '3',
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
            return {v: k for k, v in resolution_id.items()}
        resolution_value = (
            resolution if resolution is not None else meta.resolution
        )
        return {"resolution_id": resolution_id.get(resolution_value, "10")}
