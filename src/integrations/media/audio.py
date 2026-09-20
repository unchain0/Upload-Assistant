# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import json
import re
import traceback
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import cli_ui
import langcodes
from langcodes.tag_parser import LanguageTagError

from src.domain_models.release import Meta
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.common import Common


# Specific exception for lossy DTS core duplicate detection
class LossyDtsDuplicateError(ValueError):
    pass


TrackDict = dict[str, Any]

_LANGUAGE_EQUIVALENCE_GROUPS = (
    frozenset({"zh", "cmn", "cn"}),
    frozenset({"no", "nb"}),
)
_ATMOS_INDICATORS = (
    "JOC",
    "Atmos",
    "16-ch",
    "Atmos Audio",
    "TrueHD Atmos",
    "E-AC-3 JOC",
    "Dolby Atmos",
    "DTS:X",
    "XLL X",
)
_HEIGHT_INDICATORS = (
    "Tfc",
    "Tfl",
    "Tfr",
    "Tbl",
    "Tbr",
    "Tbc",
    "TFC",
    "TFL",
    "TFR",
    "TBL",
    "TBR",
    "TBC",
    "Vhc",
    "Vhl",
    "Vhr",
    "Ch",
    "Lh",
    "Rh",
    "Chr",
    "Lhr",
    "Rhr",
    "Top",
    "Height",
)
_ATMOS_HEIGHT_CHANNELS = frozenset(
    {
        "TFC",
        "TFL",
        "TFR",
        "TBL",
        "TBR",
        "TBC",
        "VHC",
        "VHL",
        "VHR",
        "CH",
        "LH",
        "RH",
        "CHR",
        "LHR",
        "RHR",
        "TSL",
        "TSR",
        "TLS",
        "TRS",
    }
)
_ATMOS_BED_CHANNELS = frozenset(
    {
        "L",
        "R",
        "C",
        "FC",
        "LS",
        "RS",
        "SL",
        "SR",
        "BL",
        "BR",
        "BC",
        "SB",
        "FLC",
        "FRC",
        "LC",
        "RC",
        "LW",
        "RW",
        "FLW",
        "FRW",
        "LSS",
        "RSS",
        "SIL",
        "SIR",
        "LB",
        "RB",
        "CB",
        "CS",
    }
)
_FALLBACK_CHANNEL_MAP = {
    1: "1.0",
    2: "2.0",
    3: "2.1",
    4: "3.1",
    5: "4.1",
    6: "5.1",
    7: "6.1",
    8: "7.1",
}


def _language_from_tag(language: str) -> str | None:
    try:
        return str(langcodes.Language.get(language).language or "").lower()
    except ValueError, LanguageTagError:
        return None


def _language_from_name(language: str) -> str | None:
    try:
        return str(langcodes.find(language).language or "").lower()
    except LookupError:
        return None


def _canonical_language_code(value: Any) -> str:
    language = str(value or "").strip()
    if not language:
        return ""
    tagged = _language_from_tag(language)
    if tagged is not None:
        return tagged
    named = _language_from_name(language)
    return named if named is not None else language.casefold()


def _languages_equivalent(left: str, right: str) -> bool:
    if not left:
        return False
    if not right:
        return False
    if left == right:
        return True
    pair = frozenset((left, right))
    return any(pair.issubset(group) for group in _LANGUAGE_EQUIVALENCE_GROUPS)


class AudioManager:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    async def get_audio_v2(
        self,
        mi: Mapping[str, Any],
        meta: Meta,
        bdinfo: Mapping[str, Any] | None,
    ) -> tuple[str, str, bool]:
        return await _get_audio_v2(self.config, mi, meta, bdinfo)


def _parsed_channel_number(channels: Any) -> int | None:
    text = str(channels).strip() if channels is not None else ""
    match = re.search(r"\d+", text)
    return int(match.group(0)) if match else None


def determine_channel_count(
    channels: Any,
    channel_layout: str | None,
    additional: Any,
    format: Any,
) -> str:
    channel_count = _parsed_channel_number(channels)
    if channel_count is None:
        return "Unknown"
    layout = channel_layout.strip() if channel_layout else ""
    if not layout:
        return fallback_channel_count(channel_count)
    if is_atmos_or_immersive_audio(additional, format, layout):
        return handle_atmos_channel_count(channel_count, layout)
    return parse_channel_layout(channel_count, layout)


