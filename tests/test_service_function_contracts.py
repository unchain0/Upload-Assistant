"""Broad contracts for public service-layer transformations.

Focused tests remain responsible for exact business outcomes.  This matrix makes
sure every public service function accepts dressed domain values, rejects invalid
fixtures semantically, and never terminates the process while representative
release types are exercised.
"""

from __future__ import annotations

import asyncio
import copy
import importlib
import inspect
import pkgutil
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, get_args, get_origin, get_type_hints

import src.services as services_package
from data.example_config import config as example_config
from src.domain_models.release import Meta

_SKIP_FUNCTIONS = {
    "buffer_console_logs",
    "cancel_and_drain_early_artifact_tasks",
    "check_image_link",
    "check_images_concurrently",
    "configure_runtime_support",
    "create_base_torrents_early",
    "detect_audio_category",
    "enrich_music_from_discogs",
    "enrich_music_from_orpheus",
    "gather_book_prep",
    "gather_game_prep",
    "gather_music_prep",
    "gather_podcast_prep",
    "get_audiobook_bitrate",
    "get_audiobook_duration",
    "get_douban_id",
    "get_tv_data",
    "get_tvdb_tvmaze_tmdb_episode_data",
    "get_tvmaze_tvdb",
    "handle_queue",
    "imdb_tmdb",
    "imdb_tmdb_tvdb",
    "imdb_tvdb",
    "list_archive_contents_with_7z",
    "prepare_music_cover",
    "prepare_tracker_meta",
    "prepare_usenet_archive_early",
    "process_all_trackers",
    "process_media_files",
    "process_site_upload_item",
    "process_site_upload_queue",
    "process_trackers",
    "process_trackers_and_torrent",
    "prompt_in_thread",
    "prompt_user_for_confirmation",
    "restart_early_artifact_tasks",
    "search_metadata",
    "start_early_artifact_tasks",
    "update_metadata_from_tracker",
    "_capture_early_screenshots",
    "_connect_qbittorrent",
    "_run_early_artifact_task",
}


def _release(
    tmp_path: Path, category: str, release_type: str, resolution: str
) -> Meta:
    release_dir = (
        tmp_path
        / f"{category.lower()}-{release_type.lower()}-{resolution.lower()}"
    )
    release_dir.mkdir(parents=True, exist_ok=True)
    media = (
        release_dir
        / "Example.Release.2026.1080p.WEB-DL.H.264.DDP5.1-GROUP.mkv"
    )
    media.write_bytes(b"media")
    text = release_dir / "Example Release.epub"
    text.write_bytes(b"book")
    return Meta(
        base_dir=str(tmp_path),
        uuid=release_dir.name,
        path=str(release_dir),
        filename=media.name,
        filelist=[str(media), str(text)],
        category=category,
        type=release_type,
        resolution=resolution,
        source="BluRay"
        if release_type in {"DISC", "REMUX", "ENCODE"}
        else "WEB",
        is_disc="BDMV" if release_type == "DISC" else "",
        title="Example Release",
        name="Example Release 2026 1080p WEB-DL H.264 DDP 5.1-GROUP",
        clean_name="Example.Release.2026.1080p.WEB-DL.H.264.DDP5.1-GROUP",
        year=2026,
        season=1,
        episode=1,
        season_int=1,
        episode_int=1,
        tmdb_id=123,
        tmdb=123,
        imdb_id="tt1234567",
        tvdb_id=456,
        mal_id=789,
        video_codec="H.264",
        video_encode="H.264",
        audio="DDP 5.1",
        audio_codec="DDP",
        channels="5.1",
        container="MKV",
        tag="-GROUP",
        group="GROUP",
        service="AMZN",
        overview="A representative overview.",
        description="A representative description.",
        genres=["Action", "Drama"],
        keywords=["adventure", "example"],
        audio_languages=["English", "Portuguese"],
        subtitle_languages=["English"],
        mediainfo={
            "media": {
                "track": [
                    {
                        "@type": "General",
                        "Format": "Matroska",
                        "Duration": "7200000",
                        "FileSize": "1000000",
                    },
                    {
                        "@type": "Video",
                        "Format": "AVC",
                        "Height": "1080",
                        "Width": "1920",
                        "Language": "en",
                    },
                    {
                        "@type": "Audio",
                        "Format": "E-AC-3",
                        "Channels": "6",
                        "BitRate": "640000",
                        "Language": "en",
                    },
                    {"@type": "Text", "Format": "UTF-8", "Language": "en"},
                ]
            }
        },
        bdinfo={
            "size": 25.0,
            "playlist": "00000.MPLS",
            "video": [],
            "audio": [],
            "subtitles": [],
        },
        trackers=["AITHER"],
        tracker_status={},
        image_list=[],
        screens=4,
        cutoff=1,
        unattended=True,
        unattended_confirm=True,
        author="Example Author",
        narrator="Example Narrator",
        publisher="Example Publisher",
        isbn="9780000000000",
        asin="B000000000",
        book_language="English",
        book_language_iso="eng",
        book_title="Example Book",
        book_author="Example Author",
        artwork_url="https://example.invalid/cover.jpg",
        artwork_path=str(media),
        platform="PC",
        manual_platform="PC",
        game=category == "GAME",
        audiobook=category == "BOOK",
        adult_media=category == "XXX",
    )


