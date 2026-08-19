from __future__ import annotations

import asyncio
from pathlib import Path
from types import TracebackType
from typing import Any, ClassVar, Self
from unittest.mock import AsyncMock

import httpx
import pytest

from src.domain_models.errors import OperationAbortedError
from src.integrations.external_apis import imdb
from src.integrations.external_apis.imdb import ImdbManager

_MISS = object()


class _Response:
    def __init__(self, payload: object = None, status_code: int = 200) -> None:
        self.payload = {} if payload is None else payload
        self.status_code = status_code
        self.request = httpx.Request("POST", "https://api.graphql.imdb.com/")

    def json(self) -> Any:
        if isinstance(self.payload, BaseException):
            raise self.payload
        return self.payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            response = httpx.Response(self.status_code, request=self.request)
            raise httpx.HTTPStatusError("failed", request=self.request, response=response)


class _Client:
    queue: ClassVar[list[object]] = []
    calls: ClassVar[list[dict[str, object]]] = []

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        return None

    async def post(self, _url: str, **kwargs: object) -> _Response:
        type(self).calls.append(dict(kwargs))
        value = type(self).queue.pop(0)
        if isinstance(value, BaseException):
            raise value
        assert isinstance(value, _Response)
        return value

    @classmethod
    def reset(cls, *values: object) -> None:
        cls.queue = list(values)
        cls.calls = []


class _Cache:
    def __init__(self, value: object = _MISS) -> None:
        self.value = value
        self.set_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def get(self, *_args: object) -> object:
        return self.value

    async def set(self, *args: object, **kwargs: object) -> None:
        self.set_calls.append((args, kwargs))


def _install(monkeypatch: pytest.MonkeyPatch, *responses: object, cache: _Cache | None = None) -> _Cache:
    _Client.reset(*responses)
    monkeypatch.setattr(imdb.httpx, "AsyncClient", _Client)
    selected = cache or _Cache()
    monkeypatch.setattr(imdb, "cache_for", lambda *_args, **_kwargs: selected)
    monkeypatch.setattr(imdb, "is_cache_miss", lambda value: value is _MISS)
    return selected


def _edge(
    imdb_id: str,
    title: str,
    *,
    year: int | None = 2026,
    title_type: str = "Movie",
    plot: str = "Plot",
) -> dict[str, Any]:
    return {
        "node": {
            "title": {
                "id": imdb_id,
                "titleText": {"text": title},
                "titleType": {"text": title_type},
                "releaseYear": {"year": year},
                "plot": {"plotText": {"plainText": plot}},
            }
        }
    }


def _search_payload(*edges: dict[str, Any]) -> dict[str, Any]:
    return {"data": {"advancedTitleSearch": {"edges": list(edges)}}}