def _contains_any_indicator(value: Any, indicators: Sequence[str]) -> bool:
    text = str(value or "")
    return any(indicator in text for indicator in indicators)


def is_atmos_or_immersive_audio(
    additional: Any, format: Any, channel_layout: str | None
) -> bool:
    """Check if this is Dolby Atmos, DTS:X, or other immersive audio format."""
    if _contains_any_indicator(additional, _ATMOS_INDICATORS):
        return True
    if _contains_any_indicator(format, _ATMOS_INDICATORS):
        return True
    return _contains_any_indicator(channel_layout, _HEIGHT_INDICATORS)


def handle_atmos_channel_count(channels: int, channel_layout: str) -> str:
    """Handle Dolby Atmos and immersive audio channel counting."""

    # Parse the layout to count bed and height channels
    bed_channels, lfe_count, height_channels = parse_atmos_layout(
        channel_layout
    )

    if height_channels > 0:
        if lfe_count > 0:
            return f"{bed_channels}.{lfe_count}.{height_channels}"
        return f"{bed_channels}.0.{height_channels}"
    # Fallback to standard counting
    return parse_channel_layout(channels, channel_layout)


def _atmos_channel_kind(channel: str) -> str | None:
    if "LFE" in channel:
        return "lfe"
    if channel in _ATMOS_HEIGHT_CHANNELS:
        return "height"
    if channel in _ATMOS_BED_CHANNELS:
        return "bed"
    return None


def parse_atmos_layout(channel_layout: str | None) -> tuple[int, int, int]:
    """Parse channel layout to separate bed channels, LFE, and height channels."""
    if not channel_layout:
        return 0, 0, 0
    counts = {"bed": 0, "lfe": 0, "height": 0}
    for channel in channel_layout.upper().split():
        kind = _atmos_channel_kind(channel.strip())
        if kind is not None:
            counts[kind] += 1
    return counts["bed"], counts["lfe"], counts["height"]


def parse_channel_layout(channels: int, channel_layout: str) -> str:
    """Parse standard channel layout to determine proper channel count notation."""
    lfe_count = channel_layout.upper().count("LFE")
    if lfe_count > 1:
        return f"{channels - lfe_count}.{lfe_count}"
    if lfe_count == 1:
        return f"{channels - 1}.1"
    if "object" in channel_layout.lower() and channels > 7:
        return f"{channels - 1}.1"
    return f"{channels}.0"


def fallback_channel_count(channels: int) -> str:
    """Fallback channel counting when no layout information is available."""
    if channels in _FALLBACK_CHANNEL_MAP:
        return _FALLBACK_CHANNEL_MAP[channels]
    return f"{channels - 1}.1"


_AUDIO_CODEC_MAP = {
    "DTS": "DTS",
    "AAC": "AAC",
    "AAC LC": "AAC",
    "AC-3": "DD",
    "E-AC-3": "DD+",
    "A_EAC3": "DD+",
    "Enhanced AC-3": "DD+",
    "MLP FBA": "TrueHD",
    "FLAC": "FLAC",
    "Opus": "Opus",
    "Vorbis": "VORBIS",
    "PCM": "LPCM",
    "LPCM Audio": "LPCM",
    "Dolby Digital Audio": "DD",
    "Dolby Digital Plus Audio": "DD+",
    "Dolby Digital Plus": "DD+",
    "Dolby TrueHD Audio": "TrueHD",
    "DTS Audio": "DTS",
    "DTS-HD Master Audio": "DTS-HD MA",
    "DTS-HD High-Res Audio": "DTS-HD HRA",
    "DTS:X Master Audio": "DTS:X",
}
_AUDIO_EXTRA_MAP = {"XLL": "-HD MA", "XLL X": ":X", "ES": "-ES"}
_FORMAT_EXTRA_MAP = {
    "JOC": " Atmos",
    "16-ch": " Atmos",
    "Atmos Audio": " Atmos",
}
_FORMAT_SETTINGS_EXTRA = {"Dolby Surround EX": "EX"}
_COMMERCIAL_CODEC_MAP = {
    "Dolby Digital": "DD",
    "Dolby Digital Plus": "DD+",
    "Dolby TrueHD": "TrueHD",
    "DTS-ES": "DTS-ES",
    "DTS-HD High": "DTS-HD HRA",
    "Free Lossless Audio Codec": "FLAC",
    "DTS-HD Master Audio": "DTS-HD MA",
}


