from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.mteam import MTeam


def _config() -> dict[str, Any]:
    return {"TRACKERS": {"MTEAM": {"api_key": "key", "base_url": "kp.m-team.cc"}}, "DEFAULT": {"tmdb_api": "0123456789abcdef0123456789abcdef"}}


def _tracker() -> MTeam:
    return MTeam(_config())


def _meta(**values: object) -> Meta:
    state: dict[str, object] = {
        "title": "Example",
        "category": "MOVIE",
        "sd": 0,
        "is_disc": "",
        "type": "WEBDL",
        "anime": False,
        "screens": 3,
        "imdb_tt": "tt1234567",
        "uuid": "release",
        "unattended": False,
        "unattended_confirm": False,
        "keywords": [],
        "combined_genres": "Drama",
        "resolution": "1080p",
        "video_codec": "H264",
        "audio": "AAC 2.0",
        "tracker_status": {},
        "base_dir": ".",
        "name": "Example 1080p WEB-DL H264 AAC 2.0",
        "imdb_info": {"imdbID": "tt1234567"},
        "douban_id": 0,
        "anon": False,
    }
    state.update(values)
    return Meta(state)


def _response(payload: Any, *, status: int = 200, url: str = "https://api.m-team.cc/api/test") -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("POST", url), json=payload)


@pytest.mark.asyncio
async def test_mteam_requests_success_and_error() -> None:
    tracker = _tracker()
    payload = {"data": {"data": [{"category": 419, "title": "Example", "rewardCurrent": "100", "id": 5}]}}
    tracker.session.post = AsyncMock(return_value=_response(payload))  # type: ignore[method-assign]
    result = await tracker.get_requests(_meta())
    assert result == [{"Name": "Example", "Reward": "100", "Link": "https://kp.m-team.cc/seekDetail?id=5"}]

    tracker.session.post = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
    assert await tracker.get_requests(_meta()) == []


def test_mteam_nested_data_guards() -> None:
    assert MTeam._nested_data_list([]) == []
    assert MTeam._nested_data_list({"data": []}) == []
    assert MTeam._nested_data_list({"data": {"data": "bad"}}) == []
    assert MTeam._request_entry({"category": 1}, 419) is None


