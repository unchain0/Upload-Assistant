"""Cross-tracker contracts for deterministic domain-to-tracker mappings."""

from __future__ import annotations

import copy
import inspect
from pathlib import Path
from typing import Any

import bencodepy
import pytest

from data.example_config import config as example_config
from src.domain_models.release import Meta
from src.integrations.trackers.registry import tracker_class_map

# These adapter methods are required to be deterministic mappings. Network,
# authentication, upload, search, and description-generation methods are
# intentionally not part of this contract.
_MAPPING_METHODS = frozenset(
    {
        "get_additional_checks",
        "get_additional_data",
        "get_additional_files",
        "get_anonymous",
        "get_audio",
        "get_audio_codec",
        "get_basename",
        "get_bdinfo",
        "get_cat_id",
        "get_category",
        "get_category_id",
        "get_checkboxes",
        "get_codec",
        "get_container",
        "get_distributor_id",
        "get_doubleup",
        "get_edition",
        "get_episode_number",
        "get_featured",
        "get_file_info",
        "get_flag",
        "get_free",
        "get_group_tag",
        "get_hdr",
        "get_igdb",
        "get_imdb",
        "get_internal",
        "get_keywords",
        "get_lang",
        "get_mal",
        "get_name",
        "get_nfo",
        "get_personal_release",
        "get_region_id",
        "get_requests",
        "get_res_id",
        "get_resolution",
        "get_resolution_id",
        "get_rip_type",
        "get_sd",
        "get_season_number",
        "get_source",
        "get_sticky",
        "get_stream",
        "get_subs",
        "get_tags",
        "get_tmdb",
        "get_tvdb",
        "get_type",
        "get_type_category_id",
        "get_type_id",
        "get_video_quality",
        "rules",
    }
)


_CONFIG_SECRET_MARKERS = (
    "api",
    "token",
    "passkey",
    "cookie",
    "password",
    "username",
    "announce",
)
_ARGUMENT_MISSING = object()
_MAPPING_FAILURES = (
    FileNotFoundError,
    KeyError,
    TypeError,
    ValueError,
    AttributeError,
    IndexError,
)


def _is_config_secret(key: str) -> bool:
    normalized = key.casefold()
    return any(marker in normalized for marker in _CONFIG_SECRET_MARKERS)


def _prepare_tracker_config(tracker_config: dict[str, Any]) -> None:
    for key in tuple(tracker_config):
        if _is_config_secret(key):
            tracker_config[key] = "test-value"
    tracker_config.setdefault("api_key", "test-key")
    tracker_config.setdefault(
        "announce_url", "https://tracker.invalid/announce"
    )
    tracker_config.setdefault("categorie", "podcast")
    tracker_config.setdefault("type", "audio")


def _configured_catalog() -> dict[str, Any]:
    config = copy.deepcopy(example_config)
    default = config.setdefault("DEFAULT", {})
    default.update(
        {
            "tmdb_api": "test-tmdb-key",
            "unattended": True,
            "screens": 6,
            "img_host_1": "imgbb",
        }
    )
    for tracker_config in config.get("TRACKERS", {}).values():
        if isinstance(tracker_config, dict):
            _prepare_tracker_config(tracker_config)
    return config