@dataclass
class _AudioFields:
    additional: Any = ""
    audio_format: Any = ""
    commercial: Any = ""
    chan: str = ""
    format_settings: str = ""
    format_profile: str = ""
    dual: str = ""
    has_commentary: bool = False
    is_auro3d: bool = False


@dataclass(frozen=True)
class _LanguageFlags:
    english: bool
    original: bool
    non_english_non_original: bool
    other_languages: list[str]


def _media_tracks(mi: Mapping[str, Any]) -> list[TrackDict]:
    media_value = mi.get("media", {})
    if not isinstance(media_value, Mapping):
        return []
    media = cast(Mapping[str, Any], media_value)
    raw_tracks = media.get("track", [])
    if not isinstance(raw_tracks, list):
        return []
    return [
        cast(TrackDict, track)
        for track in cast(list[Any], raw_tracks)
        if isinstance(track, dict)
    ]


def _audio_tracks(tracks: list[TrackDict]) -> list[TrackDict]:
    return [track for track in tracks if track.get("@type") == "Audio"]


def _stream_order_value(track: Mapping[str, Any]) -> int:
    return int(str(track.get("StreamOrder", "999")))


def _track_id_value(track: Mapping[str, Any]) -> int:
    match = re.search(r"\d+", str(track.get("ID", "999")))
    return int(match.group()) if match else 999


def _ordered_audio_tracks(audio_tracks: list[TrackDict]) -> list[TrackDict]:
    return [
        track
        for track in audio_tracks
        if track.get("StreamOrder")
        and not isinstance(track.get("StreamOrder"), dict)
    ]


def _id_audio_tracks(audio_tracks: list[TrackDict]) -> list[TrackDict]:
    return [
        track
        for track in audio_tracks
        if track.get("ID") and not isinstance(track.get("ID"), dict)
    ]


def _first_audio_track(audio_tracks: list[TrackDict]) -> TrackDict:
    if not audio_tracks:
        return {}
    ordered = _ordered_audio_tracks(audio_tracks)
    if ordered:
        try:
            return min(ordered, key=_stream_order_value)
        except ValueError, TypeError:
            return ordered[0]
    with_ids = _id_audio_tracks(audio_tracks)
    if with_ids:
        return min(with_ids, key=_track_id_value)
    return audio_tracks[0]


def _set_audio_meta_flags(meta: Meta, audio_tracks: list[TrackDict]) -> None:
    defaults = [
        track for track in audio_tracks if track.get("Default") == "Yes"
    ]
    meta.has_multiple_default_audio_tracks = len(defaults) > 1
    has_pcm = any(track.get("Format") == "PCM" for track in audio_tracks)
    meta.non_disc_has_pcm_audio_tracks = meta.type != "DISC" and has_pcm


def _track_channels(track: TrackDict) -> Any:
    original = track.get("Channels_Original", track.get("Channels"))
    return original if str(original).isnumeric() else track.get("Channels")


def _track_channel_layout(track: TrackDict) -> str:
    try:
        return str(
            track.get("ChannelLayout", "")
            or track.get("ChannelLayout_Original", "")
            or track.get("ChannelPositions", "")
        )
    except Exception:
        return ""


def _apply_track_fields(
    fields: _AudioFields, track: TrackDict, meta: Meta
) -> None:
    fields.audio_format = track.get("Format", "")
    fields.commercial = track.get("Format_Commercial", "") or track.get(
        "Format_Commercial_IfAny", ""
    )
    if track.get("Language", "") == "zxx":
        meta.silent = True
    fields.additional = track.get("Format_AdditionalFeatures", "")
    fields.format_settings = str(track.get("Format_Settings") or "")
    if fields.format_settings == "Explicit":
        fields.format_settings = ""
    fields.format_profile = str(track.get("Format_Profile", ""))
    channels = _track_channels(track)
    layout = _track_channel_layout(track)
    logger.debug(
        f"DEBUG: Channels: {channels}, Channel Layout: {layout}, Additional: {fields.additional}, Format: {fields.audio_format}"
    )
    fields.chan = determine_channel_count(
        channels, layout, fields.additional, fields.audio_format
    )


