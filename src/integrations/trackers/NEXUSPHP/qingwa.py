# Upload Assistant © 2026 Audionut & wastaken7 — Licensed under UAPL v1.0
"""QingWa title, category, and medium decisions."""

import re
from typing import Any

from src.domain_models.release import Meta
from src.integrations.trackers.NEXUSPHP import NEXUSPHP

_WEB_VIDEO = {"AVC": "H.264", "HEVC": "H.265"}
_HDTV_VIDEO = {
    "AVC": "H264",
    "HEVC": "H265",
    "H.264": "H264",
    "H.265": "H265",
    "MPEG-2": "MPEG2",
}
_CODEC_IDS = (
    (("av1",), 7),
    (("265", "hevc"), 6),
    (("264", "avc"), 1),
    (("vc1",), 2),
    (("mpeg2",), 4),
    (("mpeg4", "xvid", "divx"), 3),
    (("vp9",), 8),
)
_RESOLUTION_IDS = {
    "4320p": 6,
    "2160p": 7,
    "1440p": 8,
    "1080p": 1,
    "1080i": 2,
    "720p": 3,
}
_AUDIO_TABLE = (
    (("lpcm", "pcm"), 13),
    (("dd+", "ddp", "eac3"), 16),
    (("dd", "ac3"), 15),
    (("flac",), 1),
    (("aac",), 17),
    (("ape",), 18),
    (("wav",), 19),
    (("mp3",), 4),
    (("m4a",), 8),
    (("opus",), 20),
    (("av3a",), 22),
)
_GROUP_TAGS = {"-frog": 6, "-froge": 7, "-frogweb": 8, "-catedu": 10}
_REALITY = {
    "reality",
    "reality television",
    "reality tv",
    "talk show",
    "game show",
    "variety",
    "competition",
    "tv show",
}
_BIT_DEPTH_RE = re.compile(r"\b(?:8|10|12)[ -]?bits?\b", re.IGNORECASE)
_HDR10_RE = re.compile(r"\bHDR10\b(?!\+)")
_SDR_RE = re.compile(r"\bSDR\b")
_DUAL_RE = re.compile(
    r"\b(?:Dual[ -]Audio|Dubbed|\d+Audios?)\b", re.IGNORECASE
)
_DDP_RE = re.compile(r"\b(?:DD\+|DDP|E-?AC-?3)(?=\s|$)", re.IGNORECASE)
_AC3_RE = re.compile(r"\bAC-?3\b", re.IGNORECASE)
_OBJECT_RE = re.compile(r"\b(?:Atmos|Auro3D)\b", re.IGNORECASE)
_CHANNEL_RE = re.compile(r"\b\d+\.\d+\b")
_AKA_RE = re.compile(r"^AKA\s+", re.IGNORECASE)
Layout = tuple[str, str, str, str]


def _media_type(meta: Meta) -> str:
    return (meta.type or "").upper().replace("-", "")


def _is_web(media_type: str) -> bool:
    return media_type in {"WEBDL", "WEBRIP"}


def _year_text(meta: Meta) -> str:
    if meta.no_year:
        return ""
    return str(meta.manual_year or meta.year or "")


def _padded_number(value: object, prefix: str) -> str:
    text = str(value or "")
    if text.isdigit():
        return f"{prefix}{int(text):02d}"
    return text


def _tv_season_episode(meta: Meta) -> tuple[str, str]:
    year = _year_text(meta)
    if meta.category != "TV":
        return "", year
    if meta.manual_date:
        date = str(meta.manual_date).replace("-", "").replace(".", "")
        return "", date
    season = "" if meta.no_season else _padded_number(meta.season, "S")
    return f"{season}{_padded_number(meta.episode, 'E')}", year


def _bdmv_layout(meta: Meta) -> Layout | None:
    if meta.is_disc != "BDMV":
        return None
    source = "Custom BluRay" if meta.diy_disc else "Blu-ray"
    return source, "", str(meta.distributor or ""), str(meta.video_codec or "")


