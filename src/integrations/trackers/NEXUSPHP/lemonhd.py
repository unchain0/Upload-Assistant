# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from typing import Any

from src.domain_models.release import Meta
from src.integrations.trackers.NEXUSPHP import NEXUSPHP

Config = dict[str, Any]


class LemonHD(NEXUSPHP):
    """
    LemonHD is a CHINESE Private Torrent Tracker for MOVIES / TV / GENERAL
    """

    banned_groups = ()
    display_name = "LemonHD"
    base_url = "https://lemonhd.net"
    source_flag = "[lemonhd.net] LemonHD"
    torrent_url = f"{base_url}/details.php?id="
    supported_categories = ("TV", "MOVIE")
    tracker_urls = ("https://lemonhd.net",)
    allows_bloated_audio = True

    def __init__(self, config: Config) -> None:
        super().__init__(config, "LEMONHD")

    @staticmethod
    def _metadata_text(values: list[str]) -> str:
        return ", ".join(values).lower()

    @staticmethod
    def _tv_show_keywords() -> tuple[str, ...]:
        return (
            "award show",
            "competition",
            "game show",
            "music show",
            "performance",
            "reality television",
            "reality tv",
            "reality",
            "stand-up",
            "talk show",
            "tv show",
            "variety",
        )

    @classmethod
    def _is_tv_show_genre(cls, genres: str) -> bool:
        return any(
            re.search(
                rf"(^|,\s*){re.escape(keyword)}(\s*,|$)",
                genres,
                re.IGNORECASE,
            )
            for keyword in cls._tv_show_keywords()
        )

    @staticmethod
    def _themed_category(meta: Meta, genres: str, keywords: str) -> int | None:
        combined = f"{genres}, {keywords}"
        if "documentary" in combined:
            return 405
        if meta.anime or "animation" in combined:
            return 403
        return None

    def get_category(self, meta: Meta) -> int:
        category = str(meta.category).upper()
        genres = self._metadata_text(meta.genres)
        keywords = self._metadata_text(meta.keywords)
        themed = self._themed_category(meta, genres, keywords)
        if themed is not None:
            return themed
        if category == "TV":
            return 407 if self._is_tv_show_genre(genres) else 406
        return 401

    @staticmethod
    def _disc_type_id(meta: Meta) -> int | None:
        is_disc = str(meta.is_disc or "").lower()
        if is_disc == "bdmv":
            return 6 if str(meta.resolution).lower() == "2160p" else 1
        return 8 if "dvd" in is_disc else None

    @staticmethod
    def _file_type_id(release_type: str) -> int:
        if release_type == "remux":
            return 3
        if "web" in release_type:
            return 4
        if release_type == "hdtv":
            return 5
        return 2

    def get_type(self, meta: Meta) -> int:
        disc_type = self._disc_type_id(meta)
        if disc_type is not None:
            return disc_type
        return self._file_type_id(str(meta.type).lower())

    @staticmethod
    def _codec_rules() -> tuple[tuple[int, tuple[str, ...]], ...]:
        return (
            (2, ("h265", "x265", "hevc", "265")),
            (1, ("h264", "x264", "avc", "264")),
            (3, ("vc1", "vc-1")),
            (4, ("mpeg2", "mpeg-2")),
        )

    def get_codec(self, meta: Meta) -> int:
        codec = str(meta.video_codec).lower()
        for codec_id, tokens in self._codec_rules():
            if any(token in codec for token in tokens):
                return codec_id
        return 5

    @staticmethod
    def _resolution_rules() -> tuple[tuple[int, tuple[str, ...]], ...]:
        return (
            (1, ("2160", "4k")),
            (2, ("1080",)),
            (3, ("720",)),
        )

    def get_resolution(self, meta: Meta) -> int:
        resolution = str(meta.resolution).lower()
        for resolution_id, tokens in self._resolution_rules():
            if any(token in resolution for token in tokens):
                return resolution_id
        return 4 if meta.sd else 5

    @staticmethod
    def _audio_codec_rules() -> tuple[tuple[int, tuple[str, ...]], ...]:
        return (
            (1, ("atmos", "true")),
            (2, ("true",)),
            (3, ("dts-hd ma", "dtshd ma")),
            (4, ("dts:x", "dtsx")),
            (3, ("dts-hd",)),
            (5, ("dts",)),
            (7, ("ddp", "eac3", "e-ac-3")),
            (6, ("dd", "ac3", "ac-3")),
            (8, ("aac",)),
            (9, ("lpcm", "pcm")),
            (10, ("flac",)),
            (11, ("wav",)),
            (12, ("ape",)),
        )

    @staticmethod
    def _audio_rule_matches(audio_codec: str, tokens: tuple[str, ...]) -> bool:
        if tokens == ("atmos", "true"):
            return all(token in audio_codec for token in tokens)
        return any(token in audio_codec for token in tokens)

    def get_audio_codec(self, meta: Meta) -> int:
        audio_codec = str(meta.audio).lower()
        for codec_id, tokens in self._audio_codec_rules():
            if self._audio_rule_matches(audio_codec, tokens):
                return codec_id
        return 13

    def get_group_tag(self, meta: Meta) -> int:
        group_tag = {
            "-chd": 2,
            "-hds": 1,
            "-lemonhd": 6,
            "-mysilu": 3,
            "-wiki": 4,
        }

        group = meta.tag.lower() if meta.tag else ""
        return group_tag.get(group, 5)

    @staticmethod
    def _has_chinese(values: list[str] | str) -> bool:
        return "Chinese" in values or "Mandarin" in values

    @staticmethod
    def _is_four_k(meta: Meta) -> bool:
        resolution = str(meta.resolution).lower()
        return "2160" in resolution or "4k" in resolution

    @classmethod
    def _checkbox_options(cls, meta: Meta) -> tuple[tuple[bool, int], ...]:
        audio_tracks = meta.audio_languages or []
        subtitle_tracks = meta.subtitle_languages or []
        return (
            (bool(meta.exclusive), 1),
            (cls._has_chinese(audio_tracks), 5),
            (cls._has_chinese(subtitle_tracks), 6),
            ("HDR" in str(meta.hdr).upper(), 7),
            (bool(meta.diy_disc), 4),
            (cls._is_four_k(meta), 8),
            (str(meta.is_disc or "").lower() == "bdmv", 9),
        )

    def get_checkboxes(self, meta: Meta) -> list[str]:
        return [
            str(checkbox_id)
            for enabled, checkbox_id in self._checkbox_options(meta)
            if enabled
        ]

    def get_douban_url(self, meta: Meta) -> str:
        return super().get_douban_url(meta)

    def get_imdb_url(self, meta: Meta) -> str:
        _ = meta
        return ""

    async def get_anonymous_data(self, meta: Meta) -> dict[str, str]:
        _ = meta
        return {}