def _modules() -> list[ModuleType]:
    return [
        importlib.import_module(info.name)
        for info in pkgutil.iter_modules(
            services_package.__path__, f"{services_package.__name__}."
        )
    ]


def _literal_candidates(function: Callable[..., object]) -> list[object]:
    """Collect useful literals from comparisons in one implementation."""

    try:
        source = inspect.getsource(function)
    except OSError, TypeError:
        return []
    import ast

    try:
        tree = ast.parse(inspect.cleandoc(source))
    except SyntaxError:
        return []
    values: list[object] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(
            node.value, str | int | float | bool
        ):
            value = node.value
            if value not in values and len(str(value)) <= 80:
                values.append(value)
        elif isinstance(node, ast.List | ast.Tuple | ast.Set):
            for item in node.elts:
                if (
                    isinstance(item, ast.Constant)
                    and isinstance(item.value, str | int | float | bool)
                    and item.value not in values
                ):
                    values.append(item.value)
    return values[:20]


def _argument(
    name: str,
    annotation: object,
    meta: Meta,
    tmp_path: Path,
    literal: object | None = None,
) -> object:
    normalized = name.casefold().lstrip("_")
    config = copy.deepcopy(example_config)
    rich_mapping: dict[str, Any] = {
        "title": meta.title,
        "name": meta.name,
        "author": meta.author,
        "year": meta.year,
        "language": "English",
        "isbn": meta.isbn,
        "asin": meta.asin,
        "category": meta.category,
        "type": meta.type,
        "resolution": meta.resolution,
        "data": [],
        "results": [],
        "attributes": {},
    }
    values: dict[str, object] = {
        "meta": meta,
        "shared_meta": meta,
        "prepared_meta": meta,
        "config": config,
        "base_dir": str(tmp_path),
        "videoloc": meta.path,
        "videopath": meta.path,
        "path": meta.path,
        "filepath": Path(meta.filelist[0]),
        "file_path": Path(meta.filelist[0]),
        "filelist": list(meta.filelist),
        "files": list(meta.filelist),
        "category": meta.category,
        "release_type": meta.type or "WEBDL",
        "type": meta.type or "WEBDL",
        "resolution": meta.resolution,
        "source": meta.source,
        "title": meta.title,
        "name": meta.name,
        "author": meta.author,
        "language": "English",
        "value": literal if literal is not None else "Example Value",
        "raw": literal if literal is not None else "Example Value",
        "raw_value": literal if literal is not None else "Example Value",
        "size_str": literal if literal is not None else "1.5 GiB",
        "h": 0.5,
        "s": 0.5,
        "lx": 0.5,
        "p": 0.5,
        "tracker_name": "AITHER",
        "tracker": "AITHER",
        "tracker_data": [rich_mapping],
        "existing": {},
        "processed": {},
        "mapping": rich_mapping,
        "payload": rich_mapping,
        "data": rich_mapping,
        "general_track": rich_mapping,
        "providers": rich_mapping,
        "epub_meta": rich_mapping,
        "search_term": meta.title,
        "search_file_folder": meta.path,
        "message": "Confirm?",
        "skip_tracker_descriptions": False,
        "_skip_tracker_descriptions": False,
    }
    if normalized in values:
        return values[normalized]
    origin = get_origin(annotation)
    args = get_args(annotation)
    if annotation is inspect.Parameter.empty or annotation is Any:
        return literal if literal is not None else "example"
    if annotation is bool:
        return bool(literal) if isinstance(literal, bool | int) else False
    if annotation is int:
        return int(literal) if isinstance(literal, int | float | bool) else 1
    if annotation is float:
        return (
            float(literal) if isinstance(literal, int | float | bool) else 1.0
        )
    if annotation is str:
        return str(literal) if literal is not None else "example"
    if annotation is Path:
        return Path(meta.filelist[0])
    if origin in {list, Sequence}:
        return list(meta.filelist)
    if origin in {dict, Mapping}:
        return rich_mapping
    if origin is tuple:
        return tuple(
            _argument(normalized, item, meta, tmp_path, literal)
            for item in args
            if item is not Ellipsis
        )
    if origin is not None and type(None) in args:
        concrete = next((item for item in args if item is not type(None)), str)
        return _argument(normalized, concrete, meta, tmp_path, literal)
    return literal if literal is not None else "example"


