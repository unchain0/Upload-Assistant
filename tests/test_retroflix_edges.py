from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from src.domain_models.release import Meta
from src.integrations.trackers import retroflix as retroflix_module
from src.integrations.trackers.retroflix import RetroFlix


def _config(**tracker_values: object) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "api_key": "api-key",
        "anon": False,
        "announce_url": "https://peer.retroflix/announce",
        "username": "user",
        "password": "pass",
    }
    defaults.update(tracker_values)
    return {"TRACKERS": {"RETROFLIX": defaults}, "DEFAULT": {}}


def _tracker(**tracker_values: object) -> RetroFlix:
    return RetroFlix(_config(**tracker_values))


def _meta(tmp_path: Path | None = None, **values: object) -> Meta:
    base = str(tmp_path or Path())
    state: dict[str, object] = {
        "base_dir": base,
        "uuid": "release",
        "name": "Example Movie 2010 1080p",
        "title": "Example Movie",
        "category": "MOVIE",
        "year": 2010,
        "release_date": "2010-01-01",
        "bdinfo": {},
        "image_list": [],
        "imdb_info": {"imdb_url": "https://imdb.invalid/title/tt1"},
        "artwork_url": "https://image.invalid/poster.jpg",
        "debug": False,
        "tracker_status": {"RETROFLIX": {}},
        "unattended": False,
        "tvdb_episode_year": "",
        "tvdb_episode_data": {},
    }
    state.update(values)
    return Meta(state)


def _response(payload: Any = None, *, status: int = 200, text: str | None = None, method: str = "POST", url: str = "https://retroflix.club/api/upload") -> httpx.Response:
    request = httpx.Request(method, url)
    if text is not None:
        return httpx.Response(status, request=request, text=text)
    return httpx.Response(status, request=request, json=payload)


@pytest.mark.asyncio
async def test_retroflix_upload_payload_media_and_disc(tmp_path: Path) -> None:
    tracker = _tracker(anon=True)
    root = tmp_path / "tmp" / "release"
    root.mkdir(parents=True)
    (root / "MEDIAINFO.txt").write_text("Width : 1 080 pixels", encoding="utf-8")
    (root / "[RETROFLIX].torrent").write_bytes(b"torrent")
    item = _meta(tmp_path, image_list=[{"raw_url": "https://img.invalid/1.png"}, {"raw_url": None}, "bad"])
    payload = await tracker._upload_payload(item)
    assert "1,080" in payload["mediaInfo"]
    assert payload["screenshots"] == ["https://img.invalid/1.png"]
    assert payload["isAnonymous"] is True
    assert payload["file"]
    assert payload["url"].endswith("/")

    (root / "BD_SUMMARY_00.txt").write_text("BDINFO", encoding="utf-8")
    disc = _meta(tmp_path, bdinfo={"x": 1}, category="TV", imdb_info="bad")
    payload = await tracker._upload_payload(disc)
    assert payload["mediaInfo"] == "BDINFO"
    assert payload["type"] == "402"
    assert payload["url"] == ""


def test_retroflix_tracker_config_guard() -> None:
    tracker = RetroFlix({"TRACKERS": "bad"})
    assert tracker._tracker_config() == {}


@pytest.mark.asyncio
async def test_retroflix_debug_upload_truncates_file() -> None:
    tracker = _tracker()
    tracker.common.create_torrent_for_upload = AsyncMock()  # type: ignore[method-assign]
    item = _meta()
    assert await tracker._debug_upload(item, {"file": "abcdefghijklmnopqrstuvwxyz", "name": "Release"})
    assert item.tracker_status["RETROFLIX"]["status_message"].startswith("Debug mode")
    tracker.common.create_torrent_for_upload.assert_awaited_once()


@pytest.mark.asyncio
async def test_retroflix_upload_release_transport_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    item = _meta()
    cases = (
        httpx.TimeoutException("timeout"),
        httpx.RequestError("offline", request=httpx.Request("POST", tracker.upload_url)),
        RuntimeError("unexpected"),
    )
    for error in cases:
        monkeypatch.setattr(tracker, "_post_upload", AsyncMock(side_effect=error))
        item.tracker_status["RETROFLIX"] = {}
        assert not await tracker._upload_release(item, {})
        assert item.tracker_status["RETROFLIX"]["status_message"]


