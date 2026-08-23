# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from datetime import UTC, datetime
from typing import Any, cast

from src.domain_models.release import Meta
from src.integrations.trackers.AVISTAZ import AZTrackerBase
from src.integrations.trackers.common import Common

Config = dict[str, Any]

_PHD_ALLOWED_COUNTRIES = frozenset(
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
_ASIA = frozenset(
    {
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
_OCEANIA = frozenset(
    {
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
    }
)
_CINEMAZ_COUNTRIES = (
    _AFRICA | _AMERICA | _EUROPE | _OCEANIA
) - _PHD_ALLOWED_COUNTRIES
_ALLOWED_AUDIO_KEYWORDS = (
    "AC3",
    "E-AC3",
    "E-AC-3",
    "Dolby Digital",
    "Dolby TrueHD",
    "DTS",
    "DTS-HD",
    "FLAC",
    "AAC",
    "Dolby",
)
_FORBIDDEN_AUDIO_KEYWORDS = ("LPCM", "PCM", "Linear PCM")
_ALLOWED_VIDEO_CODECS = frozenset(
    {
        "avc",
        "mpeg-2",
        "vc-1",
        "h.264",
        "vp9",
        "h.265",
        "x264",
        "x265",
        "hevc",
    }
)
_BITRATE_RULES = {
    ("x265", "web", 720): 1_500_000,
    ("x265", "web", 1080): 2_500_000,
    ("x265", "bluray", 720): 2_000_000,
    ("x265", "bluray", 1080): 3_500_000,
    ("x264", "web", 720): 2_500_000,
    ("x264", "web", 1080): 4_500_000,
    ("x264", "bluray", 720): 3_500_000,
    ("x264", "bluray", 1080): 6_000_000,
}
_WEB_SOURCES = frozenset({"hdtv", "web", "hdrip"})


class PrivateHD(AZTrackerBase):
    """
    PHD Private Torrent Tracker
    """

    tracker = "PRIVATEHD"
    display_name = "PrivateHD"
    allows_bloated_audio = True
    source_flag = "PrivateHD"
    banned_groups = (
        "4K4U",
        "C4K",
        "d3g",
        "DDR",
        "EASports",
        "FaNGDiNG0",
        "FRDS",
        "HD2DVD",
        "HDTime",
        "iPlanet",
        "KiNGDOM",
        "Leffe",
        "LiGaS",
        "MeGusta",
        "NhaNc3",
        "nikt0",
        "PRoDJi",
        "RARBG",
        "RDN",
        "SANTi",
        "STUTTERSHIT",
        "SWTYBLZ",
        "TBS",
        "Tigole",
        "VisionXpert",
        "WKS",
        "x0r",
        "Xiaomi",
        "YIFY",
        "YTS",
        "Zeus",
    )
    base_url = "https://privatehd.to"
    torrent_url = f"{base_url}/torrent/"
    requests_url = f"{base_url}/requests"
    supported_categories = ("TV", "MOVIE")
    tracker_urls = ("tracker.privatehd",)

    def __init__(self, config: Config) -> None:
        super().__init__(config, tracker_name="PRIVATEHD")
        self.config: Config = config
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
    def _year_warning(meta: Meta) -> str | None:
        try:
            year = int(meta.year or 0)
        except TypeError, ValueError:
            return None
        if not year or (datetime.now(UTC).year - year) < 50:
            return None
        return "Upload movies/series 50+ years old to our sister site CINEMAZ.to instead."

    @staticmethod
    def _origin_codes(meta: Meta) -> list[str]:
        value = meta.origin_country
        if isinstance(value, list):
            return cast(list[str], value)
        return []

    @staticmethod
    def _has_country(codes: list[str], countries: frozenset[str]) -> bool:
        return any(code in countries for code in codes)

    @classmethod
    def _region_warning(cls, meta: Meta) -> str | None:
        codes = cls._origin_codes(meta)
        if cls._has_country(codes, _PHD_ALLOWED_COUNTRIES):
            return None
        if cls._has_country(codes, _CINEMAZ_COUNTRIES):
            return (
                "Upload European (EXCLUDING United Kingdom and Ireland), South American and African content "
                "to our sister site CINEMAZ.to instead."
            )
        if cls._has_country(codes, _ASIA):
            return (
                "DO NOT upload content originating from countries shown in this map (https://imgur.com/nIB9PM1).\n"
                "In case of doubt, message the staff first. Upload Asian content to our sister site Avistaz.to instead.\n"
                f"Origin country for your upload: {', '.join(codes)}"
            )
        return (
            "Only upload content to PRIVATEHD from all major English speaking countries.\n"
            "Including United States, Canada, UK, Ireland, Australia, and New Zealand."
        )

    @staticmethod
    def _tag_warnings(meta: Meta, source: str) -> list[str]:
        tag = meta.tag
        if not tag:
            return []
        normalized = tag.strip().lower()
        warnings: list[str] = []
        if normalized in ("rarbg", "fgt", "grym", "tbs"):
            warnings.append(
                "Do not upload RARBG, FGT, Grym or TBS. Existing uploads by these groups can be trumped at any time."
            )
        if normalized == "evo" and source != "web":
            warnings.append(
                "Do not upload non-web EVO releases. Existing uploads by this group can be trumped at any time."
            )
        return warnings

    @staticmethod
    def _sd_warning(meta: Meta) -> str | None:
        if meta.sd == 1:
            return "SD (Standard Definition) content is forbidden."
        return None

    @staticmethod
    def _allowed_containers(release_type: str) -> set[str]:
        allowed = {"mkv", "mp4"}
        if release_type == "hdtv":
            allowed.update({"ts", "tp"})
        return allowed

    @classmethod
    def _container_warning(
        cls, meta: Meta, is_bd_disc: bool, release_type: str
    ) -> str | None:
        container = str(meta.container or "").strip().lower().lstrip(".")
        allowed = cls._allowed_containers(release_type)
        if is_bd_disc or container in allowed:
            return None
        allowed_text = ", ".join(sorted(allowed)).upper()
        return (
            f"Container not allowed for this rip type: {container or 'unknown'}. "
            f"Allowed: {allowed_text}."
        )

    @staticmethod
    def _remux_video_warning(
        release_type: str, video_codec: str
    ) -> str | None:
        if release_type != "remux":
            return None
        if video_codec in ("mpeg-2", "vc-1", "h.264", "h.265", "avc"):
            return None
        return "Allowed Video Codecs for BluRay (Untouched + REMUX): MPEG-2, VC-1, H.264, H.265"

    @staticmethod
    def _bluray_encode_warning(
        release_type: str, source: str, video_encode: str
    ) -> str | None:
        if release_type != "encode" or source != "bluray":
            return None
        if video_encode in ("h.264", "h.265", "x264", "x265"):
            return None
        return (
            "Allowed Video Codecs for BluRay (Encoded): H.264, H.265 "
            "(x264 and x265 respectively are the only permitted encoders)"
        )

    @staticmethod
    def _web_untouched_warning(
        release_type: str, source: str, video_encode: str
    ) -> str | None:
        if release_type not in ("webdl", "web-dl") or source != "web":
            return None
        if video_encode in ("h.264", "h.265", "vp9"):
            return None
        return "Allowed Video Codecs for WEB (Untouched): H.264, H.265, VP9"

    @staticmethod
    def _web_encode_warning(
        release_type: str, source: str, video_encode: str
    ) -> str | None:
        if release_type != "encode" or source != "web":
            return None
        if video_encode in ("h.264", "h.265", "x264", "x265"):
            return None
        return (
            "Allowed Video Codecs for WEB (Encoded): H.264, H.265 "
            "(x264 and x265 respectively are the only permitted encoders)"
        )

    @staticmethod
    def _x265_depth_warning(
        release_type: str, video_encode: str, bit_depth: object
    ) -> str | None:
        if release_type != "encode" or video_encode != "x265":
            return None
        if bit_depth == "10":
            return None
        return "Allowed Video Codecs for x265 encodes must be 10-bit"

    @staticmethod
    def _resolution(meta: Meta) -> int:
        text = meta.resolution.lower().replace("p", "").replace("i", "")
        if text.isdigit():
            return int(text)
        return 0

    @staticmethod
    def _uhd_h264_warning(resolution: int, video_encode: str) -> str | None:
        if resolution > 1080 and video_encode in ("h.264", "x264"):
            return "H.264/x264 only allowed for 1080p and below."
        return None

    @staticmethod
    def _global_video_codec_warning(video_codec: str) -> str | None:
        if video_codec in _ALLOWED_VIDEO_CODECS:
            return None
        return f"Video codec not allowed in your upload: {video_codec}."

    @classmethod
    def _video_warnings(
        cls,
        meta: Meta,
        release_type: str,
        source: str,
        video_codec: str,
        video_encode: str,
        resolution: int,
    ) -> list[str]:
        warnings: list[str] = []
        for warning in (
            cls._remux_video_warning(release_type, video_codec),
            cls._bluray_encode_warning(release_type, source, video_encode),
            cls._web_untouched_warning(release_type, source, video_encode),
            cls._web_encode_warning(release_type, source, video_encode),
            cls._x265_depth_warning(
                release_type, video_encode, meta.bit_depth
            ),
            cls._uhd_h264_warning(resolution, video_encode),
            cls._global_video_codec_warning(video_codec),
        ):
            cls._append_optional(warnings, warning)
        return warnings

    @staticmethod
    def _media_tracks(meta: Meta) -> list[dict[str, Any]]:
        media = cast(dict[str, Any], meta.mediainfo.get("media", {}))
        return cast(list[dict[str, Any]], media.get("track", []))

    @staticmethod
    def _audio_track(track: dict[str, Any]) -> dict[str, str] | None:
        if track.get("@type") != "Audio":
            return None
        codec_info = track.get("Format_Commercial_IfAny") or track.get(
            "Format"
        )
        codec = codec_info if isinstance(codec_info, str) else ""
        return {"codec": codec, "language": str(track.get("Language", ""))}

    @classmethod
    def _audio_tracks(
        cls, media_tracks: list[dict[str, Any]]
    ) -> list[dict[str, str]]:
        tracks: list[dict[str, str]] = []
        for media_track in media_tracks:
            audio_track = cls._audio_track(media_track)
            if audio_track is not None:
                tracks.append(audio_track)
        return tracks

    @staticmethod
    def _usable_original_language(
        meta: Meta, audio_tracks: list[dict[str, str]]
    ) -> str | None:
        original_language = str(meta.original_language)
        if not original_language:
            return None
        if not audio_tracks:
            return None
        if not audio_tracks[0].get("language", ""):
            return None
        return original_language

    @classmethod
    def _original_language_tracks(
        cls, meta: Meta, audio_tracks: list[dict[str, str]]
    ) -> list[dict[str, str]]:
        original_language = cls._usable_original_language(meta, audio_tracks)
        if original_language is None:
            return []
        return [
            track
            for track in audio_tracks
            if track.get("language", "").lower() == original_language.lower()
        ]

    @staticmethod
    def _has_truehd_atmos(tracks: list[dict[str, str]]) -> bool:
        return any(
            "truehd" in track["codec"].lower()
            and "atmos" in track["codec"].lower()
            for track in tracks
        )

    @staticmethod
    def _has_ac3_compatibility(tracks: list[dict[str, str]]) -> bool:
        return any(
            "ac-3" in track["codec"].lower()
            or "dolby digital" in track["codec"].lower()
            for track in tracks
        )

    @classmethod
    def _compatibility_warning(
        cls, meta: Meta, audio_tracks: list[dict[str, str]]
    ) -> str | None:
        original_tracks = cls._original_language_tracks(meta, audio_tracks)
        if not original_tracks or not cls._has_truehd_atmos(original_tracks):
            return None
        if cls._has_ac3_compatibility(original_tracks):
            return None
        return (
            f"A TrueHD Atmos track was detected in the original language ({meta.original_language}), "
            "but no AC-3 (Dolby Digital) compatibility track was found for that same language.\n"
            "Rule: TrueHD/Atmos audio must have a compatibility track due to poor compatibility with most players."
        )

    @staticmethod
    def _audio_codec_allowed(codec: str) -> bool:
        if not codec:
            return True
        lowered = codec.lower()
        if any(
            keyword.lower() in lowered for keyword in _FORBIDDEN_AUDIO_KEYWORDS
        ):
            return False
        return any(
            keyword.lower() in lowered for keyword in _ALLOWED_AUDIO_KEYWORDS
        )

    @classmethod
    def _invalid_audio_warning(
        cls, audio_tracks: list[dict[str, str]]
    ) -> str | None:
        invalid = [
            track["codec"]
            for track in audio_tracks
            if not cls._audio_codec_allowed(track["codec"])
        ]
        if not invalid:
            return None
        unique_invalid = sorted(set(invalid))
        return (
            f"Unallowed audio codec(s) detected: {', '.join(unique_invalid)}\n"
            "Allowed codecs: AC3 (Dolby Digital), Dolby TrueHD, DTS, DTS-HD (MA), FLAC, AAC, all other Dolby codecs.\n"
            "Dolby Exceptions: Any uncompressed audio codec that comes on a BluRay disc like; PCM, LPCM, etc."
        )

    @classmethod
    def _audio_warnings(
        cls, meta: Meta, is_bd_disc: bool, media_tracks: list[dict[str, Any]]
    ) -> list[str]:
        if is_bd_disc:
            return []
        audio_tracks = cls._audio_tracks(media_tracks)
        warnings: list[str] = []
        cls._append_optional(
            warnings, cls._compatibility_warning(meta, audio_tracks)
        )
        cls._append_optional(
            warnings, cls._invalid_audio_warning(audio_tracks)
        )
        return warnings

    @staticmethod
    def _video_track(
        media_tracks: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        for track in media_tracks:
            if track.get("@type") == "Video":
                return track
        return None

    @classmethod
    def _video_bitrate(
        cls, meta: Meta, media_tracks: list[dict[str, Any]]
    ) -> int:
        bitrate = int(meta.video_bitrate or 0) * 1000
        video_track = cls._video_track(media_tracks)
        if video_track is None:
            return bitrate
        raw = video_track.get("BitRate")
        if raw and str(raw).isdigit():
            return int(raw)
        return bitrate

    @staticmethod
    def _source_type(source: str) -> str | None:
        if source in _WEB_SOURCES:
            return "web"
        if source == "bluray":
            return "bluray"
        return None

    @classmethod
    def _bitrate_quality_warning(
        cls,
        meta: Meta,
        source: str,
        video_encode: str,
        resolution: int,
        media_tracks: list[dict[str, Any]],
    ) -> str | None:
        source_type = cls._source_type(source)
        if source_type is None:
            return None
        minimum = _BITRATE_RULES.get((video_encode, source_type, resolution))
        if minimum is None:
            return None
        bitrate = cls._video_bitrate(meta, media_tracks)
        if bitrate >= minimum:
            return None
        quality_rule_text = (
            "Only upload proper encodes.\nAny encodes where the size and/or the bitrate "
            "imply a bad quality will be deleted."
        )
        rule = (
            "Your upload was rejected due to low quality.\n"
            f"Minimum bitrate for {resolution}p {source.upper()} {video_encode.upper()} "
            f"is {minimum / 1000} Kbps."
        )
        return quality_rule_text + rule

    @classmethod
    def _crf_warning(cls, media_tracks: list[dict[str, Any]]) -> str | None:
        video_track = cls._video_track(media_tracks)
        if video_track is None:
            return None
        settings = str(video_track.get("Encoded_Library_Settings", "") or "")
        match = re.search(
            r"\bcrf[ =:]+(\d+(?:\.\d+)?)", settings, re.IGNORECASE
        )
        if match is None or float(match.group(1)) <= 20:
            return None
        return f"CRF {match.group(1)} exceeds PrivateHD's maximum CRF of 20."

    @classmethod
    def _quality_warnings(
        cls,
        meta: Meta,
        release_type: str,
        source: str,
        video_encode: str,
        resolution: int,
        media_tracks: list[dict[str, Any]],
    ) -> list[str]:
        if release_type != "encode":
            return []
        warnings: list[str] = []
        cls._append_optional(
            warnings,
            cls._bitrate_quality_warning(
                meta, source, video_encode, resolution, media_tracks
            ),
        )
        cls._append_optional(warnings, cls._crf_warning(media_tracks))
        return warnings

    @staticmethod
    def _resolution_warning(resolution: int) -> str | None:
        if resolution < 720:
            return "Video must be at least 720p."
        return None

    @staticmethod
    def _hybrid_warning(meta: Meta, release_type: str) -> str | None:
        if release_type not in ("remux", "encode"):
            return None
        if "hybrid" not in meta.name.lower():
            return None
        return (
            "Hybrid Remuxes and Encodes are subject to the following condition:\n\n"
            "Hybrid user releases are permitted, but are treated similarly to regular "
            "user releases and must be approved by staff before you upload them "
            "(please see the torrent approvals forum for details)."
        )

    @staticmethod
    def _remux_log_warning(release_type: str) -> str | None:
        if release_type != "remux":
            return None
        return (
            "Remuxes must have a demux/eac3to log under spoilers in description.\n"
            "Do you have these logs and will you add them to the description after upload?"
        )

    @staticmethod
    def _bloated_warning(meta: Meta) -> str | None:
        if not meta.bloated:
            return None
        return (
            "Audio dubs are never preferred and can always be trumped by original audio only rip "
            "(Exception for BD50/BD25).\n"
            "Do NOT upload a multi audio release when there is already a original audio only release on site.\n"
        )

    @classmethod
    def _final_warnings(
        cls, meta: Meta, release_type: str, resolution: int
    ) -> list[str]:
        warnings: list[str] = []
        for warning in (
            cls._resolution_warning(resolution),
            cls._hybrid_warning(meta, release_type),
            cls._remux_log_warning(release_type),
            cls._bloated_warning(meta),
        ):
            cls._append_optional(warnings, warning)
        return warnings

    def rules(self, meta: Meta) -> str:
        release_type = self._normalized(meta.type)
        source = self._normalized(meta.source)
        video_codec = self._normalized(meta.video_codec)
        video_encode = self._normalized(meta.video_encode)
        is_bd_disc = meta.is_disc == "BDMV"
        resolution = self._resolution(meta)
        media_tracks = self._media_tracks(meta)

        warnings = self._category_warnings(meta)
        self._append_optional(warnings, self._year_warning(meta))
        self._append_optional(warnings, self._region_warning(meta))
        warnings.extend(self._tag_warnings(meta, source))
        self._append_optional(warnings, self._sd_warning(meta))
        self._append_optional(
            warnings, self._container_warning(meta, is_bd_disc, release_type)
        )
        warnings.extend(
            self._video_warnings(
                meta,
                release_type,
                source,
                video_codec,
                video_encode,
                resolution,
            )
        )
        warnings.extend(self._audio_warnings(meta, is_bd_disc, media_tracks))
        warnings.extend(
            self._quality_warnings(
                meta,
                release_type,
                source,
                video_encode,
                resolution,
                media_tracks,
            )
        )
        warnings.extend(self._final_warnings(meta, release_type, resolution))
        return "\n\n".join(filter(None, warnings))

    def get_rip_type(self, meta: Meta, display_name: bool = False) -> str:
        # Translation from meta keywords to site display labels
        translation = {
            "bdrip": "BDRip",
            "encode": "BluRay",
            "disc": "BluRay Raw",
            "hdrip": "HDRip",
            "hdtv": "HDTV",
            "remux": "REMUX",
            "webdl": "WEB-DL",
            "webrip": "WEBRip",
        }

        # Available rip types from HTML
        available_rip_types = {
            "BDRip": "1",
            "BluRay": "2",
            "BluRay Raw": "3",
            "HDRip": "6",
            "HDTV": "7",
            "REMUX": "14",
            "WEB-DL": "12",
            "WEBRip": "13",
        }

        source_type = str(meta.type or "").strip().lower()
        html_label = translation.get(source_type)

        if display_name:
            return html_label or ""

        if html_label is None:
            return ""

        return available_rip_types.get(html_label, "")
