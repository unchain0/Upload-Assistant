"""Broad contracts for public service-layer transformations.

Focused tests remain responsible for exact business outcomes.  This matrix makes
sure every public service function accepts dressed domain values, rejects invalid
fixtures semantically, and never terminates the process while representative
release types are exercised.
"""

from __future__ import annotations

import ast
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


def _source_tree(function: Callable[..., object]) -> ast.AST | None:
    try:
        source = inspect.getsource(function)
    except OSError, TypeError:
        return None
    try:
        return ast.parse(inspect.cleandoc(source))
    except SyntaxError:
        return None


def _add_literal(values: list[object], value: object) -> None:
    if not isinstance(value, str | int | float | bool):
        return
    if len(str(value)) > 80 or value in values:
        return
    values.append(value)


def _collect_literals(node: ast.AST, values: list[object]) -> None:
    if isinstance(node, ast.Constant):
        _add_literal(values, node.value)
        return
    if not isinstance(node, ast.List | ast.Tuple | ast.Set):
        return
    for item in node.elts:
        if isinstance(item, ast.Constant):
            _add_literal(values, item.value)


def _literal_candidates(function: Callable[..., object]) -> list[object]:
    """Collect useful literals from comparisons in one implementation."""
    tree = _source_tree(function)
    if tree is None:
        return []
    values: list[object] = []
    for node in ast.walk(tree):
        _collect_literals(node, values)
    return values[:20]


_MISSING = object()


