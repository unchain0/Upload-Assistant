from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import ClassVar, Self

import pytest

from src.integrations.external_apis import igdb
from src.integrations.external_apis.igdb import IGDBAPI


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

    async def post(self, url: str, **kwargs: object) -> _Response:
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
    monkeypatch.setattr(igdb.httpx, "AsyncClient", _Client)


def test_constructor_paths() -> None:
    assert IGDBAPI("id", "secret").token_file == ""
    assert str(IGDBAPI("id", "secret", "/base").token_file).endswith("tmp/igdb_cache/igdb_token.json")


def test_access_token_cached_valid_expired_invalid_and_network_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    api = IGDBAPI("id", "secret", str(tmp_path))
    token = Path(api.token_file)
    token.parent.mkdir(parents=True)
    token.write_text(json.dumps({"access_token": "cached", "expires_at": time.time() + 1000}), encoding="utf-8")
    assert asyncio.run(api.get_access_token()) == "cached"

    token.write_text(json.dumps({"access_token": "expired", "expires_at": 1}), encoding="utf-8")
    _Client.queue = [_Response(200, {"access_token": "fresh", "expires_in": 60})]
    monkeypatch.setattr(igdb.time, "time", lambda: 100.0)
    assert asyncio.run(api.get_access_token()) == "fresh"
    saved = json.loads(token.read_text(encoding="utf-8"))
    assert saved == {"access_token": "fresh", "expires_at": 160.0}

    token.write_text("bad json", encoding="utf-8")
    _Client.queue = [_Response(200, {"access_token": "fresh2"})]
    assert asyncio.run(api.get_access_token()) == "fresh2"
    assert json.loads(token.read_text(encoding="utf-8"))["expires_at"] == 3700.0


def test_access_token_status_exception_and_no_token_file() -> None:
    api = IGDBAPI("id", "secret")
    _Client.queue = [_Response(401, {})]
    assert asyncio.run(api.get_access_token()) is None
    _Client.queue = [RuntimeError("network")]
    assert asyncio.run(api.get_access_token()) is None
    _Client.queue = [_Response(200, {"access_token": "token", "expires_in": 1})]
    assert asyncio.run(api.get_access_token()) == "token"


def test_search_cache_token_failure_success_empty_none_status_and_error(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = _Cache([{"id": 1}])
    monkeypatch.setattr(igdb, "cache_for", lambda _base: cache)
    monkeypatch.setattr(igdb, "is_cache_miss", lambda _value: False)
    api = IGDBAPI("id", "secret")
    assert asyncio.run(api.search_game("Game Title")) == [{"id": 1}]

    cache.value = object()
    monkeypatch.setattr(igdb, "is_cache_miss", lambda _value: True)
    api.get_access_token = lambda: _async_value(None)  # type: ignore[method-assign]
    assert asyncio.run(api.search_game("Game Title")) is None

    api.get_access_token = lambda: _async_value("token")  # type: ignore[method-assign]
    for payload in ([{"id": 1}], [], None):
        _Client.queue = [_Response(200, payload)]
        assert asyncio.run(api.search_game("Game ! Title")) == payload
        if payload is not None:
            assert cache.set_calls[-1][1]["negative"] is (not bool(payload))
    assert 'search "Game ! Title"' in _Client.requests[-1][1]["content"]

    _Client.queue = [_Response(500, {}, "bad"), RuntimeError("network")]
    assert asyncio.run(api.search_game("Game")) is None
    assert asyncio.run(api.search_game("Game")) is None


def _async_value(value: object):
    async def result():
        return value

    return result()


def test_fetch_game_id_invalid_cache_token_success_empty_none_status_error(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = _Cache({"id": 1})
    monkeypatch.setattr(igdb, "cache_for", lambda _base: cache)
    monkeypatch.setattr(igdb, "is_cache_miss", lambda _value: False)
    api = IGDBAPI("id", "secret")
    assert asyncio.run(api.fetch_game_by_id("bad")) is None
    assert asyncio.run(api.fetch_game_by_id(" 1 ")) == {"id": 1}

    cache.value = object()
    monkeypatch.setattr(igdb, "is_cache_miss", lambda _value: True)
    api.get_access_token = lambda: _async_value(None)  # type: ignore[method-assign]
    assert asyncio.run(api.fetch_game_by_id("1")) is None
    api.get_access_token = lambda: _async_value("token")  # type: ignore[method-assign]

    _Client.queue = [_Response(200, [{"id": 1}])]
    assert asyncio.run(api.fetch_game_by_id("1")) == {"id": 1}
    assert cache.set_calls[-1][0][:3] == ("igdb", "game", "1")
    _Client.queue = [_Response(200, [None])]
    assert asyncio.run(api.fetch_game_by_id("1")) is None
    _Client.queue = [_Response(200, [])]
    assert asyncio.run(api.fetch_game_by_id("1")) is None
    _Client.queue = [_Response(500, {}, "bad"), RuntimeError("network")]
    assert asyncio.run(api.fetch_game_by_id("1")) is None
    assert asyncio.run(api.fetch_game_by_id("1")) is None


def test_fetch_steam_invalid_cache_token_success_empty_none_status_error(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = _Cache({"id": 2})
    monkeypatch.setattr(igdb, "cache_for", lambda _base: cache)
    monkeypatch.setattr(igdb, "is_cache_miss", lambda _value: False)
    api = IGDBAPI("id", "secret")
    assert asyncio.run(api.fetch_game_by_steam_id("bad")) is None
    assert asyncio.run(api.fetch_game_by_steam_id(" 2 ")) == {"id": 2}

    cache.value = object()
    monkeypatch.setattr(igdb, "is_cache_miss", lambda _value: True)
    api.get_access_token = lambda: _async_value(None)  # type: ignore[method-assign]
    assert asyncio.run(api.fetch_game_by_steam_id("2")) is None
    api.get_access_token = lambda: _async_value("token")  # type: ignore[method-assign]

    _Client.queue = [_Response(200, [{"id": 2}])]
    assert asyncio.run(api.fetch_game_by_steam_id("2")) == {"id": 2}
    assert cache.set_calls[-1][0][:3] == ("igdb", "steam", "2")
    _Client.queue = [_Response(200, [None])]
    assert asyncio.run(api.fetch_game_by_steam_id("2")) is None
    _Client.queue = [_Response(200, [])]
    assert asyncio.run(api.fetch_game_by_steam_id("2")) is None
    _Client.queue = [_Response(500, {}, "bad"), RuntimeError("network")]
    assert asyncio.run(api.fetch_game_by_steam_id("2")) is None
    assert asyncio.run(api.fetch_game_by_steam_id("2")) is None


def test_cache_game_details_guards_and_success(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = _Cache()
    monkeypatch.setattr(igdb, "cache_for", lambda _base: cache)
    api = IGDBAPI("id", "secret")
    asyncio.run(api.cache_game_details({"id": 1}))
    assert cache.set_calls == []
    api = IGDBAPI("id", "secret", "/base")
    for payload in ({}, {"name": "missing id"}):
        asyncio.run(api.cache_game_details(payload))
    assert cache.set_calls == []
    asyncio.run(api.cache_game_details({"id": 3, "name": "Game"}))
    assert cache.set_calls[-1][0] == ("igdb", "game", "3", {"id": 3, "name": "Game"})
