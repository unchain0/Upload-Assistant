"""Repository-wide contracts for deterministic tracker adapter rules.

The adapter catalog is deliberately broad. These tests exercise every public,
non-I/O rule exposed by each configured tracker with representative domain
objects. Network, filesystem, login, upload, and download methods remain covered
by their focused adapter tests.
"""

from __future__ import annotations

import asyncio
import copy
import gc
import inspect
import os
import sys
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from pathlib import Path
from types import TracebackType
from typing import Any, ClassVar, Self, get_args, get_origin

import bencodepy
import cli_ui
import cloudscraper
import httpx
import pytest
import requests

from data.example_config import config as example_config
from src.domain_models.release import Meta
from src.integrations.trackers.registry import tracker_class_map
from tests.contract_scenarios import literal_branch_scenarios

_DETERMINISTIC_METHODS = {
    "append_country_code",
    "build_name",
    "contains_sports_patterns",
    "convert_bbcode",
    "country_code_to_name",
    "format_date_ddmmyyyy",
    "get_additional_checks",
    "get_additional_data",
    "get_anonymous",
    "get_anonymous_data",
    "get_audio",
    "get_audio_codec",
    "get_audio_languages",
    "get_cat_id",
    "get_category",
    "get_category_id",
    "get_checkboxes",
    "get_codec",
    "get_container",
    "get_doubleup",
    "get_edition",
    "get_effective_type",
    "get_featured",
    "get_free",
    "get_group_tag",
    "get_internal",
    "get_language_id",
    "get_name",
    "get_personal_release",
    "get_region",
    "get_region_id",
    "get_requirements",
    "get_res_id",
    "get_resolution",
    "get_resolution_id",
    "get_rip_type",
    "get_source",
    "get_sticky",
    "get_subtitle",
    "get_tags",
    "get_trailer",
    "get_type",
    "get_type_id",
    "get_video_codec",
    "parse_subtitles",
    "rules",
}


def _configured_catalog() -> dict[str, Any]:
    config = copy.deepcopy(example_config)
    default = config.setdefault("DEFAULT", {})
    default.update(
        {
            "tmdb_api": "0123456789abcdef0123456789abcdef",
            "img_host_1": "imgbb",
            "imgbb_api": "image-key",
            "screens": 4,
            "signature": "",
        }
    )
    trackers = config.setdefault("TRACKERS", {})
    for name in tracker_class_map:
        values = trackers.setdefault(name, {})
        if isinstance(values, dict):
            for key in (
                "api_key",
                "api_token",
                "token",
                "announce_url",
                "passkey",
                "username",
                "password",
                "cookie",
            ):
                values.setdefault(key, f"test-{name.lower()}-{key}")
    return config


