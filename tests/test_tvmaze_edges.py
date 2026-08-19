from __future__ import annotations

import asyncio
from typing import Any, ClassVar, Self
from unittest.mock import AsyncMock

import httpx
import pytest

from src.domain_models.release import Meta
from src.integrations.external_apis import tvmaze
from src.integrations.external_apis.tvmaze import TvmazeManager


class _Cache:
    def __init__(self, value: object = None) -> None:
        self.value = value
        self.set_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def get(self, *_args: object) -> object:
        return self.value

    async def set(self, *args: object, **kwargs: object) -> None:
        self.set_calls.append((args, kwargs))


class _Response:
    def __init__(self, status_code: int = 200, payload: object = None, text: str = "response") -> None:
        self.status_code = status_code
        self.payload = payload
        self.text = text
        self.request = httpx.Request("GET", "https://api.tvmaze.invalid")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad", request=self.request, response=httpx.Response(self.status_code, request=self.request, text=self.text))

    def json(self) -> object:
        if isinstance(self.payload, BaseException):
            raise self.payload
        return self.payload


class _Client:
    queue: ClassVar[list[object]] = []
    requests: ClassVar[list[tuple[str, dict[str, object]]]] = []

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, url: str, **kwargs: object) -> _Response:
        type(self).requests.append((url, kwargs))
        value = type(self).queue.pop(0)
        if isinstance(value, BaseException):
            raise value
        assert isinstance(value, _Response)
        return value


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch) -> None:
    _Client.queue = []
    _Client.requests = []
    monkeypatch.setattr(tvmaze.httpx, "AsyncClient", _Client)


def _show(identifier: int, **values: object) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": identifier,
        "name": f"Show {identifier}",
        "premiered": "2024-01-01",
        "externals": {"thetvdb": identifier + 100},
    }
    data.update(values)
    return data


def _episode(**values: object) -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": "Episode",
        "summary": "<p>Episode overview</p>",
        "season": 1,
        "number": 2,
        "airdate": "2024-01-02",
        "runtime": 45,
        "image": {"original": "episode-original", "medium": "episode-medium"},
        "_links": {"show": {"href": "https://api.tvmaze.com/shows/1", "name": "Fallback Show"}},
    }
    data.update(values)
    return data


def test_search_manual_ids_validation_and_full_tuple() -> None:
    manager = TvmazeManager()
    assert asyncio.run(manager.search_tvmaze("Show", "2024", "tt123", "456", tvmaze_manual="789")) == 789
    assert asyncio.run(manager.search_tvmaze("Show", "2024", "123", "456", tvmaze_manual="bad", return_full_tuple=True)) == (0, 123, 456)
    assert asyncio.run(manager.search_tvmaze("Show", "2024", "bad", "bad", tvmaze_manual="1", return_full_tuple=True)) == (1, 0, 0)
    assert asyncio.run(manager.search_tvmaze("Show", "2024", None, None, tvmaze_manual=2)) == 2


