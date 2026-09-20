# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from typing import Any

from src.domain_models.release import Meta
from src.integrations.trackers.AVISTAZ import AZTrackerBase
from src.integrations.trackers.common import Common

_AFRICA = frozenset(
    [
        "AO",
        "BF",
        "BI",
        "BJ",
        "BW",
        "CD",
        "CF",
        "CG",
        "CI",
        "CM",
        "CV",
        "DJ",
        "DZ",
        "EG",
        "EH",
        "ER",
        "ET",
        "GA",
        "GH",
        "GM",
        "GN",
        "GQ",
        "GW",
        "IO",
        "KE",
        "KM",
        "LR",
        "LS",
        "LY",
        "MA",
        "MG",
        "ML",
        "MR",
        "MU",
        "MW",
        "MZ",
        "NA",
        "NE",
        "NG",
        "RE",
        "RW",
        "SC",
        "SD",
        "SH",
        "SL",
        "SN",
        "SO",
        "SS",
        "ST",
        "SZ",
        "TD",
        "TF",
        "TG",
        "TN",
        "TZ",
        "UG",
        "YT",
        "ZA",
        "ZM",
        "ZW",
    ]
)
_AMERICA = frozenset(
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
    ]
)
_ASIA = frozenset(
    [
        "AE",
        "AF",
        "AM",
        "AZ",
        "BD",
        "BH",
        "BN",
        "BT",
        "CN",
        "CY",
        "GE",
        "HK",
        "ID",
        "IL",
        "IN",
        "IQ",
        "IR",
        "JO",
        "JP",
        "KG",
        "KH",
        "KP",
        "KR",
        "KW",
        "KZ",
        "LA",
        "LB",
        "LK",
        "MM",
        "MN",
        "MO",
        "MV",
        "MY",
        "NP",
        "OM",
        "PH",
        "PK",
        "PS",
        "QA",
        "SA",
        "SG",
        "SY",
        "TH",
        "TJ",
        "TL",
        "TM",
        "TR",
        "TW",
        "UZ",
        "VN",
        "YE",
    ]
)
_EUROPE = frozenset(
    [
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
_OCEANIA = frozenset(
    [
        "AS",
        "AU",
        "CC",
        "CK",
        "CX",
        "FJ",
        "FM",
        "GU",
        "HM",
        "KI",
        "MH",
        "MP",
        "NC",
        "NF",
        "NR",
        "NU",
        "NZ",
        "PF",
        "PG",
        "PN",
        "PW",
        "SB",
        "TK",
        "TO",
        "TV",
        "UM",
        "VU",
        "WF",
        "WS",
    ]
)
_AZ_ALLOWED_COUNTRIES = frozenset(
    [
        "BD",
        "BN",
        "BT",
        "CN",
        "HK",
        "ID",
        "IN",
        "JP",
        "KH",
        "KP",
        "KR",
        "LA",
        "LK",
        "MM",
        "MN",
        "MO",
        "MY",
        "NP",
        "PH",
        "PK",
        "SG",
        "TH",
        "TL",
        "TW",
        "VN",
    ]
)
_PHD_COUNTRIES = frozenset(
    [
        "AG",
        "AI",
        "AU",
        "BB",
        "BM",
        "BS",
        "BZ",
        "CA",
        "CW",
        "DM",
        "GB",
        "GD",
        "IE",
        "JM",
        "KN",
        "KY",
        "LC",
        "MS",
        "NZ",
        "PR",
        "TC",
        "TT",
        "US",
        "VC",
        "VG",
        "VI",
    ]
)
_ALL_COUNTRIES = _AFRICA | _AMERICA | _ASIA | _EUROPE | _OCEANIA
_CINEMAZ_COUNTRIES = _ALL_COUNTRIES - _PHD_COUNTRIES - _AZ_ALLOWED_COUNTRIES
_ALLOWED_CONTAINERS = frozenset({"mkv", "mp4", "avi"})
_ALLOWED_VIDEO_CODECS = frozenset(
    {"avc", "h.264", "h.265", "x264", "x265", "hevc", "divx", "xvid"}
)
_ALLOWED_AUDIO_KEYWORDS = (
    "AC3",
    "E-AC3",
    "E-AC-3",
    "Audio Layer III",
    "MP3",
    "Dolby Digital",
    "Dolby TrueHD",
    "DTS",
    "DTS-HD",
    "FLAC",
    "AAC",
    "HE-AAC",
    "Dolby",
)
_CONDITIONAL_RIP_TYPES = frozenset({"webrip", "vodrip", "vhsrip"})
_BITRATE_MULTIPLIERS = {
    "": 1.0,
    "k": 1_000.0,
    "m": 1_000_000.0,
    "g": 1_000_000_000.0,
}


class AvistaZ(AZTrackerBase):
    """AZ Private Torrent Tracker."""

    tracker = "AVISTAZ"
    display_name = "AvistaZ"
    allows_bloated_audio = True
    source_flag = "AvistaZ"
    banned_groups = ("",)
    base_url = "https://avistaz.to"
    torrent_url = f"{base_url}/torrent/"
    requests_url = f"{base_url}/requests"
    supported_categories = ("TV", "MOVIE")
    tracker_urls = ("tracker.avistaz.to",)

    def __init__(self, config: dict[str, Any]):
        super().__init__(config, tracker_name="AVISTAZ")
        self.config = config
        self.common = Common(config)

    @staticmethod
    def _normalized(value: object) -> str:
        return str(value or "").strip().lower()

    @staticmethod
    def _category_warnings(meta: Meta) -> list[str]:
        warnings: list[str] = []
        if meta.category not in ("MOVIE", "TV"):
            warnings.append(
                "The only allowed content to be uploaded are Movies and TV Shows.\nAnything else, like games, music, software and porn is not allowed!"
            )
        if meta.anime:
            warnings.append(
                "Upload Anime content to our sister site AnimeTorrents.me instead. If it's on AniDB, it's an anime."
            )
        return warnings

    @staticmethod
    def _has_country(codes: list[str], countries: frozenset[str]) -> bool:
        return any(code in countries for code in codes)

    @classmethod
    def _region_warning(cls, meta: Meta) -> str | None:
        origin_codes = meta.origin_country
        if cls._has_country(origin_codes, _PHD_COUNTRIES):
            return (
                "DO NOT upload content from major English speaking countries (USA, UK, Canada, etc). "
                "Upload this to our sister site PRIVATEHD.to instead."
            )
        if cls._has_country(origin_codes, _CINEMAZ_COUNTRIES):
            return (
                "DO NOT upload non-allowed Asian or Western content. "
                "Upload this content to our sister site CINEMAZ.to instead."
            )
        return None

    @staticmethod
    def _allowed_containers(release_type: str) -> set[str]:
        allowed = set(_ALLOWED_CONTAINERS)
        if release_type == "hdtv":
            allowed.update({"ts", "tp"})
        return allowed

    @classmethod
    def _container_warning(
        cls, meta: Meta, is_disc: bool, release_type: str
    ) -> str | None:
        container = str(meta.container or "").strip().lower().lstrip(".")
        allowed = cls._allowed_containers(release_type)
        if is_disc or container in allowed:
            return None
        allowed_text = ", ".join(sorted(allowed)).upper()
        return (
            f"Container not allowed for this rip type: {container or 'unknown'}. "
            f"Allowed: {allowed_text}."
        )

    @staticmethod
    def _is_hdtv_mpeg2(release_type: str, video_codec: str) -> bool:
        return release_type == "hdtv" and video_codec in {"mpeg-2", "mpeg2"}

    @classmethod
    def _video_codec_warning(
        cls, is_disc: bool, release_type: str, video_codec: str
    ) -> str | None:
        if is_disc or video_codec in _ALLOWED_VIDEO_CODECS:
            return None
        if cls._is_hdtv_mpeg2(release_type, video_codec):
            return None
        return (
            f"Video codec not allowed in your upload: {video_codec}.\n"
            "Allowed: H264/x264/AVC, H265/x265/HEVC, DivX/Xvid\n"
            "Exceptions:\n"
            "    MPEG2 for Full DVD discs and HDTV recordings\n"
            "    VC-1/MPEG2 for Bluray only if that's what is on the disc"
        )

    @staticmethod
    def _resolution_value(meta: Meta) -> int:
        match = re.search(r"(\d{3,4})", str(meta.resolution or "").lower())
        if match is None:
            return 0
        return int(match.group(1))

    @staticmethod
    def _video_width(meta: Meta) -> int:
        return int(meta.video_width or 0)

    @staticmethod
    def _is_divx_xvid_hd(
        video_codec: str, resolution: int, video_width: int
    ) -> bool:
        if video_codec not in {"divx", "xvid"}:
            return False
        return resolution >= 720 or video_width >= 720

    @classmethod
    def _video_dimension_warnings(
        cls, meta: Meta, is_disc: bool, video_codec: str
    ) -> list[str]:
        warnings: list[str] = []
        resolution = cls._resolution_value(meta)
        video_width = cls._video_width(meta)
        if not is_disc and video_width and video_width < 600:
            warnings.append(
                f"Video width is {video_width}px; AvistaZ requires a minimum width of 600px."
            )
        if cls._is_divx_xvid_hd(video_codec, resolution, video_width):
            warnings.append(
                "DivX/XviD is not allowed for HD video (720p and above)."
            )
        return warnings

    @classmethod
    def _rip_type_warnings(
        cls, meta: Meta, release_type: str, source: str
    ) -> list[str]:
        warnings: list[str] = []
        if release_type in _CONDITIONAL_RIP_TYPES:
            warnings.append(
                f"{release_type.upper()} is allowed only when the video is unavailable in a preferred AvistaZ rip type; verify this manually before uploading."
            )
        if source == "brrip" and cls._resolution_value(meta) >= 720:
            warnings.append(
                "BRRip is allowed only for SD content (below 720p)."
            )
        return warnings

    @staticmethod
    def _audio_track(track: dict[str, Any]) -> dict[str, Any] | None:
        if track.get("@type") != "Audio":
            return None
        codec_info = track.get("Format_Commercial_IfAny") or track.get(
            "Format"
        )
        codec = codec_info if isinstance(codec_info, str) else ""
        return {
            "codec": codec,
            "language": track.get("Language", ""),
            "bitrate": track.get("BitRate", ""),
        }

    @classmethod
    def _audio_tracks(cls, meta: Meta) -> list[dict[str, Any]]:
        media_tracks = meta.mediainfo.get("media", {}).get("track", [])
        result: list[dict[str, Any]] = []
        for raw_track in media_tracks:
            track = cls._audio_track(raw_track)
            if track is not None:
                result.append(track)
        return result

    @staticmethod
    def _untouched_opus(meta: Meta) -> bool:
        audio = meta.audio
        if not isinstance(audio, str):
            return False
        return "opus" in audio.lower() and bool(meta.untouched)

    @staticmethod
    def _audio_codec_allowed(codec: str, untouched_opus: bool) -> bool:
        if not codec:
            return True
        if "opus" in codec.lower():
            return untouched_opus
        codec_lower = codec.lower()
        return any(
            keyword.lower() in codec_lower
            for keyword in _ALLOWED_AUDIO_KEYWORDS
        )

    @classmethod
    def _audio_codec_warning(
        cls, meta: Meta, tracks: list[dict[str, Any]]
    ) -> str | None:
        untouched_opus = cls._untouched_opus(meta)
        invalid = [
            str(track["codec"])
            for track in tracks
            if not cls._audio_codec_allowed(
                str(track["codec"]), untouched_opus
            )
        ]
        if not invalid:
            return None
        unique_invalid = sorted(set(invalid))
        return (
            f"Unallowed audio codec(s) detected: {', '.join(unique_invalid)}\n"
            "Allowed codecs: AC3 (Dolby Digital), Dolby TrueHD, DTS, DTS-HD (MA), FLAC, AAC, MP3, etc.\n"
            "Exceptions: Untouched Opus from source; Uncompressed codecs from Blu-ray discs (PCM, LPCM)."
        )

    @staticmethod
    def _bitrate_value(raw_bitrate: object) -> float | None:
        bitrate = str(raw_bitrate or "")
        if not bitrate:
            return None
        normalized = re.sub(r"[\s,]", "", bitrate)
        match = re.fullmatch(
            r"(\d+(?:\.\d+)?)([kmg]?)(?:bit/s|b/s|bps)?",
            normalized,
            flags=re.IGNORECASE,
        )
        if match is None:
            return None
        value = float(match.group(1))
        return value * _BITRATE_MULTIPLIERS[match.group(2).lower()]

    @classmethod
    def _low_bitrate_label(cls, track: dict[str, Any]) -> str | None:
        bitrate = str(track.get("bitrate", "") or "")
        value = cls._bitrate_value(bitrate)
        if value is None or value >= 128_000:
            return None
        return f"{track['codec']} ({bitrate})"

    @classmethod
    def _low_bitrate_warning(
        cls, release_type: str, tracks: list[dict[str, Any]]
    ) -> str | None:
        if release_type == "webdl":
            return None
        low_tracks: list[str] = []
        for track in tracks:
            label = cls._low_bitrate_label(track)
            if label is not None:
                low_tracks.append(label)
        if not low_tracks:
            return None
        return (
            "Audio bitrate must be at least 128 kbit/s outside WEB-DL uploads: "
            f"{', '.join(low_tracks)}."
        )

    @staticmethod
    def _append_optional(warnings: list[str], warning: str | None) -> None:
        if warning:
            warnings.append(warning)

    @classmethod
    def _general_warnings(
        cls,
        meta: Meta,
        is_disc: bool,
        release_type: str,
        video_codec: str,
        source: str,
    ) -> list[str]:
        warnings = cls._category_warnings(meta)
        for warning in (
            cls._region_warning(meta),
            cls._container_warning(meta, is_disc, release_type),
            cls._video_codec_warning(is_disc, release_type, video_codec),
        ):
            cls._append_optional(warnings, warning)
        warnings.extend(
            cls._video_dimension_warnings(meta, is_disc, video_codec)
        )
        warnings.extend(cls._rip_type_warnings(meta, release_type, source))
        return warnings

    @classmethod
    def _audio_warnings(cls, meta: Meta, release_type: str) -> list[str]:
        tracks = cls._audio_tracks(meta)
        warnings: list[str] = []
        cls._append_optional(warnings, cls._audio_codec_warning(meta, tracks))
        cls._append_optional(
            warnings, cls._low_bitrate_warning(release_type, tracks)
        )
        return warnings

    def rules(self, meta: Meta) -> str:
        is_disc = bool(meta.is_disc)
        release_type = self._normalized(meta.type)
        video_codec = self._normalized(meta.video_codec)
        source = self._normalized(meta.source)
        warnings = self._general_warnings(
            meta, is_disc, release_type, video_codec, source
        )
        if not is_disc:
            warnings.extend(self._audio_warnings(meta, release_type))
        if not warnings:
            return ""
        return "\n\n".join(filter(None, warnings))