def _media_info_path(meta: Meta) -> Path | None:
    folder_id = meta.uuid or meta.folder_id
    if not meta.base_dir or not folder_id:
        return None
    return Path(meta.base_dir) / "tmp" / str(folder_id) / "MediaInfo.json"


async def _load_tmp_media_info(
    meta: Meta, current: Mapping[str, Any]
) -> Mapping[str, Any]:
    path = _media_info_path(meta)
    if path is None or not path.exists():
        return current
    text = await asyncio.to_thread(path.read_text, encoding="utf-8")
    loaded = json.loads(text)
    logger.debug(f"[yellow]Loaded MediaInfo from file:[/yellow] {path}")
    return cast(Mapping[str, Any], loaded)


def _first_bd_audio(bdinfo: Mapping[str, Any]) -> TrackDict:
    raw = bdinfo.get("audio", [{}])
    if not isinstance(raw, list) or not raw:
        return {}
    first = cast(list[Any], raw)[0]
    return cast(TrackDict, first) if isinstance(first, dict) else {}


async def _bdinfo_source(
    config: dict[str, Any],
    meta: Meta,
    bdinfo: Mapping[str, Any],
    mi: Mapping[str, Any],
    fields: _AudioFields,
) -> tuple[Mapping[str, Any], bool]:
    first_audio = _first_bd_audio(bdinfo)
    fields.additional = first_audio.get("atmos_why_you_be_like_this", "")
    additional = str(fields.additional or "")
    if "atmos" not in additional.lower():
        fields.audio_format = first_audio.get("codec", "")
        fields.commercial = fields.audio_format
        fields.chan = str(first_audio.get("channels", "") or "")
        return mi, False
    common = Common(config)
    bd_mi = await common.get_bdmv_mediainfo(meta)
    try:
        loaded_mi = await _load_tmp_media_info(meta, mi)
    except Exception:
        logger.debug(
            "[red]Failed to load MediaInfo.json from tmp directory[/red]"
        )
        logger.debug(traceback.format_exc())
        return mi, False
    return loaded_mi, bd_mi is not None


def _track_title(track: TrackDict) -> str:
    value = track.get("title")
    if value:
        return str(value)
    return str(track.get("Title") or "")


def _first_audio_title(tracks: list[TrackDict]) -> str:
    for track in tracks:
        if track.get("@type") == "Audio":
            return _track_title(track)
    return ""


def _is_commentary_track(track: TrackDict) -> bool:
    return "commentary" in str(track.get("Title") or "").lower()


def _is_compatibility_track(track: TrackDict) -> bool:
    return "compatibility" in str(track.get("Title") or "").lower()


def _is_main_audio_track(track: TrackDict) -> bool:
    if track.get("@type") != "Audio":
        return False
    if _is_commentary_track(track):
        return False
    return not _is_compatibility_track(track)


def _language_audio_tracks(
    tracks: list[TrackDict],
) -> tuple[list[TrackDict], bool]:
    commentary = any(_is_commentary_track(track) for track in tracks)
    return [
        track for track in tracks if _is_main_audio_track(track)
    ], commentary


def _language_pair(track: TrackDict) -> tuple[str, str]:
    language = str(track.get("Language") or "").lower().strip()
    code = _canonical_language_code(language)
    logger.debug(f"DEBUG: Audio Language = {language} ({code})")
    return language, code


def _is_other_audio_language(
    language: str, code: str, original_language: str
) -> bool:
    if not language:
        return False
    if _languages_equivalent(code, original_language):
        return False
    return code not in ("en", "zxx")


def _has_original_language(codes: list[str], original_language: str) -> bool:
    for code in codes:
        if _languages_equivalent(code, original_language):
            return True
    return False


def _other_audio_languages(
    pairs: list[tuple[str, str]], original_language: str
) -> list[str]:
    return [
        language
        for language, code in pairs
        if _is_other_audio_language(language, code, original_language)
    ]


