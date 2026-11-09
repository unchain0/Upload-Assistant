from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.USENET import drunkenslug as slug_module
from src.integrations.trackers.USENET.drunkenslug import DrunkenSlug


def _config(*, limit: int = 5) -> dict[str, Any]:
    return {
        "DEFAULT": {},
        "TRACKERS": {
            "DRUNKENSLUG": {"api_key": "key", "daily_api_hit_limit": limit}
        },
    }


def _tracker(*, limit: int = 5) -> DrunkenSlug:
    return DrunkenSlug(_config(limit=limit))


def _meta(tmp_path: Path | None = None, **values: object) -> Meta:
    root = str(tmp_path or Path())
    state: dict[str, object] = {
        "base_dir": root,
        "uuid": "release",
        "category": "MOVIE",
        "title": "Example",
        "scene_name": "",
        "basename_no_ext": "Example.2025.1080p",
        "tvdb_id": 0,
        "tmdb_id": 0,
        "imdb_id": 0,
        "season_int": 0,
        "episode_int": 0,
        "nzb_path": "",
        "debug": False,
        "tracker_status": {},
    }
    state.update(values)
    return Meta(state)


def _response(
    payload: Any | None = None, *, status: int = 200, text: str | None = None
) -> httpx.Response:
    request = httpx.Request("POST", "https://nzbs.drunkenslug.com/upload.php")
    if text is not None:
        return httpx.Response(status, request=request, text=text)
    return httpx.Response(status, request=request, json=payload)


@pytest.mark.asyncio
async def test_drunkenslug_local_cache_and_disabled_api(
    tmp_path: Path,
) -> None:
    meta = _meta(tmp_path)
    root = tmp_path / "tmp" / "release"
    root.mkdir(parents=True)
    (root / "DRUNKENSLUG_upload_ok").write_text("ok", encoding="utf-8")
    assert await _tracker().search_existing(meta) == ["Example.2025.1080p"]

    disabled = _tracker(limit=0)
    assert await disabled.search_existing(_meta(tmp_path, uuid="other")) == []


@pytest.mark.asyncio
async def test_drunkenslug_api_limit_empty_response_and_dedupe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        slug_module,
        "reserve_daily_api_hit",
        AsyncMock(return_value=(False, 5)),
    )
    assert await tracker._api_search(_meta(tmp_path), {"t": "movie"}) == []

    monkeypatch.setattr(
        slug_module, "reserve_daily_api_hit", AsyncMock(return_value=(True, 1))
    )
    monkeypatch.setattr(
        tracker, "_search_response", AsyncMock(return_value=_response(text=""))
    )
    assert await tracker._api_search(_meta(tmp_path), {"t": "movie"}) == []

    dupes = [
        {"name": "One", "link": "same"},
        {"name": "Duplicate", "link": "same"},
        {"name": "Two", "link": "other"},
    ]
    assert tracker._dedupe_results(dupes) == [dupes[0], dupes[2]]


@pytest.mark.asyncio
async def test_drunkenslug_upload_missing_and_debug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        tracker, "_nzb_is_uploadable", AsyncMock(return_value=False)
    )
    meta = _meta(tmp_path)
    assert not await tracker.upload(meta)
    assert (
        "NZB file missing"
        in meta.tracker_status["DRUNKENSLUG"]["status_message"]
    )

    nzb = tmp_path / "release.nzb"
    nzb.write_bytes(b"nzb")
    monkeypatch.setattr(
        tracker, "_nzb_is_uploadable", AsyncMock(return_value=True)
    )
    debug_meta = _meta(
        tmp_path, nzb_path=str(nzb), debug=True, tracker_status={}
    )
    assert await tracker.upload(debug_meta)
    assert debug_meta.tracker_status["DRUNKENSLUG"][
        "status_message"
    ].startswith("Debug mode")


@pytest.mark.asyncio
async def test_drunkenslug_upload_nzb_transport_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tracker = _tracker()
    status: dict[str, Any] = {}
    cases = (
        httpx.TimeoutException("timeout"),
        httpx.RequestError(
            "offline", request=httpx.Request("POST", tracker.search_url)
        ),
        RuntimeError("unexpected"),
    )
    for error in cases:
        monkeypatch.setattr(tracker, "_post_nzb", AsyncMock(side_effect=error))
        status.clear()
        assert not await tracker._upload_nzb(
            _meta(tmp_path), status, "release.nzb", b"nzb"
        )
        assert status["status_message"].startswith("data error:")


@pytest.mark.asyncio
async def test_drunkenslug_upload_response_error_and_success(
    tmp_path: Path,
) -> None:
    tracker = _tracker()
    meta = _meta(tmp_path)
    status: dict[str, Any] = {}
    assert not await tracker._handle_upload_response(
        meta, status, "release.nzb", _response({"results": []}, status=500)
    )
    assert "HTTP 500" in status["status_message"]

    status = {}
    assert not await tracker._handle_upload_response(
        meta, status, "release.nzb", _response(text="not-json")
    )
    assert "decode JSON" in status["status_message"]

    status = {}
    assert not await tracker._handle_upload_response(
        meta, status, "release.nzb", _response({"results": []})
    )
    assert "No results" in status["status_message"]

    status = {}
    response = _response({"results": ["release.nzb: uploaded by secret-user"]})
    assert await tracker._handle_upload_response(
        meta, status, "release.nzb", response
    )
    assert "[redacted]" in status["status_message"]
    assert status["torrent_id"] == "release"
    assert (tmp_path / "tmp" / "release" / "DRUNKENSLUG_upload_ok").is_file()


@pytest.mark.asyncio
async def test_drunkenslug_read_nzb(tmp_path: Path) -> None:
    path = tmp_path / "release.nzb"
    path.write_bytes(b"payload")
    assert await DrunkenSlug._read_nzb(path) == b"payload"


@pytest.mark.asyncio
async def test_drunkenslug_search_existing_calls_api(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        tracker, "_api_search", AsyncMock(return_value=[{"name": "dupe"}])
    )
    assert await tracker.search_existing(_meta(tmp_path)) == [{"name": "dupe"}]


@pytest.mark.asyncio
async def test_drunkenslug_api_search_parses_results(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        slug_module, "reserve_daily_api_hit", AsyncMock(return_value=(True, 1))
    )
    monkeypatch.setattr(
        tracker,
        "_search_response",
        AsyncMock(return_value=_response(text="payload")),
    )
    monkeypatch.setattr(
        tracker,
        "_parse_dupes_from_response",
        lambda _text: [
            {"name": "One", "link": "1"},
            {"name": "One", "link": "1"},
        ],
    )
    assert await tracker._api_search(_meta(tmp_path), {"t": "movie"}) == [
        {"name": "One", "link": "1"}
    ]


@pytest.mark.asyncio
async def test_drunkenslug_upload_calls_transport(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tracker = _tracker()
    nzb = tmp_path / "release.nzb"
    nzb.write_bytes(b"nzb")
    monkeypatch.setattr(
        tracker, "_nzb_is_uploadable", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(tracker, "_upload_nzb", AsyncMock(return_value=True))
    meta = _meta(tmp_path, nzb_path=str(nzb), debug=False)
    assert await tracker.upload(meta)
    tracker._upload_nzb.assert_awaited_once()
