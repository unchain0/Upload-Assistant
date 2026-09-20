# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from datetime import UTC, datetime
from typing import Any

from src.domain_models.release import Meta
from src.integrations.trackers.AVISTAZ import AZTrackerBase
from src.integrations.trackers.common import Common

_AFRICA = frozenset(
    {
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
    }
)
_AMERICA = frozenset(
    {
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
    }
)
_EUROPE = frozenset(
    {
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
    }
)
_PHD_COUNTRIES = frozenset(
    {
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
    }
)
_AZ_COUNTRIES = frozenset(
    {
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
    }
)
_MIDDLE_EAST = frozenset(
    {
        "AE",
        "BH",
        "CY",
        "EG",
        "IR",
        "IQ",
        "IL",
        "JO",
        "KW",
        "LB",
        "OM",
        "PS",
        "QA",
        "SA",
        "SY",
        "TR",
        "YE",
    }
)
_CZ_ALLOWED_COUNTRIES = (
    (_EUROPE - {"GB", "IE"})
    | (_AMERICA - _PHD_COUNTRIES)
    | _AFRICA
    | _MIDDLE_EAST
    | {"RU"}
)
_ALLOWED_VIDEO_CODECS = frozenset(
    {"avc", "h.264", "h.265", "x264", "x265", "hevc", "vp9", "divx", "xvid"}
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
    "Dolby",
)
_CONDITIONAL_RIP_TYPES = frozenset(
    {"webrip", "vodrip", "vhsrip", "vcdrip", "vcd"}
)
_BITRATE_THRESHOLDS: dict[str, dict[str | int, int]] = {
    "x264": {"sd": 1000, 720: 1500, 1080: 3000, 2160: 12000},
    "x265": {720: 1000, 1080: 2000, 2160: 8000},
}