def _language_flags(
    audio_tracks: list[TrackDict], original_language: str
) -> _LanguageFlags:
    pairs = [_language_pair(track) for track in audio_tracks]
    codes = [code for _language, code in pairs]
    other_languages = _other_audio_languages(pairs, original_language)
    return _LanguageFlags(
        english="en" in codes,
        original=_has_original_language(codes, original_language),
        non_english_non_original=bool(other_languages),
        other_languages=other_languages,
    )


def _check_language_bloat(
    meta: Meta, flags: _LanguageFlags, original_language: str
) -> None:
    if not flags.other_languages:
        return
    is_english_original = (
        original_language == "en"
        and flags.english
        and flags.non_english_non_original
    )
    bloated_check(
        meta,
        flags.other_languages,
        is_eng_original_with_non_eng=is_english_original,
    )


def _distinct_language_pair(
    flags: _LanguageFlags, original_language: str
) -> bool:
    return any(
        (
            flags.english and flags.original and original_language != "en",
            flags.english and flags.non_english_non_original,
            flags.original and flags.non_english_non_original,
        )
    )


def _should_mark_dual(
    meta: Meta,
    flags: _LanguageFlags,
    original_language: str,
    audio_track_count: int,
) -> bool:
    return all(
        (
            _distinct_language_pair(flags, original_language),
            audio_track_count > 1,
            not meta.no_dual,
        )
    )


def _should_mark_dubbed(
    meta: Meta, flags: _LanguageFlags, original_language: str
) -> bool:
    return all(
        (
            flags.english,
            not flags.original,
            original_language not in ("zxx", "xx", "en", ""),
            not meta.no_dub,
        )
    )


def _dual_label(
    meta: Meta,
    flags: _LanguageFlags,
    original_language: str,
    audio_track_count: int,
) -> str:
    if _should_mark_dual(meta, flags, original_language, audio_track_count):
        meta.dual_audio = True
        return "Dual-Audio"
    if _should_mark_dubbed(meta, flags, original_language):
        return "Dubbed"
    return ""


def _analyze_audio_languages(
    meta: Meta, tracks: list[TrackDict], fields: _AudioFields
) -> None:
    try:
        fields.is_auro3d = "auro3d" in _first_audio_title(tracks).lower()
        audio_tracks, fields.has_commentary = _language_audio_tracks(tracks)
        original_language = _canonical_language_code(meta.original_language)
        logger.debug(f"DEBUG: Original Language: {original_language}")
        logger.debug(
            f"DEBUG: Audio Tracks (not commentary)= {len(audio_tracks)}"
        )
        flags = _language_flags(audio_tracks, original_language)
        _check_language_bloat(meta, flags, original_language)
        fields.dual = _dual_label(
            meta, flags, original_language, len(audio_tracks)
        )
    except Exception:
        logger.info(traceback.format_exc())


def _populate_from_mediainfo(
    mi: Mapping[str, Any], meta: Meta, fields: _AudioFields
) -> None:
    tracks = _media_tracks(mi)
    audio_tracks = _audio_tracks(tracks)
    _set_audio_meta_flags(meta, audio_tracks)
    _apply_track_fields(fields, _first_audio_track(audio_tracks), meta)
    dts_core_additional_check(meta)
    if meta.dual_audio:
        fields.dual = "Dual-Audio"
        return
    if not meta.is_disc:
        _analyze_audio_languages(meta, tracks, fields)


def _mapped_commercial_codec(commercial: str) -> str:
    codec = ""
    for key, value in _COMMERCIAL_CODEC_MAP.items():
        if key in commercial:
            codec = value
    return codec


def _search_format_codec(format_name: str, additional: str) -> tuple[str, str]:
    codec = _AUDIO_CODEC_MAP.get(format_name, "") + _AUDIO_EXTRA_MAP.get(
        additional, ""
    )
    return codec, _FORMAT_EXTRA_MAP.get(additional, "")


def _mpeg_codec(profile: str) -> str:
    return {"Layer 2": "MP2", "Layer 3": "MP3"}.get(profile, "")


def _normalized_additional(value: Any) -> str:
    return "" if isinstance(value, dict) else str(value or "")