def _rich_mapping(meta: Meta) -> dict[str, Any]:
    return {
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


def _literal_or(literal: object | None, default: object) -> object:
    return default if literal is None else literal


def _named_arguments(
    meta: Meta,
    tmp_path: Path,
    literal: object | None,
    rich_mapping: dict[str, Any],
) -> dict[str, object]:
    config = copy.deepcopy(example_config)
    literal_value = _literal_or(literal, "Example Value")
    return {
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
        "value": literal_value,
        "raw": literal_value,
        "raw_value": literal_value,
        "size_str": _literal_or(literal, "1.5 GiB"),
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


def _bool_argument(literal: object | None) -> bool:
    if isinstance(literal, bool | int):
        return bool(literal)
    return False


def _int_argument(literal: object | None) -> int:
    if isinstance(literal, int | float | bool):
        return int(literal)
    return 1


def _float_argument(literal: object | None) -> float:
    if isinstance(literal, int | float | bool):
        return float(literal)
    return 1.0


def _str_argument(literal: object | None) -> str:
    return str(literal) if literal is not None else "example"


_SCALAR_ARGUMENTS: dict[object, Callable[[object | None], object]] = {
    bool: _bool_argument,
    int: _int_argument,
    float: _float_argument,
    str: _str_argument,
}


def _scalar_argument(annotation: object, literal: object | None) -> object:
    if annotation in {inspect.Parameter.empty, Any}:
        return _literal_or(literal, "example")
    factory = _SCALAR_ARGUMENTS.get(annotation)
    if factory is None:
        return _MISSING
    return factory(literal)


def _collection_argument(
    origin: object, meta: Meta, rich_mapping: dict[str, Any]
) -> object:
    if origin in {list, Sequence}:
        return list(meta.filelist)
    if origin in {dict, Mapping}:
        return rich_mapping
    return _MISSING


def _tuple_argument(
    normalized: str,
    args: tuple[object, ...],
    meta: Meta,
    tmp_path: Path,
    literal: object | None,
) -> tuple[object, ...]:
    return tuple(
        _argument(normalized, item, meta, tmp_path, literal)
        for item in args
        if item is not Ellipsis
    )


def _optional_argument(
    normalized: str,
    args: tuple[object, ...],
    meta: Meta,
    tmp_path: Path,
    literal: object | None,
) -> object:
    concrete = next((item for item in args if item is not type(None)), str)
    return _argument(normalized, concrete, meta, tmp_path, literal)


def _composite_argument(
    normalized: str,
    annotation: object,
    meta: Meta,
    tmp_path: Path,
    literal: object | None,
    rich_mapping: dict[str, Any],
) -> object:
    origin = get_origin(annotation)
    args = get_args(annotation)
    collection = _collection_argument(origin, meta, rich_mapping)
    if collection is not _MISSING:
        return collection
    if origin is tuple:
        return _tuple_argument(normalized, args, meta, tmp_path, literal)
    if origin is None or type(None) not in args:
        return _literal_or(literal, "example")
    return _optional_argument(normalized, args, meta, tmp_path, literal)


def _argument(
    name: str,
    annotation: object,
    meta: Meta,
    tmp_path: Path,
    literal: object | None = None,
) -> object:
    normalized = name.casefold().lstrip("_")
    rich_mapping = _rich_mapping(meta)
    named = _named_arguments(meta, tmp_path, literal, rich_mapping)
    if normalized in named:
        return named[normalized]
    if annotation is Path:
        return Path(meta.filelist[0])
    scalar = _scalar_argument(annotation, literal)
    if scalar is not _MISSING:
        return scalar
    return _composite_argument(
        normalized, annotation, meta, tmp_path, literal, rich_mapping
    )


def _safe_type_hints(target: object) -> dict[str, Any]:
    try:
        return get_type_hints(target)
    except NameError, TypeError:
        return {}


def _required_parameters(
    function: Callable[..., object],
) -> list[inspect.Parameter]:
    return [
        parameter
        for parameter in inspect.signature(function).parameters.values()
        if parameter.kind
        not in {
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        }
        and parameter.default is inspect.Parameter.empty
    ]


def _invocation_arguments(
    function: Callable[..., object],
    meta: Meta,
    tmp_path: Path,
    literal: object | None,
) -> tuple[list[object], dict[str, object]]:
    hint_target = function.__init__ if inspect.isclass(function) else function
    hints = _safe_type_hints(hint_target)
    args: list[object] = []
    kwargs: dict[str, object] = {}
    for parameter in _required_parameters(function):
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
    return args, kwargs


async def _invoke(
    function: Callable[..., object],
    meta: Meta,
    tmp_path: Path,
    literal: object | None = None,
) -> object:
    args, kwargs = _invocation_arguments(function, meta, tmp_path, literal)
    result = function(*args, **kwargs)
    if inspect.isawaitable(result):
        return await asyncio.wait_for(result, timeout=0.05)
    return result


def _variants(tmp_path: Path) -> list[Meta]:
    return [
        _release(tmp_path, "MOVIE", "DISC", "2160p"),
        _release(tmp_path, "MOVIE", "REMUX", "1080p"),
        _release(tmp_path, "TV", "WEBDL", "1080p"),
        _release(tmp_path, "MUSIC", "FLAC", "OTHER"),
        _release(tmp_path, "BOOK", "M4B", "OTHER"),
        _release(tmp_path, "GAME", "ISO", "OTHER"),
        _release(tmp_path, "XXX", "WEBDL", "1080p"),
    ]


def _module_functions(
    module: ModuleType,
) -> list[tuple[str, Callable[..., object]]]:
    return [
        (name, function)
        for name, function in inspect.getmembers(module, inspect.isfunction)
        if not name.startswith("__")
        and name not in _SKIP_FUNCTIONS
        and function.__module__ == module.__name__
    ]


async def _run_service_scenario(
    qualified: str,
    function: Callable[..., object],
    meta: Meta,
    tmp_path: Path,
    literal: object | None,
    process_terminations: list[str],
    successes: set[str],
    validation_failures: list[str],
) -> None:
    try:
        await _invoke(function, meta.copy(), tmp_path, literal)
    except (KeyboardInterrupt, SystemExit) as error:
        process_terminations.append(f"{qualified}: {type(error).__name__}")
    except Exception as error:
        validation_failures.append(f"{qualified}: {type(error).__name__}")
    else:
        successes.add(qualified)


async def _exercise_module(
    module: ModuleType,
    variants: list[Meta],
    tmp_path: Path,
    attempted: set[str],
    process_terminations: list[str],
    successes: set[str],
    validation_failures: list[str],
) -> None:
    for name, function in _module_functions(module):
        qualified = f"{module.__name__}.{name}"
        attempted.add(qualified)
        literals: list[object | None] = [None, *_literal_candidates(function)]
        for index, literal in enumerate(literals[:8]):
            await _run_service_scenario(
                qualified,
                function,
                variants[index % len(variants)],
                tmp_path,
                literal,
                process_terminations,
                successes,
                validation_failures,
            )


async def _exercise_modules(
    modules: list[ModuleType],
    variants: list[Meta],
    tmp_path: Path,
    attempted: set[str],
    process_terminations: list[str],
    successes: set[str],
    validation_failures: list[str],
) -> None:
    for module in modules:
        await _exercise_module(
            module,
            variants,
            tmp_path,
            attempted,
            process_terminations,
            successes,
            validation_failures,
        )


def test_public_service_functions_accept_dressed_domain_values(
    tmp_path: Path,
) -> None:
    attempted: set[str] = set()
    process_terminations: list[str] = []
    successes: set[str] = set()
    validation_failures: list[str] = []
    asyncio.run(
        _exercise_modules(
            _modules(),
            _variants(tmp_path),
            tmp_path,
            attempted,
            process_terminations,
            successes,
            validation_failures,
        )
    )
    assert len(attempted) >= 30
    assert len(successes) >= 20
    assert process_terminations == []
