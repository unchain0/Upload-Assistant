from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.speedapp import SpeedApp


def _config(
    *, channel: object = "", use_metadata_name: bool = False
) -> dict[str, Any]:
    return {
        "DEFAULT": {},
        "TRACKERS": {
            "SPEEDAPP": {
                "api_key": "test-key",
                "channel": channel,
                "use_metadata_name": use_metadata_name,
            }
        },
    }


def _tracker(**kwargs: object) -> SpeedApp:
    return SpeedApp(_config(**kwargs))


def _meta(**values: object) -> Meta:
    state: dict[str, object] = {
        "category": "MOVIE",
        "title": "Example",
        "artist": "Artist",
        "imdb_id": 0,
        "imdb_tt": "tt1234567",
        "language_checked": True,
        "subtitle_languages": [],
        "audio_languages": [],
        "origin_country": [],
        "genres": [],
        "keywords": [],
        "anime": False,
        "tv_pack": False,
        "sd": 0,
        "resolution": "1080p",
        "type": "WEBDL",
        "console_game": False,
        "spd_channel": "",
        "scene_name": "",
        "basename_no_ext": "Example",
        "clean_name": "Example",
        "base_dir": ".",
        "uuid": "speedapp",
        "requirements_minimum": "",
        "requirements_recommended": "",
        "menu_images": [],
        "image_list": [],
        "spectrograms_images": [],
        "dynamic_hdr_plot_images": [],
        "backdrop": "",
        "artwork_url": "",
        "overview_meta": "Overview",
        "overview": "Overview",
        "imdb_info": {},
        "debug": False,
        "tracker_status": {},
    }
    state.update(values)
    return Meta(state)


def _response(
    payload: Any,
    *,
    status: int = 200,
    method: str = "GET",
    url: str = "https://speedapp.io/api/channel",
) -> httpx.Response:
    return httpx.Response(
        status, request=httpx.Request(method, url), json=payload
    )


def test_speedapp_special_categories_and_direct_channel() -> None:
    tracker = _tracker()
    assert asyncio.run(tracker.get_cat_id(_meta(origin_country=["RO"]))) == 59
    assert (
        asyncio.run(
            tracker.get_cat_id(_meta(category="TV", origin_country=["RO"]))
        )
        == 60
    )
    assert (
        asyncio.run(
            tracker.get_cat_id(
                _meta(genres=["Documentary"], audio_languages=["Romanian"])
            )
        )
        == 63
    )
    assert SpeedApp._direct_channel_id("42") == 42


@pytest.mark.asyncio
async def test_speedapp_search_channel_exception_and_http_failure() -> None:
    tracker = _tracker(channel="tag")
    tracker.session.get = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
    assert await tracker.search_channel(_meta()) is None

    tracker.session.get = AsyncMock(return_value=_response({}, status=503))  # type: ignore[method-assign]
    assert await tracker._lookup_channel("tag") is None


@pytest.mark.asyncio
async def test_speedapp_channel_lookup_match_and_no_match() -> None:
    tracker = _tracker()
    tracker.session.get = AsyncMock(
        return_value=_response([{"id": 7, "tag": "release"}])
    )  # type: ignore[method-assign]
    assert await tracker._lookup_channel("release") == 7

    tracker.session.get = AsyncMock(
        return_value=_response([{"id": 8, "tag": "other"}])
    )  # type: ignore[method-assign]
    assert await tracker._lookup_channel("release") is None
    assert SpeedApp._matching_channel_id("bad", "release") is None


@pytest.mark.asyncio
async def test_speedapp_metadata_name_and_existing_nfo(tmp_path: Path) -> None:
    tracker = _tracker(use_metadata_name=True)
    name = await tracker.get_name(_meta(scene_name="Móvie DD+ HDR10+!"))
    assert name == "Movie DDP HDR10P"

    root = tmp_path / "tmp" / "release"
    root.mkdir(parents=True)
    (root / "release.nfo").write_bytes(b"nfo")
    encoded = await tracker.get_nfo(
        _meta(base_dir=str(tmp_path), uuid="release")
    )
    assert encoded is not None


def test_speedapp_requirements_join_and_strip_bbcode() -> None:
    meta = _meta(
        requirements_minimum="<b>Minimum</b>",
        requirements_recommended="<i>Recommended</i>",
    )
    result = _tracker().get_requirements(meta)
    assert "Minimum" in result
    assert "Recommended" in result


@pytest.mark.asyncio
async def test_speedapp_game_requirements_and_debug_nfo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        tracker, "get_requirements", lambda _meta: "Requirements"
    )
    data: dict[str, Any] = {}
    await tracker._apply_category_upload_data(
        data, _meta(category="GAME", console_game=False)
    )
    assert data["systemRequirements"] == "Requirements"

    debug_data = {"file": "a" * 80, "nfo": "b" * 80}
    tracker._redact_debug_payload(debug_data, _meta(debug=True))
    assert debug_data["file"].endswith("...[DEBUG MODE]")
    assert debug_data["nfo"].endswith("...[DEBUG MODE]")


@pytest.mark.asyncio
async def test_speedapp_upload_skips_missing_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker, "fetch_data", AsyncMock(return_value={}))
    monkeypatch.setattr(
        tracker, "search_channel", AsyncMock(return_value=None)
    )
    meta = _meta()
    assert await tracker.upload(meta) is None
    assert meta.skipping == "SPEEDAPP"


@pytest.mark.asyncio
async def test_speedapp_upload_release_exception_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    request = httpx.Request("POST", tracker.upload_url)
    response = httpx.Response(422, request=request, text="invalid")
    errors = (
        httpx.HTTPStatusError("bad", request=request, response=response),
        httpx.TimeoutException("timeout"),
        httpx.RequestError("offline", request=request),
        RuntimeError("unexpected"),
    )
    for error in errors:
        monkeypatch.setattr(
            tracker, "_post_upload", AsyncMock(side_effect=error)
        )
        status: dict[str, Any] = {}
        assert not await tracker._upload_release(_meta(), {}, status)
        assert status["status_message"].startswith("data error:")


@pytest.mark.asyncio
async def test_speedapp_upload_response_success_and_failures() -> None:
    tracker = _tracker()
    tracker.common.download_tracker_torrent = AsyncMock()  # type: ignore[method-assign]

    status: dict[str, Any] = {}
    assert not await tracker._handle_upload_response(
        _meta(),
        status,
        _response({"status": False, "error": True}, method="POST"),
    )

    status = {}
    assert not await tracker._handle_upload_response(
        _meta(),
        status,
        _response({"status": True, "error": False}, method="POST"),
    )

    status = {}
    payload = {
        "status": True,
        "error": False,
        "downloadUrl": "present",
        "torrent": {"id": 99},
    }
    assert await tracker._handle_upload_response(
        _meta(), status, _response(payload, method="POST")
    )
    assert status["torrent_id"] == "99"
    tracker.common.download_tracker_torrent.assert_awaited_once()