def _commercial_codec_fields(
    commercial: str, additional: str, format_name: str
) -> tuple[str, str]:
    codec = _mapped_commercial_codec(commercial) if commercial else ""
    if not codec:
        return _search_format_codec(format_name, additional)
    has_atmos = (
        "Atmos" in commercial or _FORMAT_EXTRA_MAP.get(additional) == " Atmos"
    )
    return codec, " Atmos" if has_atmos else ""


def _format_settings_label(fields: _AudioFields) -> str:
    value = _FORMAT_SETTINGS_EXTRA.get(fields.format_settings, "")
    return value if value == "EX" and fields.chan == "5.1" else ""


def _dts_codec_override(result: str, format_name: str, additional: str) -> str:
    if format_name.startswith("DTS") and additional.endswith("X"):
        return "DTS:X"
    return result


def _mpeg_codec_override(result: str, format_name: str, profile: str) -> str:
    if format_name != "MPEG Audio":
        return result
    return _mpeg_codec(profile) or result


def _dd_channel_override(result: str, chan: str) -> str:
    if result != "DD" or chan != "7.1":
        return result
    logger.info(
        "[warning] Detected codec is DD but channel count is 7.1, correcting to DD+"
    )
    return "DD+"


def _codec_overrides(
    codec: str,
    format_name: str,
    additional: str,
    profile: str,
    chan: str,
) -> str:
    result = codec or format_name
    result = _dts_codec_override(result, format_name, additional)
    result = _mpeg_codec_override(result, format_name, profile)
    return _dd_channel_override(result, chan)


def _audio_extra(extra: str, is_auro3d: bool) -> str:
    if extra:
        return extra
    return " Auro3D" if is_auro3d else ""


def _named_codec(fields: _AudioFields) -> tuple[str, str, str]:
    additional = _normalized_additional(fields.additional)
    format_name = str(fields.audio_format or "")
    commercial = str(fields.commercial or "")
    codec, extra = _commercial_codec_fields(
        commercial, additional, format_name
    )
    codec = _codec_overrides(
        codec,
        format_name,
        additional,
        fields.format_profile,
        fields.chan,
    )
    return (
        codec,
        _format_settings_label(fields),
        _audio_extra(extra, fields.is_auro3d),
    )


def _audio_label(fields: _AudioFields) -> str:
    codec, format_settings, extra = _named_codec(fields)
    value = f"{fields.dual} {codec} {format_settings} {fields.chan}{extra}"
    return " ".join(value.split())


async def _get_audio_v2(
    config: dict[str, Any],
    mi: Mapping[str, Any],
    meta: Meta,
    bdinfo: Mapping[str, Any] | None,
) -> tuple[str, str, bool]:
    meta.bloated = False
    fields = _AudioFields()
    should_parse_mediainfo = bdinfo is None
    if bdinfo is not None:
        mi, should_parse_mediainfo = await _bdinfo_source(
            config, meta, bdinfo, mi, fields
        )
    if should_parse_mediainfo:
        _populate_from_mediainfo(mi, meta, fields)
    return _audio_label(fields), fields.chan, fields.has_commentary


def _tracker_class_for_bloat(tracker_name: str) -> Any | None:
    try:
        from src.integrations.trackers.registry import tracker_class_map

        return tracker_class_map.get(tracker_name.upper())
    except Exception:
        return None


def _normalized_bloat_languages(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, str):
        return (value.lower(),)
    return tuple(str(language).lower() for language in value)


def _tracker_bloat_rules(
    tracker_name: str,
) -> tuple[bool, tuple[str, ...], bool]:
    tracker_class = _tracker_class_for_bloat(tracker_name)
    if tracker_class is None:
        return False, (), False
    return (
        bool(getattr(tracker_class, "allows_bloated_audio", False)),
        _normalized_bloat_languages(
            getattr(tracker_class, "allowed_bloated_audio_languages", ())
        ),
        bool(getattr(tracker_class, "reject_english_original_bloat", False)),
    )


def _tracker_allows_language(tracker: str, audio_language: str) -> bool:
    _allows, allowed_languages, _reject = _tracker_bloat_rules(tracker)
    return any(
        audio_language.lower().startswith(language)
        for language in allowed_languages
    )