def _dvd_layout(meta: Meta, source: str) -> Layout | None:
    if meta.is_disc != "DVD":
        return None
    return f"{source} {meta.dvd_size}".strip(), "", str(meta.distributor or ""), ""


def _hddvd_layout(meta: Meta) -> Layout | None:
    if meta.is_disc != "HDDVD":
        return None
    return "HD DVD", "", "", str(meta.video_codec or "")


def _disc_layout(meta: Meta, source: str) -> Layout | None:
    bdmv = _bdmv_layout(meta)
    if bdmv is not None:
        return bdmv
    dvd = _dvd_layout(meta, source)
    if dvd is not None:
        return dvd
    return _hddvd_layout(meta)


def _web_layout(meta: Meta, media_type: str, video: str) -> Layout | None:
    if not _is_web(media_type):
        return None
    spec = "WEB-DL" if media_type == "WEBDL" else "WEBRip"
    return "", spec, str(meta.service or ""), _WEB_VIDEO.get(video, video)


def _hdtv_distributor(meta: Meta, source: str) -> str:
    if meta.service:
        return str(meta.service)
    if str(source).upper() == "HDTV":
        return ""
    return str(source)


def _hdtv_layout(
    meta: Meta, media_type: str, source: str, video: str
) -> Layout | None:
    if media_type != "HDTV":
        return None
    return "", "HDTV", _hdtv_distributor(meta, source), _HDTV_VIDEO.get(video, video)


def _remux_layout(meta: Meta, media_type: str, source: str) -> Layout | None:
    if media_type != "REMUX":
        return None
    return source, "Remux", "", str(meta.video_codec or "")


def _dvdrip_layout(media_type: str, video: str) -> Layout | None:
    if media_type != "DVDRIP":
        return None
    return "DVDRip", "", "", video


def _stream_layout(
    meta: Meta, media_type: str, source: str, video: str
) -> Layout | None:
    web = _web_layout(meta, media_type, video)
    if web is not None:
        return web
    hdtv = _hdtv_layout(meta, media_type, source, video)
    if hdtv is not None:
        return hdtv
    remux = _remux_layout(meta, media_type, source)
    if remux is not None:
        return remux
    return _dvdrip_layout(media_type, video)


def _base_layout(source: str, video: str) -> Layout:
    return source, "", "", video


def _layout_video(meta: Meta) -> str:
    return str(meta.video_encode or meta.video_codec or "")


def _layout_source(meta: Meta) -> str:
    return str(meta.source or "")


def _stream_or_base(
    meta: Meta, media_type: str, source: str, video: str
) -> tuple[Layout, bool]:
    stream = _stream_layout(meta, media_type, source, video)
    if stream is not None:
        return stream, _is_web(media_type)
    return _base_layout(source, video), False


def _choose_layout(meta: Meta) -> tuple[Layout, bool]:
    source = _layout_source(meta)
    disc = _disc_layout(meta, source)
    if disc is not None:
        return disc, False
    return _stream_or_base(meta, _media_type(meta), source, _layout_video(meta))


def _strip_bit_depth(video: str) -> str:
    return _BIT_DEPTH_RE.sub("", video).strip()


def _with_hi10p(video: str, bit_depth: object) -> str:
    ten_bit = str(bit_depth) == "10"
    avc = "264" in video or video == "AVC"
    if ten_bit and avc and "Hi10P" not in video:
        return f"Hi10P {video}"
    return video


def _hdr_label(hdr: object) -> str:
    text = _HDR10_RE.sub("HDR", str(hdr or ""))
    return _SDR_RE.sub("", text).strip()


def _edition_label(edition: object) -> str:
    return re.sub(
        r"^Complete$", "", str(edition or ""), flags=re.IGNORECASE
    ).strip()


def _hybrid_label(meta: Meta, edition: str) -> str:
    if meta.webdv and "hybrid" not in edition.lower():
        return "Hybrid"
    return ""


def _track_label(track: dict[str, Any]) -> str:
    fields = ("Title", "title", "TrackTitle", "ServiceKind")
    return " ".join(str(track.get(field) or "") for field in fields).lower()


