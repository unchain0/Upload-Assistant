"""Exhaustive local-boundary coverage for TMDb metadata workflows."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Self
from unittest.mock import AsyncMock

import httpx
import pytest

import src.integrations.external_apis.tmdb as tmdb
from src.domain_models.errors import (
    OperationAbortedError,
    TmdbCredentialMissingError,
)
from src.domain_models.release import Meta

_MISS = object()


class Response:
    def __init__(
        self,
        payload: Any = None,
        status_code: int = 200,
        *,
        raise_error: BaseException | None = None,
    ) -> None:
        self.payload = {} if payload is None else payload
        self.status_code = status_code
        self.raise_error = raise_error
        self.text = "response"
        self.headers: dict[str, str] = {"content-type": "application/json"}

    def json(self) -> Any:
        if isinstance(self.payload, BaseException):
            raise self.payload
        return self.payload

    def raise_for_status(self) -> None:
        if self.raise_error is not None:
            raise self.raise_error
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "failed",
                request=httpx.Request("GET", "https://example.invalid"),
                response=httpx.Response(self.status_code),
            )


class RouterClient:
    calls: ClassVar[list[tuple[str, dict[str, Any]]]] = []
    handler: ClassVar[Callable[[str, dict[str, Any]], Any]] = (
        lambda _url, _kwargs: Response()
    )

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.kwargs = _kwargs

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, url: str, **kwargs: Any) -> Any:
        self.__class__.calls.append((url, kwargs))
        result = self.__class__.handler(url, kwargs)
        if isinstance(result, BaseException):
            raise result
        return result


class Cache:
    def __init__(self, value: Any = _MISS) -> None:
        self.value = value
        self.sets: list[tuple[str, str, str, Any]] = []

    async def get(self, *_args: object) -> Any:
        return self.value

    async def set(self, *args: Any, **_kwargs: Any) -> None:
        self.sets.append(args)


def install_client(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[str, dict[str, Any]], Any],
) -> None:
    RouterClient.calls = []
    RouterClient.handler = handler
    monkeypatch.setattr(tmdb, "_tmdb_client", lambda **_kwargs: RouterClient())


def meta(tmp_path: Path, **values: Any) -> Meta:
    defaults: dict[str, Any] = {
        "base_dir": str(tmp_path),
        "uuid": "tmdb",
        "tmdb": 123,
        "tmdb_id": 123,
        "imdb_id": 1234567,
        "tvdb_id": 456,
        "category": "MOVIE",
        "title": "Example",
        "year": 2026,
        "season": "S01",
        "episode": "E01",
        "season_int": 1,
        "episode_int": 1,
        "path": str(tmp_path / "Example.2026.mkv"),
        "filename": "Example.2026.mkv",
        "mode": "cli",
        "manual_language": "",
        "original_language": "en",
        "artwork_url": "",
        "aka": "",
        "anime": False,
        "mal_manual": 0,
        "quickie_search": False,
        "debug": False,
    }
    defaults.update(values)
    return Meta(defaults)


def movie_payload() -> dict[str, Any]:
    return {
        "id": 123,
        "title": "Example",
        "original_title": "Example Original",
        "release_date": "2026-05-06",
        "runtime": 123,
        "imdb_id": "tt7654321",
        "adult": False,
        "production_companies": [{"name": "Studio"}],
        "production_countries": [{"iso_3166_1": "US"}],
        "overview": "Overview",
        "original_language": "en",
        "poster_path": "/poster.jpg",
        "backdrop_path": "/backdrop.jpg",
        "origin_country": ["US"],
        "genres": [{"id": 28, "name": "Action, Adventure"}],
        "created_by": [{"name": "Creator"}, {"original_name": "Creator"}],
    }


def tv_payload() -> dict[str, Any]:
    return {
        "id": 123,
        "name": "Example 2026",
        "original_name": "Original Example",
        "first_air_date": "",
        "last_air_date": "2026-12-31",
        "status": "Returning Series",
        "episode_run_time": [],
        "type": "Scripted",
        "networks": [{"name": "Network"}],
        "adult": False,
        "production_companies": [],
        "production_countries": [],
        "overview": "Overview",
        "original_language": "ja",
        "poster_path": "",
        "backdrop_path": "",
        "origin_country": ["JP"],
        "genres": [{"id": 16, "name": "Animation"}],
        "created_by": [{"name": "Creator"}],
    }


def endpoint_payload(url: str, *, category: str = "MOVIE") -> Response:
    if url.endswith("/external_ids"):
        return Response({"imdb_id": "tt7654321", "tvdb_id": "654"})
    if url.endswith("/videos"):
        return Response(
            {
                "results": [
                    {"site": "YouTube", "type": "Trailer", "key": "trailer"}
                ]
            }
        )
    if url.endswith("/keywords"):
        key = "keywords" if category == "MOVIE" else "results"
        return Response({key: [{"name": "action,hero"}]})
    if url.endswith("/credits"):
        return Response(
            {
                "cast": [
                    {
                        "known_for_department": "Acting",
                        "original_name": "Actor",
                    }
                ],
                "crew": [{"job": "Director", "name": "Director"}],
            }
        )
    if url.endswith("/images"):
        return Response(
            {"logos": [{"iso_639_1": "en", "file_path": "/logo.png"}]}
        )
    if "/movie/" in url:
        return Response(movie_payload())
    return Response(tv_payload())


def test_client_missing_credential_and_title_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as context:
        context.setattr(tmdb, "_tmdb_credential", None)
        with pytest.raises(TmdbCredentialMissingError):
            tmdb._tmdb_client()
    assert asyncio.run(tmdb.normalize_title("  A &  B  ")) == "a and b"
    assert (
        tmdb.extract_imdb_id("https://www.imdb.com/title/tt1234567/")
        == 1234567
    )
    assert tmdb.extract_imdb_id("tt1234567") == 1234567
    assert tmdb.extract_imdb_id("1234567") == 1234567
    assert tmdb.extract_imdb_id("invalid") is None


def test_get_tmdb_from_imdb_external_preferences_tvdb_and_manual_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    both = {
        "movie_results": [{"id": 11, "original_language": "en"}],
        "tv_results": [{"id": 22, "original_language": "ja"}],
    }
    install_client(monkeypatch, lambda _url, _kwargs: Response(both))
    assert asyncio.run(
        tmdb.get_tmdb_from_imdb(123, category_preference="MOVIE")
    ) == ("MOVIE", 11, "en", False)
    assert asyncio.run(
        tmdb.get_tmdb_from_imdb("123", category_preference="TV")
    ) == ("TV", 22, "ja", False)
    assert asyncio.run(tmdb.get_tmdb_from_imdb(123)) == (
        "MOVIE",
        11,
        "en",
        False,
    )

    install_client(
        monkeypatch,
        lambda _url, _kwargs: Response(
            {"tv_results": [{"id": 22, "original_language": "ja"}]}
        ),
    )
    assert asyncio.run(tmdb.get_tmdb_from_imdb("tt1234567")) == (
        "TV",
        22,
        "ja",
        False,
    )

    responses = iter(
        [
            Response({}),
            Response({"tv_results": [{"id": 33, "original_language": "fr"}]}),
        ]
    )
    install_client(monkeypatch, lambda _url, _kwargs: next(responses))
    assert asyncio.run(tmdb.get_tmdb_from_imdb(123, tvdb_id=456)) == (
        "TV",
        33,
        "fr",
        False,
    )

    install_client(monkeypatch, lambda _url, _kwargs: Response({}, 500))
    monkeypatch.setattr(
        tmdb.imdb_manager,
        "get_imdb_info_api",
        lambda *_args, **_kwargs: async_value(
            {"title": "Fallback", "year": 2026}
        ),
    )
    results = iter([(0, "MOVIE"), (44, "TV")])
    monkeypatch.setattr(
        tmdb,
        "get_tmdb_id",
        lambda *_args, **_kwargs: async_value(next(results)),
    )
    assert asyncio.run(tmdb.get_tmdb_from_imdb(123, filename="Fallback")) == (
        "TV",
        44,
        "en",
        True,
    )

    results = iter([(0, "MOVIE"), (0, "TV")])
    monkeypatch.setattr(
        tmdb,
        "get_tmdb_id",
        lambda *_args, **_kwargs: async_value(next(results)),
    )
    monkeypatch.setattr(
        tmdb,
        "prompt_in_thread",
        lambda *_args, **_kwargs: async_value("movie/55"),
    )
    assert asyncio.run(
        tmdb.get_tmdb_from_imdb(123, filename="Fallback", mode="cli")
    ) == ("MOVIE", 55, "en", True)
    assert asyncio.run(tmdb.get_tmdb_from_imdb(None)) == ("", 0, "", False)


def search_handler(_url: str, kwargs: dict[str, Any]) -> Response:
    query = str(kwargs.get("params", {}).get("query", ""))
    if "single" in query.casefold():
        return Response(
            {
                "results": [
                    {
                        "id": 101,
                        "title": "Single",
                        "release_date": "2026-01-01",
                        "overview": "One",
                    }
                ]
            }
        )
    if "multiple" in query.casefold():
        return Response(
            {
                "results": [
                    {
                        "id": 201,
                        "title": "Other Name",
                        "original_title": "Other",
                        "release_date": "2026-01-01",
                        "overview": "A",
                    },
                    {
                        "id": 202,
                        "title": "Second Name",
                        "original_title": "Second",
                        "release_date": "2025-01-01",
                        "overview": "B",
                    },
                ]
            }
        )
    if "exact" in query.casefold():
        return Response(
            {
                "results": [
                    {
                        "id": 301,
                        "title": "Exact Movie",
                        "original_title": "Exact Movie",
                        "release_date": "2026-01-01",
                    },
                    {
                        "id": 302,
                        "title": "Different",
                        "original_title": "Different",
                        "release_date": "2026-01-01",
                    },
                ]
            }
        )
    return Response({"results": []})


def test_get_tmdb_id_single_multiple_manual_unattended_and_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_client(monkeypatch, search_handler)
    monkeypatch.setattr(
        tmdb.asyncio, "sleep", lambda *_args, **_kwargs: async_value(None)
    )
    assert asyncio.run(tmdb.get_tmdb_id("single", 2026, "MOVIE")) == (
        101,
        "MOVIE",
    )
    assert asyncio.run(
        tmdb.get_tmdb_id("single", 2026, "TV", attempted=1)
    ) == (101, "TV")
    assert asyncio.run(tmdb.get_tmdb_id("exact movie", 2026, "MOVIE")) == (
        301,
        "MOVIE",
    )
    assert asyncio.run(
        tmdb.get_tmdb_id("multiple", 2026, "MOVIE", unattended=True)
    ) == (201, "MOVIE")

    selections = iter(["bad", "99", "movie/444"])
    monkeypatch.setattr(
        tmdb,
        "prompt_in_thread",
        lambda *_args, **_kwargs: async_value(next(selections)),
    )
    assert asyncio.run(tmdb.get_tmdb_id("multiple", 2026, "MOVIE")) == (
        444,
        "MOVIE",
    )

    monkeypatch.setattr(
        tmdb, "prompt_in_thread", lambda *_args, **_kwargs: async_value("2")
    )
    assert asyncio.run(tmdb.get_tmdb_id("multiple", 2026, "MOVIE")) == (
        202,
        "MOVIE",
    )

    monkeypatch.setattr(
        tmdb, "guessit_fn", lambda *_args, **_kwargs: {"title": ""}
    )
    monkeypatch.setattr(tmdb, "anitopy_parse_fn", lambda *_args, **_kwargs: {})
    assert asyncio.run(
        tmdb.get_tmdb_id(
            "No Match mkv", "bad-year", {"category": "MOVIE"}, unattended=True
        )
    ) == (0, "MOVIE")

    monkeypatch.setattr(
        tmdb,
        "prompt_in_thread",
        lambda *_args, **_kwargs: async_value("tv/777"),
    )
    assert asyncio.run(tmdb.get_tmdb_id("No Match", None, "TV")) == (777, "TV")

    async def eof(*_args: object, **_kwargs: object) -> str:
        raise EOFError

    monkeypatch.setattr(tmdb, "prompt_in_thread", eof)
    monkeypatch.setattr(
        tmdb.cleanup_manager, "cleanup", lambda: async_value(None)
    )
    monkeypatch.setattr(tmdb.cleanup_manager, "reset_terminal", lambda: None)
    with pytest.raises(OperationAbortedError):
        asyncio.run(tmdb.get_tmdb_id("No Match", None, "MOVIE"))


def test_tmdb_other_meta_movie_tv_cache_failure_and_lookup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = Cache()
    monkeypatch.setattr(tmdb, "cache_for", lambda *_args, **_kwargs: cache)
    tmdb.default_config = {"add_logo": True, "logo_language": "en"}
    install_client(
        monkeypatch,
        lambda url, _kwargs: endpoint_payload(url, category="MOVIE"),
    )
    monkeypatch.setattr(
        tmdb,
        "get_anime",
        lambda *_args, **_kwargs: async_value((7, " AKA", True, "Shounen")),
    )
    data = asyncio.run(
        tmdb.tmdb_other_meta(
            123,
            category="MOVIE",
            imdb_id=1234567,
            quickie_search=True,
            mal_manual=8,
            base_dir=str(tmp_path),
            config={"DEFAULT": tmdb.default_config},
        )
    )
    assert data["title"] == "Example"
    assert data["imdb_mismatch"] is True
    assert data["mismatched_imdb_id"] == 7654321
    assert data["mal_id"] == 8
    assert data["youtube"].endswith("trailer")
    assert data["logo"].endswith("logo.png")
    assert data["keywords"] == ["action hero"]
    assert cache.sets

    install_client(
        monkeypatch, lambda url, _kwargs: endpoint_payload(url, category="TV")
    )
    monkeypatch.setattr(
        tmdb,
        "get_anime",
        lambda *_args, **_kwargs: async_value((0, "", False, "")),
    )
    tv = asyncio.run(
        tmdb.tmdb_other_meta(
            123, category="TV", base_dir=str(tmp_path), config={"DEFAULT": {}}
        )
    )
    assert tv["title"] == "Example 2026"
    assert tv["year"] == 2026
    assert tv["runtime"] == 60
    assert tv["networks"] == [{"name": "Network"}]

    cache.value = movie_payload()
    install_client(monkeypatch, lambda url, _kwargs: endpoint_payload(url))
    assert (
        asyncio.run(
            tmdb.tmdb_other_meta(
                123,
                category="MOVIE",
                base_dir=str(tmp_path),
                config={"DEFAULT": {}},
            )
        )["title"]
        == "Example"
    )

    cache.value = object()
    install_client(monkeypatch, lambda _url, _kwargs: Response({}, 500))
    assert (
        asyncio.run(
            tmdb.tmdb_other_meta(
                123,
                category="MOVIE",
                base_dir=str(tmp_path),
                config={"DEFAULT": {}},
            )
        )
        == {}
    )

    monkeypatch.setattr(
        tmdb, "guessit_fn", lambda *_args, **_kwargs: {"title": "lookup"}
    )
    lookup_results = iter([(0, "MOVIE"), (0, "MOVIE")])
    monkeypatch.setattr(
        tmdb,
        "get_tmdb_id",
        lambda *_args, **_kwargs: async_value(next(lookup_results)),
    )
    assert (
        asyncio.run(
            tmdb.tmdb_other_meta(
                0, path="lookup", category="MOVIE", mode="non_cli"
            )
        )
        == {}
    )
    lookup_results = iter([(0, "MOVIE"), (0, "MOVIE")])
    monkeypatch.setattr(
        tmdb,
        "get_tmdb_id",
        lambda *_args, **_kwargs: async_value(next(lookup_results)),
    )
    with pytest.raises(OperationAbortedError):
        asyncio.run(
            tmdb.tmdb_other_meta(
                0, path="lookup", category="MOVIE", mode="cli"
            )
        )


def test_tmdb_small_helpers_success_and_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_client(
        monkeypatch,
        lambda url, _kwargs: endpoint_payload(url, category="MOVIE"),
    )
    assert asyncio.run(tmdb.get_keywords(123, "MOVIE")) == ["action hero"]
    assert asyncio.run(tmdb.get_directors(123, "MOVIE")) == ["Director"]
    assert asyncio.run(tmdb.get_genres(movie_payload())) == {
        "genre_names": ["Action  Adventure"],
        "genre_ids": "28",
    }
    assert asyncio.run(tmdb.get_genres(None)) == {
        "genre_names": [],
        "genre_ids": "",
    }

    install_client(monkeypatch, lambda _url, _kwargs: Response({}, 500))
    assert asyncio.run(tmdb.get_keywords(123, "TV")) == []
    assert asyncio.run(tmdb.get_directors(123, "TV")) == []
    install_client(monkeypatch, lambda _url, _kwargs: RuntimeError("network"))
    assert asyncio.run(tmdb.get_keywords(123, "MOVIE")) == []
    assert asyncio.run(tmdb.get_directors(123, "MOVIE")) == []

    assert asyncio.run(
        tmdb.get_anime({"genres": []}, Meta(title="Title", aka="", mal_id=0))
    ) == (0, "", False, "")
    anime_data = {
        "genres": [{"id": 16}],
        "origin_country": ["JP"],
        "original_language": "ja",
        "name": "Anime",
    }
    monkeypatch.setattr(
        tmdb,
        "get_romaji",
        lambda *_args, **_kwargs: async_value(
            ("Anime Romaji", 123, "Anime", "2026", 12, "Shounen")
        ),
    )
    assert asyncio.run(
        tmdb.get_anime(anime_data, Meta(title="Anime", aka="", mal_id=0))
    ) == (123, "AKA Anime Romaji", True, "Shounen")

    assert asyncio.run(
        tmdb.get_tmdb_imdb_from_mediainfo(
            {
                "media": {
                    "track": [
                        {"extra": {"TMDB": "55", "IMDB": "tt66", "TVDB": "77"}}
                    ]
                }
            },
            "MOVIE",
            False,
            0,
            0,
            0,
        )
    ) == (
        "MOVIE",
        55,
        66,
        77,
    )
    assert asyncio.run(
        tmdb.get_tmdb_imdb_from_mediainfo(
            {"media": {"track": [{}]}}, "TV", True, 1, 2, 3
        )
    ) == ("TV", 1, 2, 3)


def test_daily_episode_season_logo_translation_and_localized(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    responses = iter(
        [
            Response(
                {
                    "seasons": [
                        {"season_number": 1, "air_date": None},
                        {"season_number": 2, "air_date": "2026-01-01"},
                    ]
                }
            ),
            Response(
                {"episodes": [{"episode_number": 3, "air_date": "2026-02-02"}]}
            ),
        ]
    )
    install_client(monkeypatch, lambda _url, _kwargs: next(responses))
    assert asyncio.run(
        tmdb.daily_to_tmdb_season_episode(123, "2026-02-02")
    ) == (2, 3)

    episode = {
        "name": "Episode",
        "overview": "Overview",
        "air_date": "2026-01-01",
        "still_path": "/still.jpg",
        "vote_average": 8,
        "episode_number": 1,
        "season_number": 2,
        "runtime": 50,
        "external_ids": {"imdb_id": "tt1"},
        "crew": [
            {"name": "Director", "job": "Director", "department": "Directing"},
            {"name": "Writer", "job": "Writer", "department": "Writing"},
        ],
        "guest_stars": [
            {
                "name": "Guest",
                "character": "Role",
                "profile_path": "/guest.jpg",
            }
        ],
    }
    install_client(monkeypatch, lambda _url, _kwargs: Response(episode))
    details = asyncio.run(tmdb.get_episode_details(123, 2, 1))
    assert (
        details["director"] == "Director"
        and details["writer"] == "Writer"
        and details["guest_stars"]
    )

    season = {
        "_id": "x",
        "air_date": "2026-01-01",
        "name": "Season",
        "overview": "Overview",
        "id": 2,
        "poster_path": "/p.jpg",
        "season_number": 2,
        "vote_average": 8,
        "vote_count": 9,
        "episodes": [episode],
    }
    install_client(monkeypatch, lambda _url, _kwargs: Response(season))
    assert (
        asyncio.run(tmdb.get_season_details(123, 2))["episodes"][0]["name"]
        == "Episode"
    )

    logo_json = {
        "logos": [
            {"iso_639_1": "fr", "file_path": "/fr.png"},
            {"iso_639_1": None, "file_path": "/none.png"},
        ]
    }
    tmdb.default_config = {"logo_language": "fr"}
    assert asyncio.run(
        tmdb.get_logo(123, "MOVIE", logo_json=logo_json)
    ).endswith("fr.png")
    tmdb.default_config = {"logo_language": "de"}
    assert asyncio.run(
        tmdb.get_logo(123, "MOVIE", logo_json=logo_json)
    ).endswith("none.png")

    translations = {
        "translations": [{"iso_639_1": "pt", "data": {"title": "Título"}}]
    }
    install_client(monkeypatch, lambda _url, _kwargs: Response(translations))
    assert (
        asyncio.run(tmdb.get_tmdb_translations(123, "MOVIE", "pt")) == "Título"
    )
    assert asyncio.run(tmdb.get_tmdb_translations(123, "MOVIE", "de")) == ""

    cache = Cache()
    monkeypatch.setattr(tmdb, "cache_for", lambda *_args, **_kwargs: cache)
    install_client(
        monkeypatch, lambda _url, _kwargs: Response({"title": "Localized"})
    )
    main = meta(tmp_path)
    assert (
        asyncio.run(
            tmdb.get_tmdb_localized_data(main, "main", "pt-BR", "credits")
        )["title"]
        == "Localized"
    )
    assert cache.sets
    assert (
        asyncio.run(
            tmdb.get_tmdb_localized_data(
                meta(tmp_path, tmdb=None), "main", "pt", ""
            )
        )
        == {}
    )
    assert (
        asyncio.run(
            tmdb.get_tmdb_localized_data(
                meta(tmp_path, season_int=None), "season", "pt", ""
            )
        )
        == {}
    )
    assert (
        asyncio.run(
            tmdb.get_tmdb_localized_data(
                meta(tmp_path, episode_int=None), "episode", "pt", ""
            )
        )
        == {}
    )


def test_set_tmdb_metadata_retry_success_and_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = 0

    async def flaky(*_args: object, **_kwargs: object) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {} if calls == 1 else {"title": "Resolved", "year": 2026}

    monkeypatch.setattr(tmdb, "tmdb_other_meta", flaky)
    monkeypatch.setattr(
        tmdb.asyncio, "sleep", lambda *_args, **_kwargs: async_value(None)
    )
    target = meta(tmp_path, edit=True)
    asyncio.run(tmdb.set_tmdb_metadata(target, "Example"))
    assert target.title == "Resolved" and calls == 2

    monkeypatch.setattr(
        tmdb, "tmdb_other_meta", lambda *_args, **_kwargs: async_value({})
    )
    with pytest.raises(RuntimeError):
        asyncio.run(
            tmdb.set_tmdb_metadata(meta(tmp_path, edit=True), "Example")
        )


def async_value(value: Any):
    async def resolved() -> Any:
        return value

    return resolved()


def test_manager_wrappers_delegate_all_operations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = tmdb.TmdbManager({"DEFAULT": {"tmdb_api": "manager-key"}})
    monkeypatch.setattr(
        tmdb,
        "normalize_title",
        lambda *_args, **_kwargs: async_value("normalized"),
    )
    monkeypatch.setattr(
        tmdb,
        "get_tmdb_from_imdb",
        lambda *_args, **_kwargs: async_value(("MOVIE", 1, "en", False)),
    )
    monkeypatch.setattr(
        tmdb, "get_tmdb_id", lambda *_args, **_kwargs: async_value((2, "TV"))
    )
    monkeypatch.setattr(
        tmdb,
        "tmdb_other_meta",
        lambda *_args, **_kwargs: async_value({"title": "meta"}),
    )
    monkeypatch.setattr(
        tmdb, "get_keywords", lambda *_args, **_kwargs: async_value(["key"])
    )
    monkeypatch.setattr(
        tmdb,
        "get_genres",
        lambda *_args, **_kwargs: async_value(
            {"genre_names": ["Action"], "genre_ids": "28"}
        ),
    )
    monkeypatch.setattr(
        tmdb,
        "get_directors",
        lambda *_args, **_kwargs: async_value(["Director"]),
    )
    monkeypatch.setattr(
        tmdb,
        "get_anime",
        lambda *_args, **_kwargs: async_value((1, "AKA", True, "Shounen")),
    )
    monkeypatch.setattr(
        tmdb,
        "get_romaji",
        lambda *_args, **_kwargs: async_value(
            ("Romaji", 1, "English", "2026", 12, "Shounen")
        ),
    )
    monkeypatch.setattr(
        tmdb,
        "get_tmdb_imdb_from_mediainfo",
        lambda *_args, **_kwargs: async_value(("MOVIE", 1, 2, 3)),
    )
    monkeypatch.setattr(
        tmdb,
        "daily_to_tmdb_season_episode",
        lambda *_args, **_kwargs: async_value((1, 2)),
    )
    monkeypatch.setattr(
        tmdb,
        "get_episode_details",
        lambda *_args, **_kwargs: async_value({"name": "Episode"}),
    )
    monkeypatch.setattr(
        tmdb,
        "get_season_details",
        lambda *_args, **_kwargs: async_value({"name": "Season"}),
    )
    monkeypatch.setattr(
        tmdb, "get_logo", lambda *_args, **_kwargs: async_value("logo")
    )
    monkeypatch.setattr(
        tmdb,
        "get_tmdb_translations",
        lambda *_args, **_kwargs: async_value("translated"),
    )
    monkeypatch.setattr(
        tmdb, "set_tmdb_metadata", lambda *_args, **_kwargs: async_value(None)
    )
    monkeypatch.setattr(
        tmdb,
        "get_tmdb_localized_data",
        lambda *_args, **_kwargs: async_value({"title": "localized"}),
    )

    target = meta(tmp_path)
    assert asyncio.run(manager.normalize_title("title")) == "normalized"
    assert asyncio.run(manager.get_tmdb_from_imdb(1)) == (
        "MOVIE",
        1,
        "en",
        False,
    )
    assert asyncio.run(manager.get_tmdb_id("title", 2026, "MOVIE")) == (
        2,
        "TV",
    )
    assert asyncio.run(manager.tmdb_other_meta(1, base_dir=str(tmp_path))) == {
        "title": "meta"
    }
    assert asyncio.run(manager.get_keywords(1, "MOVIE")) == ["key"]
    assert asyncio.run(manager.get_genres({})) == {
        "genre_names": ["Action"],
        "genre_ids": "28",
    }
    assert asyncio.run(manager.get_directors(1, "MOVIE")) == ["Director"]
    assert asyncio.run(manager.get_anime({}, target)) == (
        1,
        "AKA",
        True,
        "Shounen",
    )
    assert asyncio.run(manager.get_romaji("title", 1, target))[0] == "Romaji"
    assert asyncio.run(manager.get_tmdb_imdb_from_mediainfo({}, target)) == (
        "MOVIE",
        1,
        2,
        3,
    )
    assert manager.extract_imdb_id("tt1234567") == 1234567
    assert asyncio.run(
        manager.daily_to_tmdb_season_episode(
            1, datetime(2026, 1, 1, tzinfo=UTC)
        )
    ) == (1, 2)
    assert asyncio.run(manager.get_episode_details(1, 1, 1)) == {
        "name": "Episode"
    }
    assert asyncio.run(manager.get_season_details(1, 1)) == {"name": "Season"}
    assert asyncio.run(manager.get_logo(1, "MOVIE")) == "logo"
    assert (
        asyncio.run(manager.get_tmdb_translations(1, "MOVIE")) == "translated"
    )
    asyncio.run(manager.set_tmdb_metadata(target, "name"))
    assert asyncio.run(
        manager.get_tmdb_localized_data(target, "main", "pt", "")
    ) == {"title": "localized"}


def test_search_transformations_failures_translations_and_tv_boost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tmdb.asyncio, "sleep", lambda *_args, **_kwargs: async_value(None)
    )

    def transformed(url: str, kwargs: dict[str, Any]) -> Response:
        query = str(kwargs.get("params", {}).get("query", ""))
        year = str(
            kwargs.get("params", {}).get("year")
            or kwargs.get("params", {}).get("first_air_date_year")
            or ""
        )
        if (
            query
            in {"Movie 2", "Secondary", "Guessed", "One Two", "One Two Three"}
            or year == "2027"
            or "/search/tv" in url
        ):
            key = "name" if "/search/tv" in url else "title"
            date_key = "first_air_date" if key == "name" else "release_date"
            return Response(
                {
                    "results": [
                        {
                            "id": 900 + len(query),
                            key: query or "TV",
                            date_key: "2027-01-01",
                        }
                    ]
                }
            )
        return Response({"results": []})

    install_client(monkeypatch, transformed)
    assert asyncio.run(
        tmdb.get_tmdb_id("Movie II", 2026, "MOVIE", unattended=True)
    )[0]
    assert asyncio.run(
        tmdb.get_tmdb_id(
            "Nothing",
            2026,
            "MOVIE",
            secondary_title="Secondary",
            unattended=True,
        )
    )[0]
    assert asyncio.run(
        tmdb.get_tmdb_id("Nothing", 2026, "MOVIE", unattended=True)
    )[0]  # year+1 or TV fallback

    install_client(
        monkeypatch,
        lambda _url, kwargs: (
            Response(
                {
                    "results": [
                        {
                            "id": 77,
                            "title": "Guessed",
                            "release_date": "2026-01-01",
                        }
                    ]
                }
            )
            if kwargs.get("params", {}).get("query") == "Guessed"
            else Response({"results": []})
        ),
    )
    monkeypatch.setattr(
        tmdb, "guessit_fn", lambda *_args, **_kwargs: {"title": "Guessed"}
    )
    assert asyncio.run(
        tmdb.get_tmdb_id("Unparsed Filename", 2026, "MOVIE", unattended=True)
    ) == (77, "MOVIE")

    install_client(
        monkeypatch,
        lambda _url, kwargs: (
            Response(
                {
                    "results": [
                        {
                            "id": 88,
                            "title": "One Two",
                            "release_date": "2026-01-01",
                        }
                    ]
                }
            )
            if kwargs.get("params", {}).get("query")
            in {"One Two", "One Two Three"}
            else Response({"results": []})
        ),
    )
    monkeypatch.setattr(tmdb, "guessit_fn", lambda *_args, **_kwargs: {})
    assert (
        asyncio.run(
            tmdb.get_tmdb_id(
                "One Two Three Four mkv", 2026, "MOVIE", unattended=True
            )
        )[0]
        == 88
    )

    failure = Response({}, 500)
    install_client(monkeypatch, lambda _url, _kwargs: failure)
    monkeypatch.setattr(
        tmdb,
        "anitopy_parse_fn",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyError("bad parse")),
    )
    monkeypatch.setattr(
        tmdb,
        "prompt_in_thread",
        lambda *_args, **_kwargs: async_value("movie/999"),
    )
    assert asyncio.run(tmdb.get_tmdb_id("Unknown", None, "MOVIE")) == (
        999,
        "MOVIE",
    )

    results = [
        {
            "id": 1,
            "title": "The Example",
            "original_title": "Exemple",
            "release_date": "2026-01-01",
            "overview": "A",
        },
        {
            "id": 2,
            "title": "Other",
            "original_title": "Autre",
            "release_date": "2025-01-01",
            "overview": "B",
        },
    ]
    install_client(
        monkeypatch, lambda _url, _kwargs: Response({"results": results})
    )
    monkeypatch.setattr(
        tmdb,
        "get_tmdb_translations",
        lambda item_id, *_args, **_kwargs: async_value(
            "Example" if item_id == 1 else "Other"
        ),
    )
    assert (
        asyncio.run(
            tmdb.get_tmdb_id(
                "Example",
                2026,
                "MOVIE",
                secondary_title="Example",
                unattended=True,
            )
        )[0]
        == 1
    )

    tv_results = [
        {
            "id": 10,
            "name": "The Show",
            "original_name": "Show",
            "first_air_date": "2026-01-01",
        },
        {
            "id": 11,
            "name": "Different",
            "original_name": "Different",
            "first_air_date": "2026-01-01",
        },
    ]
    install_client(
        monkeypatch, lambda _url, _kwargs: Response({"results": tv_results})
    )
    assert (
        asyncio.run(tmdb.get_tmdb_id("Show", None, "TV", unattended=True))[0]
        == 10
    )


def test_get_romaji_cached_title_mal_retry_error_and_season_matching(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    anime = {
        "id": 1,
        "idMal": 101,
        "title": {
            "romaji": "Example Season 2",
            "english": "Example Season 2",
            "native": "例",
        },
        "seasonYear": 2026,
        "episodes": 12,
        "tags": [{"name": "Shounen"}],
    }
    other = {
        "id": 2,
        "idMal": 102,
        "title": {"romaji": "Example", "english": "Example", "native": "例"},
        "seasonYear": None,
        "episodes": None,
        "tags": [],
    }

    class AniResponse(Response):
        text = "Shounen"

    class AniClient(RouterClient):
        outcomes: ClassVar[list[Any]] = []

        async def post(self, _url: str, **_kwargs: Any) -> Any:
            outcome = self.__class__.outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

    cache = Cache()
    monkeypatch.setattr(tmdb, "cache_for", lambda *_args, **_kwargs: cache)
    monkeypatch.setattr(tmdb.httpx, "AsyncClient", AniClient)
    monkeypatch.setattr(
        tmdb.asyncio, "sleep", lambda *_args, **_kwargs: async_value(None)
    )
    monkeypatch.setattr(
        tmdb,
        "anitopy_parse_fn",
        lambda *_args, **_kwargs: {"anime_season": "2"},
    )
    response = AniResponse({"data": {"Page": {"media": [anime, other]}}})
    response.text = "Shounen"
    AniClient.outcomes = [response]
    result = asyncio.run(
        tmdb.get_romaji(
            "Example",
            None,
            meta(tmp_path, filename="Example Season 2.mkv", season="S02"),
        )
    )
    assert result == (
        "Example Season 2",
        101,
        "Example Season 2",
        "2026",
        12,
        "Shounen",
    )
    assert cache.sets

    cache.value = {"media": [anime], "demographic": "Seinen"}
    result = asyncio.run(
        tmdb.get_romaji("Example", 999, meta(tmp_path, manual_season="S02"))
    )
    assert result[1] == 999 and result[-1] == "Seinen"

    cache.value = object()
    AniClient.outcomes = [
        httpx.ReadTimeout("timeout"),
        AniResponse({"data": {"Page": {"media": [anime]}}}),
    ]
    assert (
        asyncio.run(tmdb.get_romaji("Example", None, meta(tmp_path)))[0]
        == "Example Season 2"
    )

    AniClient.outcomes = [RuntimeError("failed")]
    assert asyncio.run(
        tmdb.get_romaji("Example", None, meta(tmp_path, filename=""))
    ) == ("", 0, "", "", 0, "Mina")


def test_helper_edge_payloads_and_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Genres without IDs/names and credits without matching jobs.
    assert asyncio.run(
        tmdb.get_genres({"genres": [{"id": None}, {"name": "Drama"}, {}]})
    ) == {
        "genre_names": ["Drama"],
        "genre_ids": "",
    }
    install_client(
        monkeypatch,
        lambda url, _kwargs: (
            Response({"crew": [{"job": "Writer", "name": "Writer"}]})
            if url.endswith("credits")
            else Response({})
        ),
    )
    assert asyncio.run(tmdb.get_directors(1, "MOVIE")) == []

    responses = iter([Response({"seasons": []}), Response({"episodes": []})])
    install_client(monkeypatch, lambda _url, _kwargs: next(responses))
    assert asyncio.run(tmdb.daily_to_tmdb_season_episode(1, "2026-01-01")) == (
        1,
        1,
    )
    install_client(
        monkeypatch,
        lambda _url, _kwargs: Response(
            {"seasons": [{"season_number": 1, "air_date": "bad-date"}]}
        ),
    )
    with pytest.raises(ValueError):
        asyncio.run(tmdb.daily_to_tmdb_season_episode(1, "2026-01-01"))

    install_client(monkeypatch, lambda _url, _kwargs: Response({}, 500))
    assert asyncio.run(tmdb.get_episode_details(1, 1, 1)) == {}
    assert asyncio.run(tmdb.get_season_details(1, 1)) == {}
    install_client(monkeypatch, lambda _url, _kwargs: RuntimeError("network"))
    assert asyncio.run(tmdb.get_episode_details(1, 1, 1)) == {}
    assert asyncio.run(tmdb.get_season_details(1, 1)) == {}

    assert (
        asyncio.run(tmdb.get_logo(1, "MOVIE", logo_json={"logos": []})) == ""
    )
    install_client(monkeypatch, lambda _url, _kwargs: Response({}, 500))
    assert asyncio.run(tmdb.get_logo(1, "MOVIE")) == ""
    install_client(monkeypatch, lambda _url, _kwargs: RuntimeError("network"))
    assert asyncio.run(tmdb.get_logo(1, "MOVIE")) == ""
    assert asyncio.run(tmdb.get_tmdb_translations(0, "MOVIE", "en")) == ""
    install_client(monkeypatch, lambda _url, _kwargs: Response({}, 500))
    assert asyncio.run(tmdb.get_tmdb_translations(1, "MOVIE", "en")) == ""

    cache = Cache({"title": "Cached"})
    monkeypatch.setattr(tmdb, "cache_for", lambda *_args, **_kwargs: cache)
    assert asyncio.run(
        tmdb.get_tmdb_localized_data(meta(tmp_path), "main", "pt", "")
    ) == {"title": "Cached"}
    cache.value = object()
    install_client(monkeypatch, lambda _url, _kwargs: Response({}, 500))
    assert (
        asyncio.run(
            tmdb.get_tmdb_localized_data(meta(tmp_path), "main", "pt", "")
        )
        == {}
    )
    install_client(
        monkeypatch,
        lambda _url, _kwargs: httpx.ReadError(
            "network", request=httpx.Request("GET", "https://example.invalid")
        ),
    )
    assert (
        asyncio.run(
            tmdb.get_tmdb_localized_data(meta(tmp_path), "main", "pt", "")
        )
        == {}
    )


def test_search_similarity_secondary_translation_the_prefix_and_prompt_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = [
        {
            "id": 501,
            "title": "The Example",
            "original_title": "Exemple",
            "release_date": "2026-01-01",
            "overview": "A" * 250,
        },
        {
            "id": 502,
            "title": "Other",
            "original_title": "Example Alternate",
            "release_date": "2027-01-01",
            "overview": "B",
        },
    ]
    install_client(
        monkeypatch, lambda _url, _kwargs: Response({"results": results})
    )
    monkeypatch.setattr(
        tmdb,
        "get_tmdb_translations",
        lambda item_id, *_args, **_kwargs: async_value(
            "Example" if item_id == 501 else "Alternate"
        ),
    )
    # The first result needs prefix normalization and translation; the second
    # contributes secondary-title weighting and +1-year boosting.
    assert asyncio.run(
        tmdb.get_tmdb_id(
            "Example",
            2026,
            "MOVIE",
            secondary_title="Example Alternate",
            unattended=True,
        )
    )[0] in {501, 502}

    tv_results = [
        {
            "id": 601,
            "name": "The Example",
            "original_name": "Exemple",
            "first_air_date": "2027-01-01",
            "overview": "A",
        },
        {
            "id": 602,
            "name": "Other",
            "original_name": "Other",
            "first_air_date": "2026-01-01",
            "overview": "B",
        },
    ]
    install_client(
        monkeypatch, lambda _url, _kwargs: Response({"results": tv_results})
    )
    monkeypatch.setattr(
        tmdb,
        "get_tmdb_translations",
        lambda *_args, **_kwargs: async_value(""),
    )
    assert (
        asyncio.run(tmdb.get_tmdb_id("Example", 2026, "TV", unattended=True))[
            0
        ]
        == 601
    )

    install_client(
        monkeypatch, lambda _url, _kwargs: Response({"results": results})
    )
    selections = iter(["", "movie/not-a-number", "bad", "99", "1"])
    monkeypatch.setattr(
        tmdb,
        "prompt_in_thread",
        lambda *_args, **_kwargs: async_value(next(selections, "movie/501")),
    )
    assert asyncio.run(tmdb.get_tmdb_id("unrelated", 2026, "MOVIE"))[0] in {
        501,
        502,
    }

    monkeypatch.setattr(
        tmdb,
        "parse_tmdb_id",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad id")),
    )
    selections = iter(["movie/invalid", "1"])
    monkeypatch.setattr(
        tmdb,
        "prompt_in_thread",
        lambda *_args, **_kwargs: async_value(next(selections)),
    )
    assert asyncio.run(tmdb.get_tmdb_id("unrelated", 2026, "MOVIE"))[0] == 501


def test_tmdb_other_meta_partial_failures_and_alias_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = Cache()
    monkeypatch.setattr(tmdb, "cache_for", lambda *_args, **_kwargs: cache)
    tmdb.default_config = {"add_logo": True, "logo_language": "en"}

    def partial(url: str, _kwargs: dict[str, Any]) -> Any:
        if url.endswith("/external_ids"):
            return RuntimeError("external IDs")
        if url.endswith("/videos"):
            return Response({"results": []})
        if url.endswith("/keywords"):
            return RuntimeError("keywords")
        if url.endswith("/credits"):
            return Response(ValueError("bad credits"))
        if url.endswith("/images"):
            return Response(ValueError("bad logo"))
        payload = movie_payload()
        payload.update(
            {
                "title": "Example",
                "original_title": "Example",
                "release_date": "",
                "runtime": None,
                "imdb_id": None,
                "adult": True,
                "production_companies": None,
                "production_countries": None,
                "overview": "",
                "original_language": "",
                "poster_path": None,
                "backdrop_path": None,
                "origin_country": None,
                "created_by": [{"name": "Creator"}, {"name": "Creator"}, {}],
                "genres": [],
            }
        )
        return Response(payload)

    install_client(monkeypatch, partial)
    monkeypatch.setattr(
        tmdb,
        "get_anime",
        lambda *_args, **_kwargs: async_value(
            (0, " AKA Example (2026)", False, "")
        ),
    )
    data = asyncio.run(
        tmdb.tmdb_other_meta(
            123,
            path="Example.2026.mkv",
            search_year=2026,
            category="MOVIE",
            original_language="pt",
            poster="https://poster.invalid/manual.jpg",
            filename="Example.2026.mkv",
            base_dir=str(tmp_path),
            config={"DEFAULT": tmdb.default_config},
        )
    )
    assert data["tmdb_adult_media"] is True
    assert data["artwork_url"].endswith("manual.jpg")
    assert data["original_language"] == "pt"
    assert data["retrieved_aka"] == "AKA Example"
    assert data["tmdb_directors"] == [] and data["tmdb_cast"] == []
    assert data["logo"] == "" and data["tmdb_logo"] == ""

    # Quick metadata mode intentionally skips the gathered endpoints.
    install_client(
        monkeypatch,
        lambda url, _kwargs: (
            Response(movie_payload())
            if "/movie/" in url
            else RuntimeError("must not be called")
        ),
    )
    monkeypatch.setattr(
        tmdb,
        "get_anime",
        lambda *_args, **_kwargs: async_value((0, "", False, "")),
    )
    quick = asyncio.run(
        tmdb.tmdb_other_meta(
            123,
            category="MOVIE",
            quickie_search=True,
            base_dir=str(tmp_path),
            config={"DEFAULT": {}},
        )
    )
    assert quick["title"] == "Example" and quick["keywords"] == []


def test_get_anime_non_anime_manual_and_no_romaji(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    non_japanese = {
        "genres": [{"id": 16}],
        "origin_country": ["US"],
        "original_language": "en",
    }
    assert asyncio.run(tmdb.get_anime(non_japanese, meta(tmp_path))) == (
        0,
        "",
        False,
        "",
    )

    anime_data = {
        "genres": [{"id": 16}],
        "origin_country": ["JP"],
        "original_language": "ja",
        "title": "Anime",
    }
    monkeypatch.setattr(
        tmdb,
        "get_romaji",
        lambda *_args, **_kwargs: async_value(("", 0, "", "", 0, "Mina")),
    )
    assert asyncio.run(
        tmdb.get_anime(
            anime_data, meta(tmp_path, mal_id=42, mal_manual=42, aka=" AKA")
        )
    ) == (42, " AKA", True, "Mina")


def test_daily_mapping_success_logo_language_fallback_and_episode_sparse_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            Response(
                {"seasons": [{"season_number": 2, "air_date": "2026-01-01"}]}
            ),
            Response(
                {"episodes": [{"episode_number": 4, "air_date": "2026-02-03"}]}
            ),
        ]
    )
    install_client(monkeypatch, lambda _url, _kwargs: next(responses))
    assert asyncio.run(
        tmdb.daily_to_tmdb_season_episode(
            123, datetime(2026, 2, 3, tzinfo=UTC)
        )
    ) == (2, 4)

    sparse = {
        "name": "Sparse",
        "overview": "",
        "air_date": None,
        "still_path": None,
        "vote_average": None,
        "episode_number": 0,
        "season_number": 0,
        "runtime": None,
        "external_ids": {},
        "crew": [
            {"name": "Unknown", "job": "Producer", "department": "Production"}
        ],
        "guest_stars": [
            {"name": "Guest", "character": None, "profile_path": None}
        ],
    }
    install_client(monkeypatch, lambda _url, _kwargs: Response(sparse))
    details = asyncio.run(tmdb.get_episode_details(1, 0, 0))
    assert details["director"] == "" and details["writer"] == ""

    logos = {"logos": [{"iso_639_1": "de", "file_path": "/de.png"}]}
    tmdb.default_config = {"logo_language": "fr"}
    assert asyncio.run(tmdb.get_logo(1, "MOVIE", logo_json=logos)) == ""
    install_client(monkeypatch, lambda _url, _kwargs: Response({"logos": []}))
    assert asyncio.run(tmdb.get_logo(1, "MOVIE")) == ""


def test_set_metadata_retrieved_aka_non_edit_and_localized_variants(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        tmdb,
        "tmdb_other_meta",
        lambda *_args, **_kwargs: async_value(
            {
                "title": "Resolved",
                "year": 2026,
                "retrieved_aka": " AKA Resolved",
            }
        ),
    )
    target = meta(tmp_path, edit=True)
    asyncio.run(tmdb.set_tmdb_metadata(target, "Resolved"))
    assert target.aka == " AKA Resolved"

    untouched = meta(
        tmp_path,
        title="Already",
        year=2026,
        genres=["Action"],
        overview="Overview",
        edit=False,
    )
    asyncio.run(tmdb.set_tmdb_metadata(untouched, "Already"))
    assert untouched.title == "Already"

    cache = Cache()
    monkeypatch.setattr(tmdb, "cache_for", lambda *_args, **_kwargs: cache)
    install_client(
        monkeypatch, lambda _url, _kwargs: Response({"name": "Localized"})
    )
    assert (
        asyncio.run(
            tmdb.get_tmdb_localized_data(
                meta(tmp_path, category="TV"), "season", "pt", "images"
            )
        )["name"]
        == "Localized"
    )
    assert (
        asyncio.run(
            tmdb.get_tmdb_localized_data(
                meta(tmp_path, category="TV"), "episode", "pt", ""
            )
        )["name"]
        == "Localized"
    )


def test_get_tmdb_id_remaining_selection_and_fallback_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tmdb.asyncio, "sleep", lambda *_args, **_kwargs: async_value(None)
    )
    monkeypatch.setattr(
        tmdb,
        "get_tmdb_translations",
        lambda *_args, **_kwargs: async_value(""),
    )

    identical = [
        {
            "id": 701,
            "title": "Exact Name",
            "original_title": "Exact Name",
            "release_date": "2026-01-01",
            "overview": "A",
        },
        {
            "id": 702,
            "title": "Exact Name",
            "original_title": "Exact Name",
            "release_date": "2026-01-01",
            "overview": "B",
        },
    ]
    install_client(
        monkeypatch, lambda _url, _kwargs: Response({"results": identical})
    )
    assert asyncio.run(
        tmdb.get_tmdb_id("Exact Name", 2026, "MOVIE", unattended=True)
    ) == (701, "MOVIE")

    tv_results = [
        {
            "id": 711,
            "name": "Example Show",
            "original_name": "Example Show",
            "first_air_date": "2026-01-01",
            "overview": "A",
        },
        {
            "id": 712,
            "name": "Example Series",
            "original_name": "Example Series",
            "first_air_date": "2026-01-01",
            "overview": "B",
        },
    ]
    install_client(
        monkeypatch, lambda _url, _kwargs: Response({"results": tv_results})
    )
    assert (
        asyncio.run(tmdb.get_tmdb_id("Example", 2026, "TV", unattended=True))[
            0
        ]
        == 711
    )

    choices = iter(["unsupported", "1"])
    install_client(
        monkeypatch, lambda _url, _kwargs: Response({"results": identical})
    )
    monkeypatch.setattr(
        tmdb,
        "prompt_in_thread",
        lambda *_args, **_kwargs: async_value(next(choices)),
    )
    assert asyncio.run(tmdb.get_tmdb_id("Unrelated", None, "MOVIE")) == (
        701,
        "MOVIE",
    )

    def fallback_handler(url: str, kwargs: dict[str, Any]) -> Response:
        query = str(kwargs.get("params", {}).get("query", ""))
        if "/search/tv" in url or query in {
            "Guessed Fallback",
            "Secondary Fallback",
            "Clean Name",
        }:
            key = "name" if "/search/tv" in url else "title"
            date_key = "first_air_date" if key == "name" else "release_date"
            return Response(
                {
                    "results": [
                        {"id": 799, key: query or "TV", date_key: "2026-01-01"}
                    ]
                }
            )
        return Response({"results": []})

    install_client(monkeypatch, fallback_handler)
    monkeypatch.setattr(
        tmdb,
        "guessit_fn",
        lambda *_args, **_kwargs: {"title": "Guessed Fallback"},
    )
    assert (
        asyncio.run(
            tmdb.get_tmdb_id(
                "Unknown.File.mkv", 2026, "MOVIE", unattended=True
            )
        )[0]
        == 799
    )

    monkeypatch.setattr(tmdb, "guessit_fn", lambda *_args, **_kwargs: {})
    assert (
        asyncio.run(
            tmdb.get_tmdb_id(
                "Unknown",
                2026,
                "MOVIE",
                secondary_title="Secondary Fallback",
                unattended=True,
            )
        )[0]
        == 799
    )
    assert (
        asyncio.run(
            tmdb.get_tmdb_id("Clean.Name.mkv", 2026, "MOVIE", unattended=True)
        )[0]
        == 799
    )

    install_client(
        monkeypatch, lambda _url, _kwargs: Response({"results": []})
    )
    monkeypatch.setattr(tmdb, "anitopy_parse_fn", lambda *_args, **_kwargs: {})
    assert asyncio.run(
        tmdb.get_tmdb_id("Absolutely Unknown", None, "MOVIE", unattended=True)
    ) == (0, "MOVIE")


def test_tmdb_other_meta_gather_exception_and_missing_date_branches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = Cache()
    monkeypatch.setattr(tmdb, "cache_for", lambda *_args, **_kwargs: cache)
    payload = movie_payload()
    payload.update(
        {
            "release_date": "",
            "runtime": None,
            "genres": [],
            "origin_country": [],
            "created_by": [],
        }
    )
    install_client(monkeypatch, lambda _url, _kwargs: Response(payload))

    async def exceptional_gather(
        *coroutines: Any, **_kwargs: Any
    ) -> list[Any]:
        for coroutine in coroutines:
            if asyncio.iscoroutine(coroutine):
                coroutine.close()
        return [
            RuntimeError("external"),
            RuntimeError("videos"),
            RuntimeError("keywords"),
            RuntimeError("credits"),
            RuntimeError("images"),
        ]

    monkeypatch.setattr(tmdb.asyncio, "gather", exceptional_gather)
    monkeypatch.setattr(
        tmdb,
        "get_anime",
        lambda *_args, **_kwargs: async_value((0, "", False, "")),
    )
    data = asyncio.run(
        tmdb.tmdb_other_meta(
            123,
            category="MOVIE",
            base_dir=str(tmp_path),
            config={"DEFAULT": {"add_logo": True}},
        )
    )
    assert data["year"] == 0 and data["runtime"] == 0
    assert data["youtube"] == "" and data["logo"] == ""
    assert data["tmdb_directors"] == [] and data["tmdb_cast"] == []


def test_get_keywords_directors_anime_romaji_and_metadata_error_edges(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install_client(
        monkeypatch,
        lambda url, _kwargs: (
            Response(ValueError("bad json"))
            if url.endswith(("keywords", "credits"))
            else Response({})
        ),
    )
    assert asyncio.run(tmdb.get_keywords(1, "MOVIE")) == []
    assert asyncio.run(tmdb.get_directors(1, "MOVIE")) == []

    assert asyncio.run(
        tmdb.get_anime(
            {
                "genres": [{"id": 16}],
                "origin_country": [],
                "original_language": "ja",
            },
            meta(tmp_path),
        )
    ) == (
        0,
        "",
        True,
        "Mina",
    )

    class AniClient(RouterClient):
        outcomes: ClassVar[list[Any]] = []

        async def post(self, _url: str, **_kwargs: Any) -> Any:
            result = self.__class__.outcomes.pop(0)
            if isinstance(result, BaseException):
                raise result
            return result

    cache = Cache()
    monkeypatch.setattr(tmdb, "cache_for", lambda *_args, **_kwargs: cache)
    monkeypatch.setattr(tmdb.httpx, "AsyncClient", AniClient)
    monkeypatch.setattr(
        tmdb.asyncio, "sleep", lambda *_args, **_kwargs: async_value(None)
    )
    AniClient.outcomes = [
        Response({}, status_code=429),
        Response({"data": {"Page": {"media": []}}}),
    ]
    assert asyncio.run(tmdb.get_romaji("Unknown", None, meta(tmp_path))) == (
        "",
        0,
        "",
        "",
        0,
        "Mina",
    )

    class FailingCache(Cache):
        async def set(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("cache")

    monkeypatch.setattr(
        tmdb, "cache_for", lambda *_args, **_kwargs: FailingCache()
    )
    anime = {
        "id": 9,
        "idMal": 10,
        "title": {"romaji": "Cached Failure", "english": "Cached Failure"},
        "seasonYear": 2026,
        "episodes": 1,
        "tags": [],
    }
    AniClient.outcomes = [Response({"data": {"Page": {"media": [anime]}}})]
    assert (
        asyncio.run(tmdb.get_romaji("Cached Failure", None, meta(tmp_path)))[0]
        == "Cached Failure"
    )

    monkeypatch.setattr(
        tmdb,
        "tmdb_other_meta",
        lambda *_args, **_kwargs: async_value(
            {"title": "Resolved", "year": 2026, "retrieved_aka": " AKA Only"}
        ),
    )
    target = meta(
        tmp_path, edit=True, title="", year=0, genres=[], overview=""
    )
    asyncio.run(tmdb.set_tmdb_metadata(target, "Name"))
    assert target.aka == " AKA Only"


def test_daily_season_episode_and_logo_error_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_client(
        monkeypatch, lambda _url, _kwargs: Response({}, status_code=500)
    )
    assert asyncio.run(tmdb.daily_to_tmdb_season_episode(1, "2026-01-01")) == (
        0,
        0,
    )
    install_client(monkeypatch, lambda _url, _kwargs: RuntimeError("network"))
    assert asyncio.run(tmdb.daily_to_tmdb_season_episode(1, "2026-01-01")) == (
        0,
        0,
    )

    responses = iter(
        [
            Response(
                {"seasons": [{"season_number": 1, "air_date": "2025-01-01"}]}
            ),
            Response({}, status_code=500),
        ]
    )
    install_client(monkeypatch, lambda _url, _kwargs: next(responses))
    assert asyncio.run(tmdb.daily_to_tmdb_season_episode(1, "2026-01-01")) == (
        0,
        0,
    )

    install_client(
        monkeypatch, lambda _url, _kwargs: Response({}, status_code=500)
    )
    assert asyncio.run(tmdb.get_logo(1, "MOVIE")) == ""
    install_client(
        monkeypatch, lambda _url, _kwargs: Response(ValueError("bad json"))
    )
    assert asyncio.run(tmdb.get_logo(1, "MOVIE")) == ""
    assert asyncio.run(tmdb.get_logo(1, "MOVIE", logo_json={})) == ""


def test_guessit_payload_wrapper(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        tmdb.guessit_module,
        "guessit",
        lambda value, options=None: {"title": value, "options": options},
    )
    assert tmdb.guessit_fn("Example", {"type": "movie"}) == {
        "title": "Example",
        "options": {"type": "movie"},
    }


def test_get_tmdb_id_parser_type_info_and_result_edge_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tmdb.asyncio, "sleep", lambda *_args, **_kwargs: async_value(None)
    )
    install_client(
        monkeypatch, lambda _url, _kwargs: Response({"results": []})
    )

    parsed = iter([("MOVIE", "not-an-int"), ("MOVIE", 901)])
    monkeypatch.setattr(
        tmdb, "parse_tmdb_id", lambda *_args, **_kwargs: next(parsed)
    )
    prompts = iter(["bad", "movie/901"])
    monkeypatch.setattr(
        tmdb,
        "prompt_in_thread",
        lambda *_args, **_kwargs: async_value(next(prompts)),
    )
    assert asyncio.run(tmdb.get_tmdb_id("No Result", None, "MOVIE")) == (
        901,
        "MOVIE",
    )

    class BrokenTypeInfo:
        def __bool__(self) -> bool:
            return True

        def get(self, _key: str, _default: object = None) -> object:
            raise TypeError("bad mapping")

    install_client(
        monkeypatch,
        lambda url, _kwargs: Response(
            {
                "results": [
                    {
                        "id": 902,
                        "name" if "/tv" in url else "title": "Preferred",
                        "first_air_date"
                        if "/tv" in url
                        else "release_date": "2026-01-01",
                    }
                ]
            }
        ),
    )
    monkeypatch.setattr(
        tmdb,
        "parse_tmdb_id",
        lambda value, category: (
            (category or "MOVIE", int(str(value).split("/")[-1]))
            if str(value).split("/")[-1].isdigit()
            else (category or "MOVIE", 0)
        ),
    )
    assert asyncio.run(
        tmdb.get_tmdb_id("Preferred", 2026, "MOVIE", category_preference="TV")
    ) == (902, "TV")
    assert asyncio.run(
        tmdb.get_tmdb_id("Preferred", 2026, BrokenTypeInfo())
    ) == (902, "MOVIE")  # type: ignore[arg-type]

    # Multiple sparse provider entries force all title/date normalization paths.
    sparse_results = [
        {"id": 903},
        {"id": 904, "original_title": None, "release_date": None},
    ]
    install_client(
        monkeypatch,
        lambda _url, _kwargs: Response({"results": sparse_results}),
    )
    monkeypatch.setattr(
        tmdb,
        "get_tmdb_translations",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("translation")
        ),
    )
    assert asyncio.run(
        tmdb.get_tmdb_id("Sparse", None, "MOVIE", unattended=True)
    )[0] in {903, 904}

    exact_without_year = [
        {
            "id": 905,
            "title": "Exact",
            "original_title": "Exact",
            "release_date": "",
        },
        {
            "id": 906,
            "title": "Exact",
            "original_title": "Exact",
            "release_date": "",
        },
    ]
    install_client(
        monkeypatch,
        lambda _url, _kwargs: Response({"results": exact_without_year}),
    )
    assert asyncio.run(
        tmdb.get_tmdb_id("Exact", None, "MOVIE", unattended=True)
    ) == (905, "MOVIE")

    selections = iter(["custom/invalid", "movie/907"])
    monkeypatch.setattr(
        tmdb,
        "parse_tmdb_id",
        lambda value, category: (
            category or "MOVIE",
            907 if str(value).endswith("907") else 0,
        ),
    )
    monkeypatch.setattr(
        tmdb,
        "prompt_in_thread",
        lambda *_args, **_kwargs: async_value(next(selections)),
    )
    assert asyncio.run(tmdb.get_tmdb_id("Unrelated", None, "MOVIE")) == (
        907,
        "MOVIE",
    )


def test_get_tmdb_id_intermediate_search_exception_is_nonfatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tmdb.asyncio, "sleep", lambda *_args, **_kwargs: async_value(None)
    )
    monkeypatch.setattr(
        tmdb,
        "guessit_fn",
        lambda *_args, **_kwargs: {"title": "Exploding Fallback"},
    )
    monkeypatch.setattr(tmdb, "anitopy_parse_fn", lambda *_args, **_kwargs: {})

    def handler(_url: str, kwargs: dict[str, Any]) -> Any:
        query = str(kwargs.get("params", {}).get("query", ""))
        if query == "Exploding Fallback":
            raise RuntimeError("recursive search failed")
        return Response({"results": []})

    install_client(monkeypatch, handler)
    assert asyncio.run(
        tmdb.get_tmdb_id("Initial", None, "MOVIE", unattended=True)
    ) == (0, "MOVIE")


def test_tmdb_other_meta_cache_and_provider_decoding_edges(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cached = movie_payload()
    cache = Cache(cached)
    monkeypatch.setattr(tmdb, "cache_for", lambda *_args, **_kwargs: cache)
    install_client(
        monkeypatch,
        lambda _url, _kwargs: (_ for _ in ()).throw(
            AssertionError("cache should avoid provider")
        ),
    )
    monkeypatch.setattr(
        tmdb,
        "get_anime",
        lambda *_args, **_kwargs: async_value((0, "", False, "")),
    )
    assert (
        asyncio.run(
            tmdb.tmdb_other_meta(
                123,
                category="MOVIE",
                base_dir=str(tmp_path),
                config={"DEFAULT": {}},
            )
        )["title"]
        == "Example"
    )

    cache.value = object()
    install_client(
        monkeypatch, lambda _url, _kwargs: Response(ValueError("invalid JSON"))
    )
    assert (
        asyncio.run(
            tmdb.tmdb_other_meta(
                123,
                category="MOVIE",
                base_dir=str(tmp_path),
                config={"DEFAULT": {}},
            )
        )
        == {}
    )

    def malformed_edges(url: str, _kwargs: dict[str, Any]) -> Any:
        if url.endswith("/external_ids"):
            return Response(ValueError("external ids"))
        if url.endswith("/videos"):
            return Response({"results": []})
        if url.endswith("/keywords"):
            return Response(ValueError("keywords"))
        if url.endswith("/credits"):
            return Response(ValueError("credits"))
        if url.endswith("/images"):
            return Response(ValueError("images"))
        return Response(movie_payload())

    install_client(monkeypatch, malformed_edges)
    result = asyncio.run(
        tmdb.tmdb_other_meta(
            123,
            category="MOVIE",
            base_dir=str(tmp_path),
            config={"DEFAULT": {"add_logo": True}},
        )
    )
    assert result["youtube"] == ""
    assert result["keywords"] == []
    assert result["tmdb_directors"] == []
    assert result["logo"] == ""


def test_mediainfo_tmdb_ids_and_daily_json_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert asyncio.run(
        tmdb.get_tmdb_imdb_from_mediainfo(
            {"media": {}}, "MOVIE", False, 1, 2, 3
        )
    ) == ("MOVIE", 1, 2, 3)
    assert asyncio.run(
        tmdb.get_tmdb_imdb_from_mediainfo(
            {"media": {"track": []}}, "TV", True, 1, 2, 3
        )
    ) == ("TV", 1, 2, 3)
    assert asyncio.run(
        tmdb.get_tmdb_imdb_from_mediainfo(
            {
                "media": {
                    "track": [
                        {
                            "extra": {
                                "TMDB": "88",
                                "IMDB": "tt99",
                                "TVDB": "100",
                            }
                        }
                    ]
                }
            },
            "BDMV",
            False,
            0,
            0,
            0,
        )
    ) == ("MOVIE", 88, 99, 100)

    responses = iter(
        [
            Response(ValueError("bad tv JSON")),
            Response(ValueError("bad season JSON")),
        ]
    )
    install_client(monkeypatch, lambda _url, _kwargs: next(responses))
    assert asyncio.run(tmdb.daily_to_tmdb_season_episode(1, "2026-01-01")) == (
        0,
        0,
    )
    responses = iter(
        [
            Response(
                {"seasons": [{"season_number": 1, "air_date": "2025-01-01"}]}
            ),
            Response(ValueError("bad season JSON")),
        ]
    )
    install_client(monkeypatch, lambda _url, _kwargs: next(responses))
    assert asyncio.run(tmdb.daily_to_tmdb_season_episode(1, "2026-01-01")) == (
        0,
        0,
    )


def test_logo_translation_and_set_metadata_final_edges(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    logos = {
        "logos": [
            {"iso_639_1": "fr", "file_path": ""},
            {"iso_639_1": None, "file_path": "/neutral.png"},
        ]
    }
    tmdb.default_config = {"logo_language": "fr"}
    assert asyncio.run(tmdb.get_logo(1, "MOVIE", logo_json=logos)).endswith(
        "neutral.png"
    )

    install_client(
        monkeypatch, lambda _url, _kwargs: RuntimeError("translation network")
    )
    assert asyncio.run(tmdb.get_tmdb_translations(1, "MOVIE", "en")) == ""

    monkeypatch.setattr(
        tmdb, "tmdb_other_meta", lambda *_args, **_kwargs: async_value({})
    )
    monkeypatch.setattr(
        tmdb.asyncio, "sleep", lambda *_args, **_kwargs: async_value(None)
    )
    with pytest.raises(RuntimeError, match="Unable to resolve TMDb metadata"):
        asyncio.run(
            tmdb.set_tmdb_metadata(meta(tmp_path, edit=True), "Missing")
        )


def test_get_tmdb_id_last_similarity_category_and_reduction_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tmdb.asyncio, "sleep", lambda *_args, **_kwargs: async_value(None)
    )
    monkeypatch.setattr(
        tmdb,
        "get_tmdb_translations",
        lambda *_args, **_kwargs: async_value(""),
    )

    def invalid_category_handler(
        _url: str, _kwargs: dict[str, Any]
    ) -> Response:
        return Response(
            {
                "results": [
                    {
                        "id": 1101,
                        "title": "Invalid Category",
                        "release_date": "2026-01-01",
                    }
                ]
            }
        )

    install_client(monkeypatch, invalid_category_handler)
    assert asyncio.run(
        tmdb.get_tmdb_id("Invalid Category", 2026, "OTHER", unattended=True)
    ) == (1101, "MOVIE")

    secondary_results = [
        {
            "id": 1102,
            "title": "Secondary Exact",
            "original_title": "Other",
            "release_date": "2026-01-01",
        },
        {
            "id": 1103,
            "title": "Different",
            "original_title": "Different",
            "release_date": "2026-01-01",
        },
    ]
    install_client(
        monkeypatch,
        lambda _url, _kwargs: Response({"results": secondary_results}),
    )
    assert asyncio.run(
        tmdb.get_tmdb_id(
            "Unrelated Primary",
            2026,
            "MOVIE",
            secondary_title="Secondary Exact",
            unattended=True,
        )
    ) == (1102, "MOVIE")

    weighted_results = [
        {
            "id": 1104,
            "title": "Primary Candidate",
            "original_title": "Original Candidate",
            "release_date": "",
        },
        {
            "id": 1105,
            "title": "Different Candidate",
            "original_title": "Other Candidate",
            "release_date": "",
        },
    ]
    install_client(
        monkeypatch,
        lambda _url, _kwargs: Response({"results": weighted_results}),
    )
    assert (
        asyncio.run(
            tmdb.get_tmdb_id(
                "Primary Candidate Extended",
                None,
                "MOVIE",
                secondary_title="Primary Candidate",
                unattended=True,
            )
        )[0]
        == 1104
    )

    next_year_results = [
        {
            "id": 1106,
            "title": "A Very Long Excellent Movie Title One Two Three Four Extended",
            "original_title": "Completely Different Original",
            "release_date": "2027-01-01",
        },
        {
            "id": 1107,
            "title": "Unrelated Result",
            "original_title": "Unrelated Result",
            "release_date": "2026-01-01",
        },
    ]
    install_client(
        monkeypatch,
        lambda _url, _kwargs: Response({"results": next_year_results}),
    )
    assert (
        asyncio.run(
            tmdb.get_tmdb_id(
                "A Very Long Excellent Movie Title One Two Three Four",
                2026,
                "MOVIE",
                unattended=True,
            )
        )[0]
        == 1106
    )

    monkeypatch.setattr(tmdb, "guessit_fn", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(tmdb, "anitopy_parse_fn", lambda *_args, **_kwargs: {})

    def reduction_handler(_url: str, kwargs: dict[str, Any]) -> Response:
        query = str(kwargs.get("params", {}).get("query", ""))
        if query == "Alpha Beta":
            return Response(
                {
                    "results": [
                        {
                            "id": 1108,
                            "title": "Alpha Beta",
                            "release_date": "2026-01-01",
                        }
                    ]
                }
            )
        return Response({"results": []})

    install_client(monkeypatch, reduction_handler)
    assert asyncio.run(
        tmdb.get_tmdb_id(
            "Alpha Beta Gamma Delta", 2026, "MOVIE", unattended=True
        )
    ) == (1108, "MOVIE")


class _InterruptSelection:
    def strip(self) -> _InterruptSelection:
        return self

    def __bool__(self) -> bool:
        return True

    def __contains__(self, _item: object) -> bool:
        return False

    def __int__(self) -> int:
        raise KeyboardInterrupt


def test_get_tmdb_id_inner_selection_cancellation_and_final_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tmdb.asyncio, "sleep", lambda *_args, **_kwargs: async_value(None)
    )
    monkeypatch.setattr(
        tmdb,
        "get_tmdb_translations",
        lambda *_args, **_kwargs: async_value(""),
    )
    ambiguous = [
        {
            "id": 1201,
            "title": "Same Result",
            "original_title": "Same Result",
            "release_date": "2026-01-01",
        },
        {
            "id": 1202,
            "title": "Same Result",
            "original_title": "Same Result",
            "release_date": "2026-01-01",
        },
    ]
    install_client(
        monkeypatch, lambda _url, _kwargs: Response({"results": ambiguous})
    )
    cleanup = AsyncMock()
    monkeypatch.setattr(tmdb.cleanup_manager, "cleanup", cleanup)
    monkeypatch.setattr(tmdb.cleanup_manager, "reset_terminal", lambda: None)

    async def eof(*_args: object, **_kwargs: object) -> str:
        raise EOFError

    monkeypatch.setattr(tmdb, "prompt_in_thread", eof)
    with pytest.raises(OperationAbortedError, match="cancelled"):
        asyncio.run(tmdb.get_tmdb_id("Unrelated", 2026, "MOVIE"))
    assert cleanup.await_count >= 1

    monkeypatch.setattr(
        tmdb,
        "prompt_in_thread",
        lambda *_args, **_kwargs: async_value("movie/123"),
    )
    monkeypatch.setattr(
        tmdb,
        "parse_tmdb_id",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    with pytest.raises(OperationAbortedError, match="cancelled"):
        asyncio.run(tmdb.get_tmdb_id("Unrelated", 2026, "MOVIE"))

    monkeypatch.setattr(
        tmdb,
        "prompt_in_thread",
        lambda *_args, **_kwargs: async_value(_InterruptSelection()),
    )
    with pytest.raises(OperationAbortedError, match="cancelled"):
        asyncio.run(tmdb.get_tmdb_id("Unrelated", 2026, "MOVIE"))

    install_client(
        monkeypatch, lambda _url, _kwargs: Response({"results": []})
    )
    monkeypatch.setattr(tmdb, "guessit_fn", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(tmdb, "anitopy_parse_fn", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        tmdb,
        "parse_tmdb_id",
        lambda value, category: (category, int(str(value).split("/")[-1])),
    )
    prompts = iter(("movie/0", "movie/1203"))
    monkeypatch.setattr(
        tmdb,
        "prompt_in_thread",
        lambda *_args, **_kwargs: async_value(next(prompts)),
    )
    assert asyncio.run(tmdb.get_tmdb_id("No Match", None, "MOVIE")) == (
        1203,
        "MOVIE",
    )


def test_get_tmdb_id_split_exceptions_cover_all_fallback_guards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenSplit(str):
        def split(self, *_args: object, **_kwargs: object) -> list[str]:
            raise RuntimeError("split failed")

    monkeypatch.setattr(
        tmdb.asyncio, "sleep", lambda *_args, **_kwargs: async_value(None)
    )
    monkeypatch.setattr(tmdb, "guessit_fn", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(tmdb, "anitopy_parse_fn", lambda *_args, **_kwargs: {})
    install_client(
        monkeypatch, lambda _url, _kwargs: Response({"results": []})
    )
    assert asyncio.run(
        tmdb.get_tmdb_id(
            BrokenSplit("Broken Title"), None, "MOVIE", unattended=True
        )
    ) == (0, "MOVIE")


def test_tmdb_similarity_plus_one_year_and_semantic_cancellation_propagation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tmdb.asyncio, "sleep", lambda *_args, **_kwargs: async_value(None)
    )
    monkeypatch.setattr(
        tmdb,
        "get_tmdb_translations",
        lambda *_args, **_kwargs: async_value(""),
    )

    plus_one = [
        {
            "id": 1301,
            "title": "Exact Shared",
            "original_title": "Exact Shared",
            "release_date": "2027-01-01",
        },
        {
            "id": 1302,
            "title": "Exact Shared",
            "original_title": "Exact Shared",
            "release_date": "2027-02-01",
        },
    ]
    install_client(
        monkeypatch, lambda _url, _kwargs: Response({"results": plus_one})
    )
    assert asyncio.run(
        tmdb.get_tmdb_id("Exact Shared", 2026, "MOVIE", unattended=True)
    )[0] in {1301, 1302}

    async def cancelled(*_args: object, **_kwargs: object) -> str:
        raise EOFError

    monkeypatch.setattr(tmdb, "prompt_in_thread", cancelled)

    def roman_handler(_url: str, kwargs: dict[str, Any]) -> Response:
        query = str(kwargs.get("params", {}).get("query", ""))
        return Response({"results": plus_one if query == "Movie 2" else []})

    install_client(monkeypatch, roman_handler)
    with pytest.raises(OperationAbortedError, match="cancelled"):
        asyncio.run(tmdb.get_tmdb_id("Movie II", 2026, "MOVIE"))

    monkeypatch.setattr(tmdb, "guessit_fn", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(tmdb, "anitopy_parse_fn", lambda *_args, **_kwargs: {})

    def reduced_handler(_url: str, kwargs: dict[str, Any]) -> Response:
        query = str(kwargs.get("params", {}).get("query", ""))
        return Response({"results": plus_one if query == "Alpha Beta" else []})

    install_client(monkeypatch, reduced_handler)
    with pytest.raises(OperationAbortedError, match="cancelled"):
        asyncio.run(tmdb.get_tmdb_id("Alpha Beta Gamma", 2026, "MOVIE"))

    install_client(monkeypatch, reduced_handler)
    with pytest.raises(OperationAbortedError, match="cancelled"):
        asyncio.run(tmdb.get_tmdb_id("Alpha Beta Gamma Delta", 2026, "MOVIE"))


def test_tmdb_final_manual_parse_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tmdb.asyncio, "sleep", lambda *_args, **_kwargs: async_value(None)
    )
    monkeypatch.setattr(tmdb, "guessit_fn", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(tmdb, "anitopy_parse_fn", lambda *_args, **_kwargs: {})
    install_client(
        monkeypatch, lambda _url, _kwargs: Response({"results": []})
    )
    monkeypatch.setattr(
        tmdb,
        "prompt_in_thread",
        lambda *_args, **_kwargs: async_value("movie/123"),
    )
    monkeypatch.setattr(
        tmdb,
        "parse_tmdb_id",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    cleanup = AsyncMock()
    monkeypatch.setattr(tmdb.cleanup_manager, "cleanup", cleanup)
    monkeypatch.setattr(tmdb.cleanup_manager, "reset_terminal", lambda: None)
    with pytest.raises(OperationAbortedError, match="cancelled"):
        asyncio.run(tmdb.get_tmdb_id("No Match", None, "MOVIE"))
    cleanup.assert_awaited_once()


def test_tmdb_other_meta_last_air_mismatch_and_video_parse_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = Cache(_MISS)
    monkeypatch.setattr(tmdb, "cache_for", lambda *_args, **_kwargs: cache)
    monkeypatch.setattr(
        tmdb,
        "get_anime",
        lambda *_args, **_kwargs: async_value((0, "", False, "")),
    )
    payload = tv_payload()
    payload["name"] = "Example Series"
    payload["first_air_date"] = ""
    payload["last_air_date"] = "2026-12-31"

    def handler(url: str, _kwargs: dict[str, Any]) -> Response:
        if url.endswith("/external_ids"):
            return Response({"imdb_id": "tt7654321", "tvdb_id": "654"})
        if url.endswith("/videos"):
            return Response(ValueError("videos parse failed"))
        if url.endswith("/keywords"):
            return Response({"results": []})
        if url.endswith("/credits"):
            return Response({"cast": [], "crew": []})
        if url.endswith("/images"):
            return Response({"logos": []})
        return Response(payload)

    install_client(monkeypatch, handler)
    result = asyncio.run(
        tmdb.tmdb_other_meta(
            123,
            search_year=None,
            category="TV",
            imdb_id=1234567,
            base_dir=str(tmp_path),
            config={"DEFAULT": {}},
        )
    )
    assert result["year"] == 2026
    assert result["mismatched_imdb_id"] == 7654321
    assert result["youtube"] == ""


def test_tmdb_romaji_empty_cache_timeout_daily_logo_and_metadata_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cached = Cache({"media": [], "demographic": "Josei"})
    monkeypatch.setattr(tmdb, "cache_for", lambda *_args, **_kwargs: cached)
    empty_meta = meta(
        tmp_path, filename="Fallback Anime", manual_season="", season=""
    )
    result = asyncio.run(tmdb.get_romaji("Primary Anime", 0, empty_meta))
    assert result[-1] == "Josei"

    class TimeoutClient:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, *_args: object, **_kwargs: object) -> Response:
            raise httpx.ReadTimeout(
                "timeout",
                request=httpx.Request("POST", "https://graphql.anilist.co"),
            )

    miss = Cache(_MISS)
    monkeypatch.setattr(tmdb, "cache_for", lambda *_args, **_kwargs: miss)
    monkeypatch.setattr(
        tmdb.httpx, "AsyncClient", lambda *_args, **_kwargs: TimeoutClient()
    )
    monkeypatch.setattr(
        tmdb.asyncio, "sleep", lambda *_args, **_kwargs: async_value(None)
    )
    assert (
        asyncio.run(
            tmdb.get_romaji("Timeout Anime", 0, meta(tmp_path, filename=""))
        )[0]
        == ""
    )

    responses = iter(
        [
            Response(
                {"seasons": [{"season_number": 1, "air_date": "2025-01-01"}]}
            ),
            RuntimeError("season request failed"),
        ]
    )
    install_client(monkeypatch, lambda _url, _kwargs: next(responses))
    assert asyncio.run(
        tmdb.daily_to_tmdb_season_episode(123, "2026-01-01")
    ) == (0, 0)

    logos = {"logos": [{"iso_639_1": "fr", "file_path": "/fr.png"}]}
    assert asyncio.run(
        tmdb.get_logo(123, "MOVIE", logo_languages="fr,en", logo_json=logos)
    ).endswith("fr.png")

    calls = 0

    async def flaky_metadata(
        *_args: object, **_kwargs: object
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary metadata failure")
        return {
            "title": "Recovered",
            "year": 2026,
            "retrieved_aka": "Recovered AKA",
        }

    monkeypatch.setattr(tmdb, "tmdb_other_meta", flaky_metadata)
    target = meta(tmp_path, edit=True)
    asyncio.run(tmdb.set_tmdb_metadata(target, "Recovered"))
    assert (
        calls == 2
        and target.title == "Recovered"
        and target.aka == "Recovered AKA"
    )


def test_tmdb_similarity_plus_one_year_boost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Similar:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def ratio(self) -> float:
            return 0.95

    monkeypatch.setattr(tmdb, "SequenceMatcher", Similar)
    monkeypatch.setattr(
        tmdb,
        "get_tmdb_translations",
        lambda *_args, **_kwargs: async_value(""),
    )
    results = [
        {
            "id": 1301,
            "title": "Candidate A",
            "original_title": "Original A",
            "release_date": "2027-01-01",
        },
        {
            "id": 1302,
            "title": "Candidate B",
            "original_title": "Original B",
            "release_date": "2025-01-01",
        },
    ]
    install_client(
        monkeypatch, lambda _url, _kwargs: Response({"results": results})
    )

    assert asyncio.run(
        tmdb.get_tmdb_id("Unrelated Query", 2026, "MOVIE", unattended=True)
    ) == (1301, "MOVIE")


@pytest.mark.parametrize(
    ("filename", "ambiguous_query"),
    [
        ("Title II", "Title 2"),
        ("Alpha Beta Gamma", "Alpha Beta"),
        ("Alpha Beta Gamma Delta", "Alpha Beta"),
    ],
)
def test_tmdb_fallback_searches_preserve_user_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
    ambiguous_query: str,
) -> None:
    ambiguous = [
        {
            "id": 1401,
            "title": "Same",
            "original_title": "Same",
            "release_date": "2026-01-01",
        },
        {
            "id": 1402,
            "title": "Same",
            "original_title": "Same",
            "release_date": "2026-01-01",
        },
    ]

    def handler(_url: str, kwargs: dict[str, Any]) -> Response:
        query = str(kwargs.get("params", {}).get("query", ""))
        return Response(
            {"results": ambiguous if query == ambiguous_query else []}
        )

    install_client(monkeypatch, handler)
    monkeypatch.setattr(tmdb, "guessit_fn", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(tmdb, "anitopy_parse_fn", lambda *_args, **_kwargs: {})

    async def cancel(*_args: object, **_kwargs: object) -> str:
        raise EOFError

    monkeypatch.setattr(tmdb, "prompt_in_thread", cancel)
    monkeypatch.setattr(tmdb.cleanup_manager, "cleanup", AsyncMock())
    monkeypatch.setattr(tmdb.cleanup_manager, "reset_terminal", lambda: None)

    with pytest.raises(OperationAbortedError, match="cancelled"):
        asyncio.run(tmdb.get_tmdb_id(filename, None, "MOVIE"))


def test_tmdb_final_manual_parser_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_client(
        monkeypatch, lambda _url, _kwargs: Response({"results": []})
    )
    monkeypatch.setattr(tmdb, "guessit_fn", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(tmdb, "anitopy_parse_fn", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        tmdb,
        "prompt_in_thread",
        lambda *_args, **_kwargs: async_value("movie/123"),
    )
    monkeypatch.setattr(
        tmdb,
        "parse_tmdb_id",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    cleanup = AsyncMock()
    monkeypatch.setattr(tmdb.cleanup_manager, "cleanup", cleanup)
    monkeypatch.setattr(tmdb.cleanup_manager, "reset_terminal", lambda: None)

    with pytest.raises(OperationAbortedError, match="cancelled"):
        asyncio.run(tmdb.get_tmdb_id("No Match", None, "MOVIE"))
    cleanup.assert_awaited_once()


def test_tmdb_other_meta_mismatch_video_json_logo_and_tv_last_air_year(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cache = Cache()
    monkeypatch.setattr(tmdb, "cache_for", lambda *_args, **_kwargs: cache)
    monkeypatch.setattr(
        tmdb,
        "get_anime",
        lambda *_args, **_kwargs: async_value((0, "", False, "")),
    )
    tmdb.default_config = {"add_logo": True, "logo_language": "en"}

    def movie_handler(url: str, _kwargs: dict[str, Any]) -> Response:
        if url.endswith("/external_ids"):
            return Response({"imdb_id": "tt7654321", "tvdb_id": "654"})
        if url.endswith("/videos"):
            return Response(ValueError("bad videos"))
        if url.endswith("/keywords"):
            return Response({"keywords": []})
        if url.endswith("/credits"):
            return Response({"cast": [], "crew": []})
        if url.endswith("/images"):
            return Response(
                {"logos": [{"iso_639_1": "en", "file_path": "/logo.png"}]}
            )
        return Response(movie_payload())

    install_client(monkeypatch, movie_handler)
    result = asyncio.run(
        tmdb.tmdb_other_meta(
            123,
            category="MOVIE",
            imdb_id=123,
            quickie_search=False,
            base_dir=str(tmp_path),
            config={"DEFAULT": {"add_logo": True, "logo_language": "en"}},
        )
    )
    assert result["imdb_id"] == 123
    assert result["mismatched_imdb_id"] == 7654321
    assert result["youtube"] == ""
    assert result["tmdb_type"] == "Movie"
    assert result["logo"].endswith("logo.png")

    cache.value = _MISS
    tv_data = tv_payload()
    tv_data.update(
        name="No Year In Title", first_air_date="", last_air_date="2025-12-31"
    )

    def tv_handler(url: str, _kwargs: dict[str, Any]) -> Response:
        if url.endswith("/external_ids"):
            return Response({})
        if url.endswith("/videos"):
            return Response({"results": []})
        if url.endswith("/keywords"):
            return Response({"results": []})
        if url.endswith("/credits"):
            return Response({"cast": [], "crew": []})
        return Response(tv_data)

    install_client(monkeypatch, tv_handler)
    result = asyncio.run(
        tmdb.tmdb_other_meta(
            123, category="TV", base_dir=str(tmp_path), config={"DEFAULT": {}}
        )
    )
    assert result["year"] == 2025


def test_romaji_cached_empty_and_final_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = Cache({"media": [], "demographic": "Mina"})
    monkeypatch.setattr(tmdb, "cache_for", lambda *_args, **_kwargs: cache)
    assert asyncio.run(
        tmdb.get_romaji(
            "No Result", None, meta(tmp_path, filename="Fallback Name")
        )
    ) == ("", 0, "", "", 0, "Mina")

    class TimeoutClient(RouterClient):
        async def post(self, _url: str, **_kwargs: Any) -> Any:
            raise httpx.ReadTimeout(
                "timeout",
                request=httpx.Request("POST", "https://graphql.anilist.co"),
            )

    monkeypatch.setattr(tmdb, "cache_for", lambda *_args, **_kwargs: Cache())
    monkeypatch.setattr(tmdb.httpx, "AsyncClient", TimeoutClient)
    monkeypatch.setattr(
        tmdb.asyncio, "sleep", lambda *_args, **_kwargs: async_value(None)
    )
    assert asyncio.run(
        tmdb.get_romaji("Timeout", None, meta(tmp_path, filename=""))
    ) == ("", 0, "", "", 0, "Mina")


def test_daily_season_request_error_logo_languages_and_metadata_exception_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    responses = iter(
        [
            Response(
                {"seasons": [{"season_number": 1, "air_date": "2025-01-01"}]}
            ),
            RuntimeError("season request failed"),
        ]
    )
    install_client(monkeypatch, lambda _url, _kwargs: next(responses))
    assert asyncio.run(tmdb.daily_to_tmdb_season_episode(1, "2026-01-01")) == (
        0,
        0,
    )

    logos = {"logos": [{"iso_639_1": "en", "file_path": "/english.png"}]}
    assert asyncio.run(
        tmdb.get_logo(1, "MOVIE", logo_languages="fr, en", logo_json=logos)
    ).endswith("english.png")

    other_meta = AsyncMock(
        side_effect=[
            RuntimeError("temporary failure"),
            {"title": "Recovered", "year": 2026, "retrieved_aka": ""},
        ]
    )
    monkeypatch.setattr(tmdb, "tmdb_other_meta", other_meta)
    monkeypatch.setattr(
        tmdb.asyncio, "sleep", lambda *_args, **_kwargs: async_value(None)
    )
    target = meta(tmp_path, edit=True)
    asyncio.run(tmdb.set_tmdb_metadata(target, "Recovered"))
    assert target.title == "Recovered"
    assert other_meta.await_count == 2


def test_get_tmdb_id_exact_plus_one_year_and_broken_result_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tmdb.asyncio, "sleep", lambda *_args, **_kwargs: async_value(None)
    )
    monkeypatch.setattr(
        tmdb,
        "get_tmdb_translations",
        lambda *_args, **_kwargs: async_value(""),
    )
    results = [
        {
            "id": 1301,
            "title": "Exact Future",
            "original_title": "Exact Future",
            "release_date": "2027-01-01",
        },
        {
            "id": 1302,
            "title": "Different",
            "original_title": "Different",
            "release_date": "2026-01-01",
        },
    ]
    install_client(
        monkeypatch, lambda _url, _kwargs: Response({"results": results})
    )
    assert asyncio.run(
        tmdb.get_tmdb_id("Exact Future", 2026, "MOVIE", unattended=True)
    ) == (1301, "MOVIE")

    class BrokenResult(dict[str, Any]):
        def get(self, _key: str, _default: object = None) -> Any:
            raise RuntimeError("broken result")

    install_client(
        monkeypatch,
        lambda _url, _kwargs: Response(
            {"results": [BrokenResult(id=1), BrokenResult(id=2)]}
        ),
    )
    monkeypatch.setattr(tmdb, "guessit_fn", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(tmdb, "anitopy_parse_fn", lambda *_args, **_kwargs: {})
    assert asyncio.run(
        tmdb.get_tmdb_id("Broken", None, "MOVIE", unattended=True)
    ) == (0, "MOVIE")


def test_get_tmdb_id_recursive_cancellation_and_final_parser_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tmdb.asyncio, "sleep", lambda *_args, **_kwargs: async_value(None)
    )
    monkeypatch.setattr(
        tmdb,
        "get_tmdb_translations",
        lambda *_args, **_kwargs: async_value(""),
    )
    monkeypatch.setattr(tmdb, "guessit_fn", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(tmdb, "anitopy_parse_fn", lambda *_args, **_kwargs: {})
    cleanup = AsyncMock()
    monkeypatch.setattr(tmdb.cleanup_manager, "cleanup", cleanup)
    monkeypatch.setattr(tmdb.cleanup_manager, "reset_terminal", lambda: None)

    async def eof(*_args: object, **_kwargs: object) -> str:
        raise EOFError

    monkeypatch.setattr(tmdb, "prompt_in_thread", eof)
    ambiguous = [
        {
            "id": 1401,
            "title": "Candidate",
            "original_title": "Candidate",
            "release_date": "2026-01-01",
        },
        {
            "id": 1402,
            "title": "Candidate",
            "original_title": "Candidate",
            "release_date": "2026-01-01",
        },
    ]

    def roman_handler(_url: str, kwargs: dict[str, Any]) -> Response:
        query = str(kwargs.get("params", {}).get("query", ""))
        return Response({"results": ambiguous if query == "Part 2" else []})

    install_client(monkeypatch, roman_handler)
    with pytest.raises(OperationAbortedError, match="cancelled"):
        asyncio.run(tmdb.get_tmdb_id("Part II", 2026, "MOVIE"))

    def reduced_handler(_url: str, kwargs: dict[str, Any]) -> Response:
        query = str(kwargs.get("params", {}).get("query", ""))
        return Response({"results": ambiguous if query == "Alpha" else []})

    install_client(monkeypatch, reduced_handler)
    with pytest.raises(OperationAbortedError, match="cancelled"):
        asyncio.run(tmdb.get_tmdb_id("Alpha Beta", None, "MOVIE"))

    install_client(monkeypatch, reduced_handler)
    with pytest.raises(OperationAbortedError, match="cancelled"):
        asyncio.run(tmdb.get_tmdb_id("Alpha Beta Gamma", None, "MOVIE"))

    install_client(
        monkeypatch, lambda _url, _kwargs: Response({"results": []})
    )
    monkeypatch.setattr(
        tmdb,
        "prompt_in_thread",
        lambda *_args, **_kwargs: async_value("movie/123"),
    )
    monkeypatch.setattr(
        tmdb,
        "parse_tmdb_id",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    with pytest.raises(OperationAbortedError, match="cancelled"):
        asyncio.run(tmdb.get_tmdb_id("No Match", None, "MOVIE"))
    assert cleanup.await_count >= 4


def test_tmdb_other_meta_no_id_tv_last_air_mismatch_and_video_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        tmdb, "get_tmdb_id", AsyncMock(return_value=(0, "MOVIE"))
    )
    monkeypatch.setattr(
        tmdb, "guessit_fn", lambda *_args, **_kwargs: {"title": "Unknown"}
    )
    assert (
        asyncio.run(
            tmdb.tmdb_other_meta(
                0, path="Unknown", category="MOVIE", mode="non_cli"
            )
        )
        == {}
    )

    cache = Cache()
    monkeypatch.setattr(tmdb, "cache_for", lambda *_args, **_kwargs: cache)
    monkeypatch.setattr(
        tmdb,
        "get_anime",
        lambda *_args, **_kwargs: async_value((0, "", False, "")),
    )

    def tv_handler(url: str, _kwargs: dict[str, Any]) -> Response:
        if url.endswith("/external_ids"):
            return Response({"imdb_id": "tt9999999", "tvdb_id": "654"})
        if url.endswith("/videos"):
            return Response({"results": [None]})
        if url.endswith("/keywords"):
            return Response({"results": [{"name": "one,two"}]})
        if url.endswith("/credits"):
            return Response({"cast": [], "crew": []})
        payload = tv_payload()
        payload["name"] = "Example"
        payload["first_air_date"] = ""
        payload["last_air_date"] = "2026-12-31"
        return Response(payload)

    install_client(monkeypatch, tv_handler)
    result = asyncio.run(
        tmdb.tmdb_other_meta(
            123,
            category="TV",
            imdb_id=1234567,
            base_dir=str(tmp_path),
            config={"DEFAULT": {}},
        )
    )
    assert result["year"] == 2026
    assert result["keywords"] == ["one two"]
    assert result["imdb_id"] == 1234567
    assert result["youtube"] == ""


def test_tmdb_keyword_anilist_daily_season_logo_and_retry_edges(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install_client(
        monkeypatch,
        lambda _url, _kwargs: Response({"results": [{"name": "one,two"}]}),
    )
    assert asyncio.run(tmdb.get_keywords(1, "TV")) == ["one two"]

    cache = Cache({"media": [], "demographic": "Mina"})
    monkeypatch.setattr(tmdb, "cache_for", lambda *_args, **_kwargs: cache)
    assert asyncio.run(
        tmdb.get_romaji("Cached Empty", None, meta(tmp_path))
    ) == ("", 0, "", "", 0, "Mina")

    class TimeoutClient(RouterClient):
        attempts: ClassVar[int] = 0

        async def post(self, _url: str, **_kwargs: Any) -> Any:
            type(self).attempts += 1
            raise httpx.ReadTimeout(
                "timeout",
                request=httpx.Request("POST", "https://graphql.anilist.co"),
            )

    TimeoutClient.attempts = 0
    monkeypatch.setattr(tmdb, "cache_for", lambda *_args, **_kwargs: Cache())
    monkeypatch.setattr(tmdb.httpx, "AsyncClient", TimeoutClient)
    monkeypatch.setattr(
        tmdb.asyncio, "sleep", lambda *_args, **_kwargs: async_value(None)
    )
    assert asyncio.run(tmdb.get_romaji("Timeout", None, meta(tmp_path))) == (
        "",
        0,
        "",
        "",
        0,
        "Mina",
    )
    assert TimeoutClient.attempts >= 3

    def daily_handler(url: str, _kwargs: dict[str, Any]) -> Any:
        if "/season/" in url:
            return RuntimeError("season network")
        return Response(
            {"seasons": [{"season_number": 1, "air_date": "2025-01-01"}]}
        )

    install_client(monkeypatch, daily_handler)
    assert asyncio.run(tmdb.daily_to_tmdb_season_episode(1, "2026-01-01")) == (
        0,
        0,
    )

    season_payload = {
        "episodes": [],
        "images": {"posters": [{"file_path": "/poster.jpg"}]},
        "credits": {"cast": [{"name": "Actor"}]},
    }
    install_client(monkeypatch, lambda _url, _kwargs: Response(season_payload))
    season = asyncio.run(tmdb.get_season_details(1, 1))
    assert season["images"]["posters"] and season["credits"]["cast"]

    logos = {
        "logos": [
            {"iso_639_1": "fr", "file_path": "/fr.png"},
            {"iso_639_1": "en", "file_path": "/en.png"},
        ]
    }
    assert asyncio.run(
        tmdb.get_logo(1, "MOVIE", logo_languages="fr,en", logo_json=logos)
    ).endswith("fr.png")
    assert asyncio.run(
        tmdb.get_logo(1, "MOVIE", logo_languages="fr", logo_json=logos)
    ).endswith("fr.png")

    outcomes: list[object] = [
        RuntimeError("temporary"),
        {"title": "Recovered", "year": 2026, "retrieved_aka": ""},
    ]

    async def metadata(*_args: object, **_kwargs: object) -> dict[str, Any]:
        value = outcomes.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr(tmdb, "tmdb_other_meta", metadata)
    target = meta(
        tmp_path, edit=True, title="", year=0, genres=[], overview=""
    )
    asyncio.run(tmdb.set_tmdb_metadata(target, "Recovered"))
    assert target.title == "Recovered"


def test_tmdb_other_meta_non_cli_lookup_exception_returns_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        tmdb,
        "guessit_fn",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("guess failed")
        ),
    )
    assert (
        asyncio.run(
            tmdb.tmdb_other_meta(
                0,
                path="broken.mkv",
                category="MOVIE",
                mode="non_cli",
                base_dir=str(tmp_path),
                config={"DEFAULT": {}},
            )
        )
        == {}
    )


def test_tmdb_other_meta_refreshes_authentication_from_active_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(tmdb, "_tmdb_credential", None)
    monkeypatch.setattr(tmdb, "default_config", {})
    monkeypatch.setattr(tmdb, "cache_for", lambda *_args, **_kwargs: Cache())
    monkeypatch.setattr(
        tmdb,
        "get_anime",
        lambda *_args, **_kwargs: async_value((0, "", False, "")),
    )
    install_client(monkeypatch, lambda url, _kwargs: endpoint_payload(url))

    result = asyncio.run(
        tmdb.tmdb_other_meta(
            123,
            category="MOVIE",
            base_dir=str(tmp_path),
            config={"DEFAULT": {"tmdb_api": "  active-key  "}},
        )
    )

    assert result["title"] == "Example"
    assert tmdb._tmdb_credential is not None
    assert tmdb._tmdb_credential.value == "active-key"
