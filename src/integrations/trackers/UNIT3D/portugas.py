# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from pathlib import Path
from typing import Any

from src.domain_models.release import Meta
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.common import Common
from src.integrations.trackers.UNIT3D import UNIT3D

Config = dict[str, Any]


class Portugas(UNIT3D):
    """
    Portugas is a PORTUGUESE Private Torrent Tracker for 0DAY / GENERAL
    """

    tracker = "PORTUGAS"
    display_name = "Portugas"
    base_url = "https://portugas.org"
    banned_groups = ()
    id_url = f"{base_url}/api/torrents/"
    upload_url = f"{base_url}/api/torrents/upload"
    search_url = f"{base_url}/api/torrents/filter"
    torrent_url = f"{base_url}/torrents/"
    supported_categories = ("TV", "MOVIE")
    tracker_urls = ("https://portugas.org",)
    allowed_bloated_audio_languages = ("pt",)

    def __init__(self, config: Config) -> None:
        super().__init__(config, tracker_name="PORTUGAS")
        self.config: Config = config
        self.common = Common(config)

    async def get_type_id(self, meta: Meta, type: str | None = None, reverse: bool = False, mapping_only: bool = False) -> dict[str, str]:
        _ = (type, reverse, mapping_only)
        type_id = {"DISC": "1", "REMUX": "2", "WEBDL": "4", "WEBRIP": "39", "HDTV": "6", "ENCODE": "3"}.get(str(meta.type), "0")
        return {"type_id": type_id}

    async def get_resolution_id(self, meta: Meta, resolution: str | None = None, reverse: bool = False, mapping_only: bool = False) -> dict[str, str]:
        _ = (resolution, reverse, mapping_only)
        resolution_id = {
            "4320p": "1",
            "2160p": "2",
            "1440p": "13",
            "1080p": "3",
            "1080i": "4",
            "720p": "5",
            "576p": "6",
            "576i": "7",
            "540p": "11",
            "480p": "8",
            "480i": "9",
        }.get(meta.resolution, "10")
        return {"resolution_id": resolution_id}

    async def get_name(self, meta: Meta) -> dict[str, str]:
        name = meta.name.replace(" ", ".")
        tag_value = meta.tag or ""
        if self._needs_nogroup_tag(tag_value):
            name = f"{self._strip_invalid_group_tags(name)}-NOGROUP"
        return {"name": name}

    @staticmethod
    def _needs_nogroup_tag(tag_value: str) -> bool:
        if not tag_value:
            return True
        lowered = tag_value.casefold()
        return any(value in lowered for value in ("nogrp", "nogroup", "unknown", "-unk-"))

    @staticmethod
    def _strip_invalid_group_tags(name: str) -> str:
        result = name
        for invalid_tag in ("nogrp", "nogroup", "unknown", "-unk-"):
            result = re.sub(f"-{invalid_tag}", "", result, flags=re.IGNORECASE)
        return result

    def get_audio(self, meta: Meta) -> int:
        found = self._bdmv_has_portuguese_audio(meta)
        if not found:
            found = self._mediainfo_has_portuguese(meta, "Audio")
        return int(found)

    def get_subtitles(self, meta: Meta) -> int:
        found = self._bdmv_has_portuguese_subtitle(meta)
        if not found:
            found = self._mediainfo_has_portuguese(meta, "Text") or self._mediainfo_has_portuguese(meta, "Subtitle")
        return int(found)

    @staticmethod
    def _bdmv_has_portuguese_audio(meta: Meta) -> bool:
        if meta.is_disc != "BDMV":
            return False
        tracks = meta.bdinfo.get("audio", []) if isinstance(meta.bdinfo, dict) else []
        return any(Portugas._audio_track_is_portuguese(track) for track in tracks if isinstance(track, dict))

    @staticmethod
    def _audio_track_is_portuguese(track: dict[str, Any]) -> bool:
        return str(track.get("language", "")).strip().casefold() == "portuguese"

    @staticmethod
    def _bdmv_has_portuguese_subtitle(meta: Meta) -> bool:
        if meta.is_disc != "BDMV":
            return False
        tracks = meta.bdinfo.get("subtitles", []) if isinstance(meta.bdinfo, dict) else []
        return any(isinstance(track, str) and track.strip().casefold() == "portuguese" for track in tracks)

    @classmethod
    def _mediainfo_has_portuguese(cls, meta: Meta, section_name: str) -> bool:
        text = cls._read_mediainfo_text(meta)
        if not text:
            return False
        return any(cls._section_is_portuguese(section) for section in cls._mediainfo_sections(text, section_name))

    @classmethod
    def _read_mediainfo_text(cls, meta: Meta) -> str:
        base_dir, uuid = cls._temp_identity(meta)
        return cls._read_text_file(Path(base_dir) / "tmp" / uuid / "MEDIAINFO.txt")

    @staticmethod
    def _temp_identity(meta: Meta) -> tuple[str, str]:
        base_dir = "." if meta.base_dir is None else str(meta.base_dir)
        uuid = "default_uuid" if meta.uuid is None else str(meta.uuid)
        return base_dir, uuid

    @staticmethod
    def _read_text_file(path: Path) -> str:
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""
        except Exception as error:
            logger.info(f"ERRO: Falha ao processar MediaInfo para verificar Português: {error}", extra={"markup": False})
            return ""

    @staticmethod
    def _mediainfo_sections(text: str, section_name: str) -> list[str]:
        pattern = rf"{re.escape(section_name)}(?: #\d+)?\s*\n(.*?)(?=\n\n(?:Audio|Video|Text|Subtitle|Menu)|$)"
        return re.findall(pattern, text, re.DOTALL | re.IGNORECASE)

    @staticmethod
    def _section_is_portuguese(section: str) -> bool:
        language_match = re.search(r"Language\s*:\s*(.+)", section, re.IGNORECASE)
        title_match = re.search(r"Title\s*:\s*(.+)", section, re.IGNORECASE)
        language = language_match.group(1).strip() if language_match else ""
        title = title_match.group(1).strip() if title_match else ""
        text = f"{language} {title}".casefold()
        if "portuguese" not in text:
            return False
        return "(br)" not in text and "brazilian" not in text

    async def get_distributor_ids(self, _meta: Meta) -> dict[str, str]:
        return {}

    async def get_region_id(self, meta: Meta) -> dict[str, str]:
        _ = meta
        return {}

    async def get_additional_data(self, meta: Meta) -> dict[str, str]:
        audio_flag = self.get_audio(meta)
        subtitle_flag = self.get_subtitles(meta)

        data: dict[str, str] = {
            "audio_pt": str(audio_flag),
            "legenda_pt": str(subtitle_flag),
        }

        return data
