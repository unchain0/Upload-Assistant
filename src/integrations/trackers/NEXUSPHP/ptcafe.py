# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from typing import Any

from src.domain_models.release import Meta
from src.integrations.trackers.NEXUSPHP import NEXUSPHP

Config = dict[str, Any]


class PTCafe(NEXUSPHP):
    """
    PTCAFE (咖啡) is a CHINESE Private Torrent Tracker for MOVIES / TV / GENERAL
    """

    banned_groups = ()
    display_name = "PTCafe"
    base_url = "https://ptcafe.club"
    source_flag = "[ptcafe.club] 咖啡"
    torrent_url = f"{base_url}/details.php?id="
    supported_categories = ("TV", "MOVIE")
    tracker_urls = ("https://tracker.ptcafe.club",)
    allows_bloated_audio = True

    def __init__(self, config: Config) -> None:
        super().__init__(config, "PTCAFE")

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
    def _western_regions() -> frozenset[str]:
        return frozenset(
            (
                "AG",
                "AI",
                "AR",
                "AW",
                "BB",
                "BL",
                "BM",
                "BO",
                "BQ",
                "BR",
                "BS",
                "BV",
                "BZ",
                "CA",
                "CL",
                "CO",
                "CR",
                "CU",
                "CW",
                "DM",
                "DO",
                "EC",
                "FK",
                "GD",
                "GF",
                "GL",
                "GP",
                "GS",
                "GT",
                "GY",
                "HN",
                "HT",
                "JM",
                "KN",
                "KY",
                "LC",
                "MF",
                "MQ",
                "MS",
                "MX",
                "NI",
                "PA",
                "PE",
                "PM",
                "PR",
                "PY",
                "SR",
                "SV",
                "SX",
                "TC",
                "TT",
                "US",
                "UY",
                "VC",
                "VE",
                "VG",
                "VI",
                "AD",
                "AL",
                "AT",
                "AX",
                "BA",
                "BE",
                "BG",
                "BY",
                "CH",
                "CZ",
                "DE",
                "DK",
                "EE",
                "ES",
                "FI",
                "FO",
                "FR",
                "GB",
                "GG",
                "GI",
                "GR",
                "HR",
                "HU",
                "IE",
                "IM",
                "IS",
                "IT",
                "JE",
                "LI",
                "LT",
                "LU",
                "LV",
                "MC",
                "MD",
                "ME",
                "MK",
                "MT",
                "NL",
                "NO",
                "PL",
                "PT",
                "RO",
                "RS",
                "RU",
                "SE",
                "SI",
                "SJ",
                "SK",
                "SM",
                "SU",
                "UA",
                "VA",
                "XC",
            )
        )

    def get_region(self, meta: Meta) -> int:
        country = meta.origin_country[0].upper()
        if country in self._western_regions():
            return 3
        return {
            "CN": 1,
            "TW": 2,
            "HK": 2,
            "JP": 4,
            "KR": 5,
            "IN": 6,
        }.get(country, 7)

    @staticmethod
    def _bdmv_type_id(meta: Meta) -> int:
        uhd = str(meta.resolution).lower() == "2160p"
        if meta.diy_disc:
            return 2 if uhd else 5
        return 1 if uhd else 4

    @classmethod
    def _disc_type_id(cls, meta: Meta) -> int | None:
        is_disc = str(meta.is_disc).lower()
        if is_disc == "bdmv":
            return cls._bdmv_type_id(meta)
        return 10 if "dvd" in is_disc else None

    @staticmethod
    def _file_type_id(meta: Meta) -> int:
        release_type = str(meta.type).lower()
        if release_type == "remux":
            return 3 if str(meta.resolution).lower() == "2160p" else 6
        if release_type in {"webdl", "webrip"}:
            return 8
        if "tv" in release_type:
            return 9
        return 7

    def get_type(self, meta: Meta) -> int:
        disc_type = self._disc_type_id(meta)
        return disc_type if disc_type is not None else self._file_type_id(meta)

    def get_codec(self, meta: Meta) -> int:
        codec = meta.video_codec.lower()
        mappings = (
            (("h265", "x265", "hevc"), 1),
            (("h264", "x264", "avc"), 2),
            (("vc1", "vc-1"), 5),
            (("mpeg2", "mpeg-2"), 6),
            (("mpeg4", "mpeg-4"), 7),
            (("xvid",), 8),
            (("vp9",), 9),
            (("divx",), 10),
        )
        return next(
            (
                value
                for tokens, value in mappings
                if any(token in codec for token in tokens)
            ),
            11,
        )

    @staticmethod
    def _resolution_rules() -> tuple[tuple[int, str], ...]:
        return (
            (3, "1080"),
            (4, "720"),
            (2, "2160"),
            (1, "4320"),
        )

    def get_resolution(self, meta: Meta) -> int:
        resolution = str(meta.resolution).lower()
        for resolution_id, token in self._resolution_rules():
            if token in resolution:
                return resolution_id
        return 5 if meta.sd else 6

    @staticmethod
    def _audio_codec_rules() -> tuple[tuple[int, str], ...]:
        return (
            (1, "dts:x 7.1"),
            (2, "hd ma"),
            (3, "hd hr"),
            (4, "dts-hd"),
            (5, "dts:x"),
            (6, "lpcm"),
            (7, "dd"),
            (8, "atmos"),
            (9, "aac"),
            (10, "true"),
            (11, "dts"),
            (12, "flac"),
            (13, "ape"),
            (14, "mp3"),
            (15, "wav"),
            (16, "opus"),
            (17, "ogg"),
        )

    def get_audio_codec(self, meta: Meta) -> int:
        audio_codec = str(meta.audio).lower()
        for codec_id, token in self._audio_codec_rules():
            if token in audio_codec:
                return codec_id
        return 18

    def get_group_tag(self, meta: Meta) -> int:
        group_tag = {
            "-ade": 1,
            "-adweb": 2,
            "-audies": 3,
            "-beast": 4,
            "-beitai": 5,
            "-beyondhd": 6,
            "-btstv": 7,
            "-cafetv": 8,
            "-cafeweb": 9,
            "-chd": 10,
            "-chdweb": 11,
            "-cmct": 12,
            "-djweb": 13,
            "-frds": 14,
            "-hdctv": 15,
            "-hdh": 16,
            "-hdhome": 17,
            "-hdsky": 18,
            "-hdsweb": 19,
            "-hhweb": 20,
            "-mteam": 21,
            "-mweb": 22,
            "-ourbits": 23,
            "-ourtv": 24,
            "-ptcafe": 25,
            "-pterweb": 26,
            "-qhstudio": 27,
            "-ttg": 28,
            "-wiki": 29,
        }

        group = meta.tag.lower() if meta.tag else ""
        return group_tag.get(group, 30)

    @staticmethod
    def _has_chinese(values: list[str] | str) -> bool:
        return "Chinese" in values or "Mandarin" in values

    @classmethod
    def _checkbox_options(cls, meta: Meta) -> tuple[tuple[bool, int], ...]:
        audio_tracks = meta.audio_languages or []
        subtitle_tracks = meta.subtitle_languages or []
        hdr = str(meta.hdr).upper()
        return (
            (bool(meta.exclusive), 5),
            (cls._has_chinese(audio_tracks), 7),
            ("Cantonese" in audio_tracks, 8),
            (cls._has_chinese(subtitle_tracks), 9),
            ("HDR" in hdr, 12),
            ("DV" in hdr, 11),
            (bool(meta.diy_disc), 13),
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

    def get_imdb_url(self, meta: Meta) -> str:
        _ = meta
        return ""
