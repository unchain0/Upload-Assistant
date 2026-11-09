"""Private-helper branch matrix for external API integrations.

Every external effect is replaced with deterministic protocol doubles. Focused
provider tests still assert exact schemas; this matrix guarantees that helper
branches and error translations remain measurable without touching the network.
"""

from __future__ import annotations

import asyncio
import copy
import importlib
import inspect
import os
import pkgutil
from collections.abc import (
    AsyncIterator,
    Callable,
    Iterator,
    Mapping,
    Sequence,
)
from pathlib import Path
from types import ModuleType
from typing import Any, ClassVar, Self, get_args, get_origin, get_type_hints

import httpx
import pytest
import requests

from data.example_config import config as example_config
from src.domain_models.release import Meta
from src.integrations import external_apis
from tests.contract_scenarios import literal_branch_scenarios


class _Response:
    scenario = "success"
    request = httpx.Request("GET", "https://api.invalid")
    url = "https://api.invalid/result"
    headers: ClassVar[dict[str, str]] = {
        "content-type": "application/json",
        "content-length": "2",
    }

    @property
    def status_code(self) -> int:
        return {
            "success": 200,
            "empty": 200,
            "not_found": 404,
            "unauthorized": 401,
            "rate_limited": 429,
            "server_error": 503,
        }[type(self).scenario]

    @property
    def text(self) -> str:
        return "ok" if self.status_code == 200 else "error"

    @property
    def content(self) -> bytes:
        return b"{}"

    def json(self) -> Any:
        if type(self).scenario == "empty":
            return {}
        return {
            "success": True,
            "status": "ok",
            "status_code": 1,
            "id": 123,
            "name": "Example",
            "title": "Example",
            "year": 2026,
            "release_date": "2026-01-02",
            "first_air_date": "2026-01-02",
            "overview": "Overview",
            "description": "Description",
            "imdb_id": "tt1234567",
            "tmdb_id": 123,
            "tvdb_id": 456,
            "tvmaze_id": 789,
            "mal_id": 321,
            "externals": {"imdb": "tt1234567", "thetvdb": 456},
            "genres": [{"id": 1, "name": "Drama"}],
            "keywords": {"keywords": [{"id": 1, "name": "drama"}]},
            "images": {"posters": [], "backdrops": [], "logos": []},
            "poster_path": "/poster.jpg",
            "backdrop_path": "/backdrop.jpg",
            "profile_path": "/profile.jpg",
            "results": [],
            "result": {},
            "data": [],
            "items": [],
            "totalItems": 0,
            "torrents": {},
            "show": {
                "id": 123,
                "name": "Example",
                "externals": {"thetvdb": 456},
            },
            "series": {
                "id": 123,
                "title": "Example",
                "tvdbId": 456,
                "imdbId": "tt1234567",
            },
            "movieFile": {
                "originalFilePath": "/media/example.mkv",
                "releaseGroup": "GROUP",
            },
            "musicInfo": {"artists": [{"name": "Artist"}]},
            "group": {"id": 1, "name": "Album"},
            "torrent": {"id": 1},
            "access_token": "token",
            "expires_in": 3600,
        }

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "API error",
                request=self.request,
                response=httpx.Response(
                    self.status_code, request=self.request
                ),
            )

    def iter_bytes(self) -> Iterator[bytes]:
        yield self.content

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        yield self.content

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