def _release(
    root: Path, *, category: str, release_type: str, resolution: str
) -> Meta:
    root.mkdir(parents=True, exist_ok=True)
    content = root / f"Example.2025.{resolution}.{release_type}.mkv"
    content.write_bytes(b"media")
    temp = root / "tmp" / "contract"
    temp.mkdir(parents=True, exist_ok=True)
    media_text = "General\nFormat : Matroska\nVideo\nFormat : AVC\nAudio\nFormat : DTS\n"
    for filename in (
        "MEDIAINFO.txt",
        "MEDIAINFO_CLEANPATH.txt",
        "MEDIAINFO_CLEANPATH.json",
    ):
        (temp / filename).write_text(media_text, encoding="utf-8")
    (temp / "MediaInfo.json").write_text(
        '{"media":{"track":[{"@type":"General","Format":"Matroska"},'
        '{"@type":"Video","Format":"AVC","Width":"1920","Height":"1080"},'
        '{"@type":"Audio","Format":"DTS","Language":"en","Channels":"6"}]}}',
        encoding="utf-8",
    )
    (temp / "DESCRIPTION.txt").write_text(
        "A representative release description.", encoding="utf-8"
    )
    (temp / "BD_SUMMARY_00.txt").write_text("DISC INFO", encoding="utf-8")
    (temp / "BASE.torrent").write_bytes(
        bencodepy.encode(
            {
                b"announce": b"https://tracker.invalid/announce",
                b"info": {
                    b"length": 5,
                    b"name": content.name.encode(),
                    b"piece length": 16384,
                    b"pieces": b"0" * 20,
                },
            }
        )
    )
    image = temp / "Example-0.png"
    image.write_bytes(b"image")

    is_disc = "BDMV" if release_type == "DISC" else ""
    return Meta(
        base_dir=str(root),
        uuid="contract",
        path=str(content),
        filename=content.name,
        clean_name=f"Example 2025 {resolution} {release_type} DTS x264-GRP",
        name=f"Example 2025 {resolution} {release_type} DTS x264-GRP",
        title="Example",
        year=2025,
        category=category,
        type=release_type,
        source="BluRay"
        if release_type in {"DISC", "REMUX", "ENCODE"}
        else "Web",
        resolution=resolution,
        video_codec="H.264",
        audio="DTS-HD MA 5.1",
        channels="5.1",
        tag="-GRP",
        group="GRP",
        imdb="tt1234567",
        imdb_id=1234567,
        tmdb=123,
        tvdb_id=456,
        mal_id=789,
        igdb_id=321,
        season="S01",
        episode="E01",
        season_int=1,
        episode_int=1,
        release_title=f"Example.2025.{resolution}.{release_type}.DTS.x264-GRP",
        is_disc=is_disc,
        disctype="BD50" if is_disc else "",
        unattended=True,
        anime=False,
        adult_media=category == "XXX",
        screens=6,
        image_list=[
            {
                "img_url": "https://img.invalid/0.png",
                "raw_url": "https://img.invalid/0.png",
                "web_url": "https://img.invalid/0",
            }
        ],
        filelist=[str(content)],
        tracker_status={},
        keywords=["action", "adventure"],
        genres=["Action", "Adventure"],
        combined_genres=["Action", "Adventure"],
        audio_languages=["English", "Portuguese", "Spanish", "Italian"],
        subtitle_languages=["English", "Portuguese", "Spanish", "Italian"],
        language_checked=True,
        original_language="en",
        mediainfo={
            "media": {
                "track": [
                    {"@type": "General", "Format": "Matroska"},
                    {
                        "@type": "Video",
                        "Format": "AVC",
                        "Width": "1920",
                        "Height": "1080",
                    },
                    {
                        "@type": "Audio",
                        "Format": "DTS",
                        "Language": "en",
                        "Channels": "6",
                    },
                    {"@type": "Text", "Language": "en", "Forced": "No"},
                ]
            }
        },
        bdinfo={
            "size": 50_000_000_000,
            "playlist": "00001.MPLS",
            "video": [{"codec": "MPEG-4 AVC", "resolution": "1080p"}],
            "audio": [
                {
                    "codec": "DTS-HD Master Audio",
                    "language": "English",
                    "channels": "5.1",
                }
            ],
            "subtitles": ["English"],
        },
        discs=[],
        edition="Director's Cut",
        freeleech=0,
        anon=False,
        debug=False,
        personalrelease=False,
        internal=False,
        stream=False,
        sd=False,
        region="US",
        distributor="Criterion",
        container="MKV",
        hdr="HDR10",
        bit_depth="10",
        has_subs=True,
        manual=False,
    )


def _exact_argument(name: str, meta: Meta) -> object:
    values: dict[str, object] = {
        "meta": meta,
        "metadata": meta,
        "cat": meta.category,
        "cat_id": meta.category,
        "res": meta.resolution,
        "res_id": meta.resolution,
        "type": meta.type,
        "release_type": meta.type,
        "type_id": meta.type,
    }
    return values.get(name, _ARGUMENT_MISSING)


def _identity_argument(name: str, meta: Meta) -> object:
    if "category" in name:
        return meta.category
    if "resolution" in name:
        return meta.resolution
    if "source" in name:
        return meta.source
    if "codec" in name:
        return meta.video_codec
    return _ARGUMENT_MISSING


def _contains_any(name: str, markers: tuple[str, ...]) -> bool:
    return any(marker in name for marker in markers)


