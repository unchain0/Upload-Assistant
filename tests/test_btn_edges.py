from __future__ import annotations

import asyncio
from typing import Any, ClassVar, Self
from unittest.mock import AsyncMock

import httpx
import pytest

from src.domain_models.release import Meta
from src.integrations.external_apis import btn
from src.integrations.external_apis.btn import BtnIdManager


class _Response:
    def __init__(
        self,
        status_code: int = 200,
        payload: object = None,
        text: str = "response",
    ) -> None:
        self.status_code = status_code
        self.payload = payload
        self.text = text
        self.request = httpx.Request("POST", "https://api.invalid")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "bad status",
                request=self.request,
                response=httpx.Response(
                    self.status_code, request=self.request
                ),
            )

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


class _BBCode:
    calls: ClassVar[list[str]] = []

    def clean_bhd_description(
        self, description: str, _meta: Meta
    ) -> tuple[str, list[dict[str, str]]]:
        type(self).calls.append(description)
        return f"clean:{description}", [
            {"raw_url": "https://images.invalid/raw.jpg"}
        ]


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch) -> None:
    _Client.queue = []
    _Client.requests = []
    _BBCode.calls = []
    monkeypatch.setattr(btn.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(btn, "BBCODE", _BBCode)


def _meta(**values: object) -> Meta:
    state: dict[str, object] = {
        "category": "MOVIE",
        "keep_images": False,
        "description": "",
        "image_list": [],
        "debug": False,
        "framestor": False,
        "flux": False,
    }
    state.update(values)
    return Meta(state)


def test_guid_and_tmdb_parsing_wrappers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        btn.uuid, "uuid4", lambda: "12345678-aaaa-bbbb-cccc-dddddddddddd"
    )
    assert (
        asyncio.run(BtnIdManager.generate_guid())
        == "12345678-aaaa-bbbb-cccc-dddddddddddd"
    )
    assert (
        asyncio.run(btn.generate_guid())
        == "12345678-aaaa-bbbb-cccc-dddddddddddd"
    )
    assert asyncio.run(
        BtnIdManager.parse_tmdb_id(" tv/123-slug ", "MOVIE")
    ) == ("TV", 123)
    assert asyncio.run(btn.parse_tmdb_id("movie/456-extra")) == ("MOVIE", 456)
    assert asyncio.run(BtnIdManager.parse_tmdb_id("789", None)) == (None, 789)
    assert asyncio.run(BtnIdManager.parse_tmdb_id("invalid", "TV")) == (
        "TV",
        0,
    )


def test_btn_success_empty_ids_no_torrents_json_and_request_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        BtnIdManager, "generate_guid", AsyncMock(return_value="abcdefgh123")
    )
    _Client.queue = [
        _Response(
            payload={
                "id": "x",
                "result": {
                    "torrents": {"1": {"ImdbID": "123", "TvdbID": "456"}}
                },
            }
        )
    ]
    assert asyncio.run(BtnIdManager.get_btn_torrents("api", "id")) == (
        123,
        456,
    )
    assert _Client.requests[-1][1]["json"]["id"] == "abcdefgh"  # type: ignore[index]

    _Client.queue = [
        _Response(
            payload={
                "result": {"torrents": {"1": {"ImdbID": "", "TvdbID": "789"}}}
            }
        )
    ]
    assert asyncio.run(btn.get_btn_torrents("api", "id")) == (0, 789)
    for payload in (
        {},
        {"result": {}},
        {"result": {"torrents": {}}},
        {"result": {"torrents": {"1": {}}}},
    ):
        _Client.queue = [_Response(payload=payload)]
        assert asyncio.run(BtnIdManager.get_btn_torrents("api", "id")) == (
            0,
            0,
        )

    _Client.queue = [
        _Response(payload=ValueError("bad json")),
        RuntimeError("network"),
        _Response(500, {}),
    ]
    assert asyncio.run(BtnIdManager.get_btn_torrents("api", "id")) == (0, 0)
    assert asyncio.run(BtnIdManager.get_btn_torrents("api", "id")) == (0, 0)
    assert asyncio.run(BtnIdManager.get_btn_torrents("api", "id")) == (0, 0)


def test_btn_api_errors_unauthorized_and_generic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        BtnIdManager, "generate_guid", AsyncMock(return_value="abcdefgh")
    )
    for error in (
        {"code": 401, "message": "Unauthorized IP address"},
        {"code": 500, "message": "General failure"},
        {"message": None},
    ):
        _Client.queue = [_Response(payload={"error": error})]
        assert asyncio.run(BtnIdManager.get_btn_torrents("api", "id")) == (
            0,
            0,
        )


def _bhd_result(**values: object) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": 10,
        "name": "Release",
        "description": "Description",
        "imdb_id": "tt1234567",
        "tmdb_id": "movie/456",
    }
    result.update(values)
    return result