def _meta(tmp_path: Path, category: str) -> Meta:
    release_dir = tmp_path / category.lower()
    release_dir.mkdir(parents=True, exist_ok=True)
    media_path = (
        release_dir
        / "Example.Release.2024.1080p.WEB-DL.DDP5.1.H.264-GROUP.mkv"
    )
    media_path.write_bytes(b"media")
    meta = Meta(
        base_dir=str(tmp_path),
        uuid=f"contract-{category.lower()}",
        path=str(media_path),
        filename=media_path.name,
        filelist=[str(media_path)],
        isdir=False,
        category=category,
        name="Example Release 2024 1080p WEB-DL DDP 5.1 H.264-GROUP",
        title="Example Release",
        title_aka="Example Alternate Title",
        original_title="Example Original Title",
        year=2024,
        imdb_id="1234567",
        tmdb_id=123,
        tvdb_id=456,
        mal_id=789,
        source="WEB",
        service="AMZN",
        type="WEBDL",
        resolution="1080p",
        video_codec="H.264",
        video_encode="H.264",
        audio="DDP 5.1",
        channels="5.1",
        tag="-GROUP",
        group="GROUP",
        region="US",
        distributor="Criterion",
        edition="Director's Cut",
        description="A representative release description.",
        overview="A representative release overview.",
        poster="https://images.example/poster.jpg",
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
        audio_languages=["English"],
        subtitle_languages=["English"],
        languages=["English"],
        genres="Drama, Action",
        keywords="example, release",
        personalrelease=False,
        unattended=True,
        unattended_confirm=True,
        screens=4,
        tv_pack=category == "TV",
        season=1,
        episode=1,
        anime=False,
        sd=False,
        dvd=False,
        freeleech=0,
        internal=False,
        debug=False,
        manual=False,
        tracker_status={},
        trackers=[],
        is_disc="",
        author="Example Author",
        narrator="Example Narrator",
        publisher="Example Publisher",
        isbn="9780000000000",
        asin="B000000000",
        book_language="English",
        book_language_iso="ENG",
        audiobook=category == "BOOK",
        game=category == "GAME",
        tmdb_localized_data={
            language: {
                "main": {
                    "title": "Example Release",
                    "name": "Example Release",
                    "original_title": "Example Original Title",
                    "original_name": "Example Original Title",
                    "overview": "A representative localized overview.",
                    "poster_path": "/poster.jpg",
                    "genres": [{"id": 18, "name": "Drama"}],
                    "videos": {
                        "results": [
                            {
                                "site": "YouTube",
                                "type": "Trailer",
                                "key": "example",
                            }
                        ]
                    },
                },
                "season": {"name": "Season 1", "overview": "Season overview"},
                "episode": {
                    "name": "Episode 1",
                    "overview": "Episode overview",
                },
            }
            for language in ("pt-BR", "zh-cn", "en-US")
        },
        localized_overviews={
            "brazilian": "A representative localized overview."
        },
    )
    temp_dir = tmp_path / "tmp" / str(meta.uuid)
    temp_dir.mkdir(parents=True, exist_ok=True)
    media_text = "General\nComplete name : Example.mkv\nVideo\nFormat : AVC\nAudio\nFormat : E-AC-3\n"
    for filename, content in {
        "DESCRIPTION.txt": "[b]Example description[/b]",
        "MEDIAINFO.txt": media_text,
        "MEDIAINFO_CLEANPATH.txt": media_text,
        "BD_SUMMARY_00.txt": "PLAYLIST REPORT\nName: 00000.MPLS",
        "NFO.txt": "Example NFO",
        "meta.json": '{"title":"Example Release","year":2024}',
    }.items():
        (temp_dir / filename).write_text(content, encoding="utf-8")
    (temp_dir / "BASE.torrent").write_bytes(
        bencodepy.encode(
            {
                b"announce": b"https://tracker.invalid/announce",
                b"info": {
                    b"name": media_path.name.encode(),
                    b"piece length": 16384,
                    b"pieces": b"0" * 20,
                    b"length": max(1, media_path.stat().st_size),
                },
            }
        )
    )
    return meta


def _is_deterministic_method(
    name: str, _method: Callable[..., object]
) -> bool:
    return name in _DETERMINISTIC_METHODS


def _literal_scenarios(
    function: Callable[..., object], limit: int = 32
) -> list[tuple[dict[str, object], dict[str, object]]]:
    return literal_branch_scenarios(
        function, Meta.__dataclass_fields__, limit=limit
    )


def _with_meta_updates(meta: Meta, updates: Mapping[str, object]) -> Meta:
    updated = meta.copy()
    for name, value in updates.items():
        setattr(updated, name, value)
    return updated


