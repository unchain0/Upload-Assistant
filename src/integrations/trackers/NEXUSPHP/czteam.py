# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
from __future__ import annotations

import re
from typing import Any, ClassVar

from src.domain_models.release import Meta
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.NEXUSPHP import NEXUSPHP

Config = dict[str, Any]


class CZTeam(NEXUSPHP):
    """CZTeam is a Romanian private tracker that runs on NexusPHP.

    The category, type, codec and resolution ids are not hard-coded here:
    they are read from ``TRACKERS.CZTEAM`` in the configuration until the
    site ids are confirmed. An unmapped category never reaches the upload
    call.
    """

    tracker = "CZTEAM"
    display_name = "CZTeam"
    base_url = "https://czteam.me"
    source_flag = "[czteam.me] CZTeam"
    torrent_url = f"{base_url}/details.php?id="
    tracker_urls = ("https://czteam.me",)
    supported_categories = ("MOVIE", "TV", "GAME", "MUSIC", "XXX")
    allows_bloated_audio = True
    banned_groups: tuple[str, ...] = ()

    _ID_MAPPING_KEYS: ClassVar[dict[str, str]] = {
        "category": "categories",
        "type": "types",
        "codec": "codecs",
        "resolution": "resolutions",
    }

    def __init__(self, config: Config) -> None:
        super().__init__(config, "CZTEAM")

    def _configured_ids(self, kind: str) -> dict[str, int]:
        raw = self.tracker_config.get(self._ID_MAPPING_KEYS[kind], {})
        if not isinstance(raw, dict):
            return {}
        values: dict[str, int] = {}
        for key, value in raw.items():
            try:
                mapped = int(value)
            except (TypeError, ValueError):
                continue
            if mapped > 0:
                values[str(key).strip().upper()] = mapped
        return values

    def _mapped_id(self, kind: str, meta_value: object) -> int:
        key = str(meta_value or "").strip().upper()
        if not key:
            return 0
        return self._configured_ids(kind).get(key, 0)

    _CATEGORY_IDS: ClassVar[dict[str, int]] = {
        "ANIME": 23,
        "DOCS": 25,
        "GAME": 21,
        "IMAGES": 24,
        "MAC": 31,
        "MOBILE": 9,
        "MOVIE": 29,
        "MUSIC": 6,
        "PROGRAMS": 22,
        "SOFTWARE": 22,
        "SPORTS": 32,
        "TV": 5,
        "XXX": 1,
    }
    _RESOLUTIONS: ClassVar[tuple[str, ...]] = (
        "480p",
        "576p",
        "720p",
        "1080p",
        "2160p",
    )
    _CODECS: ClassVar[tuple[str, ...]] = (
        "H.264",
        "H.265",
        "x264",
        "x265",
        "XviD",
        "AV1",
    )
    _CONTAINERS: ClassVar[tuple[str, ...]] = (
        "MKV",
        "AVI",
        "MP4",
        "TS",
        "ISO",
    )
    _SOURCES: ClassVar[tuple[str, ...]] = (
        "BluRay",
        "BDRip",
        "WEB-DL",
        "WEBRip",
        "HDTV",
        "DVD",
        "DVDRip",
    )

    def get_category(self, meta: Meta) -> int:
        configured = self._mapped_id("category", meta.category)
        if configured:
            return configured
        category = str(meta.category or "").strip().upper()
        if category == "MOVIE":
            return self._movie_category(meta)
        if category == "TV":
            return 5 if str(meta.resolution or "") in {"720p", "1080p", "2160p"} else 7
        if category == "GAME":
            return self._game_category(meta)
        if category == "MUSIC":
            format_value = str(meta.format or meta.type or "").upper()
            return 35 if format_value in {"FLAC", "ALAC", "WAV", "AIFF", "DSD"} else 6
        return self._CATEGORY_IDS.get(category, 0)

    @staticmethod
    def _movie_category(meta: Meta) -> int:
        if meta.is_disc:
            return 36
        release_type = str(meta.type or "").upper()
        if release_type in {"XVID", "DVDRIP"}:
            return 19
        if "3D" in str(meta.name or "").upper():
            return 37
        return 29

    @staticmethod
    def _game_category(meta: Meta) -> int:
        platform = str(meta.platform or "").casefold()
        if any(
            value in platform
            for value in ("playstation", "xbox", "switch", "nintendo", "wii", "ps4", "ps5")
        ):
            return 12
        if meta.is_disc:
            return 4
        return 21

    @classmethod
    def _enum_value(cls, value: object, allowed: tuple[str, ...]) -> str:
        text = str(value or "").strip()
        for candidate in allowed:
            if text.casefold() == candidate.casefold():
                return candidate
        return ""

    def get_type(self, meta: Meta) -> int:
        return self._mapped_id("type", meta.type)

    def get_codec(self, meta: Meta) -> int:
        return self._mapped_id("codec", meta.video_codec)

    def get_resolution(self, meta: Meta) -> int:
        return self._mapped_id("resolution", meta.resolution)

    _VIDEO_CATEGORIES: ClassVar[tuple[str, ...]] = ("MOVIE", "TV", "ANIME")
    _RESOLUTION_TOKENS: ClassVar[tuple[str, ...]] = (
        "2160p",
        "1080p",
        "1080i",
        "720p",
        "576p",
        "480p",
    )
    _SCENE_NAME_RE: ClassVar[re.Pattern[str]] = re.compile(
        r"^[^\s]+\.(?:19|20)\d{2}\..+-[A-Za-z0-9]+$"
    )

    def _video_skip_reason(self, meta: Meta) -> str:
        if str(meta.category or "").upper() not in self._VIDEO_CATEGORIES:
            return ""
        name = str(meta.name or "").strip()
        if name:
            if re.search(r"(?i)(?:^|[.\s_-])(?:nuked|scene[.\s_-]?rejected)(?:$|[.\s_-])", name):
                return "nuked or scene-rejected releases are not allowed"
            if not self._SCENE_NAME_RE.search(name) or not any(
                token in name for token in self._RESOLUTION_TOKENS
            ):
                return (
                    "the release title must follow the scene naming "
                    "Title.Year[.Episode].Resolution.Source.Codec.Audio-GROUP"
                )
        missing = [
            label
            for label, present in (
                ("IMDb id", bool(meta.imdb_id or meta.imdb_tt)),
                ("MediaInfo", bool(meta.mediainfo or meta.bdinfo)),
                ("video codec", bool(meta.video_codec)),
                ("source", bool(meta.source)),
                ("resolution", bool(meta.resolution)),
                ("container", bool(meta.container)),
            )
            if not present
        ]
        if missing:
            return "required video metadata is missing: " + ", ".join(missing)
        return ""

    async def upload(self, meta: Meta) -> bool:
        reason = self._video_skip_reason(meta)
        if reason:
            logger.info(
                f"{self.tracker}: [bold red]{reason}. Skipping upload.[/bold red]"
            )
            meta.skipping = self.tracker
            return False
        if self.get_category(meta) <= 0:
            logger.info(
                f"{self.tracker}: [bold red]The category '{meta.category}' has no "
                "configured id for this tracker; skipping upload.[/bold red]"
            )
            meta.skipping = self.tracker
            return False
        return await super().upload(meta)