def _media_argument(name: str, meta: Meta) -> object:
    aliases: tuple[tuple[tuple[str, ...], object], ...] = (
        (("audio",), meta.audio),
        (("channel",), meta.channels),
        (("title", "name"), meta.name),
        (("path", "file"), meta.path),
    )
    for markers, value in aliases:
        if _contains_any(name, markers):
            return value
    return _ARGUMENT_MISSING


def _episode_argument(name: str, meta: Meta) -> object:
    if "season" in name:
        return meta.season_int
    if "episode" in name:
        return meta.episode_int
    if "year" in name:
        return meta.year
    if name.endswith("id"):
        return 1
    return _ARGUMENT_MISSING


def _movie_info(meta: Meta) -> dict[str, object]:
    return {
        "type": "movie",
        "runtime": "120",
        "genres": ["Action"],
        "title": meta.title,
        "year": meta.year,
    }


def _structured_name_argument(name: str, meta: Meta) -> object:
    if name in {"imdb_info", "movie_info"}:
        return _movie_info(meta)
    if _contains_any(name, ("data", "mapping", "config", "info")):
        return {}
    if _contains_any(name, ("list", "items", "tags")):
        return []
    return _ARGUMENT_MISSING


def _annotation_argument(annotation: object) -> object:
    if annotation is bool:
        return False
    if "bool" in str(annotation).casefold():
        return False
    return _ARGUMENT_MISSING


def _structured_argument(name: str, annotation: object, meta: Meta) -> object:
    named = _structured_name_argument(name, meta)
    if named is not _ARGUMENT_MISSING:
        return named
    return _annotation_argument(annotation)


def _argument(parameter: inspect.Parameter, meta: Meta) -> object:
    name = parameter.name.casefold()
    exact = _exact_argument(name, meta)
    if exact is not _ARGUMENT_MISSING:
        return exact
    for resolver in (_identity_argument, _media_argument, _episode_argument):
        value = resolver(name, meta)
        if value is not _ARGUMENT_MISSING:
            return value
    structured = _structured_argument(name, parameter.annotation, meta)
    if structured is not _ARGUMENT_MISSING:
        return structured
    return ""


_RELEASE_VARIANTS = (
    ("MOVIE", "DISC", "2160p"),
    ("MOVIE", "REMUX", "1080p"),
    ("MOVIE", "ENCODE", "720p"),
    ("TV", "WEBDL", "1080p"),
    ("TV", "WEBRIP", "2160p"),
    ("MUSIC", "WEB", "OTHER"),
    ("BOOK", "WEB", "OTHER"),
    ("GAME", "WEB", "OTHER"),
    ("XXX", "WEBDL", "1080p"),
)


def _contract_releases(tmp_path: Path) -> list[Meta]:
    return [
        _release(
            tmp_path / f"case-{index}",
            category=category,
            release_type=release_type,
            resolution=resolution,
        )
        for index, (category, release_type, resolution) in enumerate(
            _RELEASE_VARIANTS
        )
    ]


def _supported_categories(tracker: object) -> set[str]:
    values = getattr(tracker, "supported_categories", ())
    if not values:
        return set()
    return {str(value).upper() for value in values}


def _contract_method(tracker: object, method_name: str) -> Any | None:
    method = getattr(tracker, method_name, None)
    if method is None:
        return None
    if not callable(method):
        return None
    if inspect.iscoroutinefunction(method):
        return None
    return method


def _required_arguments(method: Any, meta: Meta) -> list[object]:
    args: list[object] = []
    for parameter in inspect.signature(method).parameters.values():
        if parameter.kind in {
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        }:
            continue
        if parameter.default is not inspect.Parameter.empty:
            continue
        args.append(_argument(parameter, meta))
    return args


def _unsupported_release(supported: set[str], meta: Meta) -> bool:
    return bool(supported) and meta.category not in supported


async def _mapping_failure(method: Any, meta: Meta) -> str | None:
    try:
        result = method(*_required_arguments(method, meta))
        if inspect.isawaitable(result):
            await result
    except _MAPPING_FAILURES as error:
        return f"{meta.category}/{meta.type}: {type(error).__name__}: {error}"
    return None