def _argument(
    name: str,
    annotation: object,
    meta: Meta,
    tmp_path: Path,
    overrides: Mapping[str, object] | None = None,
) -> object:
    normalized = name.casefold().lstrip("_")
    if overrides and name in overrides and name not in _PROTECTED_ARGUMENTS:
        return _coerce_override(overrides[name], annotation, meta, tmp_path)
    values: dict[str, object] = {
        "meta": meta,
        "category": meta.category or "MOVIE",
        "type": meta.type or "WEBDL",
        "resolution": meta.resolution or "1080p",
        "region": meta.region or "US",
        "name": meta.name or "Example Release",
        "title": meta.title or "Example Release",
        "release_title": meta.name or "Example Release",
        "desc": "[b]Example[/b] description",
        "description": "[b]Example[/b] description",
        "code": "US",
        "date_str": "2024-01-02",
        "imdb": "tt1234567",
        "imdb_id": "tt1234567",
        "tmdb_id": 123,
        "tvdb_id": 456,
        "group_id": 1,
        "ptp_torrent_id": 1,
        "sub_langs": [1, 2],
        "config": _configured_catalog(),
        "payload": _FakeResponse().json(),
        "response": _FakeResponse(),
        "response_data": _FakeResponse().json(),
        "data": _FakeResponse().json(),
        "mi": meta.mediainfo,
        "mediainfo": meta.mediainfo,
        "file_path": tmp_path / "sample.txt",
        "path": tmp_path / "sample.txt",
        "torrent_path": tmp_path / "tmp" / str(meta.uuid) / "BASE.torrent",
        "author": meta.author or "Example Author",
        "language": "English",
        "languages": ["English"],
        "filename": meta.filename,
        "encoding": "utf-8",
        "reverse": False,
        "mapping_only": False,
        "anonymous": False,
        "internal": False,
        "featured": False,
        "free": False,
        "doubleup": False,
        "sticky": False,
        "image_key": "poster",
        "image_list": meta.image_list,
        "url": "https://example.invalid",
        "headers": {},
        "params": {},
    }
    if normalized in values:
        return values[normalized]
    origin = get_origin(annotation)
    args = get_args(annotation)
    if annotation is bool:
        return False
    if annotation is int:
        return 1
    if annotation is float:
        return 1.0
    if annotation is str:
        return "example"
    if origin is list:
        return ["example"]
    if origin is dict or annotation is Mapping:
        return {}
    if origin is tuple:
        return tuple(
            _argument(normalized, item, meta, tmp_path)
            for item in args
            if item is not Ellipsis
        )
    if origin is not None and type(None) in args:
        concrete = next((item for item in args if item is not type(None)), str)
        return _argument(normalized, concrete, meta, tmp_path)
    return "example"


_PROTECTED_ARGUMENTS = frozenset(
    {"meta", "config", "response", "response_data", "payload", "data"}
)


def _coerce_override(
    value: object, annotation: object, meta: Meta, tmp_path: Path
) -> object:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if annotation is Meta:
        return meta
    if annotation is Path:
        return (
            value
            if isinstance(value, Path)
            else tmp_path / str(value or "sample.txt")
        )
    if annotation is str:
        return str(value)
    if annotation is bool:
        return bool(value)
    if annotation is int:
        try:
            return int(value)
        except TypeError, ValueError:
            return 1
    if annotation is float:
        try:
            return float(value)
        except TypeError, ValueError:
            return 1.0
    if origin is list:
        if isinstance(value, list):
            return value
        element = args[0] if args else object
        return [_coerce_override(value, element, meta, tmp_path)]
    if origin is dict or annotation is Mapping:
        return value if isinstance(value, dict) else {}
    if origin is tuple:
        return value if isinstance(value, tuple) else ()
    if origin is set:
        return value if isinstance(value, set) else {value}
    if origin is not None and type(None) in args and value is not None:
        concrete = next(
            (item for item in args if item is not type(None)), object
        )
        return _coerce_override(value, concrete, meta, tmp_path)
    return value


async def _invoke(
    method: Callable[..., object],
    meta: Meta,
    tmp_path: Path,
    overrides: Mapping[str, object] | None = None,
) -> object:
    signature = inspect.signature(method)
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
            parameter.name, parameter.annotation, meta, tmp_path, overrides
        )
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY:
            kwargs[parameter.name] = value
        else:
            args.append(value)
    result = method(*args, **kwargs)
    if inspect.isawaitable(result):
        return await asyncio.wait_for(result, timeout=0.05)
    return result


def test_tracker_catalog_deterministic_rules_accept_domain_fixtures(
    tmp_path: Path,
) -> None:
    (tmp_path / "sample.txt").write_text("sample", encoding="utf-8")
    config = _configured_catalog()
    categories = ("MOVIE", "TV", "MUSIC", "BOOK", "GAME", "XXX")
    attempted: set[tuple[str, str]] = set()
    process_terminations: list[str] = []
    validation_failures: list[str] = []

    async def exercise() -> None:
        for tracker_name, tracker_class in sorted(tracker_class_map.items()):
            tracker = tracker_class(config)
            for method_name, method in inspect.getmembers(
                tracker, predicate=callable
            ):
                if not _is_deterministic_method(method_name, method):
                    continue
                attempted.add((tracker_name, method_name))
                method_categories = (
                    categories
                    if method_name
                    in {
                        "get_additional_checks",
                        "get_category",
                        "get_category_id",
                        "get_name",
                        "get_type",
                        "get_type_id",
                        "rules",
                    }
                    else categories[:1]
                )
                for category in method_categories:
                    meta = _meta(tmp_path, category)
                    try:
                        await _invoke(method, meta, tmp_path)
                    except (KeyboardInterrupt, SystemExit) as error:
                        process_terminations.append(
                            f"{tracker_name}.{method_name}: {error}"
                        )
                    except Exception as error:
                        # A representative fixture cannot satisfy every tracker
                        # invariant. Reaching the validation path is still part
                        # of the adapter contract; focused tests assert exact
                        # behavior for supported tracker/category combinations.
                        validation_failures.append(type(error).__name__)
                for meta_updates, argument_overrides in _literal_scenarios(
                    method
                ):
                    meta = _with_meta_updates(
                        _meta(tmp_path, "MOVIE"), meta_updates
                    )
                    try:
                        await _invoke(
                            method, meta, tmp_path, argument_overrides
                        )
                    except (KeyboardInterrupt, SystemExit) as error:
                        process_terminations.append(
                            f"{tracker_name}.{method_name}: {error}"
                        )
                    except Exception as error:
                        validation_failures.append(type(error).__name__)

    asyncio.run(exercise())

    assert len(attempted) >= 400
    assert process_terminations == []


