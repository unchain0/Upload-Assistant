"""Boundary-smoke contracts for external metadata adapters.

Focused tests assert provider-specific mappings. This catalog test complements
those checks by invoking every adapter-owned callable with deterministic fakes,
ensuring no contract silently escapes to a live network service.
"""

from __future__ import annotations

import asyncio
import copy
import importlib
import inspect
import pkgutil
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from types import ModuleType, TracebackType
from typing import Any, ClassVar, Self, get_args, get_origin, get_type_hints

import cli_ui
import cloudscraper
import httpx
import requests
from bs4 import BeautifulSoup

import src.integrations.external_apis as external_api_package
from data.example_config import config as example_config
from src.domain_models.external_api import TmdbCredential
from src.domain_models.release import Meta
from tests.contract_scenarios import literal_branch_scenarios


class _Cookies(dict[str, str]):
    def get_dict(self) -> dict[str, str]:
        return dict(self)


class _Response:
    scenario: ClassVar[str] = "success"
    url = "https://metadata.example/api"

    @property
    def status_code(self) -> int:
        return {
            "success": 200,
            "empty": 200,
            "not_found": 404,
            "unauthorized": 401,
            "rate_limited": 429,
            "server_error": 503,
            "malformed": 200,
        }[self.scenario]

    headers: ClassVar[dict[str, str]] = {"content-type": "application/json"}
    content = b"{}"
    cookies: ClassVar[_Cookies] = _Cookies(session="metadata-session")

    @property
    def text(self) -> str:
        if self.scenario == "empty":
            return ""
        if self.scenario == "malformed":
            return "not-json-or-html"
        if self.scenario != "success":
            return f"metadata provider error: {self.scenario}"
        return (
            '<html><head><meta name="csrf-token" content="csrf"></head><body>'
            '<a href="https://www.blu-ray.com/movies/example/1/">Example</a>'
            '<div class="title">Example Release</div><script type="application/ld+json">'
            '{"name":"Example Release","datePublished":"2024-01-01"}</script></body></html>'
        )

    def json(self) -> dict[str, Any]:
        if self.scenario == "malformed":
            raise ValueError("malformed response")
        if self.scenario == "empty":
            return {
                "success": True,
                "results": [],
                "data": [],
                "items": [],
                "docs": [],
            }
        if self.scenario != "success":
            return {
                "success": False,
                "status_code": self.status_code,
                "status_message": self.scenario.replace("_", " "),
                "message": self.scenario.replace("_", " "),
                "results": [],
                "data": [],
                "items": [],
            }
        movie = {
            "id": 123,
            "movie_id": 123,
            "tvdb_id": 456,
            "imdb_id": "tt1234567",
            "name": "Example Release",
            "title": "Example Release",
            "original_title": "Example Original Title",
            "original_name": "Example Original Title",
            "overview": "Representative metadata overview.",
            "release_date": "2024-01-02",
            "first_air_date": "2024-01-02",
            "year": 2024,
            "poster_path": "/poster.jpg",
            "backdrop_path": "/backdrop.jpg",
            "original_language": "en",
            "status": "Released",
            "type": "Movie",
            "media_type": "movie",
            "popularity": 100.0,
            "vote_count": 100,
            "genres": [{"id": 18, "name": "Drama"}],
            "genre_ids": [18],
            "production_countries": [
                {"iso_3166_1": "US", "name": "United States"}
            ],
            "spoken_languages": [
                {"iso_639_1": "en", "english_name": "English"}
            ],
            "external_ids": {"imdb_id": "tt1234567", "tvdb_id": 456},
            "credits": {
                "crew": [{"job": "Director", "name": "Example Director"}],
                "cast": [],
            },
            "keywords": {
                "keywords": [{"name": "example"}],
                "results": [{"name": "example"}],
            },
            "alternative_titles": {"titles": []},
            "translations": {"translations": []},
            "videos": {
                "results": [
                    {"site": "YouTube", "type": "Trailer", "key": "abc"}
                ]
            },
            "images": {"logos": [], "posters": [], "backdrops": []},
            "seasons": [{"season_number": 1, "episode_count": 1}],
            "episodes": [
                {
                    "id": 1,
                    "season_number": 1,
                    "episode_number": 1,
                    "name": "Pilot",
                    "air_date": "2024-01-02",
                }
            ],
        }
        release = {
            "id": "release-1",
            "title": "Example Album",
            "date": "2024-01-02",
            "year": 2024,
            "country": "US",
            "status": "Official",
            "barcode": "1234567890123",
            "catno": "CAT-001",
            "catalog_number": "CAT-001",
            "format": ["CD"],
            "formats": [{"name": "CD", "qty": "1", "descriptions": ["Album"]}],
            "media": [{"format": "CD", "track-count": 10}],
            "track-count": 10,
            "artist-credit": [{"name": "Example Artist"}],
            "artists": [{"name": "Example Artist"}],
            "labels": [{"name": "Example Label", "catno": "CAT-001"}],
            "label-info": [
                {
                    "catalog-number": "CAT-001",
                    "label": {"name": "Example Label"},
                }
            ],
            "genres": ["Rock"],
            "styles": ["Alternative Rock"],
            "master_id": 99,
        }
        volume = {
            "id": "volume-1",
            "volumeInfo": {
                "title": "Example Book",
                "authors": ["Example Author"],
                "publisher": "Example Publisher",
                "publishedDate": "2024-01-02",
                "description": "Example book description.",
                "industryIdentifiers": [
                    {"type": "ISBN_13", "identifier": "9780000000000"}
                ],
                "language": "en",
                "pageCount": 320,
                "categories": ["Fiction"],
                "imageLinks": {"thumbnail": "https://images.example/book.jpg"},
            },
        }
        return {
            **movie,
            "success": True,
            "status_code": 1,
            "status_message": "Success",
            "access_token": "token",
            "expires_in": 3600,
            "results": [movie],
            "data": [movie],
            "items": [volume],
            "volumes": [volume],
            "releases": [release],
            "release-groups": [release],
            "masters": [release],
            "artists": [{"name": "Example Artist"}],
            "works": [{"key": "/works/OL1W", "title": "Example Book"}],
            "docs": [
                {
                    "key": "OL1W",
                    "title": "Example Book",
                    "author_name": ["Example Author"],
                    "first_publish_year": 2024,
                }
            ],
            "movie_results": [movie],
            "tv_results": [movie],
            "person_results": [],
            "find_results": [movie],
            "credits": movie["credits"],
            "keywords": movie["keywords"],
            "external_ids": movie["external_ids"],
        }

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("GET", str(self.url)),
                response=httpx.Response(self.status_code),
            )

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


