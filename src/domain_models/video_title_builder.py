"""Build movie and TV titles from release components."""

import re
from dataclasses import dataclass, replace
from typing import Literal

from src.domain_models.release import Meta
from src.domain_models.release_identity import has_english_language

DVD_SOURCES = ("PAL DVD", "NTSC DVD", "DVD")
BLU_EDITION_TERMS = re.compile(
    r"\b(?:\d+(?:st|nd|rd|th)\s+)?anniversary(?:\s+edition)?\b"
    r"|\b(?:4k\s+)?remaster(?:ed)?\b"
    r"|\bcriterion(?:\s+collection)?\b"
    r"|\blimited(?:\s+edition)?\b"
    r"|\bcollector'?s?(?:\s+edition)?\b"
    r"|\bdeluxe(?:\s+edition)?\b",
    re.IGNORECASE,
)
TVDB_TITLE_YEAR = re.compile(r"\((19\d{2}|20\d{2})\)\s*$")


@dataclass(frozen=True)
class NameComponents:
    """Fields shared by the generic name and tracker title templates."""

    type: str
    title: str
    alt_title: str
    year: str
    resolution: str
    audio: str
    hardcoded_subs: str
    service: str
    season: str
    episode: str
    part: str
    repack: str
    three_d: str
    tag: str
    source: str
    uhd: str
    hdr: str
    hybrid: str
    episode_title: str
    edition: str


@dataclass(frozen=True)
class VideoNameRules:
    """How one tracker title differs from the shared video template."""

    language_slot: Literal["", "before_cut", "before_resolution"] = ""
    english_dub_only: bool = False
    keep_multi: bool = False
    dual_audio_label: str = "Dual-Audio"
    edition_terms: re.Pattern[str] | None = None
    dvd_resolution: bool = False
    dvd_video_codec: bool = False
    three_d_slot: Literal["cut", "source", "source_on_discs"] = "source"
    aka: bool = True
    hybrid_on_webdl: bool = True
    honor_manual_year_on_tv: bool = False
    tv_year_from_tvdb: bool = True


BLU_NAME_RULES = VideoNameRules(
    english_dub_only=True, edition_terms=BLU_EDITION_TERMS
)


def _name_text(value: object) -> str:
    return "" if value is None or value is False else str(value)


def _positive_year(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, int):
        return ""
    if value <= 0:
        return ""
    return str(value)


def _tv_component_year(meta: Meta, year: str) -> str:
    if meta.category == "TV" and meta.search_year == "":
        return ""
    return year


def _component_year(meta: Meta) -> str:
    manual = _positive_year(meta.manual_year)
    if manual and not meta.no_year:
        return manual
    if meta.no_year:
        return ""
    year = "" if meta.year is None else str(meta.year)
    return _tv_component_year(meta, year)


def _field_text(value: object) -> str:
    return str(value or "")


def _component_season_episode(meta: Meta) -> tuple[str, str]:
    if meta.category == "TV" and meta.manual_date:
        return "", ""
    season = "" if meta.no_season else _field_text(meta.season)
    return season, _field_text(meta.episode)


def _component_episode_title(meta: Meta) -> str:
    if meta.manual_episode_title:
        return str(meta.manual_episode_title)
    return str(meta.daily_episode_title or "")


def _edition_without_hybrid(edition: object) -> str:
    return " ".join(
        re.sub(r"\bhybrid\b", " ", str(edition or ""), flags=re.IGNORECASE).split()
    )


def _resolution_text(meta: Meta) -> str:
    if meta.resolution == "OTHER":
        return ""
    return _name_text(meta.resolution)


def _alt_title(meta: Meta) -> str:
    if meta.no_aka:
        return ""
    return _name_text(meta.aka)


def _hybrid_text(meta: Meta) -> str:
    if meta.webdv:
        return "Hybrid"
    return ""


def _subs_text(meta: Meta) -> str:
    if meta.hardcoded_subs:
        return "HC"
    return ""