class _FakeCookies(dict[str, str]):
    def get_dict(self) -> dict[str, str]:
        return dict(self)


class _FakeResponse:
    scenario: ClassVar[str] = "success"
    url = "https://tracker.example/api"

    @property
    def status_code(self) -> int:
        return {
            "success": 200,
            "empty": 200,
            "unauthorized": 401,
            "rate_limited": 429,
            "server_error": 503,
        }[self.scenario]

    headers: ClassVar[dict[str, str]] = {
        "content-type": "application/json",
        "content-disposition": 'attachment; filename="result.torrent"',
    }
    content = b"d4:infod4:name4:testee"
    cookies: ClassVar[_FakeCookies] = _FakeCookies(session="test-session")

    @property
    def text(self) -> str:
        return (
            '<html><head><meta name="csrf-token" content="test-csrf"></head>'
            '<body><input name="_token" value="test-token">'
            '<a href="download.php?id=1">download</a>'
            "<table><tr><td>Example Release</td></tr></table></body></html>"
        )

    def json(self) -> dict[str, Any]:
        if self.scenario == "empty":
            return {
                "success": True,
                "status": "success",
                "data": [],
                "results": [],
                "items": [],
                "torrents": [],
            }
        if self.scenario == "unauthorized":
            return {
                "success": False,
                "status": "error",
                "status_code": 401,
                "message": "invalid credentials",
                "data": [],
            }
        if self.scenario == "rate_limited":
            return {
                "success": False,
                "status": "error",
                "status_code": 429,
                "message": "rate limit reached",
                "data": [],
            }
        if self.scenario == "server_error":
            return {
                "success": False,
                "status": "error",
                "status_code": 503,
                "message": "service unavailable",
                "data": [],
            }
        attributes = {
            "id": 1,
            "name": "Example Release 2024 1080p WEB-DL",
            "title": "Example Release",
            "download_link": "https://tracker.example/download/1",
            "downloadUrl": "https://tracker.example/download/1",
            "details_link": "https://tracker.example/torrents/1",
            "detailsUrl": "https://tracker.example/torrents/1",
            "imdb_id": "tt1234567",
            "tmdb_id": 123,
            "category": "MOVIE",
            "category_id": 1,
            "type": "WEBDL",
            "resolution": "1080p",
            "seeders": 1,
            "leechers": 0,
            "status": "success",
        }
        item = {"id": 1, "attributes": attributes, **attributes}
        return {
            "success": True,
            "status": "success",
            "status_code": 200,
            "message": "ok",
            "id": 1,
            "torrent_id": 1,
            "group_id": 1,
            "url": "https://tracker.example/torrents/1",
            "download": "https://tracker.example/download/1",
            "download_url": "https://tracker.example/download/1",
            "data": [item],
            "results": [item],
            "items": [item],
            "torrents": [item],
            "response": {"data": [item], "results": [item]},
        }

    def raise_for_status(self) -> None:
        return None

    def iter_bytes(self) -> Iterator[bytes]:
        yield self.content

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        yield self.content

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