class _AsyncClient:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.cookies = _Cookies(session="metadata-session")
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

    async def get(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()

    async def post(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()

    async def put(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()

    async def request(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()

    async def aclose(self) -> None:
        return None


class _Session:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.cookies = _Cookies(session="metadata-session")
        self.headers: dict[str, str] = {}

    def get(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()

    def post(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()

    def request(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()

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


def _config() -> dict[str, Any]:
    config = copy.deepcopy(example_config)
    config.setdefault("DEFAULT", {}).update(
        {
            "tmdb_api": "0123456789abcdef0123456789abcdef",
            "tvdb_api": "tvdb-token",
            "tvmaze_api": "tvmaze-token",
            "imdb_api": "imdb-token",
            "igdb_client_id": "igdb-client",
            "igdb_client_secret": "igdb-secret",
            "radarr_url": "https://radarr.example",
            "radarr_api": "radarr-token",
            "sonarr_url": "https://sonarr.example",
            "sonarr_api": "sonarr-token",
            "discogs_token": "discogs-token",
            "musicbrainz_user_agent": "UploadAssistantTest/1.0",
        }
    )
    return config


def _meta(tmp_path: Path) -> Meta:
    media = tmp_path / "Example.Release.2024.1080p.WEB-DL.mkv"
    media.write_bytes(b"media")
    return Meta(
        base_dir=str(tmp_path),
        uuid="external-contract",
        path=str(media),
        filename=media.name,
        filelist=[str(media)],
        category="MOVIE",
        name="Example Release 2024 1080p WEB-DL",
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
        daily=False,
        resolution="1080p",
        source="WEB",
        type="WEBDL",
        service="AMZN",
        poster="https://images.example/poster.jpg",
        overview="Representative overview.",
        mediainfo={
            "media": {
                "track": [
                    {"@type": "General"},
                    {"@type": "Video", "Language": "en"},
                ]
            }
        },
        imdb_info={"title": "Example Release", "year": 2024, "type": "movie"},
        unattended=True,
        debug=False,
        anime=False,
        manual=False,
        author="Example Author",
        isbn="9780000000000",
        asin="B000000000",
        artist="Example Artist",
        album="Example Album",
    )


def _modules() -> list[ModuleType]:
    return [
        importlib.import_module(item.name)
        for item in pkgutil.iter_modules(
            external_api_package.__path__, f"{external_api_package.__name__}."
        )
    ]


def _value(
    name: str,
    annotation: object,
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    profile: int,
) -> object:
    normalized = name.casefold().lstrip("_")
    response = _Response().json()
    values: dict[str, object] = {
        "config": config,
        "meta": meta,
        "filename": meta.filename or "Example.Release.2024.mkv",
        "path": str(tmp_path / "Example.Release.2024.mkv"),
        "base_dir": str(tmp_path),
        "title": meta.title or "Example Release",
        "tmdb_name": meta.title or "Example Release",
        "secondary_title": "Example Alternate Title",
        "search_year": 2024,
        "year": 2024,
        "category": "MOVIE" if profile == 0 else "TV",
        "category_preference": "MOVIE",
        "imdb_id": "tt1234567",
        "imdb": "tt1234567",
        "tmdb_id": 123,
        "tmdbid": 123,
        "tvdb_id": 456,
        "tvdbid": 456,
        "mal": 789,
        "mal_manual": 789,
        "season": 1,
        "season_number": 1,
        "episode": 1,
        "episode_number": 1,
        "episode_id": 1,
        "tvdb_episode_id": 1,
        "date": datetime(2024, 1, 2, tzinfo=UTC),
        "air_date": "2024-01-02",
        "isbn": "9780000000000",
        "asin": "B000000000",
        "work_id": "OL1W",
        "author_id": "OL1A",
        "game_id": 1,
        "steam_id": 123,
        "query": "Example Release",
        "search_term": "Example Release",
        "reference": "release-1",
        "release_id": "release-1",
        "master_id": 99,
        "payload": response,
        "response": response,
        "response_data": response,
        "data": response,
        "raw": response,
        "volume_info": response["items"][0]["volumeInfo"],
        "release": response["releases"][0],
        "master": response["releases"][0],
        "candidate": response["releases"][0],
        "mediainfo": meta.mediainfo,
        "imdb_info": meta.imdb_info,
        "html": _Response().text,
        "soup": BeautifulSoup(_Response().text, "html.parser"),
        "url": "https://metadata.example/api",
        "endpoint": "/search",
        "debug": False,
        "unattended": True,
        "quickie_search": False,
        "anime": False,
        "mode": "non_cli",
        "attempted": 0,
        "final_attempt": False,
        "manual_language": "en",
        "original_language": "en",
        "poster": "https://images.example/poster.jpg",
        "aka": "Example Alternate Title",
        "new_category": None,
        "options": {},
        "headers": {},
        "params": {},
        "client": _AsyncClient(),
        "session": _Session(),
        "credential": TmdbCredential.parse("0123456789abcdef0123456789abcdef"),
        "style": "color: green; background-color: #eeeeee",
        "style_text": "color: green; background-color: #eeeeee",
        "genres": ["Action", "Science Fiction"],
        "tags": ["Action", "Science Fiction"],
        "languages": ["en", "pt"],
        "poster_sizes": ["w500", "original"],
        "aliases": [{"name": "Example Alternate Title", "language": "eng"}],
        "results": list(response.get("results", [])),
        "releases": list(response.get("releases", [])),
        "items": list(response.get("items", [])),
        "links": ["https://www.blu-ray.com/movies/example/1/"],
    }
    if normalized in values:
        return values[normalized]
    origin = get_origin(annotation)
    args = get_args(annotation)
    if annotation is bool:
        return profile == 0
    if annotation is int:
        return 1
    if annotation is float:
        return 1.0
    if annotation is str:
        return "example"
    if annotation in {date, datetime}:
        return datetime(2024, 1, 2, tzinfo=UTC)
    if origin is list:
        return [response]
    if origin is dict:
        return response
    if origin is tuple:
        return ()
    if origin is not None and type(None) in args:
        concrete = next((item for item in args if item is not type(None)), str)
        return _value(normalized, concrete, meta, config, tmp_path, profile)
    return "example"


_PROTECTED_SCENARIO_ARGUMENTS = frozenset(
    {
        "meta",
        "config",
        "configuration",
        "client",
        "session",
        "credential",
        "authentication",
        "response",
        "response_data",
    }
)


def _coerce_override(
    value: object,
    annotation: object,
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    profile: int,
) -> object:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if annotation is Meta:
        return meta
    if annotation is TmdbCredential or "TmdbCredential" in str(annotation):
        return TmdbCredential.parse("0123456789abcdef0123456789abcdef")
    if annotation is Path:
        return tmp_path / "Example.Release.2024.mkv"
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
        return [
            _coerce_override(value, element, meta, config, tmp_path, profile)
        ]
    if origin is dict:
        return value if isinstance(value, dict) else _Response().json()
    if origin is tuple:
        return value if isinstance(value, tuple) else ()
    if origin is set:
        return value if isinstance(value, set) else {value}
    if origin is not None and type(None) in args and value is not None:
        concrete = next(
            (item for item in args if item is not type(None)), object
        )
        return _coerce_override(
            value, concrete, meta, config, tmp_path, profile
        )
    return value


async def _invoke(
    function: Callable[..., object],
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    profile: int,
    overrides: Mapping[str, object] | None = None,
) -> object:
    args: list[object] = []
    kwargs: dict[str, object] = {}
    overrides = overrides or {}
    hint_target = function.__init__ if inspect.isclass(function) else function
    try:
        hints = get_type_hints(hint_target)
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
        if parameter.name in overrides:
            value = _coerce_override(
                value, annotation, meta, config, tmp_path, profile
            )
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY:
            kwargs[parameter.name] = value
        else:
            args.append(value)
    result = function(*args, **kwargs)
    if inspect.isawaitable(result):
        return await asyncio.wait_for(result, timeout=0.1)
    return result


def test_external_api_catalog_uses_deterministic_boundary_fakes(
    tmp_path: Path, monkeypatch: Any
) -> None:
    config = _config()
    meta = _meta(tmp_path)
    monkeypatch.setattr(httpx, "AsyncClient", _AsyncClient)
    monkeypatch.setattr(requests, "Session", _Session)
    monkeypatch.setattr(requests, "get", _Session().get)
    monkeypatch.setattr(requests, "post", _Session().post)
    monkeypatch.setattr(
        cloudscraper, "create_scraper", lambda *_args, **_kwargs: _Session()
    )
    monkeypatch.setattr(cli_ui, "ask_yes_no", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        cli_ui,
        "ask_choice",
        lambda _message, choices, **_kwargs: next(iter(choices)),
    )
    monkeypatch.setattr(cli_ui, "ask_string", lambda *_args, **_kwargs: "1")
    monkeypatch.setattr("builtins.input", lambda *_args, **_kwargs: "1")

    async def no_sleep(
        _delay: float = 0, *_args: object, **_kwargs: object
    ) -> None:
        return None

    async def affirmative_prompt(*_args: object, **_kwargs: object) -> bool:
        return True

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    attempted: set[str] = set()
    process_terminations: list[str] = []
    validation_errors: list[str] = []

    async def exercise() -> None:
        scenarios = (
            "success",
            "empty",
            "not_found",
            "unauthorized",
            "rate_limited",
            "server_error",
            "malformed",
        )
        for module in _modules():
            if hasattr(module, "prompt_in_thread"):
                monkeypatch.setattr(
                    module, "prompt_in_thread", affirmative_prompt
                )
            functions = [
                (name, function)
                for name, function in inspect.getmembers(
                    module, inspect.isfunction
                )
                if function.__module__ == module.__name__
                and not name.startswith("__")
            ]
            classes = [
                (class_name, class_type)
                for class_name, class_type in inspect.getmembers(
                    module, inspect.isclass
                )
                if class_type.__module__ == module.__name__
            ]
            for scenario_index, scenario in enumerate(scenarios):
                _Response.scenario = scenario
                profile = scenario_index % 2
                for name, function in functions:
                    attempted.add(f"{module.__name__}.{name}")
                    try:
                        await _invoke(
                            function, meta.copy(), config, tmp_path, profile
                        )
                    except (KeyboardInterrupt, SystemExit) as error:
                        process_terminations.append(
                            f"{module.__name__}.{name}:{type(error).__name__}"
                        )
                    except Exception as error:
                        validation_errors.append(
                            f"{module.__name__}.{name}:{type(error).__name__}:{error}"
                        )

                for class_name, class_type in classes:
                    try:
                        instance = await _invoke(
                            class_type, meta.copy(), config, tmp_path, profile
                        )
                    except Exception as error:
                        validation_errors.append(
                            f"{module.__name__}.{class_name}.__init__:{type(error).__name__}:{error}"
                        )
                        continue
                    for method_name, method in inspect.getmembers(
                        instance, callable
                    ):
                        if method_name.startswith("__"):
                            continue
                        attempted.add(
                            f"{module.__name__}.{class_name}.{method_name}"
                        )
                        try:
                            await _invoke(
                                method, meta.copy(), config, tmp_path, profile
                            )
                        except (KeyboardInterrupt, SystemExit) as error:
                            process_terminations.append(
                                f"{module.__name__}.{class_name}.{method_name}:{type(error).__name__}"
                            )
                        except Exception as error:
                            validation_errors.append(
                                f"{module.__name__}.{class_name}.{method_name}:{type(error).__name__}:{error}"
                            )

            _Response.scenario = "success"
            for name, function in functions:
                for (
                    meta_updates,
                    argument_overrides,
                ) in literal_branch_scenarios(
                    function, Meta.__dataclass_fields__, limit=384
                ):
                    argument_overrides = {
                        key: value
                        for key, value in argument_overrides.items()
                        if key not in _PROTECTED_SCENARIO_ARGUMENTS
                    }
                    scenario_meta = meta.copy()
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

            for class_name, class_type in classes:
                try:
                    instance = await _invoke(
                        class_type, meta.copy(), config, tmp_path, 0
                    )
                except Exception as error:
                    validation_errors.append(
                        f"{module.__name__}.{class_name}.__init__:{type(error).__name__}:{error}"
                    )
                    continue
                for method_name, method in inspect.getmembers(
                    instance, callable
                ):
                    if method_name.startswith("__"):
                        continue
                    for (
                        meta_updates,
                        argument_overrides,
                    ) in literal_branch_scenarios(
                        method, Meta.__dataclass_fields__, limit=384
                    ):
                        argument_overrides = {
                            key: value
                            for key, value in argument_overrides.items()
                            if key not in _PROTECTED_SCENARIO_ARGUMENTS
                        }
                        scenario_meta = meta.copy()
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

    assert len(attempted) >= 140
    assert process_terminations == []
