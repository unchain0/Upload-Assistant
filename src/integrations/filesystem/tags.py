# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import json
import os
import re
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from typing import Any, cast

from src.domain_models.release import Meta
from src.domain_models.release_group import (
    is_valid_prefixed_release_group,
    is_valid_release_group,
)
from src.integrations.observability.runtime_support import logger

guessit_module = import_module("guessit")
GuessitFn = Callable[[str, dict[str, Any] | None], dict[str, Any]]

SPACE_SEPARATED_RELEASE_GROUPS = {
    "BONE": "BONE",
}


def guessit_fn(
    value: str, options: dict[str, Any] | None = None
) -> dict[str, Any]:
    return cast(dict[str, Any], guessit_module.guessit(value, options))


def _prefixed_release_group(video: str, meta: Meta) -> str | None:
    if not meta.anime and meta.category not in ("TV", "MOVIE", "XXX"):
        return None
    match = re.search(r"^\s*\[([^\]]+)\]", Path(video).stem)
    if match is None:
        return None
    candidate = match.group(1).strip()
    return candidate if is_valid_prefixed_release_group(candidate) else None


_KNOWN_RELEASE_EXTENSIONS = frozenset(
    {
        ".mkv",
        ".mp4",
        ".ts",
        ".avi",
        ".divx",
        ".m2ts",
        ".pdf",
        ".epub",
        ".mobi",
        ".cbz",
        ".cbr",
        ".mp3",
        ".m4b",
        ".flac",
        ".aac",
        ".m4a",
        ".ogg",
        ".wav",
        ".zip",
        ".rar",
        ".tar",
        ".7z",
    }
)
_GENERIC_RELEASE_GROUPS = frozenset({"hd.ma.5.1", "untouched"})
_NON_ANIME_GROUP_RE = re.compile(
    r"(?<=-)((?!\s*(?:WEB-DL|Blu-ray|H-264|H-265))(?:\W|\b)(?!(?:\d{3,4}[ip]))(?!\d+\b)(?:\W|\b)([\w.]+?))(?:\[.+\])?(?:\))?(?:\s\[.+\])?$"
)


def _anime_release_group(video: str, meta: Meta) -> tuple[str | None, bool]:
    if not meta.anime:
        return None, False
    match = re.search(r"^\s*\[(.+?)\]", Path(video).stem)
    if match is None:
        return None, False
    release_group = match.group(1)
    logger.debug(f"Anime regex match: {release_group}")
    return release_group, True


def _use_meta_uuid(meta: Meta, season_pack_check: bool) -> bool:
    folder_release = meta.tv_pack or meta.keep_folder
    book_or_game = meta.category in ("BOOK", "GAME")
    return bool((folder_release or book_or_game) and not season_pack_check)


def _file_basename(video: str) -> str:
    basename = Path(video).name
    path = Path(basename)
    return basename if path.suffix and "-" in path.suffix else path.stem


def _release_basename(video: str, meta: Meta, season_pack_check: bool) -> str:
    if Path(video).is_dir():
        value = Path(os.path.normpath(video)).name
    elif _use_meta_uuid(meta, season_pack_check):
        value = meta.uuid
    else:
        value = _file_basename(video)
    path = Path(value)
    return (
        path.stem
        if path.suffix.lower() in _KNOWN_RELEASE_EXTENSIONS
        else value
    )


def _normalized_token(value: str) -> str:
    return "".join(char for char in value.lower() if char.isalnum())


def _normalized_meta_text(value: Any) -> str:
    return _normalized_token(str(value or ""))


def _normalized_group_matches_value(group: str, value: str) -> bool:
    return bool(
        group == value
        or (len(group) >= 4 and group in value)
        or (len(value) >= 4 and value in group)
    )


def _matches_book_metadata(release_group: str, meta: Meta) -> bool:
    group = _normalized_token(release_group)
    if not group:
        return False
    title = _normalized_token(meta.title or "")
    author = _normalized_token(meta.author or "")
    return _normalized_group_matches_value(
        group, title
    ) or _normalized_group_matches_value(group, author)


def _merged_hyphen_word_matches(
    basename: str, hyphen_idx: int, release_group: str, meta: Meta
) -> bool:
    prefix_match = re.search(r"(\w+)$", basename[:hyphen_idx])
    first_word_match = re.match(r"\w+", release_group)
    if prefix_match is None:
        return False
    if first_word_match is None:
        return False
    merged = _normalized_token(
        prefix_match.group(1) + first_word_match.group(0)
    )
    title = _normalized_meta_text(meta.title)
    author = _normalized_meta_text(meta.author)
    return bool(merged and (merged in title or merged in author))


def _hyphen_has_space_separator(basename: str, hyphen_idx: int) -> bool:
    if hyphen_idx <= 0:
        return False
    return basename[hyphen_idx - 1].isspace()


def _book_or_game_release_group(
    basename: str, match: re.Match[str], release_group: str, meta: Meta
) -> str | None:
    if meta.category not in ("BOOK", "GAME"):
        return release_group
    hyphen_idx = match.start() - 1
    if _hyphen_has_space_separator(basename, hyphen_idx):
        return None
    if _matches_book_metadata(release_group, meta):
        return None
    if _merged_hyphen_word_matches(basename, hyphen_idx, release_group, meta):
        return None
    return release_group