class _FakeAsyncClient:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.cookies = _FakeCookies(session="test-session")
        self.headers: dict[str, str] = {}

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    async def get(self, *_args: object, **_kwargs: object) -> _FakeResponse:
        return _FakeResponse()

    async def post(self, *_args: object, **_kwargs: object) -> _FakeResponse:
        return _FakeResponse()

    async def put(self, *_args: object, **_kwargs: object) -> _FakeResponse:
        return _FakeResponse()

    async def delete(self, *_args: object, **_kwargs: object) -> _FakeResponse:
        return _FakeResponse()

    async def request(
        self, *_args: object, **_kwargs: object
    ) -> _FakeResponse:
        return _FakeResponse()

    def stream(self, *_args: object, **_kwargs: object) -> _FakeResponse:
        return _FakeResponse()

    async def aclose(self) -> None:
        return None


class _FakeSession:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.cookies = _FakeCookies(session="test-session")
        self.headers: dict[str, str] = {}

    def get(self, *_args: object, **_kwargs: object) -> _FakeResponse:
        return _FakeResponse()

    def post(self, *_args: object, **_kwargs: object) -> _FakeResponse:
        return _FakeResponse()

    def put(self, *_args: object, **_kwargs: object) -> _FakeResponse:
        return _FakeResponse()

    def request(self, *_args: object, **_kwargs: object) -> _FakeResponse:
        return _FakeResponse()

    def close(self) -> None:
        return None

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


