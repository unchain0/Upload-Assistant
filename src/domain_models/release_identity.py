"""Pure release-file and identity decisions shared by preparation and trackers."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any, cast

from src.domain_models.release import Meta


def _year(value: Any) -> int | None:
    if value in (None, "", 0, "0"):
        return None
    try:
        year = int(str(value).strip())
    except TypeError, ValueError:
        return None
    return year if 1800 <= year <= 2200 else None


@dataclass(frozen=True, slots=True)
class ReleaseYearIdentity:
    """Semantic view of release-year sources with one explicit canonical value."""

    canonical: int | None
    metadata: int | None
    search: int | None
    manual: int | None
    imdb: int | None

    @classmethod
    def from_release(cls, meta: Meta) -> ReleaseYearIdentity:
        manual = _year(meta.manual_year)
        metadata = _year(meta.year)
        search = _year(meta.search_year)
        imdb = cls._imdb_year(meta.imdb_info)
        return cls(
            canonical=manual or metadata or search,
            metadata=metadata,
            search=search,
            manual=manual,
            imdb=imdb,
        )

    @staticmethod
    def _imdb_year(value: Any) -> int | None:
        if not isinstance(value, dict):
            return None
        mapping = cast(dict[str, Any], value)
        return _year(mapping.get("year"))

    @property
    def canonical_text(self) -> str:
        return "" if self.canonical is None else str(self.canonical)

_VIDEO_SUFFIXES = frozenset(
    {".mkv", ".mp4", ".ts", ".avi", ".m2ts", ".divx", ".vob", ".mpg", ".mpeg"}
)
_ANCILLARY_VIDEO_RE = re.compile(
    r"""(?ix)
    (?:^|[\s._\-\[\()])
    (?:
        extras? | trailers? | teasers? | featurettes? | promos? |
        pitch[\s._\-]?pilot | artwork | ncop | nced | creditless |
        previews? | samples? | ova | oad | menus? | bts |
        behind[\s._\-]?the[\s._\-]?scenes
    )
    (?=$|[\s._\-\]\)]|\d)
    """
)
_EPISODE_VIDEO_RE = re.compile(
    r"""(?ix)
    (?:
        S\d{1,2}E\d{1,3}
        | (?:^|[\s._\-\[])TV[\s._\-]?\d{1,3}(?:$|[\s._\-\]])
    )
    """
)
_CLIENT_HASH_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")


def is_ancillary_video(filename: str) -> bool:
    """True for extras, trailers, OVAs, and other clips that must not identify a pack."""
    return _ANCILLARY_VIDEO_RE.search(PurePath(filename).name) is not None


def is_episode_video(filename: str) -> bool:
    """True when a filename looks like a numbered episode rather than a movie."""
    return _EPISODE_VIDEO_RE.search(PurePath(filename).name) is not None


def order_release_videos(
    filelist: list[str],
    *,
    prefer_largest: bool = False,
    size_of: Callable[[str], int] | None = None,
) -> list[str]:
    """Keep episode or movie files ahead of extras."""
    main, extras = _partition_release_videos(filelist)
    _sort_release_files(main, prefer_largest=prefer_largest, size_of=size_of)
    _sort_release_files(extras, prefer_largest=prefer_largest, size_of=size_of)
    return _ordered_release_videos(main, extras)


def _partition_release_videos(filelist: list[str]) -> tuple[list[str], list[str]]:
    main = [path for path in filelist if not is_ancillary_video(path)]
    extras = [path for path in filelist if is_ancillary_video(path)]
    return main, extras


def _ordered_release_videos(main: list[str], extras: list[str]) -> list[str]:
    if main:
        return main + extras
    return list(extras)


def _sort_release_files(
    paths: list[str],
    *,
    prefer_largest: bool,
    size_of: Callable[[str], int] | None,
) -> None:
    if prefer_largest and size_of is not None:
        paths.sort(key=size_of, reverse=True)
        return
    paths.sort()


def lookup_category(current: object, resolved: object, tmdb_id: object) -> str:
    """Keep a known TV category when the lookup did not return a TMDB id."""
    return preserve_failed_tv_category(
        str(current or ""), str(resolved or ""), tmdb_id
    )


def preserve_failed_tv_category(
    current: str, resolved: str, tmdb_id: object
) -> str:
    """A failed lookup must not turn an already identified TV release into a movie."""
    if _failed_movie_overwrite(current, resolved, tmdb_id):
        return "TV"
    return str(resolved or "")


def _failed_movie_overwrite(current: str, resolved: str, tmdb_id: object) -> bool:
    return (
        str(current or "") == "TV"
        and str(resolved or "") == "MOVIE"
        and not _positive_id(tmdb_id)
    )


def unattended_identity_skip_reason(meta: Meta) -> str | None:
    """Block an automatic TV or movie upload whose file was not identified."""
    if not _identity_rule_applies(meta):
        return None
    return _identity_skip_reason(meta, _identity_video_names(meta))


def _identity_rule_applies(meta: Meta) -> bool:
    return bool(meta.unattended) and str(meta.category or "") in {"TV", "MOVIE"}


def _identity_video_names(meta: Meta) -> list[str]:
    names: list[str] = []
    for path in meta.filelist or []:
        name = PurePath(str(path)).name
        if PurePath(name).suffix.lower() in _VIDEO_SUFFIXES:
            names.append(name)
    return names


def _identity_skip_reason(meta: Meta, names: list[str]) -> str | None:
    ancillary = _ancillary_representative_reason(names)
    if ancillary:
        return ancillary
    packed = _movie_episode_pack_reason(meta, names)
    if packed:
        return packed
    return _missing_database_id_reason(meta)


def _ancillary_representative_reason(names: list[str]) -> str | None:
    if not names or not is_ancillary_video(names[0]):
        return None
    if any(not is_ancillary_video(name) for name in names):
        return (
            "the representative file is an extra, trailer, or OVA "
            "while episode videos exist"
        )
    return None


def _category_is_movie(meta: Meta) -> bool:
    return str(meta.category or "") == "MOVIE"


def _non_ancillary_names(names: list[str]) -> list[str]:
    return [name for name in names if not is_ancillary_video(name)]


def _movie_episode_pack_reason(meta: Meta, names: list[str]) -> str | None:
    if not _category_is_movie(meta):
        return None
    if _episode_like_count(_non_ancillary_names(names)) >= 2:
        return "a folder of episodes was classified as a movie"
    return None


def _missing_database_id_reason(meta: Meta) -> str | None:
    if _positive_id(meta.tmdb_id) or _positive_id(meta.imdb_id):
        return None
    return "the release has no TMDB or IMDb id"


def _positive_id(value: object) -> bool:
    try:
        return int(value or 0) > 0
    except (TypeError, ValueError):
        return False


def client_export_infohash(path: str, parsed_infohash: str) -> str:
    """Keep a qBittorrent export hash when the filename is that hash."""
    stem = PurePath(path).stem.strip().lower()
    if _CLIENT_HASH_RE.fullmatch(stem):
        return stem
    return str(parsed_infohash or "")


def language_is_english(value: str) -> bool:
    """English includes ISO codes and the word English."""
    text = str(value or "").casefold()
    return text.startswith("en") or "english" in text


def _string_languages(value: str) -> list[str]:
    if value.strip():
        return [value]
    return []


def _iterable_languages(value: object) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return [text for text in (str(item).strip() for item in value) if text]


def _language_values(value: object) -> list[str]:
    if isinstance(value, str):
        return _string_languages(value)
    return _iterable_languages(value)


def has_english_language(value: object) -> bool:
    """True when any listed language is English."""
    return any(language_is_english(item) for item in _language_values(value))


_SCENE_EPISODE_RE = re.compile(
    r"(?i)(?<![A-Z0-9])S\d{2}E\d{1,3}(?:v\d+)?(?![A-Z0-9])"
)
_FANSUB_CRC_RE = re.compile(r"\[[0-9A-Fa-f]{8}\]")
_LOOSE_VERSION_RE = re.compile(r"(?i)(?<![A-Z0-9])\d{2}v\d(?![A-Z0-9])")
_COMBINED_EPISODE_RE = re.compile(
    r"(?i)(?<![A-Z0-9])S(\d{1,2})E(\d{1,3})(v\d+)?"
)
_LONE_SEASON_RE = re.compile(r"(?i)(?<![A-Z0-9])S(\d{1,2})(?![A-Z0-9])")
_LONE_EPISODE_RE = re.compile(r"(?i)(?<![A-Z0-9])E(\d{1,3})(?![A-Z0-9])")
_IMDB_TT_RE = re.compile(r"tt\d+", re.IGNORECASE)


def _pad_combined_episode(match: re.Match[str]) -> str:
    version = match.group(3) or ""
    return f"S{int(match.group(1)):02d}E{int(match.group(2)):02d}{version}"


def _pad_lone_season(match: re.Match[str]) -> str:
    return f"S{int(match.group(1)):02d}"


def _pad_lone_episode(match: re.Match[str]) -> str:
    return f"E{int(match.group(1)):02d}"


def normalize_dc_episode_numbering(name: str) -> str:
    """Zero-pad DigitalCore season and episode numbers without touching v2."""
    padded = _COMBINED_EPISODE_RE.sub(_pad_combined_episode, name)
    padded = _LONE_SEASON_RE.sub(_pad_lone_season, padded)
    return _LONE_EPISODE_RE.sub(_pad_lone_episode, padded)


def _main_video_names(meta: Meta) -> list[str]:
    return [
        name
        for name in _identity_video_names(meta)
        if not is_ancillary_video(name)
    ]


def _has_imdb_id(meta: Meta) -> bool:
    if _positive_id(meta.imdb_id):
        return True
    return _IMDB_TT_RE.fullmatch(str(meta.imdb_tt or "").strip()) is not None


def _subtitle_language_missing(meta: Meta) -> bool:
    if has_english_language(meta.audio_languages):
        return False
    return not _language_values(meta.subtitle_languages)


def digitalcore_staff_skip_reason(meta: Meta) -> str | None:
    """Staff rejection for a TV or movie that DigitalCore will not accept."""
    if str(meta.category or "").upper() not in {"TV", "MOVIE"}:
        return None
    return _digitalcore_staff_reason(meta)


def _digitalcore_staff_reason(meta: Meta) -> str | None:
    identity = _digitalcore_identity_skip(meta)
    if identity:
        return identity
    return _digitalcore_naming_skip(meta, _main_video_names(meta))


def _digitalcore_identity_skip(meta: Meta) -> str | None:
    if not _has_imdb_id(meta):
        return "DigitalCore requires an IMDb id for TV and movies"
    if _subtitle_language_missing(meta):
        return (
            "DigitalCore requires a subtitle language unless the audio is English"
        )
    return None


def _digitalcore_naming_skip(meta: Meta, names: list[str]) -> str | None:
    marked = _digitalcore_marker_skip(names)
    if marked:
        return marked
    packed = _digitalcore_pack_skip(meta, names)
    if packed:
        return packed
    return _digitalcore_scene_skip(meta, names)


def _digitalcore_marker_skip(names: list[str]) -> str | None:
    if any(_FANSUB_CRC_RE.search(name) for name in names):
        return "DigitalCore rejects an 8-hex fansub CRC"
    if any(_LOOSE_VERSION_RE.search(name) for name in names):
        return "DigitalCore rejects a loose version marker"
    return None


def _episode_like_count(names: list[str]) -> int:
    return sum(1 for name in names if is_episode_video(name))


def _digitalcore_pack_skip(meta: Meta, names: list[str]) -> str | None:
    if _episode_like_count(names) < 2:
        return None
    if str(meta.category or "").upper() == "MOVIE":
        return "DigitalCore rejects an episode pack classified as a movie"
    if not meta.tv_pack:
        return (
            "DigitalCore requires a season pack when several episodes are present"
        )
    return None


def _scene_episode_name(name: str) -> bool:
    return _SCENE_EPISODE_RE.search(name) is not None


def _digitalcore_scene_skip(meta: Meta, names: list[str]) -> str | None:
    if str(meta.category or "").upper() != "TV":
        return _movie_loose_episode_skip(names)
    if any(not _scene_episode_name(name) for name in names):
        return "DigitalCore requires scene-style SxxExx episode names"
    return None


def _movie_loose_episode_skip(names: list[str]) -> str | None:
    loose = [
        name
        for name in names
        if is_episode_video(name) and not _scene_episode_name(name)
    ]
    if loose:
        return "DigitalCore requires scene-style SxxExx episode names"
    return None