def _service_kinds(track: dict[str, Any]) -> set[str]:
    raw = str(track.get("ServiceKind") or "")
    return {kind.strip().upper() for kind in raw.split("/")}


def _skip_audio_track(track: dict[str, Any]) -> bool:
    if track.get("@type") != "Audio":
        return True
    label = _track_label(track)
    kinds = _service_kinds(track)
    if "commentary" in label or "compatibility" in label or "C" in kinds:
        return True
    return False


def _media_dict(meta: Meta) -> dict[str, Any]:
    if not isinstance(meta.mediainfo, dict):
        return {}
    media = meta.mediainfo.get("media", {})
    return media if isinstance(media, dict) else {}


def _track_list(media: dict[str, Any]) -> list[Any]:
    tracks = media.get("track", [])
    return tracks if isinstance(tracks, list) else []


def _counted_audio_track(track: object) -> bool:
    return isinstance(track, dict) and not _skip_audio_track(track)


def _main_audio_count(meta: Meta) -> int:
    tracks = _track_list(_media_dict(meta))
    return sum(1 for track in tracks if _counted_audio_track(track))


def _audio_count_label(meta: Meta, media_type: str) -> str:
    if media_type != "ENCODE":
        return ""
    count = _main_audio_count(meta)
    if count > 1:
        return f"{count}Audio"
    return ""


def _normalize_audio_tokens(audio: str) -> str:
    text = _DUAL_RE.sub("", audio)
    text = _DDP_RE.sub("DDP", text)
    return _AC3_RE.sub("DD", text)


def _pull_audio_objects(audio: str) -> tuple[str, list[str]]:
    return _OBJECT_RE.sub("", audio), _OBJECT_RE.findall(audio)


def _append_channels(audio: str, channels: object) -> str:
    if channels and not _CHANNEL_RE.search(audio):
        return f"{audio} {channels}".strip()
    return audio


def _title_audio(meta: Meta) -> str:
    cleaned, objects = _pull_audio_objects(
        _normalize_audio_tokens(str(meta.audio or ""))
    )
    with_channels = _append_channels(" ".join(cleaned.split()), meta.channels)
    return " ".join([with_channels, *objects]).strip()


def _resolution_token(meta: Meta) -> str:
    resolution = str(meta.resolution or "")
    if resolution.upper() == "OTHER":
        return ""
    return resolution.lower()


def _region_token(meta: Meta) -> str:
    if meta.is_disc:
        return str(meta.region or "")
    return ""


def _uhd_token(meta: Meta, is_web: bool) -> str:
    if meta.uhd and not is_web:
        return "UHD"
    return ""


def _join_name(parts: list[str]) -> str:
    return " ".join(" ".join(parts).split())


def _append_tag(name: str, tag: object) -> str:
    text = str(tag or "")
    if not text or text.startswith("-"):
        return name + text
    return f"{name}-{text}"


def _name_parts(
    meta: Meta,
    season_episode: str,
    year: str,
    edition: str,
    layout: Layout,
    is_web: bool,
) -> list[str]:
    source, specification, distributor, video = layout
    return [
        str(meta.title or ""),
        season_episode,
        year,
        edition,
        _hybrid_label(meta, edition),
        str(meta.repack or ""),
        _resolution_token(meta),
        _region_token(meta),
        distributor,
        str(meta.three_d or ""),
        _uhd_token(meta, is_web),
        source,
        specification,
        _hdr_label(meta.hdr),
        _with_hi10p(_strip_bit_depth(video), meta.bit_depth),
        _title_audio(meta),
        _audio_count_label(meta, _media_type(meta)),
    ]


def qingwa_name(meta: Meta) -> str:
    """Build the QingWa release title from release metadata."""
    if meta.manual_name is not None:
        return str(meta.manual_name).strip()
    if not meta.title:
        return str(meta.name or "")
    season_episode, year = _tv_season_episode(meta)
    layout, is_web = _choose_layout(meta)
    edition = _edition_label(meta.edition)
    parts = _name_parts(meta, season_episode, year, edition, layout, is_web)
    return _append_tag(_join_name(parts), meta.tag)