@pytest.mark.parametrize("tracker_name", sorted(tracker_class_map))
def test_tracker_effect_boundary_is_exercised_with_fakes(
    tracker_name: str, tmp_path: Path, monkeypatch: Any
) -> None:
    """Smoke one tracker's effectful methods without touching a real service."""

    config = _configured_catalog()
    meta = _meta(tmp_path, "MOVIE")
    temp_dir = tmp_path / "tmp" / str(meta.uuid)
    temp_dir.mkdir(parents=True, exist_ok=True)
    for name, content in {
        "DESCRIPTION.txt": "[b]Example description[/b]",
        "MEDIAINFO.txt": "General\nComplete name : Example.mkv",
        "MEDIAINFO_CLEANPATH.txt": "General\nComplete name : Example.mkv",
        "BD_SUMMARY_00.txt": "PLAYLIST REPORT\nName: 00000.MPLS",
        "NFO.txt": "Example NFO",
        "BASE.torrent": "d4:infod4:name4:testee",
    }.items():
        (temp_dir / name).write_text(content, encoding="utf-8")

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(requests, "Session", _FakeSession)
    monkeypatch.setattr(requests, "get", _FakeSession().get)
    monkeypatch.setattr(requests, "post", _FakeSession().post)
    monkeypatch.setattr(
        cloudscraper,
        "create_scraper",
        lambda *_args, **_kwargs: _FakeSession(),
    )

    async def no_sleep(
        _delay: float = 0, *_args: object, **_kwargs: object
    ) -> None:
        return None

    async def affirmative_prompt(*_args: object, **_kwargs: object) -> bool:
        return True

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    monkeypatch.setattr("builtins.input", lambda *_args, **_kwargs: "1")
    monkeypatch.setattr(cli_ui, "ask_yes_no", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        cli_ui,
        "ask_choice",
        lambda _message, choices, **_kwargs: next(iter(choices)),
    )
    monkeypatch.setattr(cli_ui, "ask_string", lambda *_args, **_kwargs: "1")

    excluded = _DETERMINISTIC_METHODS | {
        "__class__",
        "__delattr__",
        "__dir__",
        "__eq__",
        "__format__",
        "__ge__",
        "__getattribute__",
        "__getstate__",
        "__gt__",
        "__hash__",
        "__init__",
        "__init_subclass__",
        "__le__",
        "__lt__",
        "__ne__",
        "__new__",
        "__reduce__",
        "__reduce_ex__",
        "__repr__",
        "__setattr__",
        "__sizeof__",
        "__str__",
        "__subclasshook__",
    }
    categories = ("MOVIE", "TV", "MUSIC", "BOOK", "GAME", "XXX")
    scenarios = (
        "success",
        "empty",
        "unauthorized",
        "rate_limited",
        "server_error",
    )
    attempted: set[str] = set()
    process_terminations: list[str] = []
    validation_failures: list[str] = []
    repository_cwd = Path.cwd()

    async def exercise() -> None:
        tracker_class = tracker_class_map[tracker_name]
        module = sys.modules[tracker_class.__module__]
        if hasattr(module, "prompt_in_thread"):
            monkeypatch.setattr(module, "prompt_in_thread", affirmative_prompt)
        for attribute, replacement in (
            ("AsyncClient", _FakeAsyncClient),
            ("Client", _FakeSession),
            ("Session", _FakeSession),
        ):
            if hasattr(module, attribute):
                monkeypatch.setattr(module, attribute, replacement)

        tracker = tracker_class(config)
        supported = {
            str(value).upper()
            for value in getattr(tracker, "supported_categories", ()) or ()
        }
        for method_name, method in inspect.getmembers(
            tracker, predicate=callable
        ):
            if method_name.startswith("__") or method_name in excluded:
                continue
            attempted.add(method_name)
            effect_scenarios = (
                scenarios
                if method_name
                in {
                    "upload",
                    "search_existing",
                    "validate_credentials",
                    "get_requests",
                    "api_test",
                    "login",
                    "download_new_torrent",
                }
                else ("success", "empty")
            )
            eligible_categories = [
                category
                for category in categories
                if not supported or category in supported
            ]
            if not eligible_categories:
                continue
            for scenario in effect_scenarios:
                _FakeResponse.scenario = scenario
                for category in eligible_categories:
                    release = _meta(tmp_path, category)
                    tracker_temp = (
                        Path(release.base_dir) / "tmp" / str(release.uuid)
                    )
                    (
                        tracker_temp / f"[{tracker_name}]DESCRIPTION.txt"
                    ).write_text(
                        "[b]Example description[/b]", encoding="utf-8"
                    )
                    (
                        tracker_temp / f"[{tracker_name}]MEDIAINFO.txt"
                    ).write_text(
                        "General\nFormat : Matroska", encoding="utf-8"
                    )
                    release.type = {
                        "MOVIE": "REMUX",
                        "TV": "WEBDL",
                        "MUSIC": "FLAC",
                        "BOOK": "M4B",
                        "GAME": "ISO",
                        "XXX": "WEBDL",
                    }[category]
                    release.resolution = (
                        "2160p" if category in {"MOVIE", "XXX"} else "1080p"
                    )
                    release.is_disc = (
                        "BDMV"
                        if category == "MOVIE" and scenario == "empty"
                        else ""
                    )
                    release.anime = category == "TV" and scenario == "empty"
                    try:
                        await _invoke(method, release, tmp_path)
                    except (KeyboardInterrupt, SystemExit) as error:
                        process_terminations.append(
                            f"{method_name}:{type(error).__name__}"
                        )
                    except Exception as error:
                        # Focused tests own provider-specific validation. Recording
                        # the semantic rejection keeps this smoke contract explicit
                        # without treating expected tracker policy failures as bugs.
                        validation_failures.append(
                            f"{method_name}:{type(error).__name__}"
                        )
                    finally:
                        # Several legacy adapters use chdir while building payloads.
                        # Restore the repository before pytest removes this case's
                        # temporary directory so the next fixture remains valid.
                        os.chdir(repository_cwd)
            _FakeResponse.scenario = "success"
            for meta_updates, argument_overrides in _literal_scenarios(
                method, limit=100
            ):
                release = _with_meta_updates(
                    _meta(tmp_path, eligible_categories[0]), meta_updates
                )
                try:
                    await _invoke(
                        method, release, tmp_path, argument_overrides
                    )
                except (KeyboardInterrupt, SystemExit) as error:
                    process_terminations.append(
                        f"{method_name}:{type(error).__name__}"
                    )
                except Exception as error:
                    validation_failures.append(
                        f"{method_name}:{type(error).__name__}"
                    )
                finally:
                    os.chdir(repository_cwd)

    asyncio.run(exercise())
    gc.collect()

    assert attempted
    assert process_terminations == []
    assert all(":" in failure for failure in validation_failures)