def _rich_title_payload() -> dict[str, Any]:
    return {
        "data": {
            "title": {
                "id": "tt1234567",
                "titleText": {"text": "Example", "isOriginalTitle": False, "country": {"text": "US"}},
                "originalTitleText": {"text": "Original Example"},
                "releaseYear": {"year": 2024, "endYear": 2026},
                "titleType": {"id": "tvSeries"},
                "plot": {"plotText": {"plainText": "Overview"}},
                "ratingsSummary": {"aggregateRating": 8.1, "voteCount": 42},
                "primaryImage": {"url": "https://img.invalid/cover.jpg"},
                "runtime": {"seconds": 3600},
                "titleGenres": {
                    "genres": [
                        {"genre": {"text": "Drama"}},
                        {"genre": {"text": "Comedy"}},
                        {"bad": True},
                    ]
                },
                "principalCredits": [
                    {
                        "category": {"text": "Directors"},
                        "credits": [
                            {"name": {"id": "nm1", "nameText": {"text": "Director"}}},
                            {"name": {"id": "", "nameText": {"text": "Ignored"}}},
                        ],
                    },
                    {"category": {"text": "Creators"}, "credits": [{"name": {"id": "nm2", "nameText": {"text": "Creator"}}}]},
                    {"category": {"text": "Writers"}, "credits": [{"name": {"id": "nm3", "nameText": {"text": "Writer"}}}]},
                    {"category": {"text": "Stars"}, "credits": [{"name": {"id": "nm4", "nameText": {"text": "Star"}}}]},
                ],
                "runtimes": {
                    "edges": [
                        {
                            "node": {
                                "seconds": 3720,
                                "displayableProperty": {"value": {"plainText": "Director's Cut"}},
                                "attributes": [{"text": "extended"}, {"text": ""}, {"bad": True}],
                            }
                        },
                        {"node": {"seconds": 0, "displayableProperty": {"value": {"plainText": "Ignored"}}}},
                    ]
                },
                "akas": {
                    "edges": [
                        {
                            "node": {
                                "text": "AKA Example",
                                "country": {"text": "BR"},
                                "language": {"text": "Portuguese"},
                                "attributes": [{"text": "festival title"}],
                            }
                        }
                    ]
                },
                "countriesOfOrigin": {"countries": [{"text": "US"}, {"text": "GB"}, {"bad": True}]},
                "episodes": {
                    "episodes": {
                        "edges": [
                            {
                                "node": {
                                    "id": "tt2000001",
                                    "series": {
                                        "displayableEpisodeNumber": {
                                            "displayableSeason": {"season": 1},
                                            "episodeNumber": {"text": "1"},
                                        }
                                    },
                                    "titleText": {"text": "Episode One"},
                                    "releaseYear": {"year": 2024},
                                    "releaseDate": {"year": 2024, "month": 1, "day": 2},
                                }
                            },
                            {
                                "node": {
                                    "id": "tt2000002",
                                    "series": {
                                        "displayableEpisodeNumber": {
                                            "displayableSeason": {"season": "bad"},
                                            "episodeNumber": {"text": "2"},
                                        }
                                    },
                                    "titleText": {"text": "Episode Two"},
                                    "releaseYear": {"year": 2025},
                                    "releaseDate": {},
                                }
                            },
                            {
                                "node": {
                                    "id": "tt2000003",
                                    "series": {
                                        "displayableEpisodeNumber": {
                                            "displayableSeason": {"season": 1},
                                            "episodeNumber": {"text": "3"},
                                        }
                                    },
                                    "titleText": {"text": "Episode Three"},
                                    "releaseYear": {"year": 2025},
                                    "releaseDate": {},
                                }
                            },
                        ],
                        "total": 3,
                    }
                },
                "technicalSpecifications": {"soundMixes": {"items": [{"text": "Dolby Digital"}, {"bad": True}]}},
            }
        }
    }


def test_safe_get_and_invalid_identifiers() -> None:
    manager = ImdbManager()
    assert manager.safe_get({"a": {"b": 1}}, ["a", "b"]) == 1
    assert manager.safe_get({"a": 1}, ["a", "b"], "fallback") == "fallback"
    assert asyncio.run(manager.get_imdb_info_api(None)) == {"type": None}
    assert asyncio.run(manager.get_imdb_info_api(0)) == {"type": None}

    class BadFormat:
        def __str__(self) -> str:
            return "bad"

        def __format__(self, _spec: str) -> str:
            raise ValueError("format failed")

    assert asyncio.run(manager.get_imdb_info_api(BadFormat())) == {}  # type: ignore[arg-type]