def _candidate_names(meta: Meta, localized: dict[str, Any]) -> list[object]:
    return [
        localized.get("title"),
        localized.get("name"),
        meta.aka,
        meta.original_title,
    ]


def _clean_alias(candidate: object) -> str:
    return _AKA_RE.sub("", str(candidate or "").strip())


def _accept_alias(name: str, title: str, existing: list[str]) -> bool:
    if not name:
        return False
    folded = name.casefold()
    if folded == title.casefold():
        return False
    return folded not in {item.casefold() for item in existing}


def _alias_names(meta: Meta, localized: dict[str, Any]) -> list[str]:
    names: list[str] = []
    title = str(meta.title or "")
    for candidate in _candidate_names(meta, localized):
        if not candidate:
            continue
        name = _clean_alias(candidate)
        if _accept_alias(name, title, names):
            names.append(name)
    return names


def qingwa_aliases(meta: Meta, localized: dict[str, Any]) -> str:
    """Small description of alternate titles, excluding the main title."""
    return " / ".join(_alias_names(meta, localized))


def _topic_values(values: object) -> set[str]:
    if not isinstance(values, list):
        return set()
    return {str(item).lower() for item in values}


def _topic_set(meta: Meta) -> set[str]:
    return _topic_values(meta.genres) | _topic_values(meta.keywords)


def _themed_category(meta: Meta, topics: set[str]) -> int | None:
    if "documentary" in topics:
        return 404
    if meta.anime or "animation" in topics:
        return 405
    return None


def _tv_category(topics: set[str]) -> int:
    if topics & _REALITY:
        return 403
    return 402


def qingwa_category(meta: Meta) -> int:
    """QingWa category id from genres, keywords, and anime."""
    topics = _topic_set(meta)
    themed = _themed_category(meta, topics)
    if themed is not None:
        return themed
    if meta.category == "TV":
        return _tv_category(topics)
    return 401


def _bdmv_type(meta: Meta) -> int | None:
    if meta.is_disc != "BDMV":
        return None
    return 1 if meta.resolution == "2160p" else 8


def _dvd_type(meta: Meta) -> int | None:
    if "DVD" in str(meta.is_disc or "").upper():
        return 2
    return None


def _file_type(media_type: str) -> int:
    if media_type == "REMUX":
        return 9
    if "WEB" in media_type:
        return 7
    if media_type == "HDTV":
        return 4
    if media_type == "ENCODE":
        return 10
    return 6


def qingwa_type(meta: Meta) -> int:
    """QingWa medium id. The site calls this selector source."""
    disc = _bdmv_type(meta)
    if disc is not None:
        return disc
    dvd = _dvd_type(meta)
    if dvd is not None:
        return dvd
    return _file_type((meta.type or "").upper())


def _codec_key(meta: Meta) -> str:
    return str(meta.video_codec or "").lower().replace("-", "").replace(".", "")


def _matching_id(
    key: str, table: tuple[tuple[tuple[str, ...], int], ...]
) -> int | None:
    for names, value in table:
        if any(name in key for name in names):
            return value
    return None


def qingwa_codec(meta: Meta) -> int:
    matched = _matching_id(_codec_key(meta), _CODEC_IDS)
    return 5 if matched is None else matched


def qingwa_resolution(meta: Meta) -> int:
    key = str(meta.resolution or "").lower()
    if key in _RESOLUTION_IDS:
        return _RESOLUTION_IDS[key]
    return 4 if meta.sd else 5


def _audio_key(meta: Meta) -> str:
    return str(meta.audio or "").lower().replace("-", "").replace(" ", "")


def _dts_x(audio: str) -> int | None:
    if "dtsx" in audio or "dts:x" in audio:
        return 9
    return None


def _dts_hd(audio: str) -> int | None:
    if "dtshdma" in audio:
        return 10
    if "dtshdhra" in audio or "dtshdhr" in audio:
        return 21
    return None


def _plain_dts(audio: str) -> int | None:
    if "dts" in audio:
        return 14
    return None


def _dts_codec(audio: str) -> int | None:
    return _dts_x(audio) or _dts_hd(audio) or _plain_dts(audio)