def build_name_components(meta: Meta) -> NameComponents:
    """Collect the fields a tracker title is assembled from."""
    season, episode = _component_season_episode(meta)
    return NameComponents(
        type=_name_text(meta.type).upper(),
        title=_name_text(meta.title),
        alt_title=_alt_title(meta),
        year=_component_year(meta),
        resolution=_resolution_text(meta),
        audio=_name_text(meta.audio),
        hardcoded_subs=_subs_text(meta),
        service=_name_text(meta.service),
        season=season,
        episode=episode,
        part=_name_text(meta.part),
        repack=_name_text(meta.repack),
        three_d=_name_text(meta.three_d),
        tag=_name_text(meta.tag),
        source=_name_text(meta.source),
        uhd=_name_text(meta.uhd),
        hdr=_name_text(meta.hdr),
        hybrid=_hybrid_text(meta),
        episode_title=_component_episode_title(meta),
        edition=_edition_without_hybrid(meta.edition),
    )


def _tvdb_names(meta: Meta) -> list[str]:
    data = (
        meta.tvdb_episode_data
        if isinstance(meta.tvdb_episode_data, dict)
        else {}
    )
    names: list[str] = []
    for key in ("series_name", "series_title"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            names.append(value.strip())
    return names


def tvdb_title_year(meta: Meta) -> str | None:
    """Year TVDB appends so same-named series stay distinct."""
    names = _tvdb_names(meta)
    if not names:
        return None
    for name in names:
        match = TVDB_TITLE_YEAR.search(name)
        if match:
            return match.group(1)
    return ""


def _imdb_mapping(meta: Meta) -> dict[str, object]:
    info = meta.imdb_info
    return info if isinstance(info, dict) else {}


def _imdb_alt(aka: str, title: str, no_aka: bool) -> str:
    if not aka or aka == title or no_aka:
        return ""
    return f"AKA {aka}"


def _imdb_year(meta: Meta, current: str, imdb_year: str) -> str:
    if meta.category == "TV" or not imdb_year or not current:
        return current
    if imdb_year == current:
        return current
    return imdb_year


def _mapping_text(info: dict[str, object], key: str) -> str:
    return str(info.get(key) or "").strip()


def _chosen_title(title: str, fallback: str) -> str:
    return title or fallback


def _chosen_alt(
    title: str, aka: str, no_aka: bool, current: str
) -> str:
    if title:
        return _imdb_alt(aka, title, no_aka)
    return current


def with_imdb_title(meta: Meta, components: NameComponents) -> NameComponents:
    """Prefer the IMDb title, AKA, and movie year when the guide requires them."""
    info = _imdb_mapping(meta)
    title = _mapping_text(info, "title")
    aka = _mapping_text(info, "aka")
    year = _mapping_text(info, "year")
    new_title = _chosen_title(title, components.title)
    return replace(
        components,
        title=new_title,
        alt_title=_chosen_alt(title, aka, bool(meta.no_aka), components.alt_title),
        year=_imdb_year(meta, components.year, year),
    )


def _audio_languages(meta: Meta) -> list[str]:
    value = meta.audio_languages
    return value if isinstance(value, list) else []


def _special_language_name(meta: Meta, first: str) -> str:
    folded = first.casefold()
    if meta.silent or folded in {"zxx", "no linguistic content"}:
        return "ZXX"
    if folded in {"multiple", "multiple languages"}:
        return "MULTIPLE LANGUAGES"
    return first.upper()


def _foreign_language_name(meta: Meta) -> str:
    languages = _audio_languages(meta)
    if not languages or has_english_language(languages):
        return ""
    return _special_language_name(meta, str(languages[0]))


def _language_label(meta: Meta, rules: VideoNameRules, is_disc: bool) -> str:
    if is_disc or not rules.language_slot:
        return ""
    return _foreign_language_name(meta)


def _renamed_dual(label: str, codec: str, rules: VideoNameRules) -> str:
    if label == "Dual-Audio":
        return f"{rules.dual_audio_label} {codec}"
    return f"{label} {codec}"


def _english_dub(meta: Meta, languages: list[str]) -> bool:
    original = str(meta.original_language or "").lower()
    return has_english_language(languages) and not original.startswith("en")


def _dub_audio(
    meta: Meta, label: str, codec: str, rules: VideoNameRules
) -> str:
    languages = _audio_languages(meta)
    if not languages:
        return _renamed_dual(label, codec, rules)
    if label == "MULTI" and rules.keep_multi:
        return f"{label} {codec}"
    if _english_dub(meta, languages):
        return f"{rules.dual_audio_label} {codec}"
    return codec


def _audio_for_rules(meta: Meta, audio: str, rules: VideoNameRules) -> str:
    label, _, codec = audio.partition(" ")
    if label not in {"Dual-Audio", "MULTI"} or not codec:
        return audio
    if not rules.english_dub_only:
        return _renamed_dual(label, codec, rules)
    return _dub_audio(meta, label, codec, rules)


def _cut_from_edition(edition: str, rules: VideoNameRules) -> str:
    text = " ".join(str(edition or "").split())
    if not text or rules.edition_terms is None:
        return text
    return " ".join(rules.edition_terms.sub(" ", text).split())


def _release_kind(meta: Meta, parts: NameComponents) -> str:
    if parts.type == "ENCODE" and parts.source in {"NTSC", "PAL"}:
        return "DVDRIP"
    if parts.type == "DISC" and meta.is_disc:
        return f"DISC-{meta.is_disc}"
    return parts.type


def _disc_kind(meta: Meta, parts: NameComponents) -> bool:
    return _release_kind(meta, parts).startswith("DISC-")


def _tvdb_year_override(meta: Meta, rules: VideoNameRules, year: str) -> str:
    if meta.category != "TV" or not rules.tv_year_from_tvdb or meta.no_year:
        return year
    tvdb_year = tvdb_title_year(meta)
    if tvdb_year is None:
        return year
    return tvdb_year


def _manual_tv_year(meta: Meta, rules: VideoNameRules, year: str) -> str:
    manual = _positive_year(meta.manual_year)
    if rules.honor_manual_year_on_tv and manual and not meta.no_year:
        return manual
    return year


def _bdmv_details(
    meta: Meta, parts: NameComponents, rules: VideoNameRules, language: str
) -> list[str]:
    return [
        parts.hybrid,
        parts.repack,
        language,
        parts.resolution,
        str(meta.region or ""),
        parts.three_d,
        parts.uhd,
        parts.source,
        parts.hdr,
        str(meta.video_codec or ""),
        _audio_for_rules(meta, parts.audio, rules),
    ]


def _included_text(value: str, include: bool) -> str:
    if not include:
        return ""
    return value


def _dvd_details(
    meta: Meta, parts: NameComponents, rules: VideoNameRules, language: str
) -> list[str]:
    return [
        parts.repack,
        language,
        _included_text(parts.resolution, rules.dvd_resolution),
        _name_text(meta.region),
        parts.source,
        _name_text(meta.dvd_size),
        _included_text(_name_text(meta.video_codec), rules.dvd_video_codec),
        _audio_for_rules(meta, parts.audio, rules),
    ]


def _web_details(
    meta: Meta, parts: NameComponents, rules: VideoNameRules, language: str
) -> list[str]:
    hybrid = parts.hybrid if rules.hybrid_on_webdl or parts.type != "WEBDL" else ""
    web_type = "WEB-DL" if parts.type == "WEBDL" else "WEBRip"
    return [
        hybrid,
        parts.repack,
        language,
        parts.resolution,
        parts.uhd,
        parts.service,
        web_type,
        parts.hardcoded_subs,
        _audio_for_rules(meta, parts.audio, rules),
        parts.hdr,
        str(meta.video_encode or ""),
    ]


def _encode_details(
    meta: Meta, parts: NameComponents, rules: VideoNameRules, language: str
) -> list[str]:
    return [
        parts.hybrid,
        parts.repack,
        language,
        parts.resolution,
        parts.three_d,
        parts.uhd,
        parts.source,
        _audio_for_rules(meta, parts.audio, rules),
        parts.hdr,
        str(meta.video_encode or meta.video_codec or ""),
    ]


def _remux_codec(meta: Meta, parts: NameComponents, rules: VideoNameRules) -> str:
    if parts.source in DVD_SOURCES and not rules.dvd_video_codec:
        return ""
    return _name_text(meta.video_codec)


def _remux_resolution(parts: NameComponents, rules: VideoNameRules) -> str:
    if parts.source in DVD_SOURCES and not rules.dvd_resolution:
        return ""
    return parts.resolution


def _remux_details(
    meta: Meta, parts: NameComponents, rules: VideoNameRules, language: str
) -> list[str]:
    codec = _remux_codec(meta, parts, rules)
    resolution = _remux_resolution(parts, rules)
    return [
        parts.hybrid,
        parts.repack,
        language,
        resolution,
        parts.uhd,
        parts.source,
        "REMUX",
        parts.hdr,
        codec,
        _audio_for_rules(meta, parts.audio, rules),
    ]


def _dvdrip_details(
    meta: Meta, parts: NameComponents, rules: VideoNameRules, language: str
) -> list[str]:
    resolution = parts.resolution if rules.dvd_resolution else ""
    return [
        parts.repack,
        language,
        resolution,
        "DVDRip",
        _audio_for_rules(meta, parts.audio, rules),
        str(meta.video_encode or ""),
    ]


def _hdtv_details(
    meta: Meta, parts: NameComponents, rules: VideoNameRules, language: str
) -> list[str]:
    return [
        parts.repack,
        language,
        parts.resolution,
        parts.source,
        _audio_for_rules(meta, parts.audio, rules),
        str(meta.video_encode or ""),
    ]


def _detail_builder(kind: str):
    builders = {
        "DISC-BDMV": _bdmv_details,
        "DISC-DVD": _dvd_details,
        "DISC-HDDVD": _encode_details,
        "REMUX": _remux_details,
        "ENCODE": _encode_details,
        "WEBDL": _web_details,
        "WEBRIP": _web_details,
        "HDTV": _hdtv_details,
        "DVDRIP": _dvdrip_details,
    }
    return builders.get(kind)


def _detail_fields(
    meta: Meta, parts: NameComponents, rules: VideoNameRules, language: str
) -> list[str] | None:
    builder = _detail_builder(_release_kind(meta, parts))
    if builder is None:
        return None
    return builder(meta, parts, rules, language)


def _title_fields(
    meta: Meta,
    parts: NameComponents,
    rules: VideoNameRules,
    language: str,
    year: str,
    cut: str,
) -> list[str]:
    episode = (
        [f"{parts.season}{parts.episode}", parts.episode_title, parts.part]
        if meta.category == "TV"
        else []
    )
    before_cut = language if rules.language_slot == "before_cut" else ""
    alt = parts.alt_title if rules.aka else ""
    return [parts.title, alt, year, *episode, before_cut, cut]


def _components_or_built(
    meta: Meta, components: NameComponents | None
) -> NameComponents:
    if components is not None:
        return components
    return build_name_components(meta)


def _slot_language(language: str, slot: str, expected: str) -> str:
    if slot == expected:
        return language
    return ""


def _join_title(fields: list[str], tag: str) -> str:
    return " ".join(" ".join(fields).split()) + tag


def build_video_name(
    meta: Meta,
    rules: VideoNameRules,
    components: NameComponents | None = None,
) -> str:
    """Build a movie or TV title. An empty string keeps the generic name."""
    if meta.manual_name is not None:
        return ""
    if meta.category not in {"MOVIE", "TV"}:
        return ""
    parts = _components_or_built(meta, components)
    is_disc = _disc_kind(meta, parts)
    language = _language_label(meta, rules, is_disc)
    before_resolution = _slot_language(
        language, rules.language_slot, "before_resolution"
    )
    details = _detail_fields(meta, parts, rules, before_resolution)
    if details is None:
        return ""
    year = _manual_tv_year(
        meta, rules, _tvdb_year_override(meta, rules, parts.year)
    )
    fields = _title_fields(
        meta, parts, rules, language, year, _cut_from_edition(parts.edition, rules)
    )
    return _join_title([*fields, *details], parts.tag)