@pytest.mark.asyncio
async def test_retroflix_created_upload_error_missing_and_success() -> None:
    tracker = _tracker()
    item = _meta()

    error = _response({"error": True, "message": "rejected"}, status=201)
    assert not await tracker._handle_upload_response(item, error)
    assert item.tracker_status["RETROFLIX"]["status_message"] == "Upload error: rejected"

    missing = _response({"error": False, "torrent": {}}, status=201)
    assert not await tracker._handle_created_upload(item, missing)
    assert "missing key torrent.id" in item.tracker_status["RETROFLIX"]["status_message"]

    tracker.common.create_torrent_ready_to_seed = AsyncMock()  # type: ignore[method-assign]
    success = _response({"error": False, "torrent": {"id": 42}, "message": "ok"}, status=201)
    assert await tracker._handle_created_upload(item, success)
    assert item.tracker_status["RETROFLIX"]["torrent_id"] == 42
    tracker.common.create_torrent_ready_to_seed.assert_awaited_once()


@pytest.mark.parametrize(
    ("status", "prefix"),
    [
        (400, "Bad request"),
        (403, "Permission denied"),
        (409, "Duplicate"),
        (413, "File size error"),
        (422, "Upload rejected"),
    ],
)
def test_retroflix_error_status_messages(status: int, prefix: str) -> None:
    tracker = _tracker()
    response = _response({"message": "custom"}, status=status)
    assert tracker._error_status_message(response) == f"{prefix}: custom"


def test_retroflix_unexpected_response_and_non_json_fallback() -> None:
    tracker = _tracker()
    response = _response(text="plain error", status=500)
    message = tracker._error_status_message(response)
    assert message.startswith("Unexpected response: HTTP 500")
    item = _meta()
    assert not asyncio.run(tracker._handle_upload_response(item, response))


def test_retroflix_json_and_torrent_id_guards() -> None:
    assert RetroFlix._json_object(_response([], status=201)) == {}
    assert RetroFlix._torrent_id({"torrent": "bad"}) is None


@pytest.mark.asyncio
async def test_retroflix_additional_checks_adult_and_age_paths() -> None:
    tracker = _tracker()
    tracker.common.check_and_confirm_adult_media_upload = AsyncMock(return_value=False)  # type: ignore[method-assign]
    assert not await tracker.get_additional_checks(_meta())

    tracker.common.check_and_confirm_adult_media_upload = AsyncMock(return_value=True)  # type: ignore[method-assign]
    recent_year = __import__("datetime").datetime.now(__import__("datetime").UTC).year
    assert not await tracker.get_additional_checks(_meta(year=recent_year, release_date=""))
    assert await tracker.get_additional_checks(_meta(category="OTHER", year=2000, release_date=""))


def test_retroflix_latest_release_age_episode_paths() -> None:
    tracker = _tracker()
    item = _meta(
        category="TV",
        year=2000,
        imdb_info={"end_year": "2011"},
        tvdb_episode_year="2012",
        tvdb_episode_data={
            "episodes": [
                {"aired": "2013-01-01"},
                {"aired": "2014-bad"},
                {"aired": ""},
                "bad",
            ]
        },
    )
    year, latest = tracker._latest_release_age(item)
    assert year == 2014
    assert latest is not None and latest.year == 2013

    years: list[int] = []
    tracker._append_numeric_year(years, "bad")
    assert years == []
    assert tracker._latest_episode_date(_meta(tvdb_episode_data={}), years) is None


def test_retroflix_movie_and_date_policy_paths() -> None:
    tracker = _tracker()
    old = _meta(release_date="2000-01-01")
    assert tracker._movie_age_policy(old, 2000)

    recent_year = __import__("datetime").datetime.now(__import__("datetime").UTC).year
    malformed_recent = _meta(release_date=f"{recent_year}-bad")
    assert not tracker._movie_age_policy(malformed_recent, None)

    future = __import__("datetime").datetime.now(__import__("datetime").UTC).date()
    assert not tracker._date_policy(_meta(), future)


def test_retroflix_search_params_and_download_urls() -> None:
    assert RetroFlix._search_params(_meta(imdb_id=123))["imdbId"] == "tt123"
    assert RetroFlix._search_params(_meta(imdb_id="tt999"))["imdbId"] == "tt999"
    assert RetroFlix._search_params(_meta(imdb_id=0, title="A: B, C's"))["search"] == "A B Cs"

    entry = {"id": 5, "name": "Release", "size": 10, "url": "https://retroflix.club/browse/t/5"}
    assert RetroFlix._download_url(entry).endswith("/api/torrent/5/download")
    no_id = {"name": "Release", "url": "https://retroflix.club/browse/t/7"}
    assert RetroFlix._download_url(no_id).endswith("/api/torrent/7/download")
    fallback = {"name": "Release", "url": "https://retroflix.club/custom"}
    assert RetroFlix._download_url(fallback) == "https://retroflix.club/custom"