def test_bhd_search_details_payloads_inline_description_and_wrappers() -> None:
    meta = _meta()
    _Client.queue = [
        _Response(
            payload={
                "success": True,
                "results": [_bhd_result(name="Movie-FRAMeSToR")],
            }
        )
    ]
    assert asyncio.run(
        BtnIdManager.get_bhd_torrents(
            "api",
            "rss",
            meta,
            info_hash="hash",
            filename="file",
            foldername="folder",
        )
    ) == (1234567, 456)
    payload = _Client.requests[-1][1]["json"]
    assert payload == {
        "action": "search",
        "rsskey": "rss",
        "info_hash": "hash",
        "file_name": "file",
        "folder_name": "folder",
    }
    assert (
        meta.framestor is True
        and meta.description == "clean:Description"
        and meta.image_list
    )

    meta = _meta()
    _Client.queue = [
        _Response(
            payload={
                "success": True,
                "result": _bhd_result(name="Movie-FLUX", tmdb_id="tv/789"),
            }
        )
    ]
    assert asyncio.run(
        btn.get_bhd_torrents("api", "rss", meta, torrent_id=55)
    ) == (1234567, 789)
    assert _Client.requests[-1][1]["json"] == {
        "action": "details",
        "torrent_id": 55,
    }
    assert meta.category == "TV" and meta.flux is True


def test_bhd_full_description_success_failure_exception_and_inline_none() -> (
    None
):
    meta = _meta()
    _Client.queue = [
        _Response(
            payload={"success": True, "results": [_bhd_result(description=1)]}
        ),
        _Response(
            payload={
                "status_code": 1,
                "success": True,
                "result": "Full Description",
            }
        ),
    ]
    assert asyncio.run(BtnIdManager.get_bhd_torrents("api", "rss", meta)) == (
        1234567,
        456,
    )
    assert meta.description == "clean:Full Description"

    meta = _meta()
    _Client.queue = [
        _Response(
            payload={
                "success": True,
                "results": [_bhd_result(description="1")],
            }
        ),
        _Response(
            payload={
                "status_code": 0,
                "success": False,
                "status_message": "no description",
            }
        ),
    ]
    assert asyncio.run(BtnIdManager.get_bhd_torrents("api", "rss", meta)) == (
        1234567,
        456,
    )
    assert meta.description == "clean:"

    request = httpx.Request("POST", "https://bhd.invalid")
    meta = _meta()
    _Client.queue = [
        _Response(
            payload={"success": True, "results": [_bhd_result(description=1)]}
        ),
        httpx.RequestError("offline", request=request),
    ]
    assert asyncio.run(BtnIdManager.get_bhd_torrents("api", "rss", meta)) == (
        1234567,
        456,
    )
    assert meta.description == "clean:"

    meta = _meta()
    _Client.queue = [
        _Response(
            payload={
                "success": True,
                "results": [_bhd_result(description=None)],
            }
        )
    ]
    assert asyncio.run(BtnIdManager.get_bhd_torrents("api", "rss", meta)) == (
        1234567,
        456,
    )
    assert meta.description == "clean:"


def test_bhd_skip_description_keep_images_and_no_ids_debug() -> None:
    skip = _meta(keep_images=False)
    _Client.queue = [
        _Response(payload={"success": True, "results": [_bhd_result()]})
    ]
    assert asyncio.run(
        BtnIdManager.get_bhd_torrents(
            "api", "rss", skip, skip_tracker_descriptions=True
        )
    ) == (1234567, 456)
    assert _BBCode.calls == [] and skip.description == ""

    keep = _meta(keep_images=True, description="existing")
    _Client.queue = [
        _Response(payload={"success": True, "results": [_bhd_result()]})
    ]
    assert asyncio.run(
        BtnIdManager.get_bhd_torrents(
            "api", "rss", keep, skip_tracker_descriptions=True
        )
    ) == (1234567, 456)
    assert keep.description == "" and keep.image_list

    no_ids = _meta(debug=True)
    _Client.queue = [
        _Response(
            payload={
                "success": True,
                "results": [_bhd_result(imdb_id="", tmdb_id="0")],
            }
        )
    ]
    assert asyncio.run(
        BtnIdManager.get_bhd_torrents("api", "rss", no_ids)
    ) == (0, 0)


def test_bhd_status_json_request_empty_and_response_shapes() -> None:
    meta = _meta()
    request = httpx.Request("POST", "https://bhd.invalid")
    _Client.queue = [
        _Response(payload=ValueError("bad json")),
        httpx.RequestError("offline", request=request),
        _Response(500, {}),
    ]
    assert asyncio.run(BtnIdManager.get_bhd_torrents("api", "rss", meta)) == (
        0,
        0,
    )
    assert asyncio.run(BtnIdManager.get_bhd_torrents("api", "rss", meta)) == (
        0,
        0,
    )
    assert asyncio.run(BtnIdManager.get_bhd_torrents("api", "rss", meta)) == (
        0,
        0,
    )

    for payload in (
        {"status_code": 0, "status_message": "bad"},
        {"success": False},
        {"success": True, "results": []},
        {"success": True, "result": []},
        {"success": True},
    ):
        _Client.queue = [_Response(payload=payload)]
        assert asyncio.run(
            BtnIdManager.get_bhd_torrents("api", "rss", meta)
        ) == (0, 0)
