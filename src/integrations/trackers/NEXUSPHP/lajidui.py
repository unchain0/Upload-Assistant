# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from typing import Any

from src.domain_models.release import Meta
from src.integrations.trackers.NEXUSPHP import NEXUSPHP

Config = dict[str, Any]


class Lajidui(NEXUSPHP):
    """
    lajidui is a CHINESE Private Torrent Tracker for MOVIES / TV / GENERAL
    """

    banned_groups = ()
    display_name = "Lajidui"
    base_url = "https://pt.lajidui.top"
    source_flag = "[pt.lajidui.top] lajidui"
    torrent_url = f"{base_url}/details.php?id="
    supported_categories = ("TV", "MOVIE")
    tracker_urls = ("https://pt.lajidui.top",)
    allows_bloated_audio = True

    def __init__(self, config: Config) -> None:
        super().__init__(config, "LAJIDUI")

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

    def get_container(self, meta: Meta) -> int:
        iso = 16
        mkv = 10
        mp4 = 11
        other = 17

        if meta.is_disc:
            return iso

        container = meta.container.lower()

        if "mp4" in container:
            return mp4
        if "mkv" in container:
            return mkv

        return other

    @staticmethod
    def _western_regions() -> frozenset[str]:
        return frozenset(
            [
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
            ]
        )

    def get_region(self, meta: Meta) -> int:
        country = meta.origin_country[0].upper()
        if country in self._western_regions():
            return 1
        return {
            "CN": 7,
            "TW": 2,
            "HK": 8,
            "JP": 10,
            "KR": 11,
            "IN": 3,
        }.get(country, 6)

    @staticmethod
    def _disc_type_id(is_disc: str) -> int | None:
        return {"bdmv": 1, "dvd": 6, "hddvd": 2}.get(is_disc)

    @staticmethod
    def _file_type_id(release_type: str) -> int:
        return {
            "remux": 3,
            "webdl": 10,
            "webrip": 10,
            "hdtv": 5,
            "encode": 7,
        }.get(release_type, 11)

    def get_type(self, meta: Meta) -> int:
        disc_type = self._disc_type_id(str(meta.is_disc).lower())
        if disc_type is not None:
            return disc_type
        return self._file_type_id(str(meta.type).lower())

    @staticmethod
    def _codec_rules() -> tuple[tuple[int, tuple[str, ...]], ...]:
        return (
            (7, ("h265", "x265", "hevc")),
            (1, ("h264", "x264", "avc")),
            (2, ("vc1", "vc-1")),
            (4, ("mpeg2", "mpeg-2")),
            (6, ("av1",)),
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
            "2160p": 6,
            "4320p": 7,
        }.get(resolution)

    def get_resolution(self, meta: Meta) -> int:
        resolution = str(meta.resolution).lower()
        mapped = self._resolution_id(resolution)
        if mapped is not None:
            return mapped
        return 4 if meta.sd else 8

    @staticmethod
    def _audio_codec_rules() -> tuple[tuple[int, str], ...]:
        return (
            (1, "flac"),
            (2, "ape"),
            (9, "dts-hd"),
            (3, "dts"),
            (4, "mp3"),
            (5, "ogg"),
            (6, "aac"),
            (8, "wav"),
            (10, "true"),
            (11, "lpcm"),
            (12, "ddp"),
            (13, "dd"),
        )

    def get_audio_codec(self, meta: Meta) -> int:
        audio_codec = str(meta.audio).lower()
        for codec_id, token in self._audio_codec_rules():
            if token in audio_codec:
                return codec_id
        return 7

    def get_group_tag(self, meta: Meta) -> int:
        group_tag = {
            "-ade": 7,
            "-agsvweb": 15,
            "-beast": 18,
            "-beitai": 21,
            "-bmdru": 20,
            "-catedu": 17,
            "-chd": 2,
            "-cmct": 8,
            "-frds": 9,
            "-godramas": 22,
            "-hdhome": 14,
            "-hdsky": 1,
            "-hhweb": 6,
            "-lhd": 19,
            "-other": 5,
            "-ourbits": 12,
            "-pter": 16,
            "-qhstudio": 13,
            "-tjupt": 10,
            "-ubits": 11,
            "-wiki": 4,
            "-原创": 3,
        }

        group = meta.tag.lower() if meta.tag else ""
        return group_tag.get(group, 5)

    @staticmethod
    def _has_chinese(values: list[str] | str) -> bool:
        return "Chinese" in values or "Mandarin" in values

    @staticmethod
    def _has_chinese_english(values: list[str] | str) -> bool:
        return "Chinese" in values and "English" in values

    @staticmethod
    def _is_single_episode(meta: Meta) -> bool:
        return meta.category == "TV" and not meta.tv_pack

    @classmethod
    def _checkbox_options(cls, meta: Meta) -> tuple[tuple[bool, int], ...]:
        audio_tracks = meta.audio_languages or []
        subtitle_tracks = meta.subtitle_languages or []
        return (
            (bool(meta.exclusive), 1),
            (cls._has_chinese(audio_tracks), 5),
            (cls._has_chinese_english(audio_tracks), 15),
            ("English" in audio_tracks, 14),
            (cls._has_chinese_english(subtitle_tracks), 16),
            (len(audio_tracks) > 1, 17),
            ("Cantonese" in audio_tracks, 11),
            (cls._has_chinese(subtitle_tracks), 6),
            ("HDR" in str(meta.hdr).upper(), 7),
            (bool(meta.diy_disc), 4),
            (cls._is_single_episode(meta), 12),
        )

    def get_checkboxes(self, meta: Meta) -> list[str]:
        return [
            str(checkbox_id)
            for enabled, checkbox_id in self._checkbox_options(meta)
            if enabled
        ]
