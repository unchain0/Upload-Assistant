from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from PIL import Image

from src.domain_models.release import Meta
from src.integrations.trackers.USENET import suio as suio_module
from src.integrations.trackers.USENET.suio import Suio


def _config(**tracker_values: object) -> dict[str, Any]:
    values: dict[str, Any] = {
        "api_key": "api-key",
        "username": "user",
        "daily_api_hit_limit": 10,
        "resolve_language": True,
    }
    values.update(tracker_values)
    return {"TRACKERS": {"SUIO": values}, "DEFAULT": {}}


def _tracker(**tracker_values: object) -> Suio:
    tracker = Suio(_config(**tracker_values))
    tracker.upload_url = "https://indexer.invalid/api-upload"
    tracker.torrent_url = "https://indexer.invalid/details.php?id="
    tracker.search_url = "https://api.indexer.invalid/api"
    return tracker


def _meta(tmp_path: Path | None = None, **values: object) -> Meta:
    root = tmp_path or Path()
    state: dict[str, object] = {
        "base_dir": str(root),
        "uuid": "release",
        "path": str(root / "release.mkv"),
        "filename": "release.mkv",
        "basename_no_ext": "Release.Name",
        "scene_name": "",
        "filelist": [str(root / "release.mkv")],
        "category": "MOVIE",
        "title": "Example Movie",
        "name": "Example.Movie.2024.1080p",
        "resolution": "1080p",
        "source": "WEB",
        "is_disc": "",
        "platform": "PC",
        "format": "FLAC",
        "audiobook": False,
        "audio_languages": ["English"],
        "book_language_iso": "",
        "original_language": "en",
        "tvdb_id": 0,
        "season_int": 1,
        "episode_int": 1,
        "imdb_tt": "tt1234567",
        "scene": False,
        "nzb_path": "",
        "ua_name": "Upload Assistant",
        "current_version": "1.0",
        "debug": False,
        "tracker_status": {"SUIO": {}},
    }
    state.update(values)
    return Meta(state)


def _response(
    *,
    status: int = 200,
    text: str = "",
    url: str = "https://indexer.invalid/success",
) -> httpx.Response:
    return httpx.Response(
        status, request=httpx.Request("POST", url), text=text
    )


def test_suio_configured_urls_empty_invalid_and_valid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert Suio._configured_urls({}) == (None, None, None)
    monkeypatch.setattr(
        Suio, "_allowed_domain", staticmethod(lambda _hostname: False)
    )
    assert Suio._configured_urls({"base_url": "https://wrong.invalid"}) == (
        None,
        None,
        None,
    )

    monkeypatch.setattr(
        Suio, "_allowed_domain", staticmethod(lambda _hostname: True)
    )
    upload, torrent, search = Suio._configured_urls(
        {"base_url": "indexer.invalid"}
    )
    assert upload == "indexer.invalid/api-upload"
    assert torrent == "indexer.invalid/details.php?id="
    assert search == "https://api.indexer.invalid/api"


def test_suio_tracker_config_guard() -> None:
    assert Suio._tracker_config({"TRACKERS": "bad"}) == {}


@pytest.mark.asyncio
async def test_suio_cached_search_and_disabled(tmp_path: Path) -> None:
    tracker = _tracker()
    item = _meta(tmp_path, basename_no_ext="Cached.Release")
    cache = tmp_path / "tmp" / "release" / "SUIO_upload_ok"
    cache.parent.mkdir(parents=True)
    cache.write_text("ok", encoding="utf-8")
    assert await tracker.search_existing(item) == ["Cached.Release"]

    tracker.search_url = None
    cache.unlink()
    assert await tracker.search_existing(item) == []

    tracker.search_url = "https://api.indexer.invalid/api"
    tracker.daily_api_hit_limit = 0
    assert not tracker._search_enabled()


