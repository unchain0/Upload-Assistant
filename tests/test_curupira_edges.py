from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock
from xml.etree import ElementTree

import httpx
import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.USENET.curupira import Curupira


def _config(*, anon: bool = False) -> dict[str, Any]:
    return {
        "DEFAULT": {},
        "TRACKERS": {"CURUPIRA": {"api_key": "key", "anon": anon}},
    }


def _tracker(*, anon: bool = False) -> Curupira:
    return Curupira(_config(anon=anon))


def _meta(tmp_path: Path | None = None, **values: object) -> Meta:
    base = str(tmp_path or Path())
    state: dict[str, object] = {
        "base_dir": base,
        "uuid": "release",
        "category": "MOVIE",
        "title": "Example",
        "scene_name": "",
        "basename_no_ext": "Example.2025.1080p",
        "tvdb_id": 0,
        "tmdb_id": 0,
        "imdb_id": 0,
        "imdb": 0,
        "mal_id": 0,
        "season_int": 0,
        "episode_int": 0,
        "resolution": "1080p",
        "anime": False,
        "audiobook": False,
        "source": "WEB",
        "type": "WEBDL",
        "is_disc": "",
        "nzb_path": "",
        "hosted_artwork": [],
        "artwork_url": "",
        "audio_languages": [],
        "subtitle_languages": [],
        "anon": 0,
        "debug": False,
        "tracker_status": {},
        "current_version": "1.0",
        "image_list": [],
        "menu_images": [],
        "spectrograms_images": [],
        "dynamic_hdr_plot_images": [],
    }
    state.update(values)
    return Meta(state)


def _response(
    payload: Any | None = None, *, text: str | None = None, status: int = 200
) -> httpx.Response:
    request = httpx.Request("GET", "https://curupira.cc/api")
    if text is not None:
        return httpx.Response(status, request=request, text=text)
    return httpx.Response(status, request=request, json=payload)


@pytest.mark.asyncio
async def test_curupira_cache_and_exact_search_params(tmp_path: Path) -> None:
    tracker = _tracker()
    root = tmp_path / "tmp" / "release"
    root.mkdir(parents=True)
    (root / "CURUPIRA_upload_ok").write_text("ok", encoding="utf-8")
    assert await tracker.search_existing(_meta(tmp_path)) == [
        "Example.2025.1080p"
    ]

    params = await tracker._search_param_list(_meta(tmp_path))
    assert params[0] == {"t": "search", "q": "Example.2025.1080p"}


def test_curupira_identifier_and_movie_search_paths() -> None:
    tracker = _tracker()
    assert tracker._tv_identifier(
        _meta(category="TV", imdb_id=123, imdb=123)
    ) == {"imdbid": "tt123"}
    assert tracker._movie_search_params(_meta(imdb_id=123, imdb=123)) == {
        "t": "movie",
        "imdbid": "tt123",
    }
    assert tracker._movie_search_params(_meta(tmdb_id=456)) == {
        "t": "movie",
        "tmdbid": "456",
    }
    assert "q" in tracker._movie_search_params(_meta())


@pytest.mark.asyncio
async def test_curupira_search_response_errors() -> None:
    tracker = _tracker()
    client = AsyncMock()
    client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
    assert await tracker._safe_search_response(client, {"t": "search"}) is None

    error = httpx.RequestError(
        "offline", request=httpx.Request("GET", tracker.base_url)
    )
    client.get = AsyncMock(side_effect=error)
    assert await tracker._safe_search_response(client, {"t": "search"}) is None

    client.get = AsyncMock(return_value=_response(text="", status=500))
    assert await tracker._safe_search_response(client, {"t": "search"}) is None


def test_curupira_parse_error_and_duplicate_suppression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        tracker,
        "_parse_dupes_from_response",
        lambda _text: (_ for _ in ()).throw(ElementTree.ParseError("bad")),
    )
    dupes: list[dict[str, Any]] = []
    tracker._extend_unique_dupes("bad", dupes, set())
    assert dupes == []

    seen = {"same"}
    tracker._append_unique_dupe(
        {"name": "duplicate", "link": "same"}, dupes, seen
    )
    assert dupes == []


@pytest.mark.asyncio
async def test_curupira_prepare_files_with_optional_nfo(
    tmp_path: Path,
) -> None:
    tracker = _tracker()
    nzb = tmp_path / "release.nzb"
    nzb.write_bytes(b"nzb")
    root = tmp_path / "tmp" / "release"
    root.mkdir(parents=True)
    (root / "release.nfo").write_bytes(b"nfo")
    tracker.common.check_nzb_file = AsyncMock(return_value=True)  # type: ignore[method-assign]
    files = await tracker._prepare_files(_meta(tmp_path, nzb_path=str(nzb)))
    assert files is not None
    assert files["nzb_file"][1] == b"nzb"
    assert files["nfo_file"][1] == b"nfo"

    tracker.common.check_nzb_file = AsyncMock(return_value=False)  # type: ignore[method-assign]
    assert (
        await tracker._prepare_files(_meta(tmp_path, nzb_path=str(nzb)))
        is None
    )