@pytest.mark.asyncio
async def test_retroflix_search_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, *_args: object, **_kwargs: object) -> httpx.Response:
            return _response([{"id": 3, "name": "Release", "size": 100, "url": "https://retroflix.club/browse/t/3"}], method="GET", url=RetroFlix.search_url)

    monkeypatch.setattr(retroflix_module.httpx, "AsyncClient", lambda *_args, **_kwargs: Client())
    results = await _tracker().search_existing(_meta())
    assert results[0]["name"] == "Release"
    assert results[0]["download"].endswith("/3/download")


@pytest.mark.asyncio
async def test_retroflix_api_test_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker, "generate_new_api", AsyncMock(return_value=True))

    class Client:
        def __init__(self, response: httpx.Response | Exception) -> None:
            self.response = response

        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, *_args: object, **_kwargs: object) -> httpx.Response:
            if isinstance(self.response, Exception):
                raise self.response
            return self.response

    monkeypatch.setattr(retroflix_module.httpx, "AsyncClient", lambda *_args, **_kwargs: Client(_response({}, status=200, method="GET", url="https://retroflix.club/api/test")))
    assert await tracker.api_test(_meta()) is True

    monkeypatch.setattr(retroflix_module.httpx, "AsyncClient", lambda *_args, **_kwargs: Client(_response({}, status=401, method="GET", url="https://retroflix.club/api/test")))
    assert await tracker.api_test(_meta()) is None

    request_error = httpx.RequestError("offline", request=httpx.Request("GET", "https://retroflix.club/api/test"))
    monkeypatch.setattr(retroflix_module.httpx, "AsyncClient", lambda *_args, **_kwargs: Client(request_error))
    assert await tracker.api_test(_meta()) is None

    monkeypatch.setattr(retroflix_module.httpx, "AsyncClient", lambda *_args, **_kwargs: Client(RuntimeError("unexpected")))
    assert await tracker.api_test(_meta()) is None


@pytest.mark.asyncio
async def test_retroflix_generate_new_api_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker, "_login_response", AsyncMock(return_value=_response({}, status=401)))
    assert await tracker.generate_new_api(_meta(tmp_path)) is None

    monkeypatch.setattr(tracker, "_login_response", AsyncMock(return_value=_response({}, status=201)))
    assert await tracker.generate_new_api(_meta(tmp_path)) is None

    monkeypatch.setattr(tracker, "_login_response", AsyncMock(return_value=_response({"token": "new-token"}, status=201)))
    monkeypatch.setattr(tracker, "_save_new_api", AsyncMock(return_value=True))
    assert await tracker.generate_new_api(_meta(tmp_path)) is True

    error = httpx.RequestError("offline", request=httpx.Request("POST", "https://retroflix.club/api/login"))
    monkeypatch.setattr(tracker, "_login_response", AsyncMock(side_effect=error))
    assert await tracker.generate_new_api(_meta(tmp_path)) is None

    monkeypatch.setattr(tracker, "_login_response", AsyncMock(side_effect=RuntimeError("unexpected")))
    assert await tracker.generate_new_api(_meta(tmp_path)) is None


@pytest.mark.asyncio
async def test_retroflix_save_new_api_success_missing_pattern_and_ioerror(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    config_dir = tmp_path / "data"
    config_dir.mkdir()
    path = config_dir / "config.py"
    path.write_text("TRACKERS = {'RETROFLIX': {'api_key': 'old'}}", encoding="utf-8")
    assert await tracker._save_new_api(_meta(tmp_path), "new") is True
    assert "'new'" in path.read_text(encoding="utf-8")
    assert tracker._tracker_config()["api_key"] == "new"

    path.write_text("TRACKERS = {}", encoding="utf-8")
    assert await tracker._save_new_api(_meta(tmp_path), "newer") is None

    monkeypatch.setattr(tracker, "_read_config", AsyncMock(side_effect=OSError("broken")))
    assert await tracker._save_new_api(_meta(tmp_path), "token") is None


def test_retroflix_updated_config_and_config_path() -> None:
    text = 'TRACKERS = {"RETROFLIX": {"api_key": "old"}}'
    updated = RetroFlix._updated_config(text, "new")
    assert updated is not None and '"new"' in updated
    assert RetroFlix._updated_config("TRACKERS = {}", "new") is None
    assert RetroFlix._config_path(_meta(base_dir=None)) == "./data/config.py"
