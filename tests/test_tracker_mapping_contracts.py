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
        if not isinstance(tracker_config, dict):
            continue
        for key in tuple(tracker_config):
            normalized = key.casefold()
            if any(marker in normalized for marker in ("api", "token", "passkey", "cookie", "password", "username", "announce")):
                tracker_config[key] = "test-value"
        tracker_config.setdefault("api_key", "test-key")
        tracker_config.setdefault("announce_url", "https://tracker.invalid/announce")
        tracker_config.setdefault("categorie", "podcast")
        tracker_config.setdefault("type", "audio")
    return config


def _release(root: Path, *, category: str, release_type: str, resolution: str) -> Meta:
    root.mkdir(parents=True, exist_ok=True)
    content = root / f"Example.2025.{resolution}.{release_type}.mkv"
    content.write_bytes(b"media")
    temp = root / "tmp" / "contract"
    temp.mkdir(parents=True, exist_ok=True)
    media_text = "General\nFormat : Matroska\nVideo\nFormat : AVC\nAudio\nFormat : DTS\n"
    for filename in ("MEDIAINFO.txt", "MEDIAINFO_CLEANPATH.txt", "MEDIAINFO_CLEANPATH.json"):
        (temp / filename).write_text(media_text, encoding="utf-8")
    (temp / "MediaInfo.json").write_text(
        '{"media":{"track":[{"@type":"General","Format":"Matroska"},'
        '{"@type":"Video","Format":"AVC","Width":"1920","Height":"1080"},'
        '{"@type":"Audio","Format":"DTS","Language":"en","Channels":"6"}]}}',
        encoding="utf-8",
    )
    (temp / "DESCRIPTION.txt").write_text("A representative release description.", encoding="utf-8")
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
        source="BluRay" if release_type in {"DISC", "REMUX", "ENCODE"} else "Web",
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
        image_list=[{"img_url": "https://img.invalid/0.png", "raw_url": "https://img.invalid/0.png", "web_url": "https://img.invalid/0"}],
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
                    {"@type": "Video", "Format": "AVC", "Width": "1920", "Height": "1080"},
                    {"@type": "Audio", "Format": "DTS", "Language": "en", "Channels": "6"},
                    {"@type": "Text", "Language": "en", "Forced": "No"},
                ]
            }
        },
        bdinfo={
            "size": 50_000_000_000,
            "playlist": "00001.MPLS",
            "video": [{"codec": "MPEG-4 AVC", "resolution": "1080p"}],
            "audio": [{"codec": "DTS-HD Master Audio", "language": "English", "channels": "5.1"}],
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


def _argument(parameter: inspect.Parameter, meta: Meta) -> object:
    name = parameter.name.casefold()
    if name in {"meta", "metadata"}:
        return meta
    if "category" in name or name in {"cat", "cat_id"}:
        return meta.category
    if "resolution" in name or name in {"res", "res_id"}:
        return meta.resolution
    if name in {"type", "release_type", "type_id"}:
        return meta.type
    if "source" in name:
        return meta.source
    if "codec" in name:
        return meta.video_codec
    if "audio" in name:
        return meta.audio
    if "channel" in name:
        return meta.channels
    if "title" in name or "name" in name:
        return meta.name
    if "path" in name or "file" in name:
        return meta.path
    if "season" in name:
        return meta.season_int
    if "episode" in name:
        return meta.episode_int
    if "year" in name:
        return meta.year
    if name.endswith("id") or name.endswith("_id"):
        return 1
    if name in {"imdb_info", "movie_info"}:
        return {"type": "movie", "runtime": "120", "genres": ["Action"], "title": meta.title, "year": meta.year}
    if "data" in name or "mapping" in name or "config" in name or "info" in name:
        return {}
    if "list" in name or "items" in name or "tags" in name:
        return []
    if parameter.annotation is bool or "bool" in str(parameter.annotation).casefold():
        return False
    return ""


@pytest.mark.asyncio
async def test_registered_trackers_implement_deterministic_mapping_contracts(tmp_path: Path) -> None:
    config = _configured_catalog()
    variants = [
        ("MOVIE", "DISC", "2160p"),
        ("MOVIE", "REMUX", "1080p"),
        ("MOVIE", "ENCODE", "720p"),
        ("TV", "WEBDL", "1080p"),
        ("TV", "WEBRIP", "2160p"),
        ("MUSIC", "WEB", "OTHER"),
        ("BOOK", "WEB", "OTHER"),
        ("GAME", "WEB", "OTHER"),
        ("XXX", "WEBDL", "1080p"),
    ]
    releases = [
        _release(tmp_path / f"case-{index}", category=category, release_type=release_type, resolution=resolution)
        for index, (category, release_type, resolution) in enumerate(variants)
    ]
    attempted: set[tuple[str, str]] = set()
    successful: set[tuple[str, str]] = set()
    failures: dict[tuple[str, str], list[str]] = {}

    for tracker_name, tracker_class in sorted(tracker_class_map.items()):
        tracker = tracker_class(config)
        supported = {str(value).upper() for value in getattr(tracker, "supported_categories", ()) or ()}
        for method_name in _MAPPING_METHODS:
            method = getattr(tracker, method_name, None)
            if method is None or not callable(method) or inspect.iscoroutinefunction(method):
                continue
            key = (tracker_name, method_name)
            attempted.add(key)
            for meta in releases:
                if supported and meta.category not in supported:
                    continue
                signature = inspect.signature(method)
                args: list[object] = []
                for parameter in signature.parameters.values():
                    if parameter.kind in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}:
                        continue
                    if parameter.default is not inspect.Parameter.empty:
                        continue
                    args.append(_argument(parameter, meta))
                try:
                    result = method(*args)
                    if inspect.isawaitable(result):
                        await result
                except (FileNotFoundError, KeyError, TypeError, ValueError, AttributeError, IndexError) as error:
                    failures.setdefault(key, []).append(f"{meta.category}/{meta.type}: {type(error).__name__}: {error}")
                    continue
                successful.add(key)

    unresolved = sorted(key for key in attempted - successful if key[0] in tracker_class_map)
    assert len(attempted) >= 100
    assert not unresolved, "Unresolved deterministic mapping contracts:\n" + "\n".join(
        f"{tracker}.{method}: {'; '.join(failures.get((tracker, method), [])[:3])}" for tracker, method in unresolved[:50]
    )