def test_search_tvdb_imdb_title_first_words_dedup_and_no_results(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = TvmazeManager()
    request = AsyncMock(side_effect=[_show(1), [], []])
    monkeypatch.setattr(manager, "_make_tvmaze_request", request)
    assert asyncio.run(manager.search_tvmaze("Show Name", "2024", 123, 456)) == 1
    assert request.await_args_list[0].args[1] == {"thetvdb": 456}

    request = AsyncMock(side_effect=[[], _show(2), []])
    monkeypatch.setattr(manager, "_make_tvmaze_request", request)
    assert asyncio.run(manager.search_tvmaze("Show Name", "2024", 123, 0)) == 2
    assert request.await_args_list[0].args[1] == {"imdb": "tt0000123"}

    request = AsyncMock(side_effect=[[{"show": _show(3)}, {"ignored": True}, {"show": _show(3)}]])
    monkeypatch.setattr(manager, "_make_tvmaze_request", request)
    assert asyncio.run(manager.search_tvmaze("Show Name", "2024", 0, 0)) == 3

    request = AsyncMock(side_effect=[[], [{"show": _show(4)}]])
    monkeypatch.setattr(manager, "_make_tvmaze_request", request)
    assert asyncio.run(manager.search_tvmaze("Long Show Name Extra", "2024", 0, 0)) == 4
    assert request.await_args_list[-1].args[1] == {"q": "Long Show"}

    request = AsyncMock(return_value=[])
    monkeypatch.setattr(manager, "_make_tvmaze_request", request)
    assert asyncio.run(manager.search_tvmaze("One", "2024", 0, 0, return_full_tuple=True)) == (0, 0, 0)


def test_search_manual_date_choices_invalid_skip_and_tvdb_update(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = TvmazeManager()
    monkeypatch.setattr(manager, "_make_tvmaze_request", AsyncMock(return_value=[{"show": _show(1)}, {"show": _show(2)}]))
    answers = iter(("bad", "9", "2"))
    monkeypatch.setattr(tvmaze.cli_ui, "ask_string", lambda *_args, **_kwargs: next(answers))
    assert asyncio.run(manager.search_tvmaze("Show", "2024", 0, 0, return_full_tuple=True)) == (2, 0, 102)

    monkeypatch.setattr(manager, "_make_tvmaze_request", AsyncMock(return_value=[{"show": _show(1)}, {"show": _show(2)}]))
    monkeypatch.setattr(tvmaze.cli_ui, "ask_string", lambda *_args, **_kwargs: "0")
    assert asyncio.run(manager.search_tvmaze("Show", "2024", 0, 0)) == 0

    monkeypatch.setattr(manager, "_make_tvmaze_request", AsyncMock(return_value=[{"show": _show(1)}, {"show": _show(2)}]))
    assert asyncio.run(manager.search_tvmaze("Show", "2024", 0, 0, manual_date="2024-01-01", return_full_tuple=True)) == (0, 0, 0)

    show = _show(3, externals={"thetvdb": None})
    monkeypatch.setattr(manager, "_make_tvmaze_request", AsyncMock(return_value=[{"show": show}]))
    monkeypatch.setattr(tvmaze.cli_ui, "ask_string", lambda *_args, **_kwargs: "1")
    assert asyncio.run(manager.search_tvmaze("Show", "2024", 0, 0, manual_date="date", return_full_tuple=True)) == (3, 0, 0)


def test_request_cache_dict_list_invalid_status_http_and_network(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = TvmazeManager()
    cache = _Cache({"id": 1})
    monkeypatch.setattr(tvmaze, "cache_for", lambda *_args: cache)
    monkeypatch.setattr(tvmaze, "is_cache_miss", lambda _value: False)
    assert asyncio.run(manager._make_tvmaze_request("url", {}, "base", {})) == {"id": 1}

    cache.value = object()
    monkeypatch.setattr(tvmaze, "is_cache_miss", lambda _value: True)
    _Client.queue = [_Response(200, {"id": 2})]
    assert asyncio.run(manager._make_tvmaze_request("url", {"q": "x"})) == {"id": 2}
    assert cache.set_calls[-1][1] == {}

    _Client.queue = [_Response(200, [{"id": 1}, "bad", {"id": 2}])]
    assert asyncio.run(manager._make_tvmaze_request("url", {})) == [{"id": 1}, {"id": 2}]
    _Client.queue = [_Response(200, "bad")]
    assert asyncio.run(manager._make_tvmaze_request("url", {})) is None
    _Client.queue = [_Response(404, {})]
    assert asyncio.run(manager._make_tvmaze_request("url", {})) is None

    request = httpx.Request("GET", "https://api.tvmaze.com")
    _Client.queue = [
        httpx.HTTPStatusError("bad", request=request, response=httpx.Response(500, request=request)),
        httpx.RequestError("offline", request=request),
    ]
    assert asyncio.run(manager._make_tvmaze_request("url", {})) is None
    assert asyncio.run(manager._make_tvmaze_request("url", {})) is None


def test_show_details_cached_success_not_found_and_error(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = TvmazeManager()
    cache = _Cache({"id": 1})
    monkeypatch.setattr(tvmaze, "cache_for", lambda *_args: cache)
    monkeypatch.setattr(tvmaze, "is_cache_miss", lambda _value: False)
    assert asyncio.run(manager.get_show_details(1, "base")) == {"id": 1}

    cache.value = object()
    monkeypatch.setattr(tvmaze, "is_cache_miss", lambda _value: True)
    monkeypatch.setattr(manager, "_make_tvmaze_request", AsyncMock(return_value={"id": 2, "name": "Show"}))
    assert asyncio.run(manager.get_show_details(2, "base")) == {"id": 2, "name": "Show"}
    assert cache.set_calls[-1][0][:3] == ("tvmaze", "show", "2")

    monkeypatch.setattr(manager, "_make_tvmaze_request", AsyncMock(return_value={}))
    assert asyncio.run(manager.get_show_details(3, "base")) == {}
    assert cache.set_calls[-1][1]["negative"] is True
    monkeypatch.setattr(manager, "_make_tvmaze_request", AsyncMock(side_effect=RuntimeError("bad")))
    with pytest.raises(RuntimeError, match="bad"):
        asyncio.run(manager.get_show_details(4, "base"))


def test_episode_by_date_cached_full_metadata_show_fallback_and_external_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = TvmazeManager()
    cache = _Cache({"episode_name": "Cached"})
    monkeypatch.setattr(tvmaze, "cache_for", lambda *_args: cache)
    monkeypatch.setattr(tvmaze, "is_cache_miss", lambda _value: False)
    assert asyncio.run(manager.get_episode_by_date(1, "2024-01-02", "base")) == {"episode_name": "Cached"}

    cache.value = object()
    monkeypatch.setattr(tvmaze, "is_cache_miss", lambda _value: True)
    show = _show(
        1,
        name="Actual Show",
        summary="<p>Show overview</p>",
        image={"original": "show-original", "medium": "show-medium"},
        externals={"thetvdb": 456, "imdb": "tt1234567"},
    )
    monkeypatch.setattr(manager, "_make_tvmaze_request", AsyncMock(side_effect=[[_episode()], show]))
    result = asyncio.run(manager.get_episode_by_date(1, "2024-01-02", "base"))
    assert result == {
        "episode_name": "Episode",
        "episode_overview": "Episode overview",
        "season": 1,
        "episode": 2,
        "airdate": "2024-01-02",
        "runtime": 45,
        "episode_image": "episode-original",
        "show_name": "Actual Show",
        "show_overview": "Show overview",
        "show_image": "show-original",
        "tvdb_id": 456,
        "imdb_id": "tt1234567",
    }
    assert cache.set_calls[-1][0][:3] == ("tvmaze", "episode-date", "1:2024-01-02")


def test_episode_by_date_optional_images_missing_show_and_link_name(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = TvmazeManager()
    monkeypatch.setattr(tvmaze, "is_cache_miss", lambda _value: True)
    monkeypatch.setattr(tvmaze, "cache_for", lambda *_args: _Cache(object()))
    episode = _episode(summary=None, image={"medium": "episode-medium"}, name=None)
    monkeypatch.setattr(manager, "_make_tvmaze_request", AsyncMock(side_effect=[[episode], {}]))
    result = asyncio.run(manager.get_episode_by_date(1, "date"))
    assert result["episode_name"] == ""
    assert "episode_overview" not in result
    assert result["episode_image"] == "episode-medium"
    assert result["show_name"] == "Fallback Show"
    assert result["show_overview"] == "" and result["show_image"] == ""
    assert result["tvdb_id"] == 0 and result["imdb_id"] == ""

    episode = _episode(image=None, _links={})
    monkeypatch.setattr(manager, "_make_tvmaze_request", AsyncMock(side_effect=[[episode], {"name": "Show", "image": {"medium": "show-medium"}, "externals": {}}]))
    result = asyncio.run(manager.get_episode_by_date(1, "date"))
    assert result["episode_image"] == "" and result["show_image"] == "show-medium"


def test_episode_by_date_no_results_invalid_episode_show_error_and_request_error(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = TvmazeManager()
    cache = _Cache(object())
    monkeypatch.setattr(tvmaze, "cache_for", lambda *_args: cache)
    monkeypatch.setattr(tvmaze, "is_cache_miss", lambda _value: True)

    monkeypatch.setattr(manager, "_make_tvmaze_request", AsyncMock(return_value=[]))
    assert asyncio.run(manager.get_episode_by_date(1, "date")) == {}
    assert cache.set_calls[-1][1]["negative"] is True

    monkeypatch.setattr(manager, "_make_tvmaze_request", AsyncMock(return_value=["invalid"]))
    assert asyncio.run(manager.get_episode_by_date(1, "date")) == {}

    monkeypatch.setattr(manager, "_make_tvmaze_request", AsyncMock(side_effect=RuntimeError("bad")))
    assert asyncio.run(manager.get_episode_by_date(1, "date")) == {}


def test_search_remaining_invalid_ids_single_candidates_and_full_tuple(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = TvmazeManager()

    # Invalid external identifiers are normalized before any search attempt.
    request = AsyncMock(return_value=[])
    monkeypatch.setattr(manager, "_make_tvmaze_request", request)
    assert asyncio.run(manager.search_tvmaze("Show", "2024", "bad", "bad", return_full_tuple=True)) == (0, 0, 0)

    # Early external-ID matches preserve all identifiers in tuple mode.
    monkeypatch.setattr(manager, "_make_tvmaze_request", AsyncMock(return_value=_show(10)))
    assert asyncio.run(manager.search_tvmaze("Show", "2024", 123, 456, return_full_tuple=True)) == (10, 123, 456)

    monkeypatch.setattr(manager, "_make_tvmaze_request", AsyncMock(side_effect=[[], _show(11)]))
    assert asyncio.run(manager.search_tvmaze("Show", "2024", 123, 0, return_full_tuple=True)) == (11, 123, 0)

    # Single title results support both wrapper and raw-show response shapes.
    monkeypatch.setattr(manager, "_make_tvmaze_request", AsyncMock(return_value=[{"show": _show(12)}]))
    assert asyncio.run(manager.search_tvmaze("Show", "2024", 0, 0, return_full_tuple=True)) == (12, 0, 112)

    monkeypatch.setattr(manager, "_make_tvmaze_request", AsyncMock(return_value=[_show(13)]))
    assert asyncio.run(manager.search_tvmaze("Show", "2024", 0, 0, return_full_tuple=True)) == (13, 0, 113)


def test_show_details_remaining_cache_and_no_cache_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = TvmazeManager()
    cache = _Cache({"not_found": True})
    monkeypatch.setattr(tvmaze, "cache_for", lambda *_args: cache)
    monkeypatch.setattr(tvmaze, "is_cache_miss", lambda _value: False)
    assert asyncio.run(manager.get_show_details(1, "base")) == {}

    cache.value = object()
    monkeypatch.setattr(tvmaze, "is_cache_miss", lambda _value: True)
    monkeypatch.setattr(manager, "_make_tvmaze_request", AsyncMock(return_value={}))
    assert asyncio.run(manager.get_show_details(2, "base")) == {}
    assert cache.set_calls[-1][1]["negative"] is True

    monkeypatch.setattr(manager, "_make_tvmaze_request", AsyncMock(return_value={"id": 3}))
    assert asyncio.run(manager.get_show_details(3)) == {"id": 3}


def test_search_dresses_sparse_results_and_adopts_imdb(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = TvmazeManager()
    sparse = ["bad", {"id": "bad"}, {"show": {"name": "missing id"}}]
    monkeypatch.setattr(manager, "_make_tvmaze_request", AsyncMock(return_value=sparse))
    assert asyncio.run(manager.search_tvmaze("Show", "2024", 0, 0)) == 0

    show = _show(20, externals={"thetvdb": 120, "imdb": "tt1234567"})
    monkeypatch.setattr(manager, "_make_tvmaze_request", AsyncMock(return_value=[{"show": show}]))
    assert asyncio.run(manager.search_tvmaze("Show", "2024", 0, 0, return_full_tuple=True)) == (20, 1234567, 120)


def test_episode_data_success_show_fallback_sparse_and_empty() -> None:
    manager = TvmazeManager()
    show = _show(
        1,
        name="Actual Show",
        summary="<p>Show overview</p>",
        image={"original": "show-original", "medium": "show-medium"},
    )
    _Client.queue = [_Response(200, _episode()), _Response(200, show)]
    result = asyncio.run(manager.get_tvmaze_episode_data(1, 1, 2))
    assert result == {
        "episode_name": "Episode",
        "overview": "Episode overview",
        "season_number": 1,
        "episode_number": 2,
        "air_date": "2024-01-02",
        "runtime": 45,
        "series_name": "Actual Show",
        "series_overview": "Show overview",
        "image": "episode-original",
        "image_medium": "episode-medium",
        "series_image": "show-original",
        "series_image_medium": "show-medium",
    }

    _Client.queue = [_Response(200, _episode(summary="", image=None)), _Response(503, {})]
    result = asyncio.run(manager.get_tvmaze_episode_data(1, 1, 2))
    assert result is not None
    assert result["series_name"] == "Fallback Show"
    assert result["overview"] == "" and result["image"] is None
    assert result["series_image"] is None

    _Client.queue = [_Response(200, {})]
    assert asyncio.run(manager.get_tvmaze_episode_data(1, 1, 2)) is None


def test_episode_data_404_manual_and_tvdb_fallbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = TvmazeManager()
    fallback = AsyncMock(return_value={"episode_name": "By Date"})
    monkeypatch.setattr(manager, "get_tvmaze_episode_data_by_date", fallback)

    _Client.queue = [_Response(404, {})]
    manual = Meta(manual_date="2024-01-02")
    assert asyncio.run(manager.get_tvmaze_episode_data(1, 1, 2, manual)) == {"episode_name": "By Date"}
    fallback.assert_awaited_with(1, "2024-01-02")

    fallback.reset_mock()
    _Client.queue = [_Response(404, {})]
    tvdb_dict = Meta(
        manual_date=None,
        tvdb_episode_id=22,
        tvdb_episode_data={"episodes": [{"id": 22, "aired": "2024-02-03"}]},
        debug=True,
    )
    assert asyncio.run(manager.get_tvmaze_episode_data(1, 1, 2, tvdb_dict)) == {"episode_name": "By Date"}
    fallback.assert_awaited_with(1, "2024-02-03")

    fallback.reset_mock()
    _Client.queue = [_Response(404, {})]
    tvdb_list = Meta(
        manual_date=None,
        tvdb_episode_id=23,
        tvdb_episode_data=[{"id": 23, "aired": "2024-03-04"}],
        debug=True,
    )
    assert asyncio.run(manager.get_tvmaze_episode_data(1, 1, 2, tvdb_list)) == {"episode_name": "By Date"}
    fallback.assert_awaited_with(1, "2024-03-04")

    _Client.queue = [_Response(404, {})]
    no_airdate = Meta(
        manual_date=None,
        tvdb_episode_id=24,
        tvdb_episode_data={"episodes": [{"id": 24, "aired": 123}, {"id": 25, "aired": "2024-01-01"}]},
        debug=True,
    )
    assert asyncio.run(manager.get_tvmaze_episode_data(1, 1, 2, no_airdate)) is None

    _Client.queue = [_Response(404, {})]
    malformed = Meta(manual_date=None, tvdb_episode_id=1, tvdb_episode_data={"episodes": "bad"}, debug=True)
    assert asyncio.run(manager.get_tvmaze_episode_data(1, 1, 2, malformed)) is None


def test_episode_data_http_request_and_generic_errors() -> None:
    manager = TvmazeManager()
    _Client.queue = [_Response(500, {})]
    assert asyncio.run(manager.get_tvmaze_episode_data(1, 1, 2)) is None

    request = httpx.Request("GET", "https://api.tvmaze.com")
    _Client.queue = [httpx.RequestError("offline", request=request)]
    assert asyncio.run(manager.get_tvmaze_episode_data(1, 1, 2)) is None

    _Client.queue = [_Response(200, ValueError("bad json"))]
    assert asyncio.run(manager.get_tvmaze_episode_data(1, 1, 2)) is None


def test_episode_by_date_direct_success_sparse_and_empty() -> None:
    manager = TvmazeManager()
    show = _show(
        1,
        name="Actual Show",
        summary="<p>Show overview</p>",
        image={"original": "show-original", "medium": "show-medium"},
    )
    _Client.queue = [_Response(200, [_episode()]), _Response(200, show)]
    result = asyncio.run(manager.get_tvmaze_episode_data_by_date(1, "2024-01-02"))
    assert result is not None
    assert result["episode_name"] == "Episode"
    assert result["series_name"] == "Actual Show"
    assert result["overview"] == "Episode overview"
    assert result["series_overview"] == "Show overview"
    assert result["image"] == "episode-original"
    assert result["series_image"] == "show-original"

    _Client.queue = [_Response(200, [_episode(summary="", image=None)]), _Response(503, {})]
    result = asyncio.run(manager.get_tvmaze_episode_data_by_date(1, "2024-01-02"))
    assert result is not None
    assert result["series_name"] == "Fallback Show"
    assert result["overview"] == "" and result["image"] is None
    assert result["series_image"] is None

    _Client.queue = [_Response(200, [])]
    assert asyncio.run(manager.get_tvmaze_episode_data_by_date(1, "2024-01-02")) is None


def test_episode_by_date_direct_http_request_and_generic_errors() -> None:
    manager = TvmazeManager()
    _Client.queue = [_Response(404, {}, "not found")]
    assert asyncio.run(manager.get_tvmaze_episode_data_by_date(1, "2024-01-02")) is None

    request = httpx.Request("GET", "https://api.tvmaze.com")
    _Client.queue = [httpx.RequestError("offline", request=request)]
    assert asyncio.run(manager.get_tvmaze_episode_data_by_date(1, "2024-01-02")) is None

    _Client.queue = [_Response(200, ValueError("bad json"))]
    assert asyncio.run(manager.get_tvmaze_episode_data_by_date(1, "2024-01-02")) is None
