# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from typing import Any

from src.domain_models.release import Meta
from src.integrations.trackers.NEXUSPHP import NEXUSPHP

Config = dict[str, Any]


class LongPT(NEXUSPHP):
    """
    LONGPT is a CHINESE Private Torrent Tracker for MOVIES / TV / GENERAL
    """

    banned_groups = ()
    display_name = "LongPT"
    base_url = "https://longpt.org"
    source_flag = "[longpt.org] LongPT"
    torrent_url = f"{base_url}/details.php?id="
    supported_categories = ("TV", "MOVIE")
    tracker_urls = ("https://longpt.org",)
    allows_bloated_audio = True

    def __init__(self, config: Config) -> None:
        super().__init__(config, "LONGPT")

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
            return 404
        if meta.anime or "animation" in combined:
            return 405
        return None

    def get_category(self, meta: Meta) -> int:
        category = str(meta.category).upper()
        genres = self._metadata_text(meta.genres)
        keywords = self._metadata_text(meta.keywords)
        themed = self._themed_category(meta, genres, keywords)
        if themed is not None:
            return themed
        if category == "TV":
            return 403 if self._is_tv_show_genre(genres) else 402
        return 401

    @staticmethod
    def _is_uhd(meta: Meta) -> bool:
        return str(meta.resolution).lower() == "2160p"

    @classmethod
    def _disc_type_id(cls, meta: Meta) -> int | None:
        is_disc = str(meta.is_disc or "").lower()
        if is_disc == "bdmv":
            return 2 if cls._is_uhd(meta) else 1
        return 6 if "dvd" in is_disc else None

    @classmethod
    def _file_type_id(cls, meta: Meta) -> int:
        release_type = str(meta.type).lower()
        if release_type == "remux":
            return 11 if cls._is_uhd(meta) else 3
        if release_type in {"webdl", "webrip"}:
            return 4
        if "tv" in release_type:
            return 5
        return 7

    def get_type(self, meta: Meta) -> int:
        disc_type = self._disc_type_id(meta)
        return disc_type if disc_type is not None else self._file_type_id(meta)

    @staticmethod
    def _codec_rules() -> tuple[tuple[int, tuple[str, ...]], ...]:
        return (
            (2, ("h265", "x265", "hevc")),
            (1, ("h264", "x264", "avc")),
            (3, ("vc1", "vc-1")),
            (4, ("mpeg2", "mpeg-2")),
            (5, ("av1",)),
        )

    def get_codec(self, meta: Meta) -> int:
        codec = str(meta.video_codec).lower()
        for codec_id, tokens in self._codec_rules():
            if any(token in codec for token in tokens):
                return codec_id
        return 6

    @staticmethod
    def _resolution_rules() -> tuple[tuple[int, str], ...]:
        return (
            (6, "4320"),
            (5, "2160"),
            (1, "1440"),
            (2, "1080"),
            (3, "720"),
        )

    def get_resolution(self, meta: Meta) -> int:
        resolution = str(meta.resolution).lower()
        for resolution_id, token in self._resolution_rules():
            if token in resolution:
                return resolution_id
        return 4 if meta.sd else 7

    def get_audio_codec(self, meta: Meta) -> int:
        audio_codec = meta.audio.lower()
        mappings = (
            ("flac", 1),
            ("dts-hd", 3),
            ("dts:x", 12),
            ("dts", 13),
            ("lpcm", 14),
            ("ddp", 10),
            ("dd", 15),
            ("alac", 16),
            ("wav", 17),
            ("av3a", 18),
            ("true", 19),
            ("ape", 2),
            ("mp3", 4),
            ("ogg", 5),
            ("aac", 6),
            ("m4a", 8),
            ("atmos", 9),
        )
        return next(
            (value for token, value in mappings if token in audio_codec), 11
        )

    def get_group_tag(self, meta: Meta) -> int:
        group_tag = {
            "-cmct": 7,
            "-hhweb": 8,
            "-longa": 1,
            "-longpt": 3,
            "-longweb": 2,
            "-rl": 6,
            "-wiki": 4,
        }

        group = meta.tag.lower() if meta.tag else ""
        return group_tag.get(group, 5)

    @staticmethod
    def _has_chinese(values: list[str] | str) -> bool:
        return "Chinese" in values or "Mandarin" in values

    @classmethod
    def _checkbox_options(cls, meta: Meta) -> tuple[tuple[bool, int], ...]:
        audio_tracks = meta.audio_languages or []
        subtitle_tracks = meta.subtitle_languages or []
        return (
            (bool(meta.exclusive), 1),
            (cls._has_chinese(audio_tracks), 5),
            ("English" in audio_tracks, 9),
            (cls._has_chinese(subtitle_tracks), 6),
            ("HDR" in str(meta.hdr).upper(), 7),
            (bool(meta.diy_disc), 4),
        )

    def get_checkboxes(self, meta: Meta) -> list[str]:
        return [
            str(checkbox_id)
            for enabled, checkbox_id in self._checkbox_options(meta)
            if enabled
        ]

    def get_douban_url(self, meta: Meta) -> str:
        _ = meta
        return ""