def test_imdb_info_cache_http_errors_and_empty_title(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    manager = ImdbManager()
    cache = _install(monkeypatch, cache=_Cache({"cached": True}))
    assert asyncio.run(manager.get_imdb_info_api(123, base_dir=str(tmp_path))) == {"cached": True}
    assert _Client.calls == [] and cache.set_calls == []

    request = httpx.Request("POST", "https://api.graphql.imdb.com/")
    _install(monkeypatch, _Response({}, 500))
    assert asyncio.run(manager.get_imdb_info_api(123, base_dir=str(tmp_path))) == {}
    _install(monkeypatch, httpx.RequestError("offline", request=request))
    assert asyncio.run(manager.get_imdb_info_api(123, base_dir=str(tmp_path))) == {}

    cache = _install(monkeypatch, _Response({"data": {"title": None}}))
    assert asyncio.run(manager.get_imdb_info_api(123, base_dir=str(tmp_path))) == {}
    assert cache.set_calls[-1][1] == {"negative": True}

    cache = _install(monkeypatch, _Response({"errors": [{"message": "missing"}], "data": {"title": None}}))
    assert asyncio.run(manager.get_imdb_info_api(123, base_dir=str(tmp_path))) == {}
    assert cache.set_calls == []


def test_imdb_info_rich_payload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cache = _install(monkeypatch, _Response(_rich_title_payload()))
    result = asyncio.run(ImdbManager().get_imdb_info_api(1234567, manual_language="pt", base_dir=str(tmp_path)))

    assert result["imdbID"] == "tt1234567"
    assert result["title"] == "Example" and result["aka"] == "Original Example"
    assert result["country"] == "US" and result["country_list"] == "US, GB"
    assert result["year"] == 2024 and result["end_year"] == 2026 and result["tv_year"] == 2026
    assert result["runtime"] == "60" and result["genres"] == "Drama, Comedy"
    assert result["directors"] == ["Director"] and result["creators"] == ["Creator"]
    assert result["writers"] == ["Writer"] and result["stars"] == ["Star"]
    assert result["edition_count"] == 1 and "Director's Cut" in result["editions"]
    assert result["edition_details"]["62"]["attributes"] == ["extended"]
    assert result["akas"][0]["country"] == "BR"
    assert len(result["episodes"]) == 3
    assert result["seasons_summary"] == [{"season": 1, "year": 2024, "year_range": "2024-2025"}]
    assert result["sound_mixes"] == ["Dolby Digital"]
    assert result["original_language"] == "pt"
    assert cache.set_calls and cache.set_calls[-1][0][-1] == result


def test_imdb_info_empty_country_runtime_episode_and_closest_year(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = _rich_title_payload()
    title = payload["data"]["title"]
    title["originalTitleText"] = {"text": "Example"}
    title["countriesOfOrigin"] = {"countries": []}
    title["releaseYear"] = {"year": 2024, "endYear": None}
    title["runtime"] = {"seconds": 0}
    title["runtimes"] = {"edges": []}
    title["episodes"]["episodes"]["edges"] = [
        {
            "node": {
                "id": "tt3000001",
                "series": {"displayableEpisodeNumber": {"displayableSeason": {"season": 2}, "episodeNumber": {"text": "1"}}},
                "titleText": {"text": "Episode"},
                "releaseYear": {"year": 2025},
                "releaseDate": {},
            }
        }
    ]
    _install(monkeypatch, _Response(payload))
    result = asyncio.run(ImdbManager().get_imdb_info_api("tt1234567", base_dir=str(tmp_path)))
    assert result["aka"] == "Example"
    assert result["country"] == "" and result["country_list"] == ""
    assert result["runtime"] == "60" and result["tv_year"] == 2025
    assert result["seasons_summary"] == [{"season": 2, "year": 2025, "year_range": "2025"}]

    payload["data"]["title"]["episodes"] = None
    _install(monkeypatch, _Response(payload))
    result = asyncio.run(ImdbManager().get_imdb_info_api(1234567, base_dir=str(tmp_path)))
    assert result["episodes"] == [] and result["seasons_summary"] == [] and result["tv_year"] is None


def test_search_quickie_constraints_type_and_year_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = ImdbManager()
    _install(monkeypatch, _Response(_search_payload(_edge("tt1111111", "A & B", year=2026))))
    sleep = AsyncMock()
    monkeypatch.setattr(imdb.asyncio, "sleep", sleep)
    result = asyncio.run(manager.search_imdb("A and B", 2026, quickie=True, category="MOVIE", duration=120, attempted=1))
    assert result == 1111111
    sleep.assert_awaited()
    query = str(_Client.calls[0]["json"]["query"])
    assert 'searchTerm: "A & B"' in query
    assert 'start: "2025-01-01"' in query and "min: 110" in query and "max: 130" in query

    _install(monkeypatch, _Response(_search_payload(_edge("tt2222222", "Show", title_type="TV Series", year=2026))))
    assert asyncio.run(manager.search_imdb("Show", 2026, quickie=True, category="TV")) == 2222222

    _install(monkeypatch, _Response(_search_payload(_edge("tt3333333", "Movie", year=2025))))
    assert asyncio.run(manager.search_imdb("Movie", 2026, quickie=True, category="MOVIE")) == 0

    _install(monkeypatch, _Response(_search_payload(_edge("tt4444444", "Movie", year=None))))
    assert asyncio.run(manager.search_imdb("Movie", None, quickie=True, category="MOVIE")) == 4444444

    no_id = _edge("", "Movie", year=2026)
    _install(monkeypatch, _Response(_search_payload(no_id)))
    assert asyncio.run(manager.search_imdb("Movie", 2026, quickie=True, category="MOVIE")) == 0

    _install(monkeypatch, _Response(_search_payload(_edge("tt5555555", "Movie", title_type="TV Series", year=2026))))
    assert asyncio.run(manager.search_imdb("Movie", 2026, quickie=True, category="MOVIE")) == 0


def test_search_single_multiple_similarity_and_unattended(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = ImdbManager()
    _install(monkeypatch, _Response(_search_payload(_edge("tt1000001", "Single"))))
    assert asyncio.run(manager.search_imdb("Single", 2026, category="MOVIE")) == 1000001

    close = _edge("tt1000002", "Exact Match", year=2026)
    previous_year = _edge("tt1000003", "Exact Match Extra", year=2025)
    distant = _edge("tt1000004", "Completely Different", year=2026)
    _install(monkeypatch, _Response(_search_payload(close, previous_year, distant)))
    assert asyncio.run(manager.search_imdb("Exact Match", 2026, category="MOVIE")) == 1000002

    equal = [
        _edge("tt1000005", "Candidate One", year=2026),
        _edge("tt1000006", "Candidate Two", year=2026),
    ]
    _install(monkeypatch, _Response(_search_payload(*equal)))
    assert asyncio.run(manager.search_imdb("Unrelated", 2026, category="MOVIE", unattended=True)) == 1000005


def test_search_fallback_prefix_wide_parsed_reduced_and_further(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = ImdbManager()

    class HandlerClient(_Client):
        matcher: ClassVar[Any] = lambda _query: None

        async def post(self, _url: str, **kwargs: object) -> _Response:
            type(self).calls.append(dict(kwargs))
            query = str(dict(kwargs).get("json", {}).get("query", ""))  # type: ignore[union-attr]
            value = type(self).matcher(query)
            if isinstance(value, BaseException):
                raise value
            return _Response(_search_payload(*value) if isinstance(value, list) else _search_payload())

    monkeypatch.setattr(imdb.httpx, "AsyncClient", HandlerClient)
    monkeypatch.setattr(imdb.asyncio, "sleep", AsyncMock())

    HandlerClient.calls = []
    HandlerClient.matcher = lambda query: [_edge("tt2000001", "Example")] if 'searchTerm: "Example"' in query else []
    assert asyncio.run(manager.search_imdb("The Example", 2026, category="MOVIE")) == 2000001

    HandlerClient.calls = []
    HandlerClient.matcher = lambda query: [_edge("tt2000002", "Wide")] if 'searchTerm: "Wide"' in query and "releaseDateConstraint" not in query else []
    assert asyncio.run(manager.search_imdb("Wide", 2026, category="MOVIE")) == 2000002

    monkeypatch.setattr(imdb, "guessit_fn", lambda *_args, **_kwargs: {"title": "Parsed Input"})
    monkeypatch.setattr(imdb, "anitopy_parse_fn", lambda *_args, **_kwargs: {"anime_title": "Parsed Anime"})
    HandlerClient.calls = []
    HandlerClient.matcher = lambda query: [_edge("tt2000003", "Parsed Anime")] if 'searchTerm: "Parsed Anime"' in query else []
    assert asyncio.run(manager.search_imdb("No Original", None, category="MOVIE", untouched_filename="Parsed Input")) == 2000003

    monkeypatch.setattr(imdb, "guessit_fn", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(imdb, "anitopy_parse_fn", lambda *_args, **_kwargs: {})
    HandlerClient.calls = []
    HandlerClient.matcher = lambda query: [_edge("tt2000004", "Alpha Beta")] if 'searchTerm: "Alpha Beta"' in query else []
    assert asyncio.run(manager.search_imdb("Alpha Beta Gamma", None, category="MOVIE")) == 2000004

    HandlerClient.calls = []
    HandlerClient.matcher = lambda query: [_edge("tt2000005", "One Two")] if 'searchTerm: "One Two"' in query else []
    assert asyncio.run(manager.search_imdb("One Two Three Four", None, category="MOVIE")) == 2000005


def test_search_secondary_and_graphql_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = ImdbManager()
    _install(
        monkeypatch,
        _Response(_search_payload()),
        _Response(_search_payload(_edge("tt3000001", "Secondary"))),
    )
    assert asyncio.run(manager.search_imdb("Primary", 2026, category="MOVIE", secondary_title="Secondary")) == 3000001

    request = httpx.Request("POST", "https://api.graphql.imdb.com/")
    _install(monkeypatch, *[httpx.RequestError("offline", request=request) for _ in range(8)])
    monkeypatch.setattr(imdb, "guessit_fn", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(imdb, "anitopy_parse_fn", lambda *_args, **_kwargs: {})
    assert asyncio.run(manager.search_imdb("No Result", None, category="MOVIE", unattended=True)) == 0


def test_search_interactive_manual_numeric_skip_and_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = ImdbManager()
    edges = [
        _edge("tt4000001", "Candidate One", plot="A" * 250),
        _edge("tt4000002", "Candidate Two", plot="Plot Two"),
    ]

    prompts = iter(("ttbad", "tt4000009"))
    _install(monkeypatch, _Response(_search_payload(*edges)))
    monkeypatch.setattr(imdb, "prompt_in_thread", lambda *_args, **_kwargs: asyncio.sleep(0, result=next(prompts)))
    assert asyncio.run(manager.search_imdb("Unrelated", 2026, category="MOVIE")) == 4000009

    prompts = iter(("99", "bad", "2"))
    _install(monkeypatch, _Response(_search_payload(*edges)))
    monkeypatch.setattr(imdb, "prompt_in_thread", lambda *_args, **_kwargs: asyncio.sleep(0, result=next(prompts)))
    assert asyncio.run(manager.search_imdb("Unrelated", 2026, category="MOVIE")) == 4000002

    _install(monkeypatch, _Response(_search_payload(*edges)))
    monkeypatch.setattr(imdb, "prompt_in_thread", lambda *_args, **_kwargs: asyncio.sleep(0, result="0"))
    assert asyncio.run(manager.search_imdb("Unrelated", 2026, category="MOVIE")) == 0

    cleanup = AsyncMock()
    _install(monkeypatch, _Response(_search_payload(*edges)))
    monkeypatch.setattr(imdb.cleanup_manager, "cleanup", cleanup)
    monkeypatch.setattr(imdb.cleanup_manager, "reset_terminal", lambda: None)

    async def eof(*_args: object, **_kwargs: object) -> str:
        raise EOFError

    monkeypatch.setattr(imdb, "prompt_in_thread", eof)
    with pytest.raises(OperationAbortedError, match="cancelled"):
        asyncio.run(manager.search_imdb("Unrelated", 2026, category="MOVIE"))
    cleanup.assert_awaited_once()


def test_search_no_results_manual_invalid_unattended_and_eof(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = ImdbManager()
    empty = [_Response(_search_payload()) for _ in range(8)]
    monkeypatch.setattr(imdb, "guessit_fn", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(imdb, "anitopy_parse_fn", lambda *_args, **_kwargs: {})

    _install(monkeypatch, *empty)
    monkeypatch.setattr(imdb, "prompt_in_thread", lambda *_args, **_kwargs: asyncio.sleep(0, result="tt5000001"))
    assert asyncio.run(manager.search_imdb("No Result", None, category="MOVIE")) == 5000001

    _install(monkeypatch, *[_Response(_search_payload()) for _ in range(8)])
    monkeypatch.setattr(imdb, "prompt_in_thread", lambda *_args, **_kwargs: asyncio.sleep(0, result="ttbad"))
    assert asyncio.run(manager.search_imdb("No Result", None, category="MOVIE")) == 0

    _install(monkeypatch, *[_Response(_search_payload()) for _ in range(8)])
    assert asyncio.run(manager.search_imdb("No Result", None, category="MOVIE", unattended=True)) == 0

    _install(monkeypatch, *[_Response(_search_payload()) for _ in range(8)])
    cleanup = AsyncMock()
    monkeypatch.setattr(imdb.cleanup_manager, "cleanup", cleanup)
    monkeypatch.setattr(imdb.cleanup_manager, "reset_terminal", lambda: None)

    async def eof(*_args: object, **_kwargs: object) -> str:
        raise EOFError

    monkeypatch.setattr(imdb, "prompt_in_thread", eof)
    with pytest.raises(OperationAbortedError, match="cancelled"):
        asyncio.run(manager.search_imdb("No Result", None, category="MOVIE"))
    cleanup.assert_awaited_once()


def _episode_payload(*, include_series: bool = True) -> dict[str, Any]:
    title: dict[str, Any] = {"id": "tt6000001", "titleText": {"text": "Episode"}}
    if include_series:
        title["series"] = {
            "displayableEpisodeNumber": {
                "displayableSeason": {"id": "season-1", "season": 1, "text": "Season 1"},
                "episodeNumber": {"id": "episode-2", "text": "2"},
            },
            "nextEpisode": {"id": "tt6000002", "titleText": {"text": "Next"}},
            "previousEpisode": {"id": "tt6000000", "titleText": {"text": "Previous"}},
            "series": {"id": "tt7000000", "titleText": {"text": "Series"}},
        }
    return {"data": {"title": title}}


def test_get_imdb_from_episode_all_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = ImdbManager()
    assert asyncio.run(manager.get_imdb_from_episode(None)) is None  # type: ignore[arg-type]
    assert asyncio.run(manager.get_imdb_from_episode(0)) is None

    _install(monkeypatch, _Response(_episode_payload()))
    result = asyncio.run(manager.get_imdb_from_episode(6000001))
    assert result is not None
    assert result["id"] == "tt6000001" and result["title"] == "Episode"
    assert result["series"] == {
        "season_id": "season-1",
        "season": 1,
        "season_text": "Season 1",
        "episode_id": "episode-2",
        "episode_text": "2",
        "series_id": "tt7000000",
        "series_title": "Series",
    }
    assert result["next_episode"] == {"id": "tt6000002", "title": "Next"}
    assert result["previous_episode"] == {"id": "tt6000000", "title": "Previous"}
    assert 'title(id: "tt6000001")' in str(_Client.calls[0]["json"]["query"])

    _install(monkeypatch, _Response(_episode_payload(include_series=False)))
    result = asyncio.run(manager.get_imdb_from_episode("tt6000001"))
    assert result is not None and result["series"] == {}

    _install(monkeypatch, _Response({"data": {"title": None}}))
    assert asyncio.run(manager.get_imdb_from_episode("tt6000001")) is None

    _install(monkeypatch, RuntimeError("offline"))
    assert asyncio.run(manager.get_imdb_from_episode("tt6000001")) is None

    class OddId:
        def __int__(self) -> int:
            raise ValueError("not numeric")

        def __str__(self) -> str:
            return "abc"

    _install(monkeypatch, _Response(_episode_payload(include_series=False)))
    assert asyncio.run(manager.get_imdb_from_episode(OddId())) is not None  # type: ignore[arg-type]
    assert 'title(id: "tt0000abc")' in str(_Client.calls[0]["json"]["query"])


def test_search_attempted_none_extension_removal_and_split_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = ImdbManager()
    monkeypatch.setattr(imdb.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(imdb, "guessit_fn", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(imdb, "anitopy_parse_fn", lambda *_args, **_kwargs: {})

    _install(monkeypatch, *[_Response(_search_payload()) for _ in range(10)])
    assert asyncio.run(manager.search_imdb("One Two Three Four mkv", None, category="MOVIE", attempted=None, unattended=True)) == 0

    class BrokenSplit(str):
        def split(self, *_args: object, **_kwargs: object) -> list[str]:
            raise RuntimeError("split failed")

    _install(monkeypatch, *[_Response(_search_payload()) for _ in range(10)])
    assert asyncio.run(manager.search_imdb(BrokenSplit("Broken Title"), None, category="MOVIE", unattended=True)) == 0


def test_search_wide_and_parsed_exception_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = ImdbManager()

    class FlakyReplace(str):
        calls = 0

        def replace(self, old: str, new: str, count: int = -1) -> str:
            type(self).calls += 1
            if type(self).calls >= 2:
                raise RuntimeError("replace failed")
            return super().replace(old, new, count)

    # MOVIE normalization calls replace three times in the primary search;
    # the first replace of the wide search then fails inside its guard.
    FlakyReplace.calls = 0
    _install(monkeypatch, *[_Response(_search_payload()) for _ in range(10)])
    monkeypatch.setattr(imdb, "guessit_fn", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("guess failed")))
    assert asyncio.run(manager.search_imdb(FlakyReplace("Wide Error"), None, category="MOVIE", unattended=True)) == 0


def test_search_previous_year_similarity_and_manual_parse_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = ImdbManager()
    previous = _edge("tt8000001", "Target", year=2025)
    other = _edge("tt8000002", "Other", year=2026)
    _install(monkeypatch, _Response(_search_payload(previous, other)))
    assert asyncio.run(manager.search_imdb("Target", 2026, category="MOVIE")) == 8000001

    class BrokenManual(str):
        def lower(self) -> BrokenManual:
            return self

        def replace(self, *_args: object, **_kwargs: object) -> str:
            raise RuntimeError("manual parse failed")

    edges = [_edge("tt8000003", "Candidate One"), _edge("tt8000004", "Candidate Two")]
    prompts = iter((BrokenManual("ttbad"), "tt8000005"))
    _install(monkeypatch, _Response(_search_payload(*edges)))
    monkeypatch.setattr(imdb, "prompt_in_thread", lambda *_args, **_kwargs: asyncio.sleep(0, result=next(prompts)))
    assert asyncio.run(manager.search_imdb("Unrelated", 2026, category="MOVIE")) == 8000005

    _install(monkeypatch, *[_Response(_search_payload()) for _ in range(10)])
    monkeypatch.setattr(imdb, "guessit_fn", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(imdb, "anitopy_parse_fn", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(imdb, "prompt_in_thread", lambda *_args, **_kwargs: asyncio.sleep(0, result=BrokenManual("ttbad")))
    assert asyncio.run(manager.search_imdb("No Result", None, category="MOVIE")) == 0


def test_guessit_wrapper(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(imdb.guessit_module, "guessit", lambda value, options: {"title": value, "options": options})
    assert imdb.guessit_fn("Example", {"excludes": ["language"]}) == {
        "title": "Example",
        "options": {"excludes": ["language"]},
    }
