# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from typing import Any

from src.domain_models.release import Meta
from src.integrations.trackers.NEXUSPHP import NEXUSPHP

Config = dict[str, Any]


class PTGTK(NEXUSPHP):
    """
    PT GTK is a CHINESE Private Torrent Tracker for MOVIES / TV / GENERAL
    """

    banned_groups = ()
    display_name = "PTGTK"
    base_url = "https://pt.gtkpw.xyz"
    source_flag = "[pt.gtkpw.xyz] PT GTK"
    torrent_url = f"{base_url}/details.php?id="
    supported_categories = ("TV", "MOVIE")
    tracker_urls = ("https://t.myaltbox.com",)
    allows_bloated_audio = True

    def __init__(self, config: Config) -> None:
        super().__init__(config, "PTGTK")

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
        is_disc = str(meta.is_disc).lower()
        if is_disc == "bdmv":
            return 10 if str(meta.resolution).lower() == "2160p" else 1
        return {"dvd": 6, "hddvd": 2}.get(is_disc)

    @staticmethod
    def _file_type_id(release_type: str) -> int:
        return {
            "remux": 3,
            "webdl": 11,
            "webrip": 11,
            "hdtv": 5,
            "encode": 7,
        }.get(release_type, 7)

    def get_type(self, meta: Meta) -> int:
        disc_type = self._disc_type_id(meta)
        if disc_type is not None:
            return disc_type
        return self._file_type_id(str(meta.type).lower())

    @staticmethod
    def _codec_rules() -> tuple[tuple[int, tuple[str, ...]], ...]:
        return (
            (7, ("av1",)),
            (6, ("h265", "x265", "hevc")),
            (1, ("h264", "x264", "avc")),
            (4, ("mpeg2", "mpeg-2")),
            (2, ("vc1", "vc-1")),
            (8, ("vp9",)),
            (3, ("xvid",)),
        )

    def get_codec(self, meta: Meta) -> int:
        codec = str(meta.video_codec).lower()
        for codec_id, tokens in self._codec_rules():
            if any(token in codec for token in tokens):
                return codec_id
        return 5

    @staticmethod
    def _resolution_id(resolution: str) -> int | None:
        return {
            "1080p": 1,
            "1080i": 2,
            "720p": 3,
            "2160p": 5,
            "4320p": 6,
        }.get(resolution)

    def get_resolution(self, meta: Meta) -> int:
        mapped = self._resolution_id(str(meta.resolution).lower())
        if mapped is not None:
            return mapped
        return 4 if meta.sd else 1

    def get_group_tag(self, meta: Meta) -> int:
        group_tag = {
            "-beast": 11,
            "-chd": 2,
            "-cmct": 6,
            "-frds": 9,
            "-hds": 1,
            "-mark": 7,
            "-mteam": 8,
            "-mysilu": 3,
            "-pthome": 10,
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
            (cls._has_chinese(subtitle_tracks), 6),
            ("HDR" in str(meta.hdr).upper(), 7),
        )

    def get_checkboxes(self, meta: Meta) -> list[str]:
        return [
            str(checkbox_id)
            for enabled, checkbox_id in self._checkbox_options(meta)
            if enabled
        ]

    def get_anonymous(self, meta: Meta) -> bool:
        return not (
            meta.anon == 0
            and not self.config["TRACKERS"][self.tracker].get("anon", False)
        )
