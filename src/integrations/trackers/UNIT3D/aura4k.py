# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
from typing import Any

import cli_ui

from src.domain_models.release import Meta
from src.integrations.image_hosts.rehosting import (
    ImageHostPolicy,
    RehostImagesManager,
)
from src.integrations.media.language_adapter import languages_manager
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.common import Common
from src.integrations.trackers.UNIT3D import UNIT3D

Config = dict[str, Any]


class Aura4K(UNIT3D):
    """
    AURA4K is a Private Torrent Tracker for MOVIES / TV
    """

    tracker = "AURA4K"
    display_name = "Aura4K"
    allows_bloated_audio = True
    base_url = "https://aura4k.net"
    approved_image_hosts = (
        "onlyimage",
        "imgbox",
        "ptscreens",
        "imgbb",
        "imgur",
        "postimg",
    )
    image_host_policy = ImageHostPolicy(
        {
            "ibb.co": "imgbb",
            "imgbox.com": "imgbox",
            "imgur.com": "imgur",
            "postimg.cc": "postimg",
            "ptscreens.com": "ptscreens",
            "onlyimage.org": "onlyimage",
        },
        approved_image_hosts,
    )
    banned_groups = (
        "BiTOR",
        "DepraveD",
        "Flights",
        "SasukeducK",
        "SPDVD",
        "TEKNO3D",
    )
    id_url = f"{base_url}/api/torrents/"
    upload_url = f"{base_url}/api/torrents/upload"
    search_url = f"{base_url}/api/torrents/filter"
    torrent_url = f"{base_url}/torrents/"
    supported_categories = ("TV", "MOVIE")

    def __init__(self, config: Config) -> None:
        super().__init__(config, tracker_name="AURA4K")
        self.config = config
        self.common = Common(config)
        self.rehost_images_manager = RehostImagesManager(config)

    async def get_type_id(
        self,
        meta: Meta,
        type: str | None = None,
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        mapping = {"DISC": "1", "REMUX": "2", "WEBDL": "4", "ENCODE": "3"}
        return self._mapping_response(
            mapping,
            type,
            meta.type,
            reverse=reverse,
            mapping_only=mapping_only,
            default="0",
            key="type_id",
        )

    async def get_resolution_id(
        self,
        meta: Meta,
        resolution: str | None = None,
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        mapping = {"4320p": "1", "2160p": "2"}
        return self._mapping_response(
            mapping,
            resolution,
            meta.resolution,
            reverse=reverse,
            mapping_only=mapping_only,
            default="10",
            key="resolution_id",
        )

    @classmethod
    def _mapping_response(
        cls,
        mapping: dict[str, str],
        requested: str | None,
        fallback: str | None,
        *,
        reverse: bool,
        mapping_only: bool,
        default: str,
        key: str,
    ) -> dict[str, str]:
        mode = cls._mapping_mode(
            mapping, reverse=reverse, mapping_only=mapping_only
        )
        if mode is not None:
            return mode
        return {
            key: mapping.get(
                cls._selected_mapping_value(requested, fallback), default
            )
        }

    @staticmethod
    def _mapping_mode(
        mapping: dict[str, str], *, reverse: bool, mapping_only: bool
    ) -> dict[str, str] | None:
        if mapping_only:
            return mapping
        return (
            {value: name for name, value in mapping.items()}
            if reverse
            else None
        )

    @staticmethod
    def _selected_mapping_value(
        requested: str | None, fallback: str | None
    ) -> str:
        return requested if requested else (fallback or "")

    async def get_additional_checks(self, meta: Meta) -> bool:
        if not self._resolution_policy_passes(meta):
            return False
        if not self._type_policy_passes(meta):
            return False
        if not await self._language_policy_passes(meta):
            return False
        return self._bitrate_policy_passes(meta)

    def _resolution_policy_passes(self, meta: Meta) -> bool:
        if meta.resolution in {"2160p", "4320p"}:
            return True
        self._log_attended(meta, "only accepts 4K uploads.")
        return False

    def _type_policy_passes(self, meta: Meta) -> bool:
        if meta.type in {"DISC", "REMUX", "WEBDL", "ENCODE"}:
            return True
        self._log_attended(
            meta, "only accepts DISC, REMUX, WEBDL, and ENCODE uploads."
        )
        return False

    async def _language_policy_passes(self, meta: Meta) -> bool:
        if meta.is_disc in {"BDMV", "DVD"}:
            return True
        return await self.common.check_language_requirements(
            meta,
            self.tracker,
            languages_to_check=["english"],
            check_audio=True,
            check_subtitle=True,
            original_language=True,
        )

    def _bitrate_policy_passes(self, meta: Meta) -> bool:
        if meta.is_disc or meta.type not in {"ENCODE", "WEBDL"}:
            return True
        return all(
            self._video_bitrate_policy_passes(meta, track)
            for track in self._video_tracks(meta)
        )

    @classmethod
    def _video_tracks(cls, meta: Meta) -> list[dict[str, Any]]:
        tracks = cls._media_tracks(meta)
        return [track for track in tracks if track.get("@type") == "Video"]

    @classmethod
    def _media_tracks(cls, meta: Meta) -> list[dict[str, Any]]:
        media = cls._media_mapping(meta)
        tracks = media.get("track", [])
        if not isinstance(tracks, list):
            return []
        return [track for track in tracks if isinstance(track, dict)]

    @staticmethod
    def _media_mapping(meta: Meta) -> dict[str, Any]:
        if not isinstance(meta.mediainfo, dict):
            return {}
        media = meta.mediainfo.get("media", {})
        return media if isinstance(media, dict) else {}

    def _video_bitrate_policy_passes(
        self, meta: Meta, track: dict[str, Any]
    ) -> bool:
        if not track.get("Encoded_Library_Settings"):
            return self._confirm_missing_bitrate(meta)
        bitrate = self._bitrate_kbps(track.get("BitRate"))
        if bitrate is None:
            return self._confirm_missing_bitrate(meta)
        return self._bitrate_above_minimum(meta, bitrate)

    @staticmethod
    def _bitrate_kbps(value: Any) -> float | None:
        try:
            return int(value) / 1000 if value is not None else None
        except TypeError, ValueError:
            return None

    def _bitrate_above_minimum(self, meta: Meta, bitrate_kbps: float) -> bool:
        minimum = self._minimum_bitrate(meta.category)
        if minimum and bitrate_kbps < minimum:
            self._log_attended(
                meta,
                f"Video bitrate too low: {bitrate_kbps:.0f} kbps for AURA4K {str(meta.category).lower()} uploads.",
            )
            return False
        return True

    @staticmethod
    def _minimum_bitrate(category: str | None) -> int:
        return {"MOVIE": 15000, "TV": 10000}.get(
            str(category or "").upper(), 0
        )

    def _confirm_missing_bitrate(self, meta: Meta) -> bool:
        if meta.unattended and not meta.unattended_confirm:
            return False
        logger.info(
            f"{self.tracker}: [bold red]Could not determine video bitrate from mediainfo for {self.tracker} upload.[/bold red]"
        )
        logger.info(
            f"{self.tracker}: [yellow]Bitrate must be above 15000 kbps for movies and 10000 kbps for TV shows.[/yellow]"
        )
        return bool(
            cli_ui.ask_yes_no("Do you want to upload anyway?", default=False)
        )

    def _log_attended(self, meta: Meta, message: str) -> None:
        if not meta.unattended:
            logger.info(f"{self.tracker}: [red]{message}")

    async def get_additional_data(self, meta: Meta) -> dict[str, Any]:
        return {
            "mod_queue_opt_in": await self.get_flag(meta, "modq"),
        }

    async def get_name(self, meta: Meta) -> dict[str, str]:
        if not meta.language_checked:
            await languages_manager.process_desc_language(
                meta, tracker=self.tracker
            )
        languages = self._string_list(meta.audio_languages)
        return {"name": await self._foreign_language_name(meta, languages)}

    async def _foreign_language_name(
        self, meta: Meta, languages: list[str]
    ) -> str:
        if not languages or await languages_manager.has_english_language(
            languages
        ):
            return meta.name
        if meta.is_disc == "BDMV":
            return meta.name
        return meta.name.replace(
            meta.resolution, f"{languages[0].upper()} {meta.resolution}", 1
        )

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        return [str(item) for item in value] if isinstance(value, list) else []