def _truehd_codec(audio: str) -> int | None:
    if "truehd" not in audio:
        return None
    return 11 if "atmos" in audio else 12


def qingwa_audio_codec(meta: Meta) -> int:
    audio = _audio_key(meta)
    special = _dts_codec(audio)
    if special is not None:
        return special
    truehd = _truehd_codec(audio)
    if truehd is not None:
        return truehd
    matched = _matching_id(audio, _AUDIO_TABLE)
    return 7 if matched is None else matched


def qingwa_group_tag(meta: Meta) -> int:
    return _GROUP_TAGS.get(str(meta.tag or "").lower(), 5)


def _append_if(tags: list[str], present: bool, value: str) -> None:
    if present:
        tags.append(value)


def _has_any(values: object, names: tuple[str, ...]) -> bool:
    if not isinstance(values, list):
        return False
    return any(language in values for language in names)


def _language_flags(meta: Meta) -> list[str]:
    tags: list[str] = []
    _append_if(tags, bool(meta.exclusive), "1")
    _append_if(tags, _has_any(meta.audio_languages, ("Chinese", "Mandarin")), "5")
    _append_if(tags, "Cantonese" in (meta.audio_languages or []), "8")
    _append_if(
        tags,
        _has_any(meta.subtitle_languages, ("Chinese", "Mandarin", "Cantonese")),
        "6",
    )
    return tags


def _format_flags(meta: Meta) -> list[str]:
    tags: list[str] = []
    if meta.is_disc == "BDMV":
        tags.append("4" if meta.diy_disc else "11")
    if (meta.type or "").upper() == "REMUX":
        tags.append("15")
    return tags


def _hdr_text(meta: Meta) -> str:
    return str(meta.hdr or "").upper()


def _vision_flag(hdr: str) -> str | None:
    if "DV" in hdr or "DOLBY VISION" in hdr:
        return "12"
    return None


def _hdr_flag_values(hdr: str) -> list[str]:
    tags: list[str] = []
    vision = _vision_flag(hdr)
    if vision:
        tags.append(vision)
    if "HDR10+" in hdr:
        tags.append("13")
    if "HDR" in hdr:
        tags.append("7")
    return tags


def qingwa_checkboxes(meta: Meta) -> list[str]:
    return [
        *_language_flags(meta),
        *_format_flags(meta),
        *_hdr_flag_values(_hdr_text(meta)),
    ]


class QingWa(NEXUSPHP):
    """QingWa NexusPHP tracker for movies and television."""

    display_name = "QingWa"
    base_url = "https://www.qingwapt.com"
    source_flag = "[www.qingwapt.com] 青蛙"
    torrent_url = f"{base_url}/details.php?id="
    tracker_urls = (base_url,)
    allows_bloated_audio = True
    search_type_parameter = "source"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config, "QINGWA")

    async def get_name(self, meta: Meta) -> dict[str, str]:
        return {"name": qingwa_name(meta)}

    async def get_data(self, meta: Meta) -> dict[str, Any]:
        data = await super().get_data(meta)
        localized = getattr(self, "tmdb_data", {}) or {}
        if not isinstance(localized, dict):
            localized = {}
        data["small_descr"] = qingwa_aliases(meta, localized)
        return data

    def get_category(self, meta: Meta) -> int:
        return qingwa_category(meta)

    def get_type(self, meta: Meta) -> int:
        return qingwa_type(meta)

    async def get_type_data(self, meta: Meta) -> dict[str, int]:
        return {"source_sel[4]": self.get_type(meta)}

    def get_codec(self, meta: Meta) -> int:
        return qingwa_codec(meta)

    def get_resolution(self, meta: Meta) -> int:
        return qingwa_resolution(meta)

    def get_audio_codec(self, meta: Meta) -> int:
        return qingwa_audio_codec(meta)

    def get_group_tag(self, meta: Meta) -> int:
        return qingwa_group_tag(meta)

    def get_checkboxes(self, meta: Meta) -> list[str]:
        return qingwa_checkboxes(meta)