@pytest.mark.asyncio
async def test_suio_search_queries_and_execute_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    queries = await tracker._search_queries(
        _meta(category="TV", tvdb_id=99, season_int=2, episode_int=3)
    )
    assert queries[0]["q"] == "Release.Name"
    assert queries[1] == {
        "cat": queries[1]["cat"],
        "t": "tvsearch",
        "tvdbid": "99",
        "season": "2",
        "ep": "3",
    }

    monkeypatch.setattr(
        suio_module,
        "reserve_daily_api_hit",
        AsyncMock(return_value=(False, 10)),
    )
    assert not await tracker._execute_search_query(
        _meta(), AsyncMock(), {}, [], set()
    )


def test_suio_append_search_dupes_deduplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        tracker,
        "_parse_dupes_from_response",
        lambda _text: [
            {"name": "one", "link": "https://x/1"},
            {"name": "duplicate", "link": "https://x/1"},
            {"name": "two"},
        ],
    )
    dupes: list[dict[str, Any]] = []
    seen: set[str] = set()
    tracker._append_search_dupes("xml", dupes, seen)
    assert [item["name"] for item in dupes] == ["one", "two"]
    tracker._append_search_dupes("", dupes, seen)


@pytest.mark.asyncio
async def test_suio_additional_checks_missing_username() -> None:
    tracker = _tracker(username="")
    assert not await tracker.get_additional_checks(_meta())


def test_suio_movie_category_disc_dvd_and_resolution_fallbacks() -> None:
    tracker = _tracker()
    assert (
        tracker.get_category_id(_meta(resolution="Other", is_disc="BDMV"))
        == "35"
    )
    assert (
        tracker.get_category_id(_meta(resolution="Other", source="PAL DVD"))
        == "17"
    )
    assert (
        tracker.get_category_id(_meta(resolution="Other", source="WEB"))
        == "movie"
    )
    assert tracker._resolution_class("480p") == "sd"
    assert tracker._resolution_class("Other") == "other"


def test_suio_language_mapping_empty_unknown_and_dual() -> None:
    tracker = _tracker()
    assert tracker._map_single_language_to_id("") == "0"
    assert tracker._map_single_language_to_id("Klingon") == "10"
    assert tracker._is_same_language("english", None) is False
    assert tracker._is_same_language("en", "en") is True
    assert (
        tracker.get_language_id(
            _meta(
                audio_languages=["English", "French"], original_language="en"
            )
        )
        == "4"
    )
    assert tracker.get_language_id(_meta(audio_languages=[])) == "0"


def test_suio_language_resolve_disabled() -> None:
    tracker = _tracker(resolve_language=False)
    assert tracker.get_language_id(_meta(audio_languages=["French"])) == "0"


@pytest.mark.asyncio
async def test_suio_prepare_files_missing_nzb() -> None:
    tracker = _tracker()
    tracker.common.check_nzb_file = AsyncMock(return_value=False)  # type: ignore[method-assign]
    assert await tracker._prepare_files(_meta(nzb_path="missing.nzb")) is None


@pytest.mark.asyncio
async def test_suio_prepare_files_nzb_mediainfo_and_jpg(
    tmp_path: Path,
) -> None:
    tracker = _tracker()
    nzb = tmp_path / "release.nzb"
    nzb.write_bytes(b"nzb")
    tracker.common.check_nzb_file = AsyncMock(return_value=True)  # type: ignore[method-assign]
    temp = tmp_path / "tmp" / "release"
    temp.mkdir(parents=True)
    (temp / "MEDIAINFO_CLEANPATH.txt").write_bytes(b"mediainfo")
    art = tmp_path / "tmp" / "release" / "artwork"
    art.mkdir(parents=True)
    Image.new("RGB", (2, 2)).save(art / "POSTER.jpg", format="JPEG")

    files = await tracker._prepare_files(
        _meta(tmp_path, category="GAME", nzb_path=str(nzb))
    )
    assert files is not None
    assert files["nzb"][1] == b"nzb"
    assert files["nfo"][0] == "MediaInfo.nfo"
    assert files["cover"][0] == "POSTER.jpg"