def _trackers_requiring_bloat_warning(
    trackers: Sequence[str], audio_language: str
) -> list[str]:
    requiring: list[str] = []
    for tracker in trackers:
        allows_bloat, _languages, _reject = _tracker_bloat_rules(tracker)
        if _tracker_allows_language(tracker, audio_language):
            continue
        if not allows_bloat:
            requiring.append(tracker)
    return requiring


def _language_display_name(audio_language: str) -> str:
    clean = audio_language.split("-")[0].split("_")[0].strip().lower()
    if not clean:
        return audio_language
    try:
        return langcodes.Language.get(clean).display_name()
    except (LookupError, AttributeError, ValueError) as error:
        logger.debug(
            f"[yellow]Debug: Unable to convert language code '{audio_language}' to full name: {error}[/yellow]"
        )
        return audio_language


def _split_bloat_trackers(
    trackers: list[str], english_original: bool
) -> tuple[list[str], list[str]]:
    if not english_original:
        return [], trackers
    rejected: list[str] = []
    warnings: list[str] = []
    for tracker in trackers:
        _allows, _languages, reject = _tracker_bloat_rules(tracker)
        (rejected if reject else warnings).append(tracker)
    return rejected, warnings


def _remove_bloated_trackers(
    meta: Meta, trackers: list[str], language_display: str
) -> None:
    tracker_list = ", ".join(trackers)
    logger.info(
        f"[bold red]This release is English original, has English audio, but also has [bold yellow]{language_display}[/bold yellow] audio and is not allowed on [yellow]{tracker_list}[/yellow][/bold red]"
    )
    meta.trackers = [
        tracker for tracker in meta.trackers if tracker not in trackers
    ]
    meta.bloated = True
    logger.debug(f"[yellow]Removed trackers: {tracker_list}[/yellow]")
    remaining = ", ".join(meta.trackers) if meta.trackers else "None"
    logger.debug(f"[yellow]Remaining trackers: {remaining}[/yellow]")


def _bloat_warning_message(
    trackers: list[str],
    language_display: str,
    english_original: bool,
    already_removed: bool,
) -> str:
    tracker_list = ", ".join(trackers)
    if already_removed:
        return f"[bold red]This release may also be considered bloated on [yellow]{tracker_list}[/yellow][/bold red]"
    if english_original:
        return f"[bold red]This release is English original, has English audio, but also has [bold yellow]{language_display}[/bold yellow] audio (not commentary).\nThis may be considered bloated on [yellow]{tracker_list}[/yellow][/bold red]"
    return f"[bold red]This release has a(n) [bold yellow]{language_display}[/bold yellow] audio track, which is not original language, not English\nand may be considered bloated on [yellow]{tracker_list}[/yellow][/bold red]"


def _warn_bloated_trackers(
    meta: Meta,
    trackers: list[str],
    language_display: str,
    english_original: bool,
    already_removed: bool,
) -> None:
    logger.info(
        _bloat_warning_message(
            trackers, language_display, english_original, already_removed
        )
    )
    meta.bloated = True


def _maybe_remove_bloated_trackers(
    meta: Meta,
    trackers: list[str],
    language_display: str,
    already_printed: bool,
) -> bool:
    if already_printed or not trackers:
        return already_printed
    _remove_bloated_trackers(meta, trackers, language_display)
    return True


def _maybe_warn_bloated_trackers(
    meta: Meta,
    trackers: list[str],
    language_display: str,
    english_original: bool,
    already_removed: bool,
    already_printed: bool,
) -> bool:
    if already_printed or not trackers:
        return already_printed
    _warn_bloated_trackers(
        meta, trackers, language_display, english_original, already_removed
    )
    return True


def _process_bloat_language(
    meta: Meta,
    audio_language: str,
    english_original: bool,
    printed_not_allowed: bool,
    printed_warning: bool,
) -> tuple[bool, bool]:
    trackers = _trackers_requiring_bloat_warning(
        cast(list[str], meta.trackers), audio_language
    )
    if not trackers:
        return printed_not_allowed, printed_warning
    display = _language_display_name(audio_language)
    rejected, warnings = _split_bloat_trackers(trackers, english_original)
    printed_not_allowed = _maybe_remove_bloated_trackers(
        meta, rejected, display, printed_not_allowed
    )
    printed_warning = _maybe_warn_bloated_trackers(
        meta,
        warnings,
        display,
        english_original,
        printed_not_allowed,
        printed_warning,
    )
    return printed_not_allowed, printed_warning