def _mapping_mode_cases(signature: inspect.Signature, meta: Meta) -> list[dict[str, object]]:
    cases: list[dict[str, object]] = []
    if "mapping_only" in signature.parameters:
        cases.append({"mapping_only": True})
    if "reverse" in signature.parameters:
        cases.append({"reverse": True})
    for name, value in (("category", meta.category), ("type", meta.type), ("resolution", meta.resolution)):
        if name in signature.parameters:
            cases.append({name: value})
    return cases


async def _call_mapping_mode(method: Any, meta: Meta, kwargs: dict[str, object]) -> object:
    result = method(meta, **kwargs)
    return await result if inspect.isawaitable(result) else result


def _mapping_method(tracker: object, method_name: str) -> Any | None:
    method = getattr(tracker, method_name, None)
    return method if callable(method) else None


async def _exercise_tracker_mapping_modes(tracker_name: str, tracker: object, meta: Meta) -> int:
    if tracker_name == "UNWALLED":
        return 0
    tracker_meta = meta.copy()
    tracker_meta.tracker_status.setdefault(tracker_name, {})
    attempted = 0
    for method_name in ("get_category_id", "get_type_id", "get_resolution_id"):
        method = _mapping_method(tracker, method_name)
        if method is None:
            continue
        for kwargs in _mapping_mode_cases(inspect.signature(method), tracker_meta):
            result = await _call_mapping_mode(method, tracker_meta, kwargs)
            assert result is not None
            attempted += 1
    return attempted


@pytest.mark.asyncio
async def test_registered_mapping_modes_cover_forward_reverse_and_mapping_only(tmp_path: Path) -> None:
    config = _configured_catalog()
    meta = _release(tmp_path / "mapping-modes", category="MOVIE", release_type="DISC", resolution="2160p")
    attempted = 0
    for tracker_name, tracker_class in sorted(tracker_class_map.items()):
        attempted += await _exercise_tracker_mapping_modes(tracker_name, tracker_class(config), meta)
    assert attempted >= 300
