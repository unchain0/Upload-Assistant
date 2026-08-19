# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from typing import Any

from src.domain_models.release import Meta
from src.integrations.media.language_adapter import languages_manager
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.common import Common
from src.integrations.trackers.UNIT3D import UNIT3D

Config = dict[str, Any]


class ItaTorrents(UNIT3D):
    """
    ItaTorrents is an ITALIAN Private tracker for MOVIES / TV / GENERAL
    """

    tracker = "ITATORRENTS"
    display_name = "ItaTorrents"
    base_url = "https://itatorrents.xyz"
    banned_groups = ()
    id_url = f"{base_url}/api/torrents/"
    upload_url = f"{base_url}/api/torrents/upload"
    requests_url = f"{base_url}/api/requests/filter"
    search_url = f"{base_url}/api/torrents/filter"
    torrent_url = f"{base_url}/torrents/"
    supported_categories = ("TV", "MOVIE")
    tracker_urls = ("https://itatorrents.xyz",)
    allowed_bloated_audio_languages = ("it",)

    def __init__(self, config: Config) -> None:
        super().__init__(config, tracker_name="ITATORRENTS")
        self.config: Config = config
        self.common = Common(config)

    async def get_type_name(self, meta: Meta) -> str | None:
        type_name: str | None = None

        uuid_string = meta.basename_no_ext
        if uuid_string:
            lower_uuid = uuid_string.lower()

            if "dlmux" in lower_uuid:
                type_name = "DLMux"
            elif "bdmux" in lower_uuid:
                type_name = "BDMux"
            elif "webmux" in lower_uuid:
                type_name = "WEBMux"
            elif "dvdmux" in lower_uuid:
                type_name = "DVDMux"
            elif "bdrip" in lower_uuid:
                type_name = "BDRip"

        if type_name is None:
            type_value = meta.type
            type_name = str(type_value) if type_value else None

        return type_name

    async def get_type_id(self, meta: Meta, type: str | None = None, reverse: bool = False, mapping_only: bool = False) -> dict[str, str]:
        type_id_map = {
            "DISC": "1",
            "REMUX": "2",
            "WEBDL": "4",
            "WEBRIP": "5",
            "HDTV": "6",
            "ENCODE": "3",
            "DLMux": "27",
            "BDMux": "29",
            "WEBMux": "26",
            "DVDMux": "39",
            "BDRip": "25",
            "DVDRIP": "24",
            "Cinema-MD": "14",
        }
        if mapping_only:
            return type_id_map
        if reverse:
            return {v: k for k, v in type_id_map.items()}
        if type is not None:
            return {"type_id": type_id_map.get(type, "0")}

        resolved_type = await self.get_type_name(meta)
        type_id = type_id_map.get(resolved_type or "", "0")

        return {"type_id": type_id}

    async def get_name(self, meta: Meta) -> dict[str, str]:
        type_name = await self.get_type_name(meta) or ""
        year = self._display_year(meta)
        season, episode = self._episode_tokens(meta)
        edition = self._edition_name(meta.edition)
        dubs = await self.get_dubs(meta)
        if type_name in {"DISC", "REMUX"}:
            name = self._disc_name(meta, type_name, year, season, episode, edition, dubs)
        else:
            name = self._encoded_name(meta, type_name, year, season, episode, edition, dubs)
        return {"name": self._clean_name(name, meta.tag or "")}

    @classmethod
    def _display_year(cls, meta: Meta) -> str:
        if meta.no_year is True:
            return ""
        if meta.category == "TV":
            return cls._tv_year(meta)
        return cls._base_year(meta)

    @staticmethod
    def _tv_year(meta: Meta) -> str:
        if meta.year is None or meta.search_year == "":
            return ""
        return str(meta.manual_year) if meta.manual_year else str(meta.year)

    @staticmethod
    def _base_year(meta: Meta) -> str:
        if meta.manual_year:
            return str(meta.manual_year)
        return str(meta.year) if meta.year is not None else ""

    @staticmethod
    def _episode_tokens(meta: Meta) -> tuple[str, str]:
        season = str(meta.season or "")
        episode = str(meta.episode or "")
        if meta.manual_date or meta.no_season is True:
            return "", ""
        return season, episode

    @staticmethod
    def _edition_name(edition: str) -> str:
        return re.sub(r"\bHybrid\b", "", edition or "", flags=re.IGNORECASE).strip()

    @staticmethod
    def _disc_name(meta: Meta, type_name: str, year: str, season: str, episode: str, edition: str, dubs: str) -> str:
        remux = "REMUX" if type_name == "REMUX" else ""
        resolution = "" if meta.resolution == "OTHER" else meta.resolution
        return (
            f"{meta.title} {year} {season}{episode} {meta.repack} {resolution} {edition} {meta.region} {meta.three_d} "
            f"{meta.source} {remux} {meta.hdr} {meta.video_codec} {dubs} {meta.audio}"
        )

    @staticmethod
    def _encoded_name(meta: Meta, type_name: str, year: str, season: str, episode: str, edition: str, dubs: str) -> str:
        normalized_type = ItaTorrents._display_type(type_name)
        resolution = "" if meta.resolution == "OTHER" else meta.resolution
        return f"{meta.title} {year} {season}{episode} {meta.repack} {resolution} {edition} {meta.three_d} {normalized_type} {dubs} {meta.audio} {meta.hdr} {meta.video_codec}"

    @staticmethod
    def _display_type(type_name: str) -> str:
        return type_name.replace("WEBDL", "WEB-DL").replace("WEBRIP", "WEBRip").replace("DVDRIP", "DVDRip").replace("ENCODE", "BluRay")

    @staticmethod
    def _clean_name(name: str, tag: str) -> str:
        return re.sub(r"\s{2,}", " ", f"{name}{tag}".replace("Dubbed", "").replace("Dual-Audio", "").strip())

    async def get_dubs(self, meta: Meta) -> str:
        if not meta.language_checked:
            await languages_manager.process_desc_language(meta, tracker=self.tracker)
        dubs = ""
        audio_languages_value = meta.audio_languages
        audio_languages: set[str] = set()
        if isinstance(audio_languages_value, list):
            audio_languages_list = audio_languages_value
            audio_languages = {str(lang) for lang in audio_languages_list}
        if audio_languages:
            dubs = " ".join(lang[:3].upper() for lang in audio_languages)
        return dubs

    async def get_additional_checks(self, meta: Meta) -> bool:
        # From rules:
        # "Non sono ammessi film e serie tv che non comprendono il doppiaggio in italiano."
        # Translates to "Films and TV series that do not include Italian dubbing are not permitted."
        italian_languages = ["italian", "italiano"]
        if not await self.common.check_language_requirements(meta, self.tracker, languages_to_check=italian_languages, check_audio=True):
            logger.info(f"{self.tracker}: Upload Rules: https://itatorrents.xyz/wikis/5")
            return False
        return True
