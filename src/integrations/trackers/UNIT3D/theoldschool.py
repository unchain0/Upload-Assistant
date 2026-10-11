# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
from typing import Any

from src.domain_models.release import Meta
from src.integrations.observability.runtime_support import logger
from src.integrations.torrent.torrent_creator import TorrentCreator
from src.integrations.trackers.common import Common
from src.integrations.trackers.UNIT3D import UNIT3D


class TheOldSchool(UNIT3D):
    """
    The Old School (TOS) is a FRENCH Private Torrent Tracker for MOVIES / TV / GENERAL
    """

    tracker = "THEOLDSCHOOL"
    display_name = "TheOldSchool"
    source_flag = "TheOldSchool"
    base_url = "https://theoldschool.cc"
    banned_groups = (
        "FL3ER",
        "SUNS3T",
        "WoLFHD",
        "EXTREME",
        "Slay3R",
        "3T3AM",
        "BARBiE",
    )
    id_url = f"{base_url}/api/torrents/"
    upload_url = f"{base_url}/api/torrents/upload"
    search_url = f"{base_url}/api/torrents/filter"
    torrent_url = f"{base_url}/torrents/"
    supported_categories = ("TV", "MOVIE")
    tracker_urls = ("https://theoldschool.cc",)
    allows_bloated_audio = True

    def __init__(self, config: dict[str, Any]):
        super().__init__(config, tracker_name="THEOLDSCHOOL")
        self.config = config
        self.common = Common(config)

    @staticmethod
    def _is_subtitled_release(meta: Meta) -> bool:
        tags_lower = meta.tag.lower() if meta.tag else ""
        return "vostfr" in tags_lower or "subfrench" in tags_lower

    @staticmethod
    def _category_mapping(subtitled: bool) -> dict[str, str]:
        return (
            {"MOVIE": "6", "TV": "7"}
            if subtitled
            else {"MOVIE": "1", "TV": "2"}
        )

    @staticmethod
    def _pack_category_id(meta: Meta, subtitled: bool) -> str | None:
        if meta.category != "TV" or not meta.tv_pack:
            return None
        return "9" if subtitled else "8"

    @classmethod
    def _category_value(cls, meta: Meta) -> str:
        subtitled = cls._is_subtitled_release(meta)
        pack_category = cls._pack_category_id(meta, subtitled)
        if pack_category is not None:
            return pack_category
        return cls._category_mapping(subtitled).get(meta.category, "0")

    async def get_category_id(
        self,
        meta: Meta,
        category: str | None = None,
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        _ = (category, reverse, mapping_only)
        return {"category_id": self._category_value(meta)}

    async def get_type_id(
        self,
        meta: Meta,
        type: str | None = None,
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        _ = (type, reverse, mapping_only)
        if meta.is_disc == "DVD":
            type_id = "7"
        elif meta.three_d == "3D":
            type_id = "8"
        else:
            type_id = {
                "DISC": "1",
                "REMUX": "2",
                "ENCODE": "3",
                "WEBDL": "4",
                "WEBRIP": "5",
                "HDTV": "6",
            }.get(meta.type or "", "0")
        return {"type_id": type_id}

    @staticmethod
    def _clean_non_scene_name(name: str) -> str:
        replacements = {
            ".mkv": "",
            ".mp4": "",
            ".torrent": "",
            " ": ".",
        }
        for old, new in replacements.items():
            name = name.replace(old, new)
        return name

    @classmethod
    def _base_release_name(cls, meta: Meta) -> str:
        is_scene = meta.scene
        name = meta.scene_name if is_scene else meta.basename_no_ext
        return cls._clean_non_scene_name(name) if is_scene is False else name

    def _announce_url(self) -> str:
        tracker_config = self.config["TRACKERS"].get(self.tracker, {})
        return str(
            tracker_config.get("announce_url", "https://fake.tracker")
        ).strip()

    def _rehash_cooldown(self) -> int:
        try:
            return int(
                self.config.get("DEFAULT", {}).get("rehash_cooldown", 0) or 0
            )
        except ValueError, TypeError:
            return 0

    async def _rehash_if_needed(self, meta: Meta) -> None:
        if not meta.keep_nfo:
            return
        cooldown = self._rehash_cooldown()
        if cooldown > 0:
            await asyncio.sleep(cooldown)
        await TorrentCreator.create_torrent(
            meta,
            str(meta.path),
            f"[{self.tracker}]",
            tracker_url=self._announce_url(),
        )

    async def get_name(self, meta: Meta) -> dict[str, str]:
        name = self._base_release_name(meta)
        await self._rehash_if_needed(meta)
        return {"name": name}

    async def get_additional_checks(self, meta: Meta) -> bool:
        # Check language requirements: must be French audio OR original audio with French subtitles
        french_languages = [
            "french",
            "fre",
            "fra",
            "fr",
            "français",
            "francais",
        ]
        if not await self.common.check_language_requirements(
            meta,
            self.tracker,
            languages_to_check=french_languages,
            check_audio=True,
            check_subtitle=True,
            require_both=False,
            original_language=True,
        ):
            logger.info(
                f"{self.tracker}: [bold red]Language requirements not met for {self.tracker}.[/bold red]"
            )
            return False

        # Check if it's a Scene release without NFO - TheOldSchool requires NFO for Scene releases
        is_scene = meta.scene
        has_nfo = meta.nfo or meta.auto_nfo

        if is_scene and not has_nfo:
            logger.info(
                f"{self.tracker}: [red]Scene release detected but no NFO file found. {self.tracker} requires NFO files for Scene releases.[/red]"
            )
            return False
        return True
