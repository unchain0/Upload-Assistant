"""Catalog-level smoke contracts for MASA service orchestration.

The focused suite verifies detailed outcomes. This test invokes the remaining
small/medium service call surfaces with domain fixtures and deterministic
boundary doubles so every service stays importable and side effects stay local.
"""

from __future__ import annotations

import asyncio
import copy
import importlib
import inspect
import pkgutil
import subprocess
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any, ClassVar, get_args, get_origin, get_type_hints

import httpx
import requests

import src.services as services_package
from data.example_config import config as example_config
from src.domain_models.release import Meta
from src.integrations.external_apis.imdb import ImdbManager
from src.integrations.external_apis.tvmaze import TvmazeManager
from tests.contract_scenarios import literal_branch_scenarios


class _Response:
    status_code = 200
    text = "ok"
    content = b"ok"
    headers: ClassVar[dict[str, str]] = {}

    def json(self) -> dict[str, Any]:
        return {
            "success": True,
            "data": [],
            "results": [],
            "items": [],
            "id": 1,
            "status": "ok",
        }

    def raise_for_status(self) -> None:
        return None


class _AsyncClient:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def __aenter__(self) -> _AsyncClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()

    async def post(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()

    async def put(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()

    async def aclose(self) -> None:
        return None


class _Session:
    def get(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()

    def post(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()

    def close(self) -> None:
        return None


class _CompletedProcess:
    returncode = 0
    stdout = "ok"
    stderr = ""


class _Repository:
    def load(self, *_args: object, **_kwargs: object) -> Any:
        from src.domain_models.configuration import (
            ApplicationConfiguration,
            ConfigurationSource,
            ConfigurationSourceKind,
        )

        return ApplicationConfiguration.from_mapping(
            {
                "DEFAULT": {
                    "tmdb_api": "0123456789abcdef0123456789abcdef",
                    "img_host_1": "imgbb",
                },
                "TRACKERS": {},
            },
            ConfigurationSource(
                path="config.py", kind=ConfigurationSourceKind.RUNTIME
            ),
        )

    def copy_atomically(
        self, *_args: object, **_kwargs: object
    ) -> Path | None:
        return None

    def write_atomically(self, *_args: object, **_kwargs: object) -> None:
        return None


class _Universal(dict[str, Any]):
    """Permissive deterministic boundary value used by service contract tests."""

    __hash__ = object.__hash__

    def __init__(self, **values: Any) -> None:
        super().__init__(values)
        self.__dict__.update(values)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("is_"):
            return False
        return _Universal()

    def __call__(self, *_args: object, **_kwargs: object) -> _Universal:
        return _Universal()

    def __enter__(self) -> _Universal:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    async def __aenter__(self) -> _Universal:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def __await__(self):
        async def resolved() -> _Universal:
            return self

        return resolved().__await__()

    def __bool__(self) -> bool:
        return False

    def __int__(self) -> int:
        return 1

    def __float__(self) -> float:
        return 1.0

    def __str__(self) -> str:
        return "example"

    def __fspath__(self) -> str:
        return "example"


class _AwaitableValue(_Universal):
    pass


class _ManagerPort(_Universal):
    """Semantic manager double that returns the shapes service orchestration expects."""

    def __init__(self, config: dict[str, Any], tmp_path: Path) -> None:
        super().__init__()
        self.config = config
        self.tmp_path = tmp_path

    @staticmethod
    def _meta(args: tuple[object, ...]) -> Meta:
        return next((arg for arg in args if isinstance(arg, Meta)), Meta())

    async def get_disc(
        self, meta: Meta
    ) -> tuple[str, str, dict[str, Any], list[dict[str, Any]]]:
        return "", str(meta.path or ""), {}, []

    async def is_scene(
        self, path: str, meta: Meta, imdb_id: object = 0
    ) -> tuple[str, bool, object]:
        return (
            Path(path).stem or meta.title or "Example Release",
            False,
            imdb_id,
        )

    async def extract_title_and_year(
        self, meta: Meta, _video: str
    ) -> tuple[str, str, int]:
        return meta.title or "Example Release", "", int(meta.year or 2024)

    async def get_dvd_size(self, *_args: object, **_kwargs: object) -> float:
        return 4.7

    async def get_season_episode(self, _video: str, meta: Meta) -> Meta:
        return meta

    async def check_season_pack_completeness(self, _meta: Meta) -> None:
        return None

    async def get_tvmaze_tvdb(
        self, *_args: object, **_kwargs: object
    ) -> tuple[int, int, dict[str, Any], str]:
        return 0, 0, {}, ""

    async def get_tv_data(self, meta: Meta) -> Meta:
        return meta

    async def all_ids(self, meta: Meta) -> Meta:
        return meta

    async def imdb_tmdb_tvdb(self, meta: Meta, _filename: str) -> Meta:
        return meta

    async def imdb_tvdb(self, meta: Meta, _filename: str) -> Meta:
        return meta

    async def imdb_tmdb(self, meta: Meta, _filename: str) -> Meta:
        return meta

    async def get_tmdb_imdb_from_mediainfo(
        self, _mi: dict[str, Any], meta: Meta
    ) -> tuple[str, int, int, int]:
        return (
            meta.category or "MOVIE",
            int(meta.tmdb_id or 123),
            int(meta.imdb_id or 1234567),
            int(meta.tvdb_id or 456),
        )

    async def get_tmdb_id(
        self, *_args: object, **_kwargs: object
    ) -> tuple[int, str]:
        return 123, "MOVIE"

    async def get_tmdb_from_imdb(
        self, *_args: object, **kwargs: object
    ) -> tuple[str, int, str, bool]:
        return (
            str(kwargs.get("category_preference") or "MOVIE"),
            123,
            "en",
            False,
        )

    async def set_tmdb_metadata(
        self, meta: Meta, _filename: str | None = None
    ) -> None:
        meta.title = meta.title or "Example Release"
        meta.year = meta.year or 2024
        meta.original_language = meta.original_language or "en"
        meta.overview = meta.overview or "Representative overview."

    async def get_tmdb_localized_data(
        self,
        _meta: Meta,
        data_type: str,
        language: str,
        append_to_response: str,
    ) -> dict[str, Any]:
        return {
            "language": language,
            "type": data_type,
            "append": append_to_response,
        }

    async def get_sonarr_data(
        self, *_args: object, **_kwargs: object
    ) -> dict[str, Any]:
        return {
            "tvdb_id": 456,
            "imdb_id": 1234567,
            "tvmaze_id": 789,
            "tmdb_id": 123,
            "genres": ["Drama"],
            "release_group": "GROUP",
            "year": 2024,
        }

    async def get_radarr_data(
        self, *_args: object, **_kwargs: object
    ) -> dict[str, Any]:
        return {
            "imdb_id": 1234567,
            "tmdb_id": 123,
            "genres": ["Drama"],
            "release_group": "GROUP",
            "year": 2024,
        }

    async def get_tracker_data(
        self, *_args: object, **_kwargs: object
    ) -> Meta:
        return self._meta(_args)

    async def get_ptp_from_hash(self, meta: Meta) -> Meta:
        return meta

    async def ping_unit3d(self, _meta: Meta) -> None:
        return None

    async def get_source_override(self, meta: Meta, **_kwargs: object) -> Meta:
        return meta

    async def get_audio_v2(
        self, *_args: object, **_kwargs: object
    ) -> tuple[str, str, bool]:
        return "DDP 5.1", "5.1", False

    async def check_hosts(
        self, meta: Meta, *_args: object, **_kwargs: object
    ) -> tuple[list[dict[str, str]], bool, bool]:
        return list(meta.image_list or []), False, False

    async def search_tvdb_series(
        self, *_args: object, **_kwargs: object
    ) -> tuple[list[dict[str, Any]], int]:
        return [{"id": 456, "name": "Example Series"}], 456

    async def search_tvmaze(self, *_args: object, **_kwargs: object) -> int:
        return 789

    async def search_imdb(self, *_args: object, **_kwargs: object) -> int:
        return 1234567

    async def get_imdb_info_api(
        self, *_args: object, **_kwargs: object
    ) -> dict[str, Any]:
        return {
            "title": "Example Release",
            "year": 2024,
            "type": "movie",
            "aka": "",
            "genres": ["Drama"],
            "stars": ["Example Actor"],
        }

    async def get_imdb_from_episode(
        self, *_args: object, **_kwargs: object
    ) -> dict[str, Any]:
        return {"series": {"series_id": "tt1234567"}}

    async def __getattr_awaitable(
        self, *_args: object, **_kwargs: object
    ) -> _AwaitableValue:
        return _AwaitableValue()

    def __getattr__(self, name: str) -> Any:
        if name.startswith(
            (
                "get_",
                "search",
                "fetch",
                "load",
                "prepare",
                "upload",
                "check",
                "process",
                "create",
                "update",
                "find",
                "resolve",
                "set_",
            )
        ):
            return self.__getattr_awaitable
        return super().__getattr__(name)


class _VideoPort(_ManagerPort):
    async def get_video(
        self, videoloc: str, _mode: str, _sorted_filelist: bool = False
    ) -> tuple[str, list[str]]:
        return videoloc, [videoloc]

    async def get_resolution(
        self, *_args: object, **_kwargs: object
    ) -> tuple[str, bool]:
        return "1080p", False

    async def is_sd(self, _resolution: str) -> int:
        return 0

    async def get_video_duration(self, _meta: Meta) -> int:
        return 120

    async def get_type(self, *_args: object, **_kwargs: object) -> str:
        return "WEBDL"

    async def get_container(self, _meta: Meta) -> str:
        return "MKV"

    async def is_3d(self, _bdinfo: Any | None) -> str:
        return ""

    async def get_uhd(self, *_args: object, **_kwargs: object) -> str:
        return "UHD"

    async def get_hdr(self, *_args: object, **_kwargs: object) -> str:
        return "HDR10"

    async def get_video_codec(self, *_args: object, **_kwargs: object) -> str:
        return "H.264"

    async def get_video_encode(
        self, *_args: object, **_kwargs: object
    ) -> tuple[str, str, bool, str]:
        return "H.264", "x264", False, "10"


class _PreparationPort(_Universal):
    def __init__(self, config: dict[str, Any], tmp_path: Path) -> None:
        super().__init__()
        self.config = config
        manager = _ManagerPort(config, tmp_path)
        for name in (
            "disc_info_manager",
            "scene_manager",
            "name_manager",
            "season_episode_manager",
            "metadata_searching_manager",
            "tmdb_manager",
            "sonarr_manager",
            "radarr_manager",
            "tracker_data_manager",
            "overrides",
            "audio_manager",
            "rehost_images_manager",
            "tvdb_handler",
        ):
            setattr(self, name, manager)

    @staticmethod
    def _resolve_book_filelist(
        meta: Meta, videoloc: str
    ) -> tuple[str, list[str], str, str]:
        path = str(meta.path or videoloc)
        return path, [path], Path(path).name, "file"

    _resolve_game_filelist = _resolve_book_filelist

    async def _gather_book_prep(self, meta: Meta, *_args: object) -> None:
        meta.book_language = meta.book_language or "English"

    async def _gather_game_prep(self, meta: Meta, *_args: object) -> None:
        meta.title = meta.title or "Example Game"

    async def get_cat(self, _video: str, meta: Meta) -> str:
        return meta.category or "MOVIE"

    @staticmethod
    def check_adult_media(meta: Meta) -> bool:
        return meta.category == "XXX"

    async def stream_optimized(self, value: bool) -> int:
        return int(value)

    async def parse_scene_nfo(self, _meta: Meta) -> None:
        return None


class _Port(_Universal):
    def __getattr__(self, name: str) -> Any:
        if name.startswith(
            (
                "get_",
                "search",
                "fetch",
                "load",
                "prepare",
                "upload",
                "check",
                "process",
                "create",
                "update",
                "find",
                "resolve",
            )
        ):

            def awaitable_method(
                *_args: object, **_kwargs: object
            ) -> _AwaitableValue:
                return _AwaitableValue()

            return awaitable_method
        return super().__getattr__(name)


def _config() -> dict[str, Any]:
    config = copy.deepcopy(example_config)
    config.setdefault("DEFAULT", {}).update(
        {
            "tmdb_api": "0123456789abcdef0123456789abcdef",
            "img_host_1": "imgbb",
            "imgbb_api": "image-key",
            "screens": 4,
            "default_trackers": ["BHD"],
            "torrent_client": "qbittorrent",
            "default_torrent_client": "qbittorrent",
        }
    )
    return config


def _meta(tmp_path: Path, profile: int = 0) -> Meta:
    category = ("MOVIE", "TV", "MUSIC", "BOOK", "GAME", "XXX")[profile % 6]
    release = tmp_path / f"Example.Release.{profile}.2024.1080p.WEB-DL.mkv"
    release.write_bytes(b"media")
    temp = tmp_path / "tmp" / f"service-{profile}"
    temp.mkdir(parents=True, exist_ok=True)
    for name, text in {
        "MEDIAINFO.txt": "General\nComplete name : Example.mkv",
        "MEDIAINFO_CLEANPATH.txt": "General\nComplete name : Example.mkv",
        "BD_SUMMARY_00.txt": "PLAYLIST REPORT\nName: 00000.MPLS",
        "DESCRIPTION.txt": "[b]Example[/b]",
        "NFO.txt": "Example NFO",
        "MediaInfo.json": '{"media":{"track":[{"@type":"General","Format":"Matroska","Duration":"7200"},{"@type":"Video","Format":"AVC","Width":"1920","Height":"1080","FrameRate":"24.000","BitRate":"12000000"},{"@type":"Audio","Format":"E-AC-3","Channels":"6","BitRate":"640000"}]}}',
    }.items():
        (temp / name).write_text(text, encoding="utf-8")
    return Meta(
        base_dir=str(tmp_path),
        uuid=f"service-{profile}",
        path=str(release),
        filename=release.name,
        filelist=[str(release)],
        isdir=False,
        category=category,
        name="Example Release 2024 1080p WEB-DL DDP 5.1 H.264-GROUP",
        title="Example Release",
        original_title="Example Original Title",
        year=2024,
        search_year=2024,
        imdb_id="1234567",
        tmdb_id=123,
        tvdb_id=456,
        mal_id=789,
        season=1,
        episode=1,
        tv_pack=category == "TV",
        source="WEB",
        type="WEBDL",
        resolution="1080p",
        service="AMZN",
        tag="-GROUP",
        group="GROUP",
        video_codec="H.264",
        audio="DDP 5.1",
        channels="5.1",
        description="Representative description.",
        overview="Representative overview.",
        image_list=[
            {
                "img_url": "https://images.example/1.jpg",
                "raw_url": "https://images.example/1.jpg",
                "web_url": "https://images.example/1",
            }
        ],
        mediainfo={
            "media": {
                "track": [
                    {"@type": "General", "Format": "Matroska"},
                    {
                        "@type": "Video",
                        "Format": "AVC",
                        "Height": "1080",
                        "Language": "en",
                    },
                    {
                        "@type": "Audio",
                        "Format": "E-AC-3",
                        "Channels": "6",
                        "Language": "en",
                    },
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
        audio_languages=["English"],
        subtitle_languages=["English"],
        languages=["English"],
        trackers=["BHD"],
        tracker_status={},
        screens=4,
        unattended=True,
        unattended_confirm=True,
        debug=False,
        manual=False,
        author="Example Author",
        narrator="Example Narrator",
        publisher="Example Publisher",
        isbn="9780000000000",
        asin="B000000000",
        artist="Example Artist",
        album="Example Album",
        book_language="English",
        book_language_iso="ENG",
        audiobook=category == "BOOK",
        game=category == "GAME",
    )


def _modules() -> list[ModuleType]:
    return [
        importlib.import_module(item.name)
        for item in pkgutil.iter_modules(
            services_package.__path__, f"{services_package.__name__}."
        )
    ]


def _safe_callable(function: Callable[..., object]) -> bool:
    name = getattr(function, "__name__", "")
    if name.startswith("__"):
        return False
    try:
        inspect.getsource(function)
    except OSError, TypeError, ValueError:
        return False
    blocked_names = {
        "wait_for_bandwidth",
        "wait_for_completion",
        "_connect_qbittorrent",
        "cleanup",
        "cleanup_all",
        "kill_all_threads",
    }
    return name not in blocked_names


_MISSING = object()


def _annotation_text(annotation: object) -> str:
    if annotation is inspect.Parameter.empty:
        return ""
    if isinstance(annotation, str):
        return annotation.replace("typing.", "").replace(
            "collections.abc.", ""
        )
    return (
        str(annotation).replace("typing.", "").replace("collections.abc.", "")
    )


def _optional_concrete_type(
    origin: object, args: tuple[object, ...]
) -> object | None:
    if origin is None or type(None) not in args:
        return None
    return next((item for item in args if item is not type(None)), object)


def _is_path_annotation(annotation: object, text: str) -> bool:
    if annotation is Path or text in {"Path", "<class 'pathlib.Path'>"}:
        return True
    return "Path" in text and "list" not in text.casefold()


def _is_text_annotation(annotation: object, text: str) -> bool:
    return annotation is str or text in {"str", "<class 'str'>"}


def _is_int_annotation(annotation: object, text: str) -> bool:
    return annotation is int or text in {"int", "<class 'int'>"}


def _is_float_annotation(annotation: object, text: str) -> bool:
    return annotation is float or text in {"float", "<class 'float'>"}


def _is_bool_annotation(annotation: object, text: str) -> bool:
    return annotation is bool or text in {"bool", "<class 'bool'>"}


def _coerce_int(value: object) -> int:
    try:
        return int(value)
    except TypeError, ValueError:
        return 1


def _coerce_float(value: object) -> float:
    try:
        return float(value)
    except TypeError, ValueError:
        return 1.0


def _coerce_path_contract_value(value: object, path: Path) -> object:
    return value if isinstance(value, Path) else path


def _coerce_text_contract_value(value: object, _path: Path) -> object:
    return str(value)


def _coerce_int_contract_value(value: object, _path: Path) -> object:
    return _coerce_int(value)


def _coerce_float_contract_value(value: object, _path: Path) -> object:
    return _coerce_float(value)


def _coerce_bool_contract_value(value: object, _path: Path) -> object:
    return bool(value)


_SCALAR_CONTRACT_COERCERS: tuple[
    tuple[Callable[[object, str], bool], Callable[[object, Path], object]], ...
] = (
    (_is_path_annotation, _coerce_path_contract_value),
    (_is_text_annotation, _coerce_text_contract_value),
    (_is_int_annotation, _coerce_int_contract_value),
    (_is_float_annotation, _coerce_float_contract_value),
    (_is_bool_annotation, _coerce_bool_contract_value),
)


def _coerce_scalar_contract_value(
    value: object, annotation: object, text: str, path: Path
) -> object:
    for predicate, coercer in _SCALAR_CONTRACT_COERCERS:
        if predicate(annotation, text):
            return coercer(value, path)
    return _MISSING


def _coerce_list_contract_value(
    value: object, text: str, path: Path
) -> object:
    if "Path" in text:
        return [path]
    if isinstance(value, list):
        return value
    return [value]


_COLLECTION_CONTRACT_TYPES = (
    (list, "list[", "list"),
    (dict, "dict[", "dict"),
    (tuple, "tuple[", "tuple"),
    (set, "set[", "set"),
)


def _collection_contract_kind(origin: object, text: str) -> str | None:
    for expected, prefix, kind in _COLLECTION_CONTRACT_TYPES:
        if origin is expected or text.startswith(prefix):
            return kind
    return None


def _coerce_dict_contract_value(
    value: object, _text: str, _path: Path
) -> object:
    return value if isinstance(value, dict) else {}


def _coerce_tuple_contract_value(
    value: object, _text: str, _path: Path
) -> object:
    return value if isinstance(value, tuple) else ()


def _coerce_set_contract_value(
    value: object, _text: str, _path: Path
) -> object:
    return value if isinstance(value, set) else set()


_COLLECTION_CONTRACT_COERCERS: dict[
    str, Callable[[object, str, Path], object]
] = {
    "list": _coerce_list_contract_value,
    "dict": _coerce_dict_contract_value,
    "tuple": _coerce_tuple_contract_value,
    "set": _coerce_set_contract_value,
}


def _coerce_collection_contract_value(
    value: object, origin: object, text: str, path: Path
) -> object:
    kind = _collection_contract_kind(origin, text)
    if kind is None:
        return _MISSING
    return _COLLECTION_CONTRACT_COERCERS[kind](value, text, path)


def _contract_callable() -> Callable[..., str]:
    return lambda *_args, **_kwargs: "example"


def _coerce_optional_contract_value(
    value: object, origin: object, args: tuple[object, ...], path: Path
) -> object:
    concrete = _optional_concrete_type(origin, args)
    if concrete is None or value is None:
        return _MISSING
    return _coerce_contract_value(value, concrete, path)


def _coerce_contract_value(
    value: object, annotation: object, path: Path
) -> object:
    text = _annotation_text(annotation)
    origin = get_origin(annotation)
    optional = _coerce_optional_contract_value(
        value, origin, get_args(annotation), path
    )
    if optional is not _MISSING:
        return optional
    scalar = _coerce_scalar_contract_value(value, annotation, text, path)
    if scalar is not _MISSING:
        return scalar
    collection = _coerce_collection_contract_value(value, origin, text, path)
    if collection is not _MISSING:
        return collection
    if "Callable" in text:
        return _contract_callable()
    return value


_CONTRACT_PATH_NAMES = frozenset(
    {
        "root",
        "directory",
        "dir_path",
        "temp_dir",
        "output_dir",
        "destination",
        "source_path",
    }
)
_CONTRACT_STRING_PATH_NAMES = frozenset(
    {"videopath", "videoloc", "torrent_path", "media_path", "input_path"}
)


def _contract_value_or(value: object, fallback: object) -> object:
    return value if value else fallback


def _contract_fixture_path(tmp_path: Path) -> Path:
    path = tmp_path / "Example.Release.2024.mkv"
    path.write_bytes(b"media")
    return path


def _named_contract_values(
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    path: Path,
) -> dict[str, object]:
    return {
        "config": config,
        "configuration": config,
        "repository": _Repository(),
        "console": _Port(),
        "client": _ManagerPort(config, tmp_path),
        "manager": _ManagerPort(config, tmp_path),
        "service": _ManagerPort(config, tmp_path),
        "prep": _PreparationPort(config, tmp_path),
        "prep_instance": _PreparationPort(config, tmp_path),
        "mi": meta.mediainfo,
        "mediainfo": meta.mediainfo,
        "bdinfo": {
            "title": "Example Release",
            "label": "Example Release",
            "size": 25_000_000_000,
            "video": [
                {
                    "fps": "24.000",
                    "3d": "",
                    "codec": "AVC",
                    "resolution": "1080p",
                    "bitrate": "20000 kbps",
                }
            ],
            "audio": [
                {"codec": "E-AC-3", "channels": "5.1", "bitrate": "640 kbps"}
            ],
            "subtitles": ["English"],
        },
        "argument_parser_factory": None,
        "publish_preview": None,
        "meta": meta,
        "shared_meta": meta,
        "prepared_meta": meta,
        "original_meta": meta,
        "path": str(path),
        "file_path": str(path),
        "files": [path],
        "filename": path.name,
        "current_author": "Example Author",
        "source_author": "Source Author",
        "current_title": "Example Title",
        "source_title": "Source Title",
        "author": "Example Author",
        "translator": "Example Translator",
        "extension": ".mkv",
        "file_pattern": "*.mkv",
        "base_dir": str(tmp_path),
        "runtime_path": tmp_path / "runtime.py",
        "legacy_path": tmp_path / "legacy.py",
        "defaults_path": tmp_path / "defaults.py",
        "explicit_path": None,
        "folder_id": str(meta.uuid),
        "uuid": str(meta.uuid),
        "category": _contract_value_or(meta.category, "MOVIE"),
        "source": _contract_value_or(meta.source, "WEB"),
        "type": _contract_value_or(meta.type, "WEBDL"),
        "resolution": _contract_value_or(meta.resolution, "1080p"),
        "name": _contract_value_or(meta.name, "Example Release"),
        "title": _contract_value_or(meta.title, "Example Release"),
        "year": 2024,
        "search_year": 2024,
        "season": 1,
        "episode": 1,
        "imdb_id": "tt1234567",
        "tmdb_id": 123,
        "tvdb_id": 456,
        "tracker": "BHD",
        "tracker_name": "BHD",
        "trackers": ["BHD"],
        "active_trackers": ["BHD"],
        "api_trackers": ["BHD"],
        "http_trackers": [],
        "other_api_trackers": [],
        "upload_target": "tracker",
        "dupes": [],
        "entries": [],
        "items": [],
        "filelist": meta.filelist,
        "paths": [str(path)],
        "screens": 4,
        "img_host": "imgbb",
        "text": "Example Release 2024",
        "value": "example",
        "description": "Representative description.",
        "data": {},
        "json_data": [],
        "payload": {},
        "tracker_status": {},
        "errors": [],
        "warnings": [],
        "show_warnings": True,
        "allowed_extensions": {".mkv", ".mp4", ".ts"},
        "selected_files": [str(path)],
        "media_files": [str(path)],
        "extensions": [".mkv"],
        "mapping": {},
        "headers": {},
        "params": {},
        "args": [],
        "argv": [],
        "debug": False,
        "unattended": True,
        "unattended_confirm": True,
        "is_disc": False,
        "tv_pack": False,
        "freeleech": 0,
        "manual": False,
        "overwrite": False,
        "check_exists": False,
        "now": datetime(2024, 1, 2, tzinfo=UTC),
    }


def _trackers_mapping_requested(
    normalized: str, annotation: object, annotation_text: str
) -> bool:
    if normalized != "trackers":
        return False
    origin = get_origin(annotation)
    return origin is dict or annotation_text.startswith("dict[")


def _fallback_numeric_contract_value(
    annotation: object, text: str, profile: int
) -> object:
    if _is_bool_annotation(annotation, text):
        return profile == 0
    if _is_int_annotation(annotation, text):
        return 1
    if _is_float_annotation(annotation, text):
        return 1.0
    return _MISSING


def _fallback_text_path_contract_value(
    annotation: object, text: str, path: Path
) -> object:
    if _is_text_annotation(annotation, text):
        return "example"
    if _is_path_annotation(annotation, text):
        return path
    return _MISSING


def _fallback_scalar_contract_value(
    annotation: object, text: str, path: Path, profile: int
) -> object:
    numeric = _fallback_numeric_contract_value(annotation, text, profile)
    if numeric is not _MISSING:
        return numeric
    return _fallback_text_path_contract_value(annotation, text, path)


def _fallback_list_contract_value(
    text: str, path: Path, profile: int
) -> object:
    if "Path" in text:
        return [path]
    return [] if profile else ["example"]


def _fallback_dict_contract_value(
    _text: str, _path: Path, _profile: int
) -> object:
    return {}


def _fallback_tuple_contract_value(
    _text: str, _path: Path, _profile: int
) -> object:
    return ()


def _fallback_set_contract_value(
    _text: str, _path: Path, _profile: int
) -> object:
    return set()


_FALLBACK_COLLECTION_VALUES: dict[str, Callable[[str, Path, int], object]] = {
    "list": _fallback_list_contract_value,
    "dict": _fallback_dict_contract_value,
    "tuple": _fallback_tuple_contract_value,
    "set": _fallback_set_contract_value,
}


def _fallback_collection_contract_value(
    origin: object, text: str, path: Path, profile: int
) -> object:
    kind = _collection_contract_kind(origin, text)
    if kind is None:
        return _MISSING
    return _FALLBACK_COLLECTION_VALUES[kind](text, path, profile)


def _fallback_contract_value(
    name: str,
    annotation: object,
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    path: Path,
    profile: int,
) -> object:
    text = _annotation_text(annotation)
    scalar = _fallback_scalar_contract_value(annotation, text, path, profile)
    if scalar is not _MISSING:
        return scalar
    origin = get_origin(annotation)
    collection = _fallback_collection_contract_value(
        origin, text, path, profile
    )
    if collection is not _MISSING:
        return collection
    concrete = _optional_concrete_type(origin, get_args(annotation))
    if concrete is not None:
        return _value(name, concrete, meta, config, tmp_path, profile)
    if "Callable" in text:
        return _contract_callable()
    return _Port()


def _value(
    name: str,
    annotation: object,
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    profile: int,
) -> object:
    normalized = name.casefold().lstrip("_")
    path = _contract_fixture_path(tmp_path)
    if normalized in _CONTRACT_PATH_NAMES:
        return _coerce_contract_value(tmp_path, annotation, path)
    if normalized in _CONTRACT_STRING_PATH_NAMES:
        return _coerce_contract_value(str(path), annotation, path)
    annotation_text = _annotation_text(annotation)
    if _trackers_mapping_requested(normalized, annotation, annotation_text):
        return dict(config.get("TRACKERS", {}))
    values = _named_contract_values(meta, config, tmp_path, path)
    if normalized in values:
        return _coerce_contract_value(values[normalized], annotation, path)
    return _fallback_contract_value(
        normalized, annotation, meta, config, tmp_path, path, profile
    )


_PROTECTED_SCENARIO_ARGUMENTS = frozenset(
    {
        "meta",
        "shared_meta",
        "prepared_meta",
        "original_meta",
        "prep",
        "prep_instance",
        "config",
        "configuration",
        "client",
        "manager",
        "service",
        "repository",
    }
)


def _safe_type_hints(function: Callable[..., object]) -> dict[str, Any]:
    try:
        return get_type_hints(function)
    except NameError, TypeError:
        return {}


def _include_contract_parameter(
    parameter: inspect.Parameter, overrides: Mapping[str, object]
) -> bool:
    if parameter.kind in {
        inspect.Parameter.VAR_POSITIONAL,
        inspect.Parameter.VAR_KEYWORD,
    }:
        return False
    if parameter.default is inspect.Parameter.empty:
        return True
    return parameter.name in overrides


def _contract_parameter_value(
    parameter: inspect.Parameter,
    annotation: object,
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    profile: int,
    overrides: Mapping[str, object],
) -> object:
    generated = _value(
        parameter.name, annotation, meta, config, tmp_path, profile
    )
    value = overrides.get(parameter.name, generated)
    return _coerce_contract_value(
        value, annotation, tmp_path / "Example.Release.2024.mkv"
    )


def _invocation_arguments(
    function: Callable[..., object],
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    profile: int,
    overrides: Mapping[str, object],
) -> tuple[list[object], dict[str, object]]:
    hints = _safe_type_hints(function)
    positional: list[object] = []
    keywords: dict[str, object] = {}
    for parameter in inspect.signature(function).parameters.values():
        if not _include_contract_parameter(parameter, overrides):
            continue
        annotation = hints.get(parameter.name, parameter.annotation)
        value = _contract_parameter_value(
            parameter, annotation, meta, config, tmp_path, profile, overrides
        )
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY:
            keywords[parameter.name] = value
        else:
            positional.append(value)
    return positional, keywords


async def _invoke(
    function: Callable[..., object],
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    profile: int,
    overrides: Mapping[str, object] | None = None,
) -> object:
    resolved_overrides = overrides or {}
    positional, keywords = _invocation_arguments(
        function, meta, config, tmp_path, profile, resolved_overrides
    )
    result = function(*positional, **keywords)
    if inspect.isawaitable(result):
        return await asyncio.wait_for(result, timeout=0.35)
    return result


async def _service_no_sleep(
    _delay: float = 0, *_args: object, **_kwargs: object
) -> None:
    return None


async def _service_affirmative_prompt(
    callback: Callable[..., object], *_args: object, **kwargs: object
) -> object:
    name = getattr(callback, "__name__", "")
    if "choice" in name:
        choices = kwargs.get("choices", ["example"])
        return next(iter(choices)) if choices else "example"
    if "string" in name:
        return "example"
    return True


def _patch_service_boundaries(
    monkeypatch: Any, config: dict[str, Any], tmp_path: Path
) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", _AsyncClient)
    monkeypatch.setattr(requests, "Session", _Session)
    monkeypatch.setattr(requests, "get", _Session().get)
    monkeypatch.setattr(requests, "post", _Session().post)
    monkeypatch.setattr(
        subprocess, "run", lambda *_args, **_kwargs: _CompletedProcess()
    )
    monkeypatch.setattr(
        subprocess, "check_output", lambda *_args, **_kwargs: b"ok"
    )
    monkeypatch.setattr(
        ImdbManager,
        "get_imdb_info_api",
        lambda *_args, **_kwargs: _AwaitableValue(),
    )
    monkeypatch.setattr(
        TvmazeManager,
        "search_tvmaze",
        lambda *_args, **_kwargs: _AwaitableValue(),
    )
    _patch_preparation_helpers(monkeypatch, config, tmp_path)
    monkeypatch.setattr(asyncio, "sleep", _service_no_sleep)


def _patch_preparation_helpers(
    monkeypatch: Any, config: dict[str, Any], tmp_path: Path
) -> None:
    import src.services.preparation_helpers as preparation_helpers

    manager_double = _ManagerPort(config, tmp_path)
    monkeypatch.setattr(
        preparation_helpers, "video_manager", _VideoPort(config, tmp_path)
    )
    monkeypatch.setattr(preparation_helpers, "imdb_manager", manager_double)
    monkeypatch.setattr(preparation_helpers, "tvmaze_manager", manager_double)


def _patch_service_module_prompt(module: ModuleType, monkeypatch: Any) -> None:
    if hasattr(module, "prompt_in_thread"):
        monkeypatch.setattr(
            module, "prompt_in_thread", _service_affirmative_prompt
        )


def _service_module_functions(
    module: ModuleType,
) -> list[tuple[str, Callable[..., object]]]:
    return [
        (name, function)
        for name, function in inspect.getmembers(module, inspect.isfunction)
        if function.__module__ == module.__name__ and _safe_callable(function)
    ]


def _service_module_classes(module: ModuleType) -> list[tuple[str, type[Any]]]:
    return [
        (name, class_type)
        for name, class_type in inspect.getmembers(module, inspect.isclass)
        if class_type.__module__ == module.__name__
        and not getattr(class_type, "_is_protocol", False)
    ]


def _service_instance_methods(
    module: ModuleType, instance: object
) -> list[tuple[str, Callable[..., object]]]:
    methods: list[tuple[str, Callable[..., object]]] = []
    for method_name, method in inspect.getmembers(instance, callable):
        if getattr(method, "__module__", None) != module.__name__:
            continue
        if method_name.startswith("__") or not _safe_callable(method):
            continue
        methods.append((method_name, method))
    return methods


def _service_scenario_overrides(
    overrides: Mapping[str, object],
) -> dict[str, object]:
    return {
        key: value
        for key, value in overrides.items()
        if key not in _PROTECTED_SCENARIO_ARGUMENTS
    }


def _service_scenario_meta(
    tmp_path: Path, updates: Mapping[str, object]
) -> Meta:
    meta = _meta(tmp_path, 0)
    for key, value in updates.items():
        if key in Meta.__dataclass_fields__:
            setattr(meta, key, value)
    return meta


async def _record_service_invocation(
    qualified: str,
    function: Callable[..., object],
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    profile: int,
    process_terminations: list[str],
    validation_errors: list[str],
    overrides: Mapping[str, object] | None = None,
) -> None:
    try:
        await _invoke(function, meta, config, tmp_path, profile, overrides)
    except (KeyboardInterrupt, SystemExit) as error:
        process_terminations.append(f"{qualified}:{type(error).__name__}")
    except Exception as error:
        validation_errors.append(f"{qualified}:{type(error).__name__}:{error}")


async def _exercise_service_profiles(
    qualified: str,
    function: Callable[..., object],
    config: dict[str, Any],
    tmp_path: Path,
    process_terminations: list[str],
    validation_errors: list[str],
) -> None:
    for profile in range(6):
        await _record_service_invocation(
            qualified,
            function,
            _meta(tmp_path, profile),
            config,
            tmp_path,
            profile,
            process_terminations,
            validation_errors,
        )


async def _exercise_service_literals(
    qualified: str,
    function: Callable[..., object],
    config: dict[str, Any],
    tmp_path: Path,
    process_terminations: list[str],
    validation_errors: list[str],
) -> None:
    for meta_updates, argument_overrides in literal_branch_scenarios(
        function, Meta.__dataclass_fields__
    ):
        await _record_service_invocation(
            qualified,
            function,
            _service_scenario_meta(tmp_path, meta_updates),
            config,
            tmp_path,
            0,
            process_terminations,
            validation_errors,
            _service_scenario_overrides(argument_overrides),
        )


async def _exercise_service_callable(
    qualified: str,
    function: Callable[..., object],
    config: dict[str, Any],
    tmp_path: Path,
    attempted: set[str],
    process_terminations: list[str],
    validation_errors: list[str],
) -> None:
    attempted.add(qualified)
    await _exercise_service_profiles(
        qualified,
        function,
        config,
        tmp_path,
        process_terminations,
        validation_errors,
    )
    await _exercise_service_literals(
        qualified,
        function,
        config,
        tmp_path,
        process_terminations,
        validation_errors,
    )


async def _service_class_instance(
    module: ModuleType,
    class_name: str,
    class_type: type[Any],
    config: dict[str, Any],
    tmp_path: Path,
    validation_errors: list[str],
) -> object | None:
    try:
        return await _invoke(class_type, _meta(tmp_path), config, tmp_path, 0)
    except Exception as error:
        validation_errors.append(
            f"{module.__name__}.{class_name}.__init__:{type(error).__name__}:{error}"
        )
        return None


async def _exercise_service_class(
    module: ModuleType,
    class_name: str,
    class_type: type[Any],
    config: dict[str, Any],
    tmp_path: Path,
    attempted: set[str],
    process_terminations: list[str],
    validation_errors: list[str],
) -> None:
    instance = await _service_class_instance(
        module, class_name, class_type, config, tmp_path, validation_errors
    )
    if instance is None:
        return
    for method_name, method in _service_instance_methods(module, instance):
        await _exercise_service_callable(
            f"{module.__name__}.{class_name}.{method_name}",
            method,
            config,
            tmp_path,
            attempted,
            process_terminations,
            validation_errors,
        )


async def _exercise_service_module(
    module: ModuleType,
    config: dict[str, Any],
    tmp_path: Path,
    monkeypatch: Any,
    attempted: set[str],
    process_terminations: list[str],
    validation_errors: list[str],
) -> None:
    _patch_service_module_prompt(module, monkeypatch)
    for name, function in _service_module_functions(module):
        await _exercise_service_callable(
            f"{module.__name__}.{name}",
            function,
            config,
            tmp_path,
            attempted,
            process_terminations,
            validation_errors,
        )
    for class_name, class_type in _service_module_classes(module):
        await _exercise_service_class(
            module,
            class_name,
            class_type,
            config,
            tmp_path,
            attempted,
            process_terminations,
            validation_errors,
        )


async def _exercise_service_catalog(
    modules: list[ModuleType],
    config: dict[str, Any],
    tmp_path: Path,
    monkeypatch: Any,
    attempted: set[str],
    process_terminations: list[str],
    validation_errors: list[str],
) -> None:
    for module in modules:
        await _exercise_service_module(
            module,
            config,
            tmp_path,
            monkeypatch,
            attempted,
            process_terminations,
            validation_errors,
        )


def test_service_catalog_accepts_domain_fixtures_and_boundary_doubles(
    tmp_path: Path, monkeypatch: Any
) -> None:
    config = _config()
    _patch_service_boundaries(monkeypatch, config, tmp_path)
    attempted: set[str] = set()
    process_terminations: list[str] = []
    validation_errors: list[str] = []
    asyncio.run(
        _exercise_service_catalog(
            _modules(),
            config,
            tmp_path,
            monkeypatch,
            attempted,
            process_terminations,
            validation_errors,
        )
    )
    assert len(attempted) >= 240
    assert process_terminations == [], process_terminations