@pytest.mark.asyncio
async def test_suio_prepare_files_scene_nfo_and_bdmv(tmp_path: Path) -> None:
    tracker = _tracker()
    nzb = tmp_path / "release.nzb"
    nzb.write_bytes(b"nzb")
    tracker.common.check_nzb_file = AsyncMock(return_value=True)  # type: ignore[method-assign]
    temp = tmp_path / "tmp" / "release"
    temp.mkdir(parents=True)
    (temp / "scene.nfo").write_bytes(b"scene")
    files = await tracker._prepare_files(
        _meta(tmp_path, scene=True, nzb_path=str(nzb))
    )
    assert files is not None and files["nfo"][0] == "scene.nfo"

    (temp / "BD_SUMMARY_00.txt").write_bytes(b"bdinfo")
    files = await tracker._prepare_files(
        _meta(tmp_path, is_disc="BDMV", nzb_path=str(nzb))
    )
    assert files is not None and files["nfo"][0] == "BDInfo.nfo"


@pytest.mark.asyncio
async def test_suio_png_cover_converts_to_jpeg(tmp_path: Path) -> None:
    tracker = _tracker()
    art = tmp_path / "tmp" / "release" / "artwork"
    art.mkdir(parents=True)
    Image.new("RGBA", (2, 2), (255, 0, 0, 128)).save(art / "POSTER.png")
    cover = await tracker._cover_file(_meta(tmp_path, category="BOOK"))
    assert cover is not None
    assert cover[0] == "POSTER.jpg"
    assert cover[1].startswith(b"\xff\xd8")


def test_suio_rgb_image_converts_non_rgb() -> None:
    rgba = Image.new("RGBA", (1, 1), (255, 0, 0, 128))
    assert Suio._rgb_image(rgba).mode == "RGB"
    gray = Image.new("L", (1, 1), 0)
    assert Suio._rgb_image(gray).mode == "RGB"


@pytest.mark.asyncio
async def test_suio_upload_preflight_and_debug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    tracker.upload_url = None
    item = _meta()
    assert not await tracker.upload(item)
    assert "base_url missing" in item.tracker_status["SUIO"]["status_message"]

    tracker.upload_url = "https://indexer.invalid/api-upload"
    monkeypatch.setattr(
        tracker, "_prepare_files", AsyncMock(return_value=None)
    )
    item = _meta()
    assert not await tracker.upload(item)

    monkeypatch.setattr(
        tracker,
        "_prepare_files",
        AsyncMock(return_value={"nzb": ("x.nzb", b"x", "application/x-nzb")}),
    )
    monkeypatch.setattr(
        tracker,
        "_prepare_data",
        AsyncMock(return_value={"rlsname": "Release"}),
    )
    assert await tracker.upload(_meta(debug=True))


@pytest.mark.asyncio
async def test_suio_submit_upload_transport_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    item = _meta()
    status = item.tracker_status["SUIO"]
    for error in (
        httpx.TimeoutException("timeout"),
        httpx.RequestError(
            "offline", request=httpx.Request("POST", tracker.upload_url)
        ),
        RuntimeError("unexpected"),
    ):
        monkeypatch.setattr(
            tracker, "_upload_response", AsyncMock(side_effect=error)
        )
        status.clear()
        assert not await tracker._submit_upload(item, status, "user", {}, {})
        assert status["status_message"]


@pytest.mark.asyncio
async def test_suio_handle_upload_failure_redacts(tmp_path: Path) -> None:
    tracker = _tracker()
    item = _meta(nzb_path=str(tmp_path / "secret.nzb"))
    status = item.tracker_status["SUIO"]
    response = _response(
        status=200,
        text='<font color="red"><b>user Release.Name secret.nzb failed</b></font>',
        url="https://indexer.invalid/404",
    )
    assert not await tracker._handle_upload_response(
        item, status, "user", {"rlsname": "Release.Name"}, response
    )
    message = status["status_message"]
    assert "user" not in message
    assert "Release.Name" not in message
    assert "secret.nzb" not in message