@pytest.mark.asyncio
async def test_mteam_douban_error_and_fallback_description(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    tracker.session.post = AsyncMock(side_effect=RuntimeError("offline"))  # type: ignore[method-assign]
    assert await tracker.get_douban_info(_meta(douban_id=1)) == {}
    monkeypatch.setattr(tracker, "get_douban_info", AsyncMock(return_value={}))
    text = await tracker.mteam_standard_desc(_meta(year=2025, overview="Plot", tmdb_poster_path="/poster.jpg"))
    assert "**Title**: Example" in text
    assert "image.tmdb.org" in text


@pytest.mark.asyncio
async def test_mteam_additional_check_policy_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    assert not await tracker.get_additional_checks(_meta(imdb_tt=""))
    assert not tracker._screenshot_policy_passes(_meta(screens="bad"))

    monkeypatch.setattr(tracker.common, "prompt_user_for_confirmation", AsyncMock(return_value=False))
    upscale = _meta(uuid="Example.Upscale", title="Example")
    assert not await tracker._upscale_policy_passes(upscale)

    lgbt = _meta(keywords=["lgbt"], combined_genres="")
    assert not await tracker._lgbt_policy_passes(lgbt)
    unattended = _meta(unattended=True, unattended_confirm=False)
    assert not await tracker._confirm_policy(unattended)


@pytest.mark.asyncio
async def test_mteam_search_no_imdb_api_error_and_valid_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    assert await tracker.search_existing(_meta(imdb_tt="")) == []
    with pytest.raises(RuntimeError, match="MTEAM API Error"):
        tracker._raise_api_error({"code": "1", "message": "bad"})

    payload = {"code": "0", "data": {"data": [{"id": 7, "name": "Release", "size": "42", "file_count": 2}]}}
    tracker.session.post = AsyncMock(return_value=_response(payload))  # type: ignore[method-assign]
    result = await tracker.search_existing(_meta())
    assert result[0]["id"] == 7

    monkeypatch.setattr(tracker, "get_dupe_bdinfo", AsyncMock(return_value="BDINFO"))
    entries = await tracker._search_entries(_meta(is_disc="BDMV"), payload)
    assert entries[0]["bd_info"] == "BDINFO"


@pytest.mark.asyncio
async def test_mteam_dupe_bdinfo_error() -> None:
    tracker = _tracker()
    tracker.session.post = AsyncMock(side_effect=RuntimeError("offline"))  # type: ignore[method-assign]
    assert await tracker.get_dupe_bdinfo(1) == ""


def test_mteam_unknown_audio_codec_and_hdr_name() -> None:
    tracker = _tracker()
    assert tracker.get_audiocodec(_meta(audio="Unknown")) == 8
    assert asyncio.run(tracker.get_name(_meta(name="Movie hdr10 60fps 60fps.mkv"))) == "Movie HDR10 HFR"


@pytest.mark.asyncio
async def test_mteam_upload_release_success(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    create_response = _response({"message": "SUCCESS", "data": {"id": 9}})
    monkeypatch.setattr(tracker, "_submit_upload", AsyncMock(return_value=create_response))
    tracker.session.post = AsyncMock(return_value=_response({"data": "https://download.invalid/9"}))  # type: ignore[method-assign]
    tracker.common.download_tracker_torrent = AsyncMock()  # type: ignore[method-assign]
    status: dict[str, Any] = {}
    assert await tracker._upload_release(_meta(), {}, status)
    assert status["torrent_id"] == "9"


@pytest.mark.asyncio
async def test_mteam_upload_error_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    request = httpx.Request("POST", tracker.api_base_url)
    response = httpx.Response(422, request=request, text="invalid")
    cases = (
        httpx.HTTPStatusError("bad", request=request, response=response),
        httpx.TimeoutException("timeout"),
        httpx.RequestError("offline", request=request),
        RuntimeError("unexpected"),
    )
    for error in cases:
        monkeypatch.setattr(tracker, "_submit_upload", AsyncMock(side_effect=error))
        status: dict[str, Any] = {}
        assert not await tracker._upload_release(_meta(), {}, status)
        assert status["status_message"].startswith("data error:")


@pytest.mark.asyncio
async def test_mteam_submit_upload_posts_torrent(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    tracker.common.create_torrent_for_upload = AsyncMock()  # type: ignore[method-assign]
    monkeypatch.setattr(tracker, "_torrent_bytes", AsyncMock(return_value=b"torrent"))
    response = _response({"message": "SUCCESS", "data": {"id": 1}})
    post = AsyncMock(return_value=response)
    tracker.session = SimpleNamespace(headers={"x-api-key": "key"}, post=post, timeout=SimpleNamespace(write=30.0))  # type: ignore[assignment]
    assert await tracker._submit_upload(_meta(), {}) is response
    assert post.await_args.kwargs["files"]["file"][1] == b"torrent"


@pytest.mark.asyncio
async def test_mteam_handle_upload_missing_id_and_download_url() -> None:
    tracker = _tracker()
    status: dict[str, Any] = {}
    assert not await tracker._handle_upload_response(_meta(), status, _response({"message": "SUCCESS", "data": {}}))
    assert tracker._upload_torrent_id({"data": "bad"}) is None

    tracker.session.post = AsyncMock(return_value=_response({"data": None}))  # type: ignore[method-assign]
    status = {}
    assert not await tracker._download_uploaded_torrent(_meta(), status, 10)
    assert "Failed to get download URL" in status["status_message"]


@pytest.mark.asyncio
async def test_mteam_douban_success_description(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    payload = {
        "code": "0",
        "data": {
            "title": "Example",
            "aka": ["Alias"],
            "countries": ["China"],
            "genres": ["Drama"],
            "languages": ["Chinese"],
            "pubdate": ["2025-01-01"],
            "durations": ["120 min"],
            "directors": [{"name": "Director"}],
            "actors": [{"name": "Actor"}],
            "score": "8.0",
            "rating": {"count": "100"},
            "subjectId": "123",
            "coverUrl": "https://example.com/cover.jpg",
            "year": 2025,
            "intro": "Plot",
        },
    }
    monkeypatch.setattr(tracker, "get_douban_info", AsyncMock(return_value=payload))
    assert "◎片　　名** Example" in await tracker.mteam_standard_desc(_meta())


@pytest.mark.asyncio
async def test_mteam_additional_checks_propagate_upscale_and_screenshot_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker, "_upscale_policy_passes", AsyncMock(return_value=False))
    assert not await tracker.get_additional_checks(_meta())

    tracker._upscale_policy_passes = AsyncMock(return_value=True)  # type: ignore[method-assign]
    monkeypatch.setattr(tracker, "_screenshot_policy_passes", lambda _meta: False)
    assert not await tracker.get_additional_checks(_meta())


@pytest.mark.asyncio
async def test_mteam_dupe_bdinfo_falls_back_to_description() -> None:
    tracker = _tracker()
    tracker.session.post = AsyncMock(return_value=_response({"data": {"mediainfo": "", "descr": "BDINFO fallback"}}))  # type: ignore[method-assign]
    assert await tracker.get_dupe_bdinfo(9) == "BDINFO fallback"


def test_mteam_known_audio_codec() -> None:
    assert _tracker().get_audiocodec(_meta(audio="AAC 2.0")) == 6