class _AsyncClient:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()

    async def post(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()

    async def put(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()

    async def delete(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()

    async def request(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()

    async def aclose(self) -> None:
        return None


class _Session:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    def get(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()

    def post(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()

    def request(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _Universal(dict[str, Any]):
    def __init__(self, **values: Any) -> None:
        super().__init__(values)
        self.__dict__.update(values)

    def __getattr__(self, name: str) -> Any:
        if name.startswith(("is_", "has_", "should_")):
            return False
        return _Universal()

    def __call__(self, *_args: object, **_kwargs: object) -> _Universal:
        return _Universal()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    async def __aenter__(self) -> Self:
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


def _modules() -> list[ModuleType]:
    return [
        importlib.import_module(info.name)
        for info in pkgutil.iter_modules(
            external_apis.__path__, f"{external_apis.__name__}."
        )
    ]


def _meta(tmp_path: Path, profile: int = 0) -> Meta:
    media = tmp_path / "Example.2026.S01E02.1080p.WEB-DL-GROUP.mkv"
    media.write_bytes(b"media")
    return Meta(
        base_dir=str(tmp_path),
        uuid=f"api-{profile}",
        path=str(media),
        filename=media.name,
        filelist=[str(media)],
        category=("MOVIE", "TV", "MUSIC", "BOOK", "GAME")[profile % 5],
        type="WEBDL",
        source="WEB",
        resolution="1080p",
        title="Example",
        name="Example.2026.S01E02.1080p.WEB-DL-GROUP",
        year=2026,
        search_year=2026,
        imdb_id=1234567,
        tmdb_id=123,
        tvdb_id=456,
        tvmaze_id=789,
        mal_id=321,
        season=1,
        episode=2,
        season_int=1,
        episode_int=2,
        tag="-GROUP",
        group="GROUP",
        mediainfo={"media": {"track": []}},
        imdb_info={"title": "Example", "year": 2026, "genres": ["Drama"]},
        tmdb_data={"id": 123, "title": "Example"},
        trackers=["AITHER"],
        tracker_ids={},
        debug=profile == 4,
        unattended=True,
        image_list=[],
    )


def _config() -> dict[str, Any]:
    config = copy.deepcopy(example_config)
    default = config.setdefault("DEFAULT", {})
    default.update(
        {
            "tmdb_api": "tmdb-key",
            "tmdb_access_token": "token",
            "tvdb_v4_api_key": "tvdb-key",
            "tvmaze_api_key": "tvmaze-key",
            "imdb_api_key": "imdb-key",
            "google_books_api_key": "books-key",
            "musicbrainz_enabled": True,
            "discogs_token": "discogs-key",
            "igdb_client_id": "igdb-id",
            "igdb_client_secret": "igdb-secret",
            "mam_id": "mam-cookie",
            "btn_api": "x" * 26,
            "bhd_api": "bhd-key",
            "bhd_rss_key": "rss-key",
            "radarr_api_key": "key",
            "radarr_url": "https://radarr.invalid",
            "sonarr_api_key": "key",
            "sonarr_url": "https://sonarr.invalid",
        }
    )
    return config


_MISSING = object()


def _named_values(
    meta: Meta, tmp_path: Path, profile: int
) -> dict[str, object]:
    response_data = _Response().json()
    return {
        "meta": meta,
        "config": _config(),
        "configuration": _config(),
        "base_dir": str(tmp_path),
        "path": str(meta.path),
        "filename": meta.filename,
        "file_path": str(meta.path),
        "url": "https://api.invalid/resource",
        "endpoint": "resource",
        "api_key": "key",
        "access_token": "token",
        "token": "token",
        "client_id": "client-id",
        "client_secret": "client-secret",
        "rss_key": "rss-key",
        "cookie": "cookie",
        "query": "Example",
        "search_term": "Example",
        "title": "Example",
        "name": "Example",
        "year": 2026,
        "search_year": 2026,
        "imdb_id": "tt1234567",
        "tmdb_id": 123,
        "tvdb_id": 456,
        "tvmaze_id": 789,
        "mal_id": 321,
        "season": 1,
        "episode": 2,
        "torrent_id": 1,
        "group_id": 1,
        "release_id": 1,
        "work_id": "OL123W",
        "isbn": "9780306406157",
        "asin": "B012345678",
        "steam_id": "123",
        "game_id": "123",
        "language": "en",
        "category": meta.category,
        "media_type": "movie",
        "data": response_data,
        "payload": response_data,
        "response": _Response(),
        "client": _AsyncClient(),
        "session": _Session(),
        "headers": {"Authorization": "Bearer token"},
        "params": {"query": "Example"},
        "details": response_data,
        "item": response_data,
        "items": [response_data],
        "results": [response_data],
        "identifiers": {"imdb": "tt1234567"},
        "fields": ["title", "year"],
        "cache": _Universal(),
        "repository": _Universal(),
        "timeout": 1.0,
        "page": 1,
        "limit": 10,
        "debug": profile == 4,
        "enabled": True,
    }


def _primitive_value(annotation: object, meta: Meta, profile: int) -> object:
    if annotation in {inspect.Parameter.empty, Any}:
        return _Universal()
    if annotation is Path:
        return Path(meta.path)
    factories: dict[object, Callable[[], object]] = {
        bool: lambda: bool(profile % 2),
        int: lambda: 1,
        float: lambda: 1.0,
        str: lambda: "example",
    }
    factory = factories.get(annotation)
    if factory is None:
        return _MISSING
    return factory()


def _collection_value(origin: object) -> object:
    factories: dict[object, Callable[[], object]] = {
        list: list,
        Sequence: list,
        dict: dict,
        Mapping: dict,
        set: set,
    }
    factory = factories.get(origin)
    if factory is None:
        return _MISSING
    return factory()


def _tuple_value(
    key: str,
    args: tuple[object, ...],
    meta: Meta,
    tmp_path: Path,
    profile: int,
) -> tuple[object, ...]:
    return tuple(
        _value(key, item, meta, tmp_path, profile)
        for item in args
        if item is not Ellipsis
    )


def _optional_value(
    key: str,
    args: tuple[object, ...],
    meta: Meta,
    tmp_path: Path,
    profile: int,
) -> object:
    concrete = next((item for item in args if item is not type(None)), str)
    return _value(key, concrete, meta, tmp_path, profile)


def _composite_value(
    key: str,
    annotation: object,
    meta: Meta,
    tmp_path: Path,
    profile: int,
) -> object:
    origin = get_origin(annotation)
    args = get_args(annotation)
    collection = _collection_value(origin)
    if collection is not _MISSING:
        return collection
    if origin is tuple:
        return _tuple_value(key, args, meta, tmp_path, profile)
    if origin is None or type(None) not in args:
        return _Universal()
    return _optional_value(key, args, meta, tmp_path, profile)


def _value(
    name: str, annotation: object, meta: Meta, tmp_path: Path, profile: int
) -> object:
    key = name.casefold().lstrip("_")
    named = _named_values(meta, tmp_path, profile)
    if key in named:
        return named[key]
    primitive = _primitive_value(annotation, meta, profile)
    if primitive is not _MISSING:
        return primitive
    return _composite_value(key, annotation, meta, tmp_path, profile)


def _safe_type_hints(target: object) -> dict[str, Any]:
    try:
        return get_type_hints(target)
    except NameError, TypeError:
        return {}


def _include_parameter(
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


def _parameter_value(
    parameter: inspect.Parameter,
    hints: Mapping[str, Any],
    overrides: Mapping[str, object],
    meta: Meta,
    tmp_path: Path,
    profile: int,
) -> object:
    if parameter.name in overrides:
        return overrides[parameter.name]
    return _value(
        parameter.name,
        hints.get(parameter.name, parameter.annotation),
        meta,
        tmp_path,
        profile,
    )


def _invocation_arguments(
    function: Callable[..., object],
    meta: Meta,
    tmp_path: Path,
    profile: int,
    overrides: Mapping[str, object],
) -> tuple[list[object], dict[str, object]]:
    target = function.__init__ if inspect.isclass(function) else function
    hints = _safe_type_hints(target)
    positional: list[object] = []
    keywords: dict[str, object] = {}
    for parameter in inspect.signature(function).parameters.values():
        if not _include_parameter(parameter, overrides):
            continue
        value = _parameter_value(
            parameter, hints, overrides, meta, tmp_path, profile
        )
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY:
            keywords[parameter.name] = value
        else:
            positional.append(value)
    return positional, keywords


async def _resolved_result(result: object) -> object:
    if inspect.isawaitable(result):
        return await asyncio.wait_for(result, timeout=0.5)
    return result


async def _invoke(
    function: Callable[..., object],
    meta: Meta,
    tmp_path: Path,
    profile: int,
    overrides: Mapping[str, object] | None = None,
) -> object:
    resolved_overrides = overrides or {}
    positional, keywords = _invocation_arguments(
        function, meta, tmp_path, profile, resolved_overrides
    )
    return await _resolved_result(function(*positional, **keywords))


def _scenario_rows(
    function: Callable[..., object],
) -> list[tuple[str, dict[str, object], dict[str, object]]]:
    scenarios: list[tuple[str, dict[str, object], dict[str, object]]] = [
        ("success", {}, {})
    ]
    scenarios.extend(
        ("success", meta_updates, argument_updates)
        for meta_updates, argument_updates in literal_branch_scenarios(
            function, Meta.__dataclass_fields__, limit=128
        )
    )
    scenarios.extend(
        (scenario, {}, {})
        for scenario in (
            "empty",
            "not_found",
            "unauthorized",
            "rate_limited",
            "server_error",
        )
    )
    return scenarios


def _apply_meta_updates(meta: Meta, updates: Mapping[str, object]) -> None:
    for key, value in updates.items():
        if key in Meta.__dataclass_fields__:
            setattr(meta, key, value)


async def _run_scenario(
    qualified: str,
    function: Callable[..., object],
    tmp_path: Path,
    repository: Path,
    profile: int,
    scenario: str,
    meta_updates: Mapping[str, object],
    argument_updates: Mapping[str, object],
    process_terminations: list[str],
    expected_rejections: list[str],
) -> None:
    _Response.scenario = scenario
    meta = _meta(tmp_path, profile % 5)
    _apply_meta_updates(meta, meta_updates)
    try:
        await _invoke(function, meta, tmp_path, profile % 5, argument_updates)
    except (KeyboardInterrupt, SystemExit) as error:
        process_terminations.append(f"{qualified}:{type(error).__name__}")
    except Exception as error:
        expected_rejections.append(f"{qualified}:{type(error).__name__}")
    finally:
        os.chdir(repository)


async def _invoke_scenarios(
    qualified: str,
    function: Callable[..., object],
    tmp_path: Path,
    repository: Path,
    attempted: set[str],
    process_terminations: list[str],
    expected_rejections: list[str],
) -> None:
    attempted.add(qualified)
    for profile, row in enumerate(_scenario_rows(function)):
        scenario, meta_updates, argument_updates = row
        await _run_scenario(
            qualified,
            function,
            tmp_path,
            repository,
            profile,
            scenario,
            meta_updates,
            argument_updates,
            process_terminations,
            expected_rejections,
        )


def _patch_module_clients(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    for attribute, replacement in (
        ("AsyncClient", _AsyncClient),
        ("Client", _Session),
        ("Session", _Session),
    ):
        if hasattr(module, attribute):
            monkeypatch.setattr(module, attribute, replacement)


def _module_functions(
    module: ModuleType,
) -> list[tuple[str, Callable[..., object]]]:
    return [
        (name, function)
        for name, function in inspect.getmembers(module, inspect.isfunction)
        if function.__module__ == module.__name__ and not name.startswith("__")
    ]


def _module_classes(module: ModuleType) -> list[tuple[str, type[Any]]]:
    return [
        (name, class_type)
        for name, class_type in inspect.getmembers(module, inspect.isclass)
        if class_type.__module__ == module.__name__
    ]


async def _instantiate_class(
    module: ModuleType,
    class_name: str,
    class_type: type[Any],
    tmp_path: Path,
    expected_rejections: list[str],
) -> object | None:
    try:
        return await _invoke(class_type, _meta(tmp_path), tmp_path, 0)
    except Exception as error:
        expected_rejections.append(
            f"{module.__name__}.{class_name}.__init__:{type(error).__name__}"
        )
        return None


def _instance_method(
    instance: object,
    module: ModuleType,
    class_name: str,
    method_name: str,
    member: object,
    expected_rejections: list[str],
) -> Callable[..., object] | None:
    if method_name.startswith("__") or not callable(member):
        return None
    try:
        return getattr(instance, method_name)
    except Exception as error:
        expected_rejections.append(
            f"{module.__name__}.{class_name}.{method_name}:{type(error).__name__}"
        )
        return None


async def _exercise_instance(
    module: ModuleType,
    class_name: str,
    instance: object,
    tmp_path: Path,
    repository: Path,
    attempted: set[str],
    process_terminations: list[str],
    expected_rejections: list[str],
) -> None:
    for method_name, member in inspect.getmembers_static(instance):
        method = _instance_method(
            instance,
            module,
            class_name,
            method_name,
            member,
            expected_rejections,
        )
        if method is None:
            continue
        await _invoke_scenarios(
            f"{module.__name__}.{class_name}.{method_name}",
            method,
            tmp_path,
            repository,
            attempted,
            process_terminations,
            expected_rejections,
        )


async def _exercise_class(
    module: ModuleType,
    class_name: str,
    class_type: type[Any],
    tmp_path: Path,
    repository: Path,
    attempted: set[str],
    process_terminations: list[str],
    expected_rejections: list[str],
) -> None:
    instance = await _instantiate_class(
        module, class_name, class_type, tmp_path, expected_rejections
    )
    if instance is None:
        return
    await _exercise_instance(
        module,
        class_name,
        instance,
        tmp_path,
        repository,
        attempted,
        process_terminations,
        expected_rejections,
    )


async def _exercise_module(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    repository: Path,
    attempted: set[str],
    process_terminations: list[str],
    expected_rejections: list[str],
) -> None:
    _patch_module_clients(module, monkeypatch)
    for name, function in _module_functions(module):
        await _invoke_scenarios(
            f"{module.__name__}.{name}",
            function,
            tmp_path,
            repository,
            attempted,
            process_terminations,
            expected_rejections,
        )
    for class_name, class_type in _module_classes(module):
        await _exercise_class(
            module,
            class_name,
            class_type,
            tmp_path,
            repository,
            attempted,
            process_terminations,
            expected_rejections,
        )


async def _exercise_modules(
    modules: list[ModuleType],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    repository: Path,
    attempted: set[str],
    process_terminations: list[str],
    expected_rejections: list[str],
) -> None:
    for module in modules:
        await _exercise_module(
            module,
            monkeypatch,
            tmp_path,
            repository,
            attempted,
            process_terminations,
            expected_rejections,
        )


def test_external_api_private_helpers_execute_with_local_doubles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    modules = _modules()
    repository = Path.cwd()
    monkeypatch.setattr(httpx, "AsyncClient", _AsyncClient)
    monkeypatch.setattr(requests, "Session", _Session)
    monkeypatch.setattr(requests, "get", _Session().get)
    monkeypatch.setattr(requests, "post", _Session().post)

    async def no_sleep(
        _delay: float = 0, *_args: object, **_kwargs: object
    ) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    attempted: set[str] = set()
    process_terminations: list[str] = []
    expected_rejections: list[str] = []
    asyncio.run(
        _exercise_modules(
            modules,
            monkeypatch,
            tmp_path,
            repository,
            attempted,
            process_terminations,
            expected_rejections,
        )
    )
    assert attempted
    assert all(
        any(name.startswith(f"{module.__name__}.") for name in attempted)
        for module in modules
    )
    assert process_terminations == []
    assert all(":" in rejection for rejection in expected_rejections)