async def _exercise_mapping_method(
    tracker_name: str,
    tracker: object,
    method_name: str,
    releases: list[Meta],
    attempted: set[tuple[str, str]],
    successful: set[tuple[str, str]],
    failures: dict[tuple[str, str], list[str]],
) -> None:
    method = _contract_method(tracker, method_name)
    if method is None:
        return
    key = (tracker_name, method_name)
    attempted.add(key)
    supported = _supported_categories(tracker)
    for meta in releases:
        if _unsupported_release(supported, meta):
            continue
        failure = await _mapping_failure(method, meta)
        if failure is not None:
            failures.setdefault(key, []).append(failure)
            continue
        successful.add(key)


async def _exercise_tracker_contracts(
    tracker_name: str,
    tracker: object,
    releases: list[Meta],
    attempted: set[tuple[str, str]],
    successful: set[tuple[str, str]],
    failures: dict[tuple[str, str], list[str]],
) -> None:
    for method_name in _MAPPING_METHODS:
        await _exercise_mapping_method(
            tracker_name,
            tracker,
            method_name,
            releases,
            attempted,
            successful,
            failures,
        )


def _unresolved_message(
    unresolved: list[tuple[str, str]],
    failures: dict[tuple[str, str], list[str]],
) -> str:
    return "Unresolved deterministic mapping contracts:\n" + "\n".join(
        f"{tracker}.{method}: {'; '.join(failures.get((tracker, method), [])[:3])}"
        for tracker, method in unresolved[:50]
    )


@pytest.mark.asyncio
async def test_registered_trackers_implement_deterministic_mapping_contracts(
    tmp_path: Path,
) -> None:
    config = _configured_catalog()
    releases = _contract_releases(tmp_path)
    attempted: set[tuple[str, str]] = set()
    successful: set[tuple[str, str]] = set()
    failures: dict[tuple[str, str], list[str]] = {}

    for tracker_name, tracker_class in sorted(tracker_class_map.items()):
        await _exercise_tracker_contracts(
            tracker_name,
            tracker_class(config),
            releases,
            attempted,
            successful,
            failures,
        )

    unresolved = sorted(
        key for key in attempted - successful if key[0] in tracker_class_map
    )
    assert len(attempted) >= 100
    assert not unresolved, _unresolved_message(unresolved, failures)


def _mapping_mode_cases(
    signature: inspect.Signature, meta: Meta
) -> list[dict[str, object]]:
    cases: list[dict[str, object]] = []
    if "mapping_only" in signature.parameters:
        cases.append({"mapping_only": True})
    if "reverse" in signature.parameters:
        cases.append({"reverse": True})
    for name, value in (
        ("category", meta.category),
        ("type", meta.type),
        ("resolution", meta.resolution),
    ):
        if name in signature.parameters:
            cases.append({name: value})
    return cases


async def _call_mapping_mode(
    method: Any, meta: Meta, kwargs: dict[str, object]
) -> object:
    result = method(meta, **kwargs)
    return await result if inspect.isawaitable(result) else result


def _mapping_method(tracker: object, method_name: str) -> Any | None:
    method = getattr(tracker, method_name, None)
    return method if callable(method) else None


async def _exercise_tracker_mapping_modes(
    tracker_name: str, tracker: object, meta: Meta
) -> int:
    if tracker_name == "UNWALLED":
        return 0
    tracker_meta = meta.copy()
    tracker_meta.tracker_status.setdefault(tracker_name, {})
    attempted = 0
    for method_name in ("get_category_id", "get_type_id", "get_resolution_id"):
        method = _mapping_method(tracker, method_name)
        if method is None:
            continue
        for kwargs in _mapping_mode_cases(
            inspect.signature(method), tracker_meta
        ):
            result = await _call_mapping_mode(method, tracker_meta, kwargs)
            assert result is not None
            attempted += 1
    return attempted


@pytest.mark.asyncio
async def test_registered_mapping_modes_cover_forward_reverse_and_mapping_only(
    tmp_path: Path,
) -> None:
    config = _configured_catalog()
    meta = _release(
        tmp_path / "mapping-modes",
        category="MOVIE",
        release_type="DISC",
        resolution="2160p",
    )
    attempted = 0
    for tracker_name, tracker_class in sorted(tracker_class_map.items()):
        attempted += await _exercise_tracker_mapping_modes(
            tracker_name, tracker_class(config), meta
        )
    assert attempted >= 300
