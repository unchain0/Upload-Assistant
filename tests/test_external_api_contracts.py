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


class _TvdbClient:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        return None

    def search(
        self, *_args: object, **_kwargs: object
    ) -> list[dict[str, Any]]:
        if _Response.scenario == "success":
            return [{"tvdb_id": "456", "year": "2024", "aliases": []}]
        return []

    def get_series_episodes(self, *_args: object, **kwargs: object) -> object:
        if _Response.scenario != "success":
            return []
        if int(kwargs.get("page", 0)) > 0:
            return []
        return {
            "slug": "example-release-2024",
            "episodes": [
                {
                    "id": 1,
                    "seasonNumber": 1,
                    "number": 1,
                    "absoluteNumber": 1,
                    "aired": "2024-01-01",
                    "name": "Episode 1",
                    "overview": "Overview",
                }
            ],
        }

    def get_series_extended(
        self, *_args: object, **_kwargs: object
    ) -> dict[str, Any]:
        return {"aliases": [], "slug": "example-release-2024", "year": "2024"}

    def get_series_translation(
        self, *_args: object, **_kwargs: object
    ) -> dict[str, Any]:
        return {"name": "Example Release", "aliases": []}

    def search_by_remote_id(
        self, *_args: object, **_kwargs: object
    ) -> list[dict[str, Any]]:
        return []

    def get_episode_extended(
        self, *_args: object, **_kwargs: object
    ) -> dict[str, Any]:
        return {"remoteIds": []}


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


_MISSING = object()


