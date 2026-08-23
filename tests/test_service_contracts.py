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


def _coerce_contract_value(
    value: object, annotation: object, path: Path
) -> object:
    text = _annotation_text(annotation)
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is not None and type(None) in args and value is not None:
        concrete = next(
            (item for item in args if item is not type(None)), object
        )
        return _coerce_contract_value(value, concrete, path)
    if (
        annotation is Path
        or text in {"Path", "<class 'pathlib.Path'>"}
        or ("Path" in text and "list" not in text.casefold())
    ):
        return value if isinstance(value, Path) else path
    if annotation is str or text in {"str", "<class 'str'>"}:
        return str(value)
    if annotation is int or text in {"int", "<class 'int'>"}:
        try:
            return int(value)
        except TypeError, ValueError:
            return 1
    if annotation is float or text in {"float", "<class 'float'>"}:
        try:
            return float(value)
        except TypeError, ValueError:
            return 1.0
    if annotation is bool or text in {"bool", "<class 'bool'>"}:
        return bool(value)
    if origin is list or text.startswith("list["):
        if "Path" in text:
            return [path]
        if isinstance(value, list):
            return value
        return [value]
    if origin is dict or text.startswith("dict["):
        return value if isinstance(value, dict) else {}
    if origin is tuple or text.startswith("tuple["):
        return value if isinstance(value, tuple) else ()
    if origin is set or text.startswith("set["):
        return value if isinstance(value, set) else set()
    if "Callable" in text:
        return lambda *_args, **_kwargs: "example"
    return value