@pytest.mark.asyncio
async def test_curupira_media_info_missing_and_present(tmp_path: Path) -> None:
    tracker = _tracker()
    meta = _meta(tmp_path)
    assert await tracker.get_media_info(meta) == ""
    path = tmp_path / "tmp" / "release" / "MEDIAINFO_CLEANPATH.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("mediainfo", encoding="utf-8")
    assert await tracker.get_media_info(meta) == "mediainfo"


def test_curupira_cover_fallback_and_nonvideo_cover_data() -> None:
    tracker = _tracker()
    meta = _meta(
        hosted_artwork=["bad"], artwork_url="https://example.com/cover.jpg"
    )
    assert tracker.get_cover(meta) == "https://example.com/cover.jpg"
    data: dict[str, Any] = {}
    tracker._apply_cover_data(
        data,
        _meta(category="BOOK", artwork_url="https://example.com/book.jpg"),
    )
    assert data["custom_cover_url"] == "https://example.com/book.jpg"


@pytest.mark.asyncio
async def test_curupira_upload_missing_files_debug_and_transport(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tracker = _tracker()
    tracker.common.check_nzb_file = AsyncMock(return_value=False)  # type: ignore[method-assign]
    meta = _meta(tmp_path)
    assert not await tracker.upload(meta)

    tracker.common.check_nzb_file = AsyncMock(return_value=True)  # type: ignore[method-assign]
    monkeypatch.setattr(
        tracker, "_prepare_files", AsyncMock(return_value=None)
    )
    meta = _meta(tmp_path, tracker_status={})
    assert not await tracker.upload(meta)

    files = {"nzb_file": ("release.nzb", b"nzb", "application/x-nzb")}
    monkeypatch.setattr(
        tracker, "_prepare_files", AsyncMock(return_value=files)
    )
    monkeypatch.setattr(
        tracker, "_prepare_data", AsyncMock(return_value={"name": "Release"})
    )
    debug = _meta(tmp_path, debug=True, tracker_status={})
    assert await tracker.upload(debug)

    monkeypatch.setattr(
        tracker, "_upload_release", AsyncMock(return_value=True)
    )
    regular = _meta(tmp_path, tracker_status={})
    assert await tracker.upload(regular)
    tracker._upload_release.assert_awaited_once()


@pytest.mark.asyncio
async def test_curupira_upload_release_errors_and_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = _tracker()
    meta = _meta(tmp_path)
    files = {"nzb_file": ("release.nzb", b"nzb", "application/x-nzb")}
    for error in (
        httpx.TimeoutException("timeout"),
        httpx.RequestError(
            "offline", request=httpx.Request("POST", tracker.base_url)
        ),
        RuntimeError("unexpected"),
    ):
        monkeypatch.setattr(
            tracker, "_post_upload", AsyncMock(side_effect=error)
        )
        status: dict[str, Any] = {}
        assert not await tracker._upload_release(meta, status, {}, files)
        assert status["status_message"].startswith("data error:")

    status = {}
    assert not await tracker._handle_upload_response(
        meta, status, _response({}, status=500)
    )
    assert "HTTP 500" in status["status_message"]

    status = {}
    assert await tracker._handle_upload_response(
        meta, status, _response({"public_id": "abc"}, status=201)
    )
    assert status["torrent_id"] == "abc"
    assert (tmp_path / "tmp" / "release" / "CURUPIRA_upload_ok").is_file()


def test_curupira_tv_identifier_query_fallback() -> None:
    assert "q" in _tracker()._tv_identifier(_meta(category="TV"))


def test_curupira_extend_unique_dupes_adds_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        tracker,
        "_parse_dupes_from_response",
        lambda _text: [{"name": "One", "link": "1"}],
    )
    dupes: list[dict[str, Any]] = []
    tracker._extend_unique_dupes("payload", dupes, set())
    assert dupes == [{"name": "One", "link": "1"}]


@pytest.mark.asyncio
async def test_curupira_media_info_read_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.integrations.trackers.USENET import curupira as curupira_module

    path = tmp_path / "tmp" / "release" / "MEDIAINFO_CLEANPATH.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("mediainfo", encoding="utf-8")
    monkeypatch.setattr(
        curupira_module.aiofiles,
        "open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("broken")),
    )
    assert await _tracker().get_media_info(_meta(tmp_path)) == ""


def test_curupira_cover_prefers_hosted_artwork() -> None:
    meta = _meta(
        hosted_artwork=[{"raw_url": "https://example.com/hosted.jpg"}],
        artwork_url="https://example.com/fallback.jpg",
    )
    assert _tracker().get_cover(meta) == "https://example.com/hosted.jpg"
