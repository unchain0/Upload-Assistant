# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from typing import Any

from src.domain_models.release import Meta
from src.integrations.trackers.NEXUSPHP import NEXUSPHP

Config = dict[str, Any]


class PTZone(NEXUSPHP):
    """
    PTZone is a CHINESE Private Torrent Tracker for MOVIES / TV / GENERAL
    """

    banned_groups = ()
    display_name = "PTZone"
    base_url = "https://ptzone.xyz"
    source_flag = "[ptzone.xyz] PTzone"
    torrent_url = f"{base_url}/details.php?id="
    supported_categories = ("TV", "MOVIE")
    tracker_urls = ("https://ptzone.xyz",)
    allows_bloated_audio = True

    def __init__(self, config: Config) -> None:
        super().__init__(config, "PTZONE")

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
    def _disc_type_id(meta: Meta) -> int | None:
        is_disc = str(meta.is_disc or "").lower()
        if is_disc == "bdmv":
            return 10 if str(meta.resolution).lower() == "2160p" else 1
        return 6 if "dvd" in is_disc else None

    @staticmethod
    def _file_type_id(release_type: str) -> int:
        if release_type == "remux":
            return 3
        if "web" in release_type:
            return 4
        if release_type == "hdtv":
            return 5
        return 7

    def get_type(self, meta: Meta) -> int:
        disc_type = self._disc_type_id(meta)
        if disc_type is not None:
            return disc_type
        return self._file_type_id(str(meta.type).lower())

    @staticmethod
    def _codec_rules() -> tuple[tuple[int, tuple[str, ...]], ...]:
        return (
            (6, ("h265", "x265", "hevc", "265")),
            (1, ("h264", "x264", "avc", "264")),
            (2, ("vc1", "vc-1")),
            (3, ("mpeg2", "mpeg-2")),
            (4, ("mpeg4", "mpeg-4")),
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
            (5, ("4320", "8k")),
            (6, ("2160", "4k")),
            (1, ("1080p",)),
            (2, ("1080i",)),
            (1, ("1080",)),
            (3, ("720",)),
        )

    def get_resolution(self, meta: Meta) -> int:
        resolution = str(meta.resolution).lower()
        for resolution_id, tokens in self._resolution_rules():
            if any(token in resolution for token in tokens):
                return resolution_id
        return 4 if meta.sd else 1

    @staticmethod
    def _audio_codec_rules() -> tuple[tuple[int, tuple[str, ...]], ...]:
        return (
            (1, ("flac",)),
            (2, ("ape",)),
            (10, ("dts-hd ma", "dtshd ma")),
            (13, ("dts-hd",)),
            (3, ("dts",)),
            (4, ("mp3",)),
            (5, ("ogg",)),
            (6, ("aac",)),
            (12, ("ddp", "eac3", "e-ac-3")),
            (11, ("dd", "ac3", "ac-3")),
            (14, ("true",)),
            (15, ("wav",)),
        )

    def get_audio_codec(self, meta: Meta) -> int:
        audio_codec = str(meta.audio).lower()
        for codec_id, tokens in self._audio_codec_rules():
            if any(token in audio_codec for token in tokens):
                return codec_id
        return 7

    def get_group_tag(self, meta: Meta) -> int:
        group_tag = {
            "-chd": 2,
            "-hds": 1,
            "-mysilu": 3,
            "-ptzweb": 6,
            "-wiki": 4,
        }

        group = meta.tag.lower() if meta.tag else ""
        return group_tag.get(group, 5)

    @staticmethod
    def _has_chinese(values: list[str] | str) -> bool:
        return "Chinese" in values or "Mandarin" in values

    @staticmethod
    def _tv_checkbox(meta: Meta) -> int | None:
        if meta.tv_pack:
            return 9
        return 8 if meta.category == "TV" else None

    @classmethod
    def _checkbox_options(cls, meta: Meta) -> tuple[tuple[bool, int], ...]:
        audio_tracks = meta.audio_languages or []
        subtitle_tracks = meta.subtitle_languages or []
        tv_checkbox = cls._tv_checkbox(meta)
        return (
            (bool(meta.exclusive), 1),
            (cls._has_chinese(audio_tracks), 5),
            (cls._has_chinese(subtitle_tracks), 6),
            (bool(meta.diy_disc), 4),
            ("HDR" in str(meta.hdr).upper(), 7),
            (tv_checkbox is not None, tv_checkbox or 0),
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
        anonymous = not (
            meta.anon == 0 and not self.tracker_config.get("anon", False)
        )
        return {"anonymous": "1"} if anonymous else {}