def _external_values(
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    profile: int,
) -> dict[str, object]:
    response = _Response().json()
    return {
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


def _scalar_external_value(annotation: object, profile: int) -> object:
    values: dict[object, object] = {
        bool: profile == 0,
        int: 1,
        float: 1.0,
        str: "example",
        date: datetime(2024, 1, 2, tzinfo=UTC),
        datetime: datetime(2024, 1, 2, tzinfo=UTC),
    }
    return values.get(annotation, _MISSING)


def _direct_external_composite(origin: object) -> object:
    values = {
        list: [_Response().json()],
        dict: _Response().json(),
        tuple: (),
    }
    return values.get(origin, _MISSING)


def _external_optional_type(args: tuple[object, ...]) -> object | None:
    if type(None) not in args:
        return None
    return next((item for item in args if item is not type(None)), str)


def _composite_external_value(
    name: str,
    annotation: object,
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    profile: int,
) -> object:
    origin = get_origin(annotation)
    direct = _direct_external_composite(origin)
    if direct is not _MISSING:
        return direct
    concrete = _external_optional_type(get_args(annotation))
    if concrete is None:
        return "example"
    return _value(name, concrete, meta, config, tmp_path, profile)


def _value(
    name: str,
    annotation: object,
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    profile: int,
) -> object:
    normalized = name.casefold().lstrip("_")
    values = _external_values(meta, config, tmp_path, profile)
    if normalized in values:
        return values[normalized]
    scalar = _scalar_external_value(annotation, profile)
    if scalar is not _MISSING:
        return scalar
    return _composite_external_value(
        normalized, annotation, meta, config, tmp_path, profile
    )


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


def _coerce_external_number(value: object, annotation: object) -> object:
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
    return _MISSING


def _coerce_external_meta(
    _value: object, meta: Meta, _tmp_path: Path
) -> object:
    return meta


def _coerce_external_path(
    _value: object, _meta: Meta, tmp_path: Path
) -> object:
    return tmp_path / "Example.Release.2024.mkv"


def _coerce_external_str(
    value: object, _meta: Meta, _tmp_path: Path
) -> object:
    return str(value)


def _coerce_external_bool(
    value: object, _meta: Meta, _tmp_path: Path
) -> object:
    return bool(value)


_EXTERNAL_SIMPLE_COERCERS: dict[
    object, Callable[[object, Meta, Path], object]
] = {
    Meta: _coerce_external_meta,
    Path: _coerce_external_path,
    str: _coerce_external_str,
    bool: _coerce_external_bool,
}


def _coerce_external_simple(
    value: object, annotation: object, meta: Meta, tmp_path: Path
) -> object:
    if annotation is TmdbCredential or "TmdbCredential" in str(annotation):
        return TmdbCredential.parse("0123456789abcdef0123456789abcdef")
    coercer = _EXTERNAL_SIMPLE_COERCERS.get(annotation)
    if coercer is None:
        return _MISSING
    return coercer(value, meta, tmp_path)


def _coerce_external_list(
    value: object,
    args: tuple[object, ...],
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    profile: int,
) -> list[object]:
    if isinstance(value, list):
        return value
    element = args[0] if args else object
    return [_coerce_override(value, element, meta, config, tmp_path, profile)]


def _coerce_external_mapping(value: object) -> object:
    return value if isinstance(value, dict) else _Response().json()


def _coerce_external_tuple(value: object) -> object:
    return value if isinstance(value, tuple) else ()


def _coerce_external_set(value: object) -> object:
    return value if isinstance(value, set) else {value}


_EXTERNAL_COLLECTION_COERCERS: dict[object, Callable[[object], object]] = {
    dict: _coerce_external_mapping,
    tuple: _coerce_external_tuple,
    set: _coerce_external_set,
}


def _coerce_external_collection(
    value: object,
    origin: object,
    args: tuple[object, ...],
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    profile: int,
) -> object:
    if origin is list:
        return _coerce_external_list(
            value, args, meta, config, tmp_path, profile
        )
    coercer = _EXTERNAL_COLLECTION_COERCERS.get(origin)
    if coercer is None:
        return _MISSING
    return coercer(value)


def _coerce_external_optional(
    value: object,
    origin: object,
    args: tuple[object, ...],
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    profile: int,
) -> object:
    if value is None or origin is None:
        return value
    concrete = _external_optional_type(args)
    if concrete is None:
        return value
    return _coerce_override(value, concrete, meta, config, tmp_path, profile)


def _coerce_override(
    value: object,
    annotation: object,
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    profile: int,
) -> object:
    simple = _coerce_external_simple(value, annotation, meta, tmp_path)
    if simple is not _MISSING:
        return simple
    number = _coerce_external_number(value, annotation)
    if number is not _MISSING:
        return number
    origin = get_origin(annotation)
    args = get_args(annotation)
    collection = _coerce_external_collection(
        value, origin, args, meta, config, tmp_path, profile
    )
    if collection is not _MISSING:
        return collection
    return _coerce_external_optional(
        value, origin, args, meta, config, tmp_path, profile
    )


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
    return (
        parameter.default is inspect.Parameter.empty
        or parameter.name in overrides
    )


def _external_parameter_value(
    parameter: inspect.Parameter,
    annotation: object,
    overrides: Mapping[str, object],
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    profile: int,
) -> object:
    value = _value(parameter.name, annotation, meta, config, tmp_path, profile)
    if parameter.name not in overrides:
        return value
    return _coerce_override(
        overrides[parameter.name], annotation, meta, config, tmp_path, profile
    )


def _external_invocation_arguments(
    function: Callable[..., object],
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    profile: int,
    overrides: Mapping[str, object],
) -> tuple[list[object], dict[str, object]]:
    hint_target = function.__init__ if inspect.isclass(function) else function
    hints = _safe_type_hints(hint_target)
    args: list[object] = []
    kwargs: dict[str, object] = {}
    for parameter in inspect.signature(function).parameters.values():
        if not _include_parameter(parameter, overrides):
            continue
        annotation = hints.get(parameter.name, parameter.annotation)
        value = _external_parameter_value(
            parameter, annotation, overrides, meta, config, tmp_path, profile
        )
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY:
            kwargs[parameter.name] = value
        else:
            args.append(value)
    return args, kwargs


async def _invoke(
    function: Callable[..., object],
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    profile: int,
    overrides: Mapping[str, object] | None = None,
) -> object:
    resolved_overrides = overrides or {}
    args, kwargs = _external_invocation_arguments(
        function, meta, config, tmp_path, profile, resolved_overrides
    )
    result = function(*args, **kwargs)
    if inspect.isawaitable(result):
        return await asyncio.wait_for(result, timeout=0.1)
    return result


_EXTERNAL_SCENARIOS = (
    "success",
    "empty",
    "not_found",
    "unauthorized",
    "rate_limited",
    "server_error",
    "malformed",
)


async def _no_sleep(
    _delay: float = 0, *_args: object, **_kwargs: object
) -> None:
    return None


async def _immediate_prompt(
    function: Callable[..., object], *args: object, **kwargs: object
) -> object:
    """Run the already-faked prompt callable without a worker thread."""
    result = function(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


async def _skip_selection_prompt(
    _function: Callable[..., object], *_args: object, **_kwargs: object
) -> str:
    """Return IMDb's universally valid interactive skip selection."""
    return "0"


def _patch_external_clients(monkeypatch: Any) -> None:
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
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)


def _patch_external_module(module: ModuleType, monkeypatch: Any) -> None:
    if module.__name__.endswith(".imdb"):
        monkeypatch.setattr(module, "prompt_in_thread", _skip_selection_prompt)
    elif hasattr(module, "prompt_in_thread"):
        monkeypatch.setattr(module, "prompt_in_thread", _immediate_prompt)
    if module.__name__.endswith(".tvdb"):
        monkeypatch.setattr(module, "TVDB", _TvdbClient)
        monkeypatch.setattr(module, "tvdb", _TvdbClient())


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


def _record_external_error(
    qualified: str,
    error: BaseException,
    process_terminations: list[str],
    validation_errors: list[str],
) -> None:
    if isinstance(error, KeyboardInterrupt | SystemExit):
        process_terminations.append(f"{qualified}:{type(error).__name__}")
        return
    validation_errors.append(f"{qualified}:{type(error).__name__}:{error}")


async def _run_external_call(
    qualified: str,
    function: Callable[..., object],
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    profile: int,
    process_terminations: list[str],
    validation_errors: list[str],
    overrides: Mapping[str, object] | None = None,
) -> object | None:
    try:
        return await _invoke(
            function, meta, config, tmp_path, profile, overrides
        )
    except BaseException as error:
        _record_external_error(
            qualified, error, process_terminations, validation_errors
        )
        return None


async def _instantiate_external_class(
    module: ModuleType,
    class_name: str,
    class_type: type[Any],
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    profile: int,
    validation_errors: list[str],
) -> object | None:
    try:
        return await _invoke(class_type, meta, config, tmp_path, profile)
    except Exception as error:
        validation_errors.append(
            f"{module.__name__}.{class_name}.__init__:{type(error).__name__}:{error}"
        )
        return None


async def _exercise_external_instance_scenario(
    module: ModuleType,
    class_name: str,
    instance: object,
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    profile: int,
    attempted: set[str],
    process_terminations: list[str],
    validation_errors: list[str],
) -> None:
    for method_name, method in inspect.getmembers(instance, callable):
        if method_name.startswith("__"):
            continue
        qualified = f"{module.__name__}.{class_name}.{method_name}"
        attempted.add(qualified)
        await _run_external_call(
            qualified,
            method,
            meta.copy(),
            config,
            tmp_path,
            profile,
            process_terminations,
            validation_errors,
        )


async def _exercise_external_http_scenario(
    module: ModuleType,
    functions: list[tuple[str, Callable[..., object]]],
    classes: list[tuple[str, type[Any]]],
    scenario: str,
    scenario_index: int,
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    attempted: set[str],
    process_terminations: list[str],
    validation_errors: list[str],
) -> None:
    _Response.scenario = scenario
    profile = scenario_index % 2
    for name, function in functions:
        qualified = f"{module.__name__}.{name}"
        attempted.add(qualified)
        await _run_external_call(
            qualified,
            function,
            meta.copy(),
            config,
            tmp_path,
            profile,
            process_terminations,
            validation_errors,
        )
    for class_name, class_type in classes:
        instance = await _instantiate_external_class(
            module,
            class_name,
            class_type,
            meta.copy(),
            config,
            tmp_path,
            profile,
            validation_errors,
        )
        if instance is not None:
            await _exercise_external_instance_scenario(
                module,
                class_name,
                instance,
                meta,
                config,
                tmp_path,
                profile,
                attempted,
                process_terminations,
                validation_errors,
            )


def _filtered_external_overrides(
    overrides: Mapping[str, object],
) -> dict[str, object]:
    return {
        key: value
        for key, value in overrides.items()
        if key not in _PROTECTED_SCENARIO_ARGUMENTS
    }


def _apply_external_meta_updates(
    meta: Meta, updates: Mapping[str, object]
) -> None:
    for key, value in updates.items():
        if key in Meta.__dataclass_fields__:
            setattr(meta, key, value)


async def _exercise_external_literal_scenarios(
    qualified: str,
    function: Callable[..., object],
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    process_terminations: list[str],
    validation_errors: list[str],
) -> None:
    for meta_updates, argument_overrides in literal_branch_scenarios(
        function, Meta.__dataclass_fields__, limit=384
    ):
        scenario_meta = meta.copy()
        _apply_external_meta_updates(scenario_meta, meta_updates)
        await _run_external_call(
            qualified,
            function,
            scenario_meta,
            config,
            tmp_path,
            0,
            process_terminations,
            validation_errors,
            _filtered_external_overrides(argument_overrides),
        )


async def _exercise_external_instance_literals(
    module: ModuleType,
    class_name: str,
    instance: object,
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    process_terminations: list[str],
    validation_errors: list[str],
) -> None:
    for method_name, method in inspect.getmembers(instance, callable):
        if method_name.startswith("__"):
            continue
        await _exercise_external_literal_scenarios(
            f"{module.__name__}.{class_name}.{method_name}",
            method,
            meta,
            config,
            tmp_path,
            process_terminations,
            validation_errors,
        )


async def _exercise_external_module_literals(
    module: ModuleType,
    functions: list[tuple[str, Callable[..., object]]],
    classes: list[tuple[str, type[Any]]],
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    process_terminations: list[str],
    validation_errors: list[str],
) -> None:
    _Response.scenario = "success"
    for name, function in functions:
        await _exercise_external_literal_scenarios(
            f"{module.__name__}.{name}",
            function,
            meta,
            config,
            tmp_path,
            process_terminations,
            validation_errors,
        )
    for class_name, class_type in classes:
        instance = await _instantiate_external_class(
            module,
            class_name,
            class_type,
            meta.copy(),
            config,
            tmp_path,
            0,
            validation_errors,
        )
        if instance is not None:
            await _exercise_external_instance_literals(
                module,
                class_name,
                instance,
                meta,
                config,
                tmp_path,
                process_terminations,
                validation_errors,
            )


async def _exercise_external_module(
    module: ModuleType,
    monkeypatch: Any,
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    attempted: set[str],
    process_terminations: list[str],
    validation_errors: list[str],
) -> None:
    _patch_external_module(module, monkeypatch)
    functions = _module_functions(module)
    classes = _module_classes(module)
    for scenario_index, scenario in enumerate(_EXTERNAL_SCENARIOS):
        await _exercise_external_http_scenario(
            module,
            functions,
            classes,
            scenario,
            scenario_index,
            meta,
            config,
            tmp_path,
            attempted,
            process_terminations,
            validation_errors,
        )
    await _exercise_external_module_literals(
        module,
        functions,
        classes,
        meta,
        config,
        tmp_path,
        process_terminations,
        validation_errors,
    )


async def _exercise_external_modules(
    modules: list[ModuleType],
    monkeypatch: Any,
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    attempted: set[str],
    process_terminations: list[str],
    validation_errors: list[str],
) -> None:
    for module in modules:
        await _exercise_external_module(
            module,
            monkeypatch,
            meta,
            config,
            tmp_path,
            attempted,
            process_terminations,
            validation_errors,
        )


def test_external_api_catalog_uses_deterministic_boundary_fakes(
    tmp_path: Path, monkeypatch: Any
) -> None:
    config = _config()
    meta = _meta(tmp_path)
    _patch_external_clients(monkeypatch)
    attempted: set[str] = set()
    process_terminations: list[str] = []
    validation_errors: list[str] = []
    asyncio.run(
        _exercise_external_modules(
            _modules(),
            monkeypatch,
            meta,
            config,
            tmp_path,
            attempted,
            process_terminations,
            validation_errors,
        )
    )
    assert len(attempted) >= 140
    assert process_terminations == []