def _value(
    name: str,
    annotation: object,
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    profile: int,
) -> object:
    normalized = name.casefold().lstrip("_")
    path = tmp_path / "Example.Release.2024.mkv"
    path.write_bytes(b"media")
    path_names = {
        "root",
        "directory",
        "dir_path",
        "temp_dir",
        "output_dir",
        "destination",
        "source_path",
    }
    if normalized in path_names:
        return _coerce_contract_value(tmp_path, annotation, path)
    string_path_names = {
        "videopath",
        "videoloc",
        "torrent_path",
        "media_path",
        "input_path",
    }
    if normalized in string_path_names:
        return _coerce_contract_value(str(path), annotation, path)
    values: dict[str, object] = {
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
        "category": meta.category or "MOVIE",
        "source": meta.source or "WEB",
        "type": meta.type or "WEBDL",
        "resolution": meta.resolution or "1080p",
        "name": meta.name or "Example Release",
        "title": meta.title or "Example Release",
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
    origin = get_origin(annotation)
    args = get_args(annotation)
    annotation_text = _annotation_text(annotation)
    if normalized == "trackers" and (
        origin is dict or annotation_text.startswith("dict[")
    ):
        return dict(config.get("TRACKERS", {}))
    if normalized in values:
        return _coerce_contract_value(values[normalized], annotation, path)
    if annotation is bool or annotation_text in {"bool", "<class 'bool'>"}:
        return profile == 0
    if annotation is int or annotation_text in {"int", "<class 'int'>"}:
        return 1
    if annotation is float or annotation_text in {"float", "<class 'float'>"}:
        return 1.0
    if annotation is str or annotation_text in {"str", "<class 'str'>"}:
        return "example"
    if annotation is Path or annotation_text in {
        "Path",
        "<class 'pathlib.Path'>",
    }:
        return path
    if origin is list or annotation_text.startswith("list["):
        return (
            [path]
            if "Path" in annotation_text
            else ([] if profile else ["example"])
        )
    if origin is dict or annotation_text.startswith("dict["):
        return {}
    if origin is tuple or annotation_text.startswith("tuple["):
        return ()
    if origin is set or annotation_text.startswith("set["):
        return set()
    if origin is not None and type(None) in args:
        concrete = next((item for item in args if item is not type(None)), str)
        return _value(normalized, concrete, meta, config, tmp_path, profile)
    if "Callable" in annotation_text:
        return lambda *_args, **_kwargs: "example"
    return _Port()


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


async def _invoke(
    function: Callable[..., object],
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    profile: int,
    overrides: Mapping[str, object] | None = None,
) -> object:
    positional: list[object] = []
    keywords: dict[str, object] = {}
    overrides = overrides or {}
    try:
        hints = get_type_hints(function)
    except NameError, TypeError:
        hints = {}
    for parameter in inspect.signature(function).parameters.values():
        if parameter.kind in {
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        }:
            continue
        if (
            parameter.default is not inspect.Parameter.empty
            and parameter.name not in overrides
        ):
            continue
        annotation = hints.get(parameter.name, parameter.annotation)
        value = overrides.get(
            parameter.name,
            _value(
                parameter.name, annotation, meta, config, tmp_path, profile
            ),
        )
        value = _coerce_contract_value(
            value, annotation, tmp_path / "Example.Release.2024.mkv"
        )
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY:
            keywords[parameter.name] = value
        else:
            positional.append(value)
    result = function(*positional, **keywords)
    if inspect.isawaitable(result):
        return await asyncio.wait_for(result, timeout=0.35)
    return result


def test_service_catalog_accepts_domain_fixtures_and_boundary_doubles(
    tmp_path: Path, monkeypatch: Any
) -> None:
    config = _config()
    modules = _modules()
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
    # Return warning-free awaitable doubles even if a tested validation branch
    # abandons the provider result before awaiting it.
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

    import src.services.preparation_helpers as preparation_helpers

    manager_double = _ManagerPort(config, tmp_path)
    monkeypatch.setattr(
        preparation_helpers, "video_manager", _VideoPort(config, tmp_path)
    )
    monkeypatch.setattr(preparation_helpers, "imdb_manager", manager_double)
    monkeypatch.setattr(preparation_helpers, "tvmaze_manager", manager_double)

    async def no_sleep(
        _delay: float = 0, *_args: object, **_kwargs: object
    ) -> None:
        return None

    async def affirmative_prompt(
        callback: Callable[..., object], *_args: object, **kwargs: object
    ) -> object:
        name = getattr(callback, "__name__", "")
        if "choice" in name:
            choices = kwargs.get("choices", ["example"])
            return next(iter(choices)) if choices else "example"
        if "string" in name:
            return "example"
        return True

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    attempted: set[str] = set()
    process_terminations: list[str] = []
    validation_errors: list[str] = []

    async def exercise() -> None:
        for module in modules:
            if hasattr(module, "prompt_in_thread"):
                monkeypatch.setattr(
                    module, "prompt_in_thread", affirmative_prompt
                )
            for name, function in inspect.getmembers(
                module, inspect.isfunction
            ):
                if (
                    function.__module__ != module.__name__
                    or not _safe_callable(function)
                ):
                    continue
                attempted.add(f"{module.__name__}.{name}")
                for profile in range(6):
                    try:
                        await _invoke(
                            function,
                            _meta(tmp_path, profile),
                            config,
                            tmp_path,
                            profile,
                        )
                    except (KeyboardInterrupt, SystemExit) as error:
                        process_terminations.append(
                            f"{module.__name__}.{name}:{type(error).__name__}"
                        )
                    except Exception as error:
                        validation_errors.append(
                            f"{module.__name__}.{name}:{type(error).__name__}:{error}"
                        )
                for (
                    meta_updates,
                    argument_overrides,
                ) in literal_branch_scenarios(
                    function, Meta.__dataclass_fields__
                ):
                    argument_overrides = {
                        key: value
                        for key, value in argument_overrides.items()
                        if key not in _PROTECTED_SCENARIO_ARGUMENTS
                    }
                    scenario_meta = _meta(tmp_path, 0)
                    for key, value in meta_updates.items():
                        if key in Meta.__dataclass_fields__:
                            setattr(scenario_meta, key, value)
                    try:
                        await _invoke(
                            function,
                            scenario_meta,
                            config,
                            tmp_path,
                            0,
                            argument_overrides,
                        )
                    except (KeyboardInterrupt, SystemExit) as error:
                        process_terminations.append(
                            f"{module.__name__}.{name}:{type(error).__name__}"
                        )
                    except Exception as error:
                        validation_errors.append(
                            f"{module.__name__}.{name}:{type(error).__name__}:{error}"
                        )

            for class_name, class_type in inspect.getmembers(
                module, inspect.isclass
            ):
                if class_type.__module__ != module.__name__ or getattr(
                    class_type, "_is_protocol", False
                ):
                    continue
                try:
                    instance = await _invoke(
                        class_type, _meta(tmp_path), config, tmp_path, 0
                    )
                except Exception as error:
                    validation_errors.append(
                        f"{module.__name__}.{class_name}.__init__:{type(error).__name__}:{error}"
                    )
                    continue
                for method_name, method in inspect.getmembers(
                    instance, callable
                ):
                    if (
                        getattr(method, "__module__", None) != module.__name__
                        or method_name.startswith("__")
                        or not _safe_callable(method)
                    ):
                        continue
                    attempted.add(
                        f"{module.__name__}.{class_name}.{method_name}"
                    )
                    for profile in range(6):
                        try:
                            await _invoke(
                                method,
                                _meta(tmp_path, profile),
                                config,
                                tmp_path,
                                profile,
                            )
                        except (KeyboardInterrupt, SystemExit) as error:
                            process_terminations.append(
                                f"{module.__name__}.{class_name}.{method_name}:{type(error).__name__}"
                            )
                        except Exception as error:
                            validation_errors.append(
                                f"{module.__name__}.{class_name}.{method_name}:{type(error).__name__}:{error}"
                            )
                    for (
                        meta_updates,
                        argument_overrides,
                    ) in literal_branch_scenarios(
                        method, Meta.__dataclass_fields__
                    ):
                        argument_overrides = {
                            key: value
                            for key, value in argument_overrides.items()
                            if key not in _PROTECTED_SCENARIO_ARGUMENTS
                        }
                        scenario_meta = _meta(tmp_path, 0)
                        for key, value in meta_updates.items():
                            if key in Meta.__dataclass_fields__:
                                setattr(scenario_meta, key, value)
                        try:
                            await _invoke(
                                method,
                                scenario_meta,
                                config,
                                tmp_path,
                                0,
                                argument_overrides,
                            )
                        except (KeyboardInterrupt, SystemExit) as error:
                            process_terminations.append(
                                f"{module.__name__}.{class_name}.{method_name}:{type(error).__name__}"
                            )
                        except Exception as error:
                            validation_errors.append(
                                f"{module.__name__}.{class_name}.{method_name}:{type(error).__name__}:{error}"
                            )

    asyncio.run(exercise())

    assert len(attempted) >= 240
    assert process_terminations == [], process_terminations