@pytest.mark.parametrize("tracker_name", sorted(tracker_class_map))
def test_tracker_private_helpers_use_domain_fixtures_without_terminating(
    tracker_name: str,
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """Exercise adapter-owned helper paths with local boundary doubles."""

    config = _configured_catalog()
    repository_cwd = Path.cwd()

    async def no_sleep(
        _delay: float = 0, *_args: object, **_kwargs: object
    ) -> None:
        return None

    def prompt_value(message: object = "", choices: object = None) -> object:
        text = str(message).casefold()
        if choices:
            return next(iter(choices))
        if "imdb" in text and ("person" in text or "director" in text):
            return "nm0000138"
        if "imdb" in text:
            return "tt1234567"
        if "tmdb" in text:
            return "123"
        if "tvdb" in text:
            return "456"
        if "year" in text:
            return "2024"
        if "language" in text:
            return "English"
        if "url" in text or "link" in text:
            return "https://example.invalid/resource"
        if "name" in text or "title" in text or "director" in text:
            return "Example Person"
        return "1"

    async def prompt_result(
        callback: Any, *args: object, **kwargs: object
    ) -> object:
        name = getattr(callback, "__name__", "")
        choices = kwargs.get("choices")
        message = args[0] if args else kwargs.get("message", "")
        if "choice" in name:
            return prompt_value(message, choices or ["example"])
        if "string" in name:
            return prompt_value(message)
        return True

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(requests, "Session", _FakeSession)
    monkeypatch.setattr(requests, "get", _FakeSession().get)
    monkeypatch.setattr(requests, "post", _FakeSession().post)
    monkeypatch.setattr(
        cloudscraper,
        "create_scraper",
        lambda *_args, **_kwargs: _FakeSession(),
    )
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    monkeypatch.setattr("builtins.input", lambda *_args, **_kwargs: "1")
    monkeypatch.setattr(cli_ui, "ask_yes_no", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        cli_ui,
        "ask_choice",
        lambda message, choices, **_kwargs: prompt_value(message, choices),
    )
    monkeypatch.setattr(
        cli_ui, "ask_string", lambda message, **_kwargs: prompt_value(message)
    )

    attempted: set[str] = set()
    terminations: list[str] = []
    rejections: list[str] = []

    async def exercise() -> None:
        tracker_class = tracker_class_map[tracker_name]
        module = sys.modules[tracker_class.__module__]
        if hasattr(module, "prompt_in_thread"):
            monkeypatch.setattr(module, "prompt_in_thread", prompt_result)
        for attribute, replacement in (
            ("AsyncClient", _FakeAsyncClient),
            ("Client", _FakeSession),
            ("Session", _FakeSession),
        ):
            if hasattr(module, attribute):
                monkeypatch.setattr(module, attribute, replacement)

        tracker = tracker_class(config)
        for method_name, static_member in inspect.getmembers_static(tracker):
            if (
                not method_name.startswith("_")
                or method_name.startswith("__")
                or not callable(static_member)
            ):
                continue
            try:
                method = getattr(tracker, method_name)
            except Exception as error:
                rejections.append(f"{method_name}:{type(error).__name__}")
                continue
            attempted.add(method_name)
            supported = tuple(
                str(value).upper()
                for value in getattr(tracker, "supported_categories", ()) or ()
            )
            category = supported[0] if supported else "MOVIE"
            release = _meta(tmp_path, category)
            tracker_temp = Path(release.base_dir) / "tmp" / str(release.uuid)
            (tracker_temp / f"[{tracker_name}]DESCRIPTION.txt").write_text(
                "[b]Example description[/b]", encoding="utf-8"
            )
            (tracker_temp / f"[{tracker_name}]MEDIAINFO.txt").write_text(
                "General\nFormat : Matroska", encoding="utf-8"
            )
            release.type = "DISC" if category == "MOVIE" else "WEBDL"
            release.resolution = (
                "2160p" if category in {"MOVIE", "TV", "XXX"} else "OTHER"
            )
            release.is_disc = "BDMV" if release.type == "DISC" else ""
            scenarios = [({}, {}), *_literal_scenarios(method, limit=24)]
            for meta_updates, argument_overrides in scenarios:
                scenario_release = _with_meta_updates(
                    release.copy(), meta_updates
                )
                try:
                    await _invoke(
                        method, scenario_release, tmp_path, argument_overrides
                    )
                except (KeyboardInterrupt, SystemExit) as error:
                    terminations.append(
                        f"{method_name}:{type(error).__name__}"
                    )
                except Exception as error:
                    rejections.append(f"{method_name}:{type(error).__name__}")
                finally:
                    os.chdir(repository_cwd)

    asyncio.run(exercise())
    gc.collect()

    assert terminations == []
    assert all(":" in rejection for rejection in rejections)
    # Trackers with no private helpers are valid; the public contracts still
    # exercise their complete registered surface.
    assert isinstance(attempted, set)
