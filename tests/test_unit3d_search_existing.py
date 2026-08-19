import asyncio
from typing import Any

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.aither import Aither
from src.integrations.trackers.UNIT3D.samaritano import Samaritano
from src.integrations.trackers.UNIT3D.torrentdesi import DesiTorrents


class FakeResponse:
    status_code = 200

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, list[object]]:
        return {"data": []}


class FakeAsyncClient:
    def __init__(self, requests: list[list[tuple[str, Any]]], **_kwargs: Any) -> None:
        self.requests = requests

    async def __aenter__(self) -> FakeAsyncClient:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        pass

    async def get(self, *, url: str, headers: dict[str, str], params: list[tuple[str, Any]]) -> FakeResponse:
        _ = (url, headers)
        self.requests.append(params)
        return FakeResponse()


def test_desitorrents_declares_metadata_id_endpoint() -> None:
    tracker = DesiTorrents({"TRACKERS": {"DESITORRENTS": {}}})

    assert tracker.id_url == "https://torrent.desi/api/v1/torrents/"


@pytest.mark.parametrize("tracker_class", [Aither, Samaritano])
def test_tmdb_duplicate_search_omits_category_filter(monkeypatch: pytest.MonkeyPatch, tracker_class: type[Aither] | type[Samaritano]) -> None:
    requests: list[list[tuple[str, Any]]] = []

    def factory(**kwargs: Any) -> FakeAsyncClient:
        return FakeAsyncClient(requests, **kwargs)

    monkeypatch.setattr("src.integrations.trackers.UNIT3D.httpx.AsyncClient", factory)

    tracker = tracker_class({"TRACKERS": {tracker_class.tracker: {"api_key": "test-key"}}})
    meta = Meta(category="TV", title="Example Show", tmdb=123, season="S01", resolution="1080p", type="WEBDL")

    asyncio.run(tracker.search_existing(meta))

    assert len(requests) == 1
    assert ("tmdbId", "123") in requests[0]
    assert ("name", "Example Show S01") in requests[0]
    assert not any(key == "categories[]" for key, _value in requests[0])


def test_missing_tmdb_keeps_category_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[list[tuple[str, Any]]] = []

    def factory(**kwargs: Any) -> FakeAsyncClient:
        return FakeAsyncClient(requests, **kwargs)

    monkeypatch.setattr("src.integrations.trackers.UNIT3D.httpx.AsyncClient", factory)

    tracker = Aither({"TRACKERS": {"AITHER": {"api_key": "test-key"}}})
    meta = Meta(category="TV", tmdb=None, season="S01", resolution="1080p", type="WEBDL")

    asyncio.run(tracker.search_existing(meta))

    assert ("categories[]", "2") in requests[0]
