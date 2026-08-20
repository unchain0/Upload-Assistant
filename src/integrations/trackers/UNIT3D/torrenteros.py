# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
from typing import Any, cast

import cli_ui

from src.domain_models.release import Meta
from src.integrations.media.language_adapter import languages_manager
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.common import Common
from src.integrations.trackers.UNIT3D import UNIT3D

Config = dict[str, Any]


class Torrenteros(UNIT3D):
    """
    Torrenteros (TTR) is a SPANISH Torrent Tracker for MOVIES / TV / GENERAL
    """

    tracker = "TORRENTEROS"
    display_name = "Torrenteros"
    base_url = "https://torrenteros.org"
    banned_groups = ()
    ttr_name = ""  # Initialize instance variable
    id_url = f"{base_url}/api/torrents/"
    upload_url = f"{base_url}/api/torrents/upload"
    search_url = f"{base_url}/api/torrents/filter"
    torrent_url = f"{base_url}/torrents/"
    supported_categories = ("TV", "MOVIE")
    tracker_urls = ("https://torrenteros.org",)
    allowed_bloated_audio_languages = ("es",)

    def __init__(self, config: Config) -> None:
        super().__init__(config, tracker_name="TORRENTEROS")
        self.config: Config = config
        self.common = Common(config)

    async def get_name(self, meta: Meta) -> dict[str, str]:
        name = self.ttr_name or self.build_name(meta)

        return {"name": name}

    def build_name(self, meta: Meta) -> str:
        name = meta.name_notag
        suffix = self._disc_language_suffix(meta) if meta.is_disc == "BDMV" else self._file_language_suffix(meta)
        if suffix:
            name += f" {suffix}"
        if meta.tag:
            name += meta.tag
        self.ttr_name = name
        return name

    def _ask_spanish_type(self, kind: str) -> str:
        logger.info(f"{self.tracker}: [green]Found Spanish {kind} track.[/green] [yellow]Is it Castellano or Latino?[/yellow]")
        logger.info(f"{self.tracker}: 1 = Castellano")
        logger.info(f"{self.tracker}: 2 = Latino")
        logger.info(f"{self.tracker}: 3 = Castellano Latino")
        return str(cli_ui.ask_string("Enter choice (1-3): "))

    @staticmethod
    def _spanish_type(lang_code: str) -> str:
        normalized = lang_code.strip().casefold()
        if normalized in {"es-es", "es", "spa"}:
            return "Castellano"
        if normalized.startswith("es-"):
            return "Latino"
        return ""

    def _disc_language_suffix(self, meta: Meta) -> str:
        spanish_audio = "Spanish" in (meta.audio_languages or [])
        spanish_subtitle = "Spanish" in (meta.subtitle_languages or [])
        if spanish_audio:
            return self._disc_suffix(meta, "audio", subs=False)
        if spanish_subtitle:
            return self._disc_suffix(meta, "subtitle", subs=True)
        return ""

    def _disc_suffix(self, meta: Meta, kind: str, *, subs: bool) -> str:
        if meta.unattended or meta.unattended_confirm:
            return "Castellano Subs" if subs else "Castellano"
        choice = self._ask_spanish_type(kind)
        suffixes = {"1": "Castellano", "2": "Latino", "3": "Castellano Latino"}
        suffix = suffixes.get(choice, "Castellano")
        return f"{suffix} Subs" if subs else suffix

    def _file_language_suffix(self, meta: Meta) -> str:
        audio = self._first_spanish_track_type(meta, "Audio")
        if audio:
            return audio
        subtitles = self._first_spanish_track_type(meta, "Text")
        return f"{subtitles} Subs" if subtitles else ""

    def _first_spanish_track_type(self, meta: Meta, track_type: str) -> str:
        for track in self._media_tracks(meta):
            if track.get("@type") != track_type:
                continue
            spanish_type = self._spanish_type(self._track_language(track))
            if spanish_type:
                return spanish_type
        return ""

    @classmethod
    def _media_tracks(cls, meta: Meta) -> list[dict[str, Any]]:
        media = cls._media_mapping(meta)
        tracks = media.get("track", [])
        if not isinstance(tracks, list):
            return []
        return [cast(dict[str, Any], track) for track in tracks if isinstance(track, dict)]

    @staticmethod
    def _media_mapping(meta: Meta) -> dict[str, Any]:
        if not isinstance(meta.mediainfo, dict):
            return {}
        media = meta.mediainfo.get("media", {})
        return cast(dict[str, Any], media) if isinstance(media, dict) else {}

    @staticmethod
    def _track_language(track: dict[str, Any]) -> str:
        value = track.get("Language", "")
        return "" if isinstance(value, dict) else str(value).strip()

    async def get_additional_data(self, meta: Meta) -> dict[str, Any]:
        data: dict[str, Any] = {
            "mod_queue_opt_in": await self.get_flag(meta, "modq"),
        }

        return data

    async def get_additional_checks(self, meta: Meta) -> bool:
        if not meta.language_checked:
            await languages_manager.process_desc_language(meta, tracker=self.tracker)
        if self._has_spanish_audio(meta):
            return True
        if not self._has_spanish_subtitles(meta):
            logger.info(f"{self.tracker}: [bold red]requires at least one Spanish audio or subtitle track.")
            return False
        return self._allow_subtitle_only(meta)

    @staticmethod
    def _has_spanish_audio(meta: Meta) -> bool:
        return "Spanish" in (meta.audio_languages or [])

    @staticmethod
    def _has_spanish_subtitles(meta: Meta) -> bool:
        return "Spanish" in (meta.subtitle_languages or [])

    def _allow_subtitle_only(self, meta: Meta) -> bool:
        if meta.unattended:
            return bool(meta.unattended_confirm)
        logger.info(f"{self.tracker}: [yellow]No Spanish audio track found, but Spanish subtitles are present.[/yellow]")
        return bool(cli_ui.ask_yes_no("Do you want to upload anyway?", default=False))
