# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from typing import Any

from src.domain_models.release import Meta
from src.integrations.trackers.NEXUSPHP import NEXUSPHP

Config = dict[str, Any]


class PTFans(NEXUSPHP):
    """
    PTFANS is a CHINESE Private Torrent Tracker for MOVIES / TV / GENERAL
    """

    banned_groups = ()
    display_name = "PTFans"
    base_url = "https://ptfans.cc"
    source_flag = "[ptfans.cc] PTFans"
    torrent_url = f"{base_url}/details.php?id="
    supported_categories = ("TV", "MOVIE")
    tracker_urls = ("https://ptfans.cc",)
    allows_bloated_audio = True

    def __init__(self, config: Config) -> None:
        super().__init__(config, "PTFANS")

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
            return 406
        if meta.anime or "animation" in combined:
            return 414
        return None

    def get_category(self, meta: Meta) -> int:
        category = str(meta.category).upper()
        genres = self._metadata_text(meta.genres)
        keywords = self._metadata_text(meta.keywords)
        themed = self._themed_category(meta, genres, keywords)
        if themed is not None:
            return themed
        if category == "TV":
            return 405 if self._is_tv_show_genre(genres) else 404
        return 401

    @staticmethod
    def _disc_type_id(is_disc: str) -> int | None:
        if is_disc == "bdmv":
            return 6
        return 2 if "dvd" in is_disc else None

    @staticmethod
    def _file_type_id(release_type: str) -> int:
        return {
            "remux": 3,
            "webdl": 5,
            "webrip": 5,
            "encode": 8,
        }.get(release_type, 9)

    def get_type(self, meta: Meta) -> int:
        disc_type = self._disc_type_id(str(meta.is_disc).lower())
        if disc_type is not None:
            return disc_type
        return self._file_type_id(str(meta.type).lower())

    @staticmethod
    def _codec_family(codec: str) -> str:
        rules = (
            ("av1", ("av1",)),
            ("h265", ("h265", "x265", "hevc")),
            ("h264", ("h264", "x264", "avc")),
            ("mpeg2", ("mpeg2", "mpeg-2")),
            ("vc1", ("vc1", "vc-1")),
            ("xvid", ("xvid",)),
        )
        for family, tokens in rules:
            if any(token in codec for token in tokens):
                return family
        return "other"

    @staticmethod
    def _is_bluray_source(source: str) -> bool:
        return "bluray" in source or "blu-ray" in source

    def get_codec(self, meta: Meta) -> int:
        family = self._codec_family(str(meta.video_codec).lower())
        bluray = self._is_bluray_source(str(meta.source or ""))
        mapping = {
            ("av1", False): 8,
            ("av1", True): 8,
            ("h265", False): 2,
            ("h265", True): 5,
            ("h264", False): 1,
            ("h264", True): 4,
            ("mpeg2", False): 6,
            ("mpeg2", True): 6,
            ("vc1", False): 9,
            ("vc1", True): 3,
            ("xvid", False): 7,
            ("xvid", True): 7,
        }
        return mapping.get((family, bluray), 9)

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
        resolution = str(meta.resolution).lower()
        mapped = self._resolution_id(resolution)
        if mapped is not None:
            return mapped
        return 4 if meta.sd else 1

    def get_group_tag(self, meta: Meta) -> int:
        group_tag = {
            "-chd": 2,
            "-hds": 1,
            "-mysilu": 3,
            "-wiki": 4,
        }

        group = meta.tag.lower() if meta.tag else ""
        return group_tag.get(group, 5)