def _normalize_release_group(
    release_group: str | None, meta: Meta
) -> str | None:
    if not release_group:
        return None
    normalized = release_group.replace("Z0N3", "D-Z0N3")
    if not meta.scene and len(normalized) > 12:
        return None
    return normalized


def _space_separated_release_group(basename: str, meta: Meta) -> str | None:
    if meta.category not in ("MOVIE", "TV"):
        return None
    match = re.search(r"\s([A-Za-z0-9]+)$", basename)
    if match is None:
        return None
    release_group = SPACE_SEPARATED_RELEASE_GROUPS.get(match.group(1).upper())
    if release_group:
        logger.debug(f"Space-separated release group match: {release_group}")
    return release_group


def _non_anime_release_group(
    video: str, meta: Meta, season_pack_check: bool
) -> str | None:
    basename = _release_basename(video, meta, season_pack_check)
    match = _NON_ANIME_GROUP_RE.search(basename)
    release_group: str | None = None
    if match is not None:
        candidate = _book_or_game_release_group(
            basename, match, match.group(1).strip(), meta
        )
        release_group = _normalize_release_group(candidate, meta)
        logger.debug(f"Non-anime regex match: {release_group}")
    return release_group or _space_separated_release_group(basename, meta)


def _disc_release_group(video: str, meta: Meta) -> str | None:
    if not meta.is_disc:
        return None
    try:
        parsed = guessit_fn(video)
        release_group = cast(str | None, parsed.get("release_group"))
        logger.debug(f"Guessit match: {release_group}")
        return release_group
    except Exception as error:
        logger.info(f"Error while parsing group tag: {error}")
        return None


def _bdmv_release_group(
    video: str, meta: Meta, release_group: str | None
) -> str | None:
    if meta.is_disc != "BDMV" or not release_group:
        return release_group
    return release_group if release_group in video else None


def _format_release_group(release_group: str | None) -> str:
    if not release_group:
        return ""
    if release_group.lower() in _GENERIC_RELEASE_GROUPS:
        return ""
    return f"-{release_group}"


async def get_tag(
    video: str, meta: Meta, season_pack_check: bool = False
) -> str:
    release_group, matched_anime = _anime_release_group(video, meta)
    scan_non_anime = (
        not meta.anime or not matched_anime
    ) and meta.is_disc != "BDMV"
    if scan_non_anime:
        release_group = _non_anime_release_group(
            video, meta, season_pack_check
        )
    if not release_group:
        release_group = _disc_release_group(video, meta)
    release_group = _bdmv_release_group(video, meta, release_group)
    return _format_release_group(release_group)


_legacy_get_tag = get_tag


async def _validated_get_tag(
    video: str, meta: Meta, season_pack_check: bool = False
) -> str:
    """Detect a release group with prefix priority and semantic validation."""
    prefixed = _prefixed_release_group(video, meta)
    if prefixed:
        logger.debug(f"Prefixed release-group match: {prefixed}")
        return f"-{prefixed}"
    tag = await _legacy_get_tag(video, meta, season_pack_check)
    if not tag or is_valid_release_group(tag):
        return tag
    logger.warning(
        f"[yellow]Ignoring invalid release-group candidate {tag!r}: value matches season/episode syntax.[/yellow]"
    )
    return ""


get_tag = _validated_get_tag


async def _load_tag_overrides(meta: Meta) -> dict[str, Any]:
    tags_text = await asyncio.to_thread(
        Path(f"{meta.base_dir}/data/tags.json").read_text, encoding="utf-8"
    )
    return cast(dict[str, Any], json.loads(tags_text))


def _tag_matches_path(tag: str, value: dict[str, Any], meta: Meta) -> bool:
    path = meta.path or ""
    return bool(value.get("in_name", "") == tag and tag in path)


def _apply_type_override(meta: Meta, value: Any) -> None:
    if meta["type"] == "ENCODE":
        meta["type"] = value


def _apply_override_value(meta: Meta, key: str, value: Any) -> None:
    if key == "type":
        _apply_type_override(meta, value)
    elif key == "personalrelease":
        meta[key] = _is_true(value)
    elif key == "template":
        meta.description_template = value
    else:
        meta[key] = value


def _apply_tag_values(meta: Meta, values: dict[str, Any]) -> None:
    for key, value in values.items():
        _apply_override_value(meta, key, value)


def _tag_is_selected(meta: Meta, tag: str) -> bool:
    return bool(meta.tag and meta.tag[1:] == tag)


def _apply_tag_override_entry(meta: Meta, tag: str, value: Any) -> None:
    if not isinstance(value, dict):
        raise TypeError(f"tag override {tag!r} must be an object")
    if _tag_matches_path(tag, value, meta):
        meta.tag = f"-{tag}"
    if _tag_is_selected(meta, tag):
        _apply_tag_values(meta, value)


async def tag_override(meta: Meta) -> Meta:
    try:
        tags = await _load_tag_overrides(meta)
        for tag, value in tags.items():
            _apply_tag_override_entry(meta, tag, value)
    except Exception as error:
        logger.info(f"Error while loading tags.json: {error}")
    return meta


def _is_true(value: Any) -> bool:
    return str(value).strip().lower() == "true"