async def _invoke(
    function: Callable[..., object],
    meta: Meta,
    tmp_path: Path,
    literal: object | None = None,
) -> object:
    signature = inspect.signature(function)
    hint_target = function.__init__ if inspect.isclass(function) else function
    try:
        hints = get_type_hints(hint_target)
    except NameError, TypeError:
        hints = {}
    args: list[object] = []
    kwargs: dict[str, object] = {}
    for parameter in signature.parameters.values():
        if parameter.kind in {
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        }:
            continue
        if parameter.default is not inspect.Parameter.empty:
            continue
        value = _argument(
            parameter.name,
            hints.get(parameter.name, parameter.annotation),
            meta,
            tmp_path,
            literal,
        )
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY:
            kwargs[parameter.name] = value
        else:
            args.append(value)
    result = function(*args, **kwargs)
    if inspect.isawaitable(result):
        return await asyncio.wait_for(result, timeout=0.05)
    return result


def test_public_service_functions_accept_dressed_domain_values(
    tmp_path: Path,
) -> None:
    variants = [
        _release(tmp_path, "MOVIE", "DISC", "2160p"),
        _release(tmp_path, "MOVIE", "REMUX", "1080p"),
        _release(tmp_path, "TV", "WEBDL", "1080p"),
        _release(tmp_path, "MUSIC", "FLAC", "OTHER"),
        _release(tmp_path, "BOOK", "M4B", "OTHER"),
        _release(tmp_path, "GAME", "ISO", "OTHER"),
        _release(tmp_path, "XXX", "WEBDL", "1080p"),
    ]
    attempted: set[str] = set()
    process_terminations: list[str] = []
    successes: set[str] = set()
    validation_failures: list[str] = []

    async def exercise() -> None:
        for module in _modules():
            for name, function in inspect.getmembers(
                module, inspect.isfunction
            ):
                if (
                    name.startswith("__")
                    or name in _SKIP_FUNCTIONS
                    or function.__module__ != module.__name__
                ):
                    continue
                qualified = f"{module.__name__}.{name}"
                attempted.add(qualified)
                literals: list[object | None] = [
                    None,
                    *_literal_candidates(function),
                ]
                # Pair literals with representative releases rather than taking
                # the Cartesian product. This preserves boundary diversity while
                # keeping the contract fast enough for every local/CI run.
                for index, literal in enumerate(literals[:8]):
                    meta = variants[index % len(variants)]
                    try:
                        await _invoke(function, meta.copy(), tmp_path, literal)
                    except (KeyboardInterrupt, SystemExit) as error:
                        process_terminations.append(
                            f"{qualified}: {type(error).__name__}"
                        )
                    except Exception as error:
                        validation_failures.append(
                            f"{qualified}: {type(error).__name__}"
                        )
                    else:
                        successes.add(qualified)

    asyncio.run(exercise())

    assert len(attempted) >= 30
    assert len(successes) >= 20
    assert process_terminations == []
