# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
from typing import Any

from src.domain_models.release import Meta
from src.integrations.media.language_adapter import languages_manager
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.common import Common
from src.integrations.trackers.UNIT3D import UNIT3D


class Aither(UNIT3D):
    """
    Aither is a Private Torrent Tracker for HD MOVIES / TV
    """

    tracker = "AITHER"
    display_name = "Aither"
    base_url = "https://aither.cc"
    banned_groups: tuple[str, ...] = ()
    banned_url = f"{base_url}/api/blacklists/releasegroups"
    claims_url = f"{base_url}/api/internals/claim"
    id_url = f"{base_url}/api/torrents/"
    upload_url = f"{base_url}/api/torrents/upload"
    search_url = f"{base_url}/api/torrents/filter"
    torrent_url = f"{base_url}/torrents/"
    requests_url = f"{base_url}/api/requests/filter"
    trumping_url = f"{base_url}/api/trumping-reports/filter"
    supported_categories = ("TV", "MOVIE")
    tracker_urls = ("https://aither.cc",)
    allowed_bloated_audio_languages = ("en",)

    def __init__(self, config: dict[str, Any]):
        super().__init__(config, tracker_name="AITHER")
        self.config = config
        self.common = Common(config)

    async def get_additional_checks(self, meta: Meta):
        should_continue = True

        if meta.is_disc not in [
            "BDMV",
            "DVD",
        ] and not await self.common.check_language_requirements(
            meta,
            self.tracker,
            languages_to_check=["english"],
            check_audio=True,
            check_subtitle=True,
            original_language=True,
            original_required=True,
        ):
            return False

        if meta.valid_mi is False:
            logger.info(
                f"{self.tracker}: [bold red]No unique ID in mediainfo, skipping {self.tracker} upload."
            )
            return False

        return should_continue

    @staticmethod
    def _hdr_flags(hdr_value: str) -> dict[str, int]:
        flags: dict[str, int] = {}
        if "DV" in hdr_value:
            flags["dv"] = 1
        if "HDR10+" in hdr_value:
            flags["hdr10p"] = 1
            return flags
        if any(flag in hdr_value for flag in ("HDR", "HLG")):
            flags["hdr"] = 1
        return flags

    async def get_additional_data(self, meta: Meta) -> dict[str, Any]:
        data: dict[str, Any] = {
            "mod_queue_opt_in": await self.get_flag(meta, "modq"),
        }
        data.update(self._hdr_flags(meta.hdr or ""))
        return data

    @staticmethod
    def _base_year(meta: Meta) -> str:
        if meta.category != "TV":
            return str(meta.year) if meta.year is not None else ""
        if meta.year is None or meta.search_year == "":
            return ""
        return str(meta.year)

    @staticmethod
    def _manual_year(meta: Meta) -> str:
        manual_year = str(meta.manual_year)
        if manual_year and int(manual_year) > 0:
            return manual_year
        return ""

    @classmethod
    def _resolved_year(cls, meta: Meta) -> str:
        year = cls._manual_year(meta) or cls._base_year(meta)
        return "" if meta.no_year else year

    async def _foreign_language(self, meta: Meta) -> str:
        if not meta.language_checked:
            await languages_manager.process_desc_language(
                meta,
                tracker=self.tracker,
            )
        audio_languages = meta.audio_languages or []
        if not audio_languages:
            return ""
        if await languages_manager.has_english_language(audio_languages):
            return ""
        return audio_languages[0].upper()

    @staticmethod
    def _foreign_dvd_remux(
        name_type: str,
        source: str,
    ) -> bool:
        return bool(
            name_type == "REMUX" and source in {"PAL DVD", "NTSC DVD", "DVD"}
        )

    @classmethod
    def _apply_foreign_language(
        cls,
        meta: Meta,
        name: str,
        year: str,
        name_type: str,
        source: str,
        foreign_lang: str,
    ) -> str:
        if not foreign_lang:
            return name
        if cls._foreign_dvd_remux(name_type, source):
            if year:
                return name.replace(year, f"{year} {foreign_lang}", 1)
            return name
        if meta.is_disc != "BDMV":
            return name.replace(
                meta.resolution,
                f"{foreign_lang} {meta.resolution}",
                1,
            )
        return name

    @staticmethod
    def _dvdrip_name(meta: Meta, name: str, resolution: str) -> str:
        value = name.replace(f"{meta.source} ", "", 1)
        value = value.replace(str(meta.video_encode), "", 1)
        value = value.replace("DVDRip", f"{resolution} DVDRip", 1)
        return value.replace(
            meta.audio,
            f"{meta.audio}{meta.video_encode}",
            1,
        )

    @staticmethod
    def _joined_name_parts(*parts: object) -> str:
        return " ".join(str(part) for part in parts if part)

    @classmethod
    def _dvd_disc_name(
        cls,
        meta: Meta,
        name: str,
        resolution: str,
        video_codec: str,
        source: str,
    ) -> str:
        region_and_source = cls._joined_name_parts(meta.region, source)
        disc_details = cls._joined_name_parts(
            resolution,
            meta.region,
            source,
        )
        if region_and_source:
            name = name.replace(region_and_source, disc_details, 1)
        return name.replace(
            meta.audio,
            f"{video_codec} {meta.audio}",
            1,
        )

    @staticmethod
    def _dvd_remux_name(
        meta: Meta,
        name: str,
        resolution: str,
        video_codec: str,
    ) -> str:
        name = name.replace(
            meta.source or "",
            f"{resolution} {meta.source}",
            1,
        )
        return name.replace(
            meta.audio,
            f"{video_codec} {meta.audio}",
            1,
        )

    @classmethod
    def _dvd_adjusted_name(
        cls,
        meta: Meta,
        name: str,
        resolution: str,
        video_codec: str,
        name_type: str,
        source: str,
    ) -> str:
        if name_type == "DVDRIP":
            return cls._dvdrip_name(meta, name, resolution)
        if meta.is_disc == "DVD":
            return cls._dvd_disc_name(
                meta,
                name,
                resolution,
                video_codec,
                source,
            )
        if cls._foreign_dvd_remux(name_type, source):
            return cls._dvd_remux_name(
                meta,
                name,
                resolution,
                video_codec,
            )
        return name

    @staticmethod
    def _final_name(meta: Meta, name: str, year: str) -> str:
        if meta.trump_reason == "exact_match":
            name = f"{name} - TRUMP"
        alt_title = meta.aka if not meta.no_aka else ""
        if alt_title:
            name = name.replace(
                f"{year} {alt_title}",
                f"{alt_title} {year}",
                1,
            )
        return name

    async def get_name(self, meta: Meta) -> dict[str, str]:
        year = self._resolved_year(meta)
        name_type = meta.type or ""
        source = meta.source or ""
        foreign_lang = await self._foreign_language(meta)
        name = self._apply_foreign_language(
            meta,
            meta.name,
            year,
            name_type,
            source,
            foreign_lang,
        )
        name = self._dvd_adjusted_name(
            meta,
            name,
            meta.resolution,
            meta.video_codec,
            name_type,
            source,
        )
        return {"name": self._final_name(meta, name, year)}