def bloated_check(
    meta: Meta,
    audio_languages: Sequence[str] | str,
    is_eng_original_with_non_eng: bool = False,
) -> None:
    languages = (
        [audio_languages]
        if isinstance(audio_languages, str)
        else audio_languages
    )
    printed_not_allowed = False
    printed_warning = False
    for audio_language in languages:
        printed_not_allowed, printed_warning = _process_bloat_language(
            meta,
            audio_language,
            is_eng_original_with_non_eng,
            printed_not_allowed,
            printed_warning,
        )
        if printed_not_allowed and printed_warning:
            return


def _dts_commercial(track: TrackDict) -> str:
    return str(track.get("Format_Commercial_IfAny") or "")


def _is_dts_hd_ma(track: TrackDict) -> bool:
    return _dts_commercial(track) == "DTS-HD Master Audio"


def _is_lossy_dts(track: TrackDict) -> bool:
    return track.get("Format") == "DTS" and not _is_dts_hd_ma(track)


def _dts_match_properties(track: TrackDict) -> tuple[Any, Any, Any, Any]:
    return (
        track.get("Duration"),
        track.get("FrameRate"),
        track.get("FrameCount"),
        track.get("Language"),
    )


def _has_meaningful_dts_properties(properties: tuple[Any, ...]) -> bool:
    return any(value is not None for value in properties)


def _dts_pair_orientation(
    first: TrackDict, second: TrackDict
) -> tuple[TrackDict, TrackDict] | None:
    if _is_dts_hd_ma(first) and _is_lossy_dts(second):
        return first, second
    if _is_dts_hd_ma(second) and _is_lossy_dts(first):
        return second, first
    return None


def _dts_duplicate_pair(
    first: TrackDict, second: TrackDict
) -> tuple[TrackDict, TrackDict] | None:
    first_properties = _dts_match_properties(first)
    second_properties = _dts_match_properties(second)
    if first_properties != second_properties:
        return None
    if not _has_meaningful_dts_properties(first_properties):
        return None
    return _dts_pair_orientation(first, second)


def _audio_tracks_from_meta(meta: Meta) -> list[TrackDict]:
    return _audio_tracks(_media_tracks(meta.mediainfo))


def _first_dts_duplicate(
    audio_tracks: list[TrackDict],
) -> tuple[int, int, TrackDict] | None:
    for first_index, first in enumerate(audio_tracks):
        for second_index in range(first_index + 1, len(audio_tracks)):
            second = audio_tracks[second_index]
            pair = _dts_duplicate_pair(first, second)
            if pair is None:
                continue
            hd_track, _lossy_track = pair
            if hd_track is first:
                return first_index + 1, second_index + 1, hd_track
            return second_index + 1, first_index + 1, hd_track
    return None


def _confirm_dts_duplicate(meta: Meta) -> bool:
    if meta.unattended and not meta.unattended_confirm:
        return False
    try:
        return bool(
            cli_ui.ask_yes_no("Do you want to upload anyway?", default=False)
        )
    except Exception:
        return False


def _raise_lossy_dts_duplicate() -> None:
    raise LossyDtsDuplicateError(
        "Upload cancelled due to lossy DTS core duplicate detected."
    )


def dts_core_additional_check(meta: Meta) -> None:
    duplicate = _first_dts_duplicate(_audio_tracks_from_meta(meta))
    if duplicate is None:
        return
    hd_idx, lossy_idx, hd_track = duplicate
    logger.debug(
        f"[yellow]DEBUG: Detected potential DTS core duplicate between tracks {hd_idx} and {lossy_idx}, matched on properties: (Duration={hd_track.get('Duration')}, FrameRate={hd_track.get('FrameRate')}, FrameCount={hd_track.get('FrameCount')}, Language={hd_track.get('Language')})[/yellow]"
    )
    logger.info(
        f"[bold red]DTS audio track #{lossy_idx} appears to be a lossy duplicate of DTS-HD MA track #{hd_idx}.[/bold red]"
    )
    if _confirm_dts_duplicate(meta):
        return
    _raise_lossy_dts_duplicate()