@pytest.mark.asyncio
async def test_suio_handle_upload_success_cache_and_id(tmp_path: Path) -> None:
    tracker = _tracker()
    item = _meta(tmp_path, nzb_path=str(tmp_path / "release.nzb"))
    status = item.tracker_status["SUIO"]
    response = _response(
        status=200,
        text="<!-- <response>Upload successful ID: ABC123</response> -->",
        url="https://indexer.invalid/success",
    )
    assert await tracker._handle_upload_response(
        item, status, "user", {"rlsname": "Release"}, response
    )
    assert status["torrent_id"] == "ABC123"
    assert (tmp_path / "tmp" / "release" / "SUIO_upload_ok").read_text(
        encoding="utf-8"
    ) == "ok"


def test_suio_failure_message_http_and_unknown() -> None:
    http_error = _response(status=500)
    parsed: dict[str, str | bool] = {
        "font_error": "",
        "comment": "",
        "error": True,
    }
    assert Suio._failure_message(http_error, parsed) == "HTTP 500"
    success = _response(status=200)
    assert Suio._failure_message(success, parsed) == "Unknown upload failure"


def test_suio_response_id_none() -> None:
    assert Suio._response_id(_response(text="no id"), "") == ""


def test_suio_configured_urls_handles_validation_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        Suio,
        "_validated_urls",
        classmethod(
            lambda _cls, _url: (_ for _ in ()).throw(RuntimeError("bad"))
        ),
    )
    assert Suio._configured_urls({"base_url": "indexer.invalid"}) == (
        None,
        None,
        None,
    )


@pytest.mark.asyncio
async def test_suio_search_existing_normal_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        tracker, "_cached_search_result", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(tracker, "_search_enabled", lambda: True)
    monkeypatch.setattr(
        tracker, "_search_queries", AsyncMock(return_value=[{"q": "Release"}])
    )
    monkeypatch.setattr(
        tracker,
        "_execute_search_queries",
        AsyncMock(return_value=[{"name": "Dupe"}]),
    )
    assert await tracker.search_existing(_meta()) == [{"name": "Dupe"}]
    assert tracker._search_enabled()


@pytest.mark.asyncio
async def test_suio_execute_search_query_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        suio_module, "reserve_daily_api_hit", AsyncMock(return_value=(True, 1))
    )
    response = _response(status=200, text="<xml/>")
    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    monkeypatch.setattr(
        tracker,
        "_append_search_dupes",
        lambda _text, dupes, _seen: dupes.append({"name": "Dupe"}),
    )
    dupes: list[dict[str, Any]] = []
    assert await tracker._execute_search_query(
        _meta(), client, {"q": "Release"}, dupes, set()
    )
    assert dupes == [{"name": "Dupe"}]


@pytest.mark.asyncio
async def test_suio_additional_checks_success() -> None:
    assert await _tracker().get_additional_checks(_meta())


def test_suio_language_multi() -> None:
    assert (
        _tracker().get_language_id(
            _meta(audio_languages=["English", "French", "German"])
        )
        == "9"
    )


@pytest.mark.asyncio
async def test_suio_nfo_file_none(tmp_path: Path) -> None:
    tracker = _tracker()
    assert await tracker._nfo_file(_meta(tmp_path)) is None


@pytest.mark.asyncio
async def test_suio_upload_non_debug_delegates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        tracker,
        "_prepare_files",
        AsyncMock(return_value={"nzb": ("x.nzb", b"x", "application/x-nzb")}),
    )
    monkeypatch.setattr(
        tracker,
        "_prepare_data",
        AsyncMock(return_value={"rlsname": "Release"}),
    )
    monkeypatch.setattr(
        tracker, "_submit_upload", AsyncMock(return_value=True)
    )
    item = _meta(debug=False)
    assert await tracker.upload(item)
    tracker._submit_upload.assert_awaited_once()


def test_suio_failure_message_prefers_comment() -> None:
    response = _response(status=200)
    parsed: dict[str, str | bool] = {
        "font_error": "",
        "comment": "Comment failure",
        "error": True,
    }
    assert Suio._failure_message(response, parsed) == "Comment failure"


def test_suio_search_enabled_true() -> None:
    tracker = _tracker()
    tracker.daily_api_hit_limit = 1
    assert tracker._search_enabled()
