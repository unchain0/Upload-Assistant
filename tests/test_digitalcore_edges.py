from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.digitalcore import DigitalCore
from tests.test_digitalcore_rules import _make_meta, _tracker


def _response(
    payload: Any, *, status: int = 200, text: str | None = None
) -> httpx.Response:
    request = httpx.Request("GET", "https://digitalcore.club/api/v1/torrents")
    if text is not None:
        return httpx.Response(status, request=request, text=text)
    return httpx.Response(status, request=request, json=payload)


def test_digitalcore_search_payload_empty_and_matching_entry() -> None:
    assert DigitalCore._search_payload(_response([], text="[]")) == []
    entry = DigitalCore._dupe_entry(
        {
            "category": 6,
            "id": 123,
            "name": "Release",
            "size": 42,
            "numfiles": 2,
        },
        6,
    )
    assert entry is not None
    assert entry["id"] == 123
    assert entry["link"] == "https://digitalcore.club/torrent/123/"


def test_digitalcore_rejects_divx_and_rar() -> None:
    tracker = _tracker()
    assert not asyncio.run(
        tracker.get_additional_checks(_make_meta(video_codec="DivX"))
    )
    assert not asyncio.run(
        tracker.get_additional_checks(_make_meta(filelist=["release.rar"]))
    )


def test_digitalcore_non_video_category_skips_video_file_rules() -> None:
    tracker = _tracker()
    assert asyncio.run(
        tracker.get_additional_checks(
            _make_meta(category="BOOK", filelist=["release.rar"])
        )
    )


def test_digitalcore_firstpic_uses_hosted_artwork() -> None:
    meta = Meta(
        category="BOOK",
        hosted_artwork=[{"raw_url": "https://example.com/cover.jpg"}],
    )
    assert (
        asyncio.run(_tracker().get_firstpic(meta))
        == "https://example.com/cover.jpg"
    )


@pytest.mark.asyncio
async def test_digitalcore_upload_release_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    response = _response({"id": 321, "message": "ok"})
    monkeypatch.setattr(
        tracker, "_submit_upload", AsyncMock(return_value=response)
    )
    tracker.common.download_tracker_torrent = AsyncMock()  # type: ignore[method-assign]
    meta = _make_meta(tracker_status={})
    status: dict[str, Any] = {}

    assert await tracker._upload_release(meta, {}, "Release", status)
    assert status["torrent_id"] == "321/"
    tracker.common.download_tracker_torrent.assert_awaited_once()


@pytest.mark.asyncio
async def test_digitalcore_upload_release_http_status_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    request = httpx.Request("POST", tracker.api_base_url)
    response = httpx.Response(422, request=request, text="invalid")
    error = httpx.HTTPStatusError(
        "invalid", request=request, response=response
    )
    monkeypatch.setattr(
        tracker, "_submit_upload", AsyncMock(side_effect=error)
    )
    status: dict[str, Any] = {}

    assert not await tracker._upload_release(
        _make_meta(), {}, "Release", status
    )
    assert "HTTP 422" in status["status_message"]


@pytest.mark.asyncio
async def test_digitalcore_upload_release_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        tracker,
        "_submit_upload",
        AsyncMock(side_effect=httpx.TimeoutException("timeout")),
    )
    status: dict[str, Any] = {}

    assert not await tracker._upload_release(
        _make_meta(), {}, "Release", status
    )
    assert "timed out" in status["status_message"]


@pytest.mark.asyncio
async def test_digitalcore_upload_release_request_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    error = httpx.RequestError(
        "offline", request=httpx.Request("POST", tracker.api_base_url)
    )
    monkeypatch.setattr(
        tracker, "_submit_upload", AsyncMock(side_effect=error)
    )
    status: dict[str, Any] = {}

    assert not await tracker._upload_release(
        _make_meta(), {}, "Release", status
    )
    assert "offline" in status["status_message"]


@pytest.mark.asyncio
async def test_digitalcore_submit_upload_posts_torrent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    tracker.common.create_torrent_for_upload = AsyncMock()  # type: ignore[method-assign]
    monkeypatch.setattr(
        tracker, "_torrent_bytes", AsyncMock(return_value=b"torrent")
    )
    response = _response({"id": 1})
    post = AsyncMock(return_value=response)
    tracker.session = SimpleNamespace(
        headers={"X-API-KEY": "key"},
        post=post,
        timeout=SimpleNamespace(write=30.0),
    )  # type: ignore[assignment]

    result = await tracker._submit_upload(
        _make_meta(), {"category": 6}, "Release"
    )

    assert result is response
    post.assert_awaited_once()
    files = post.await_args.kwargs["files"]
    assert files["file"] == (
        "Release.torrent",
        b"torrent",
        "application/x-bittorrent",
    )


def test_digitalcore_dupe_filter_rejects_webrip_for_webdl() -> None:
    meta = _make_meta(
        type="WEBDL",
        source="WEBDL",
        resolution="1080p",
        video_encode="H.264",
        video_codec="AVC",
    )
    entry = DigitalCore._dupe_entry(
        {
            "category": 6,
            "id": 201,
            "name": "Tatami.2023.Iranian.1080p.WEBRip.x265-DH",
            "size": 100,
            "numfiles": 1,
        },
        6,
        meta,
    )
    assert entry is None


def test_digitalcore_dupe_filter_rejects_codec_mismatch() -> None:
    meta = _make_meta(
        type="WEBDL",
        source="WEBDL",
        resolution="1080p",
        video_encode="H.264",
        video_codec="AVC",
    )
    entry = DigitalCore._dupe_entry(
        {
            "category": 6,
            "id": 202,
            "name": "Tatami.2024.1080p.AMZN.WEB-DL.x265-OTHER",
            "size": 100,
            "numfiles": 1,
        },
        6,
        meta,
    )
    assert entry is None


def test_digitalcore_dupe_filter_requires_video_semantics_missing() -> None:
    meta = _make_meta(
        type="WEBDL",
        source="WEBDL",
        resolution="1080p",
        video_encode="H.264",
    )
    entry = DigitalCore._dupe_entry(
        {
            "category": 6,
            "id": 203,
            "name": "Tatami 2024 FLY",
            "size": 100,
            "numfiles": "",
        },
        6,
        meta,
    )
    assert entry is None


def test_digitalcore_dupe_filter_keeps_compatible_webdl() -> None:
    meta = _make_meta(
        type="WEBDL",
        source="WEBDL",
        resolution="1080p",
        video_encode="H.264",
        video_codec="AVC",
    )
    entry = DigitalCore._dupe_entry(
        {
            "category": 6,
            "id": 204,
            "name": "Tatami.2024.1080p.AMZN.WEB-DL.H.264-FLY",
            "size": 100,
            "numfiles": 1,
        },
        6,
        meta,
    )
    assert entry is not None
    assert entry["type"] == "WEBDL"
    assert entry["res"] == "1080p"


def test_digitalcore_normalizes_generic_web_source() -> None:
    assert DigitalCore._normalized_source_type("WEB") == "WEBDL"