class CinemaZ(AZTrackerBase):
    """CZ Private Torrent Tracker."""

    tracker = "CINEMAZ"
    display_name = "CinemaZ"
    allows_bloated_audio = True
    source_flag = "CinemaZ"
    banned_groups = ("",)
    base_url = "https://cinemaz.to"
    torrent_url = f"{base_url}/torrent/"
    requests_url = f"{base_url}/requests"
    supported_categories = ("TV", "MOVIE")
    tracker_urls = ("tracker.cinemaz.to",)

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config, tracker_name="CINEMAZ")
        self.config = config
        self.common = Common(config)

    @staticmethod
    def _normalized(value: object) -> str:
        return str(value or "").strip().lower()

    @staticmethod
    def _append_optional(warnings: list[str], warning: str | None) -> None:
        if warning:
            warnings.append(warning)

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
    def _resolution(meta: Meta) -> int:
        match = re.search(r"(\d{3,4})", str(meta.resolution or "").lower())
        if match is None:
            return 0
        return int(match.group(1))

    @staticmethod
    def _year(meta: Meta) -> int:
        try:
            return int(meta.year or 0)
        except TypeError, ValueError:
            return 0

    @classmethod
    def _is_old(cls, meta: Meta) -> bool:
        year = cls._year(meta)
        if not year:
            return False
        return (datetime.now(UTC).year - year) >= 50

    @staticmethod
    def _is_sd(meta: Meta, resolution: int) -> bool:
        return bool(meta.sd) or bool(resolution and resolution < 720)

    @staticmethod
    def _has_country(codes: list[str], countries: frozenset[str]) -> bool:
        return any(code in countries for code in codes)

    @classmethod
    def _phd_region_warning(cls, meta: Meta, is_sd: bool) -> str | None:
        if cls._is_old(meta) or is_sd:
            return None
        return (
            "DO NOT upload recent mainstream English content. "
            "Upload this to our sister site PRIVATEHD.to instead."
        )

    @classmethod
    def _region_warning(cls, meta: Meta, is_sd: bool) -> str | None:
        codes = meta.origin_country
        if cls._has_country(codes, _PHD_COUNTRIES):
            return cls._phd_region_warning(meta, is_sd)
        if cls._has_country(codes, _AZ_COUNTRIES):
            return "DO NOT upload Asian content. Upload this to our sister site AVISTAZ.to instead."
        if cls._has_country(codes, _CZ_ALLOWED_COUNTRIES):
            return None
        return (
            "This content is not allowed. CINEMAZ accepts content from Europe (excluding UK/IE), "
            "Africa, the Middle East, Russia, and the Americas (excluding recent mainstream English content)."
        )

    @staticmethod
    def _allowed_containers(release_type: str) -> set[str]:
        allowed = {"mkv", "mp4", "avi"}
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
            "Video codec not allowed. CinemaZ allows H.264/x264/AVC, H.265/x265/HEVC, "
            "VP9, DivX/XviD, and MPEG-2 for HDTV recordings."
        )

    @staticmethod
    def _video_width(meta: Meta) -> int:
        return int(meta.video_width or 0)

    @staticmethod
    def _is_divx_hd(video_codec: str, resolution: int, width: int) -> bool:
        if video_codec not in {"divx", "xvid"}:
            return False
        return resolution >= 720 or width >= 720

    @classmethod
    def _dimension_warnings(
        cls, meta: Meta, is_disc: bool, video_codec: str, resolution: int
    ) -> list[str]:
        warnings: list[str] = []
        width = cls._video_width(meta)
        if not is_disc and width and width < 600:
            warnings.append(
                f"Video width is {width}px; CinemaZ requires a minimum width of 600px."
            )
        if cls._is_divx_hd(video_codec, resolution, width):
            warnings.append(
                "DivX/XviD is not allowed for HD video (720p and above)."
            )
        return warnings

    @staticmethod
    def _rip_warning(release_type: str) -> str | None:
        if release_type not in _CONDITIONAL_RIP_TYPES:
            return None
        return (
            f"{release_type.upper()} is allowed only when the video is unavailable in a preferred "
            "CinemaZ rip type; verify this manually before uploading."
        )

    @staticmethod
    def _hybrid_warning(meta: Meta) -> str | None:
        edition = str(meta.edition or "").lower()
        if "hybrid" not in edition and not bool(meta.webdv):
            return None
        return (
            "HYBRID releases require substantially improved, perfectly synchronized audio or video streams; "
            "verify this manually before uploading."
        )

    @staticmethod
    def _codec_family(video_codec: str, video_encode: str) -> str:
        joined = f"{video_codec} {video_encode}"
        if any(codec in joined for codec in ("x265", "h.265", "hevc")):
            return "x265"
        return "x264"

    @staticmethod
    def _resolution_key(is_sd: bool, resolution: int) -> str | int:
        return "sd" if is_sd else resolution

    @staticmethod
    def _sd_x265_warning(
        is_disc: bool, family: str, is_sd: bool
    ) -> str | None:
        if is_disc or family != "x265" or not is_sd:
            return None
        return "x265/HEVC is not allowed for SD content."

    @staticmethod
    def _low_bitrate_warning(
        bitrate: int, required: int | None, is_disc: bool
    ) -> str | None:
        if is_disc or not bitrate or not required:
            return None
        if bitrate >= required:
            return None
        return (
            f"Video bitrate is {bitrate} kbit/s; CinemaZ requires at least {required} kbit/s "
            "for this codec and resolution."
        )

    @classmethod
    def _quality_warning(
        cls,
        meta: Meta,
        is_disc: bool,
        video_codec: str,
        video_encode: str,
        resolution: int,
        is_sd: bool,
    ) -> str | None:
        family = cls._codec_family(video_codec, video_encode)
        sd_warning = cls._sd_x265_warning(is_disc, family, is_sd)
        if sd_warning is not None:
            return sd_warning
        required = _BITRATE_THRESHOLDS[family].get(
            cls._resolution_key(is_sd, resolution)
        )
        bitrate = int(meta.video_bitrate or 0)
        return cls._low_bitrate_warning(bitrate, required, is_disc)

    @staticmethod
    def _audio_track(track: dict[str, Any]) -> dict[str, str] | None:
        if track.get("@type") != "Audio":
            return None
        codec_info = track.get("Format_Commercial_IfAny") or track.get(
            "Format"
        )
        codec = codec_info if isinstance(codec_info, str) else ""
        return {"codec": codec, "bitrate": str(track.get("BitRate", "") or "")}

    @classmethod
    def _audio_tracks(cls, meta: Meta) -> list[dict[str, str]]:
        raw_tracks = meta.mediainfo.get("media", {}).get("track", [])
        tracks: list[dict[str, str]] = []
        for raw_track in raw_tracks:
            audio_track = cls._audio_track(raw_track)
            if audio_track is not None:
                tracks.append(audio_track)
        return tracks

    @staticmethod
    def _audio_codec_allowed(codec: str) -> bool:
        lowered = codec.lower()
        return any(
            keyword.lower() in lowered for keyword in _ALLOWED_AUDIO_KEYWORDS
        )

    @classmethod
    def _invalid_audio_codecs(
        cls, audio_tracks: list[dict[str, str]]
    ) -> list[str]:
        return sorted(
            {
                track["codec"]
                for track in audio_tracks
                if track["codec"]
                and not cls._audio_codec_allowed(track["codec"])
            }
        )

    @classmethod
    def _audio_codec_warning(
        cls, is_disc: bool, audio_tracks: list[dict[str, str]]
    ) -> str | None:
        if is_disc:
            return None
        invalid = cls._invalid_audio_codecs(audio_tracks)
        if not invalid:
            return None
        return f"Unallowed audio codec(s) detected: {', '.join(invalid)}."

    @staticmethod
    def _audio_bitrate_warning(meta: Meta, is_disc: bool) -> str | None:
        bitrate = int(meta.audio_bitrate or 0)
        if is_disc or not bitrate or bitrate >= 128:
            return None
        return f"Audio bitrate is {bitrate} kbit/s; CinemaZ requires at least 128 kbit/s."

    def rules(self, meta: Meta) -> str:
        is_disc = bool(meta.is_disc)
        release_type = self._normalized(meta.type)
        video_codec = self._normalized(meta.video_codec)
        video_encode = self._normalized(meta.video_encode)
        resolution = self._resolution(meta)
        is_sd = self._is_sd(meta, resolution)

        warnings = self._category_warnings(meta)
        self._append_optional(warnings, self._region_warning(meta, is_sd))
        self._append_optional(
            warnings, self._container_warning(meta, is_disc, release_type)
        )
        self._append_optional(
            warnings,
            self._video_codec_warning(is_disc, release_type, video_codec),
        )
        warnings.extend(
            self._dimension_warnings(meta, is_disc, video_codec, resolution)
        )
        self._append_optional(warnings, self._rip_warning(release_type))
        self._append_optional(warnings, self._hybrid_warning(meta))
        self._append_optional(
            warnings,
            self._quality_warning(
                meta, is_disc, video_codec, video_encode, resolution, is_sd
            ),
        )
        audio_tracks = self._audio_tracks(meta)
        self._append_optional(
            warnings, self._audio_codec_warning(is_disc, audio_tracks)
        )
        self._append_optional(
            warnings, self._audio_bitrate_warning(meta, is_disc)
        )
        return "\n\n".join(filter(None, warnings))

    @staticmethod
    def _minimum_screenshots(meta: Meta) -> int:
        if meta.is_disc == "BDMV":
            return 6
        if meta.type == "REMUX":
            return 6
        if meta.resolution == "2160p":
            return 6
        return 3

    def check_data(self, meta: Meta, data: dict[str, Any]):
        issue = super().check_data(meta, data)
        if issue:
            return issue
        if meta.debug:
            return issue
        minimum = self._minimum_screenshots(meta)
        if len(data["screenshots[]"]) < minimum:
            return f"UPLOAD FAILED: CinemaZ requires at least {minimum} screenshots for this upload."
        return False
