from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.torrentleech import TorrentLeech


def _config(
    *, api_upload: bool = False, anon: bool = False, img_rehost: bool = True
) -> dict[str, Any]:
    return {
        "DEFAULT": {},
        "TRACKERS": {
            "TORRENTLEECH": {
                "api_upload": api_upload,
                "passkey": "passkey",
                "anon": anon,
                "img_rehost": img_rehost,
            }
        },
    }


def _tracker(
    *, api_upload: bool = False, anon: bool = False, img_rehost: bool = True
) -> TorrentLeech:
    return TorrentLeech(
        _config(api_upload=api_upload, anon=anon, img_rehost=img_rehost)
    )


def _meta(tmp_path: Path | None = None, **values: object) -> Meta:
    root = tmp_path or Path()
    state: dict[str, object] = {
        "base_dir": str(root),
        "uuid": "release",
        "path": str(root / "movie.mkv"),
        "filelist": [str(root / "movie.mkv")],
        "name": "Example Movie 2010 1080p WEB-DL-GROUP",
        "aka": "",
        "title": "Example Movie",
        "category": "MOVIE",
        "original_language": "en",
        "genres": ["Drama"],
        "resolution": "1080p",
        "is_disc": "",
        "type": "WEBDL",
        "source": "WEB",
        "tv_pack": False,
        "sd": False,
        "platform": "PC",
        "comic": False,
        "manga": False,
        "anime": False,
        "mal_id": 0,
        "scene": False,
        "imdb_info": {
            "imdbID": "tt123",
            "imdb_url": "https://imdb.invalid/title/tt123",
        },
        "tvmaze_id": 0,
        "season": "S01",
        "episode": "E01",
        "year": 2010,
        "anon": 0,
        "debug": False,
        "tracker_status": {"TORRENTLEECH": {}},
        "menu_images": [],
        "image_list": [],
        "spectrograms_images": [],
        "dynamic_hdr_plot_images": [],
        "ua_signature": "UA",
    }
    state.update(values)
    return Meta(state)


def _response(
    *,
    status: int = 200,
    text: str = "",
    url: str = "https://www.torrentleech.org/torrents/upload/",
) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("GET", url), text=text)


@pytest.mark.asyncio
async def test_torrentleech_login_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _tracker(api_upload=True)
    assert await api.login(_meta())

    tracker = _tracker()
    tracker.cookie_validator.load_session_cookies = AsyncMock(
        return_value=None
    )  # type: ignore[method-assign]
    assert not await tracker.login(_meta())

    cookies = httpx.Cookies({"sid": "1"})
    tracker.cookie_validator.load_session_cookies = AsyncMock(
        return_value=cookies
    )  # type: ignore[method-assign]
    monkeypatch.setattr(
        tracker, "_validate_cookie_session", AsyncMock(return_value=True)
    )
    assert await tracker.login(_meta())


@pytest.mark.asyncio
async def test_torrentleech_validate_cookie_session_success_failure_and_request_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        tracker,
        "_login_probe",
        AsyncMock(
            return_value=_response(status=200, url=tracker.http_upload_url)
        ),
    )
    assert await tracker._validate_cookie_session(False)

    monkeypatch.setattr(
        tracker,
        "_login_probe",
        AsyncMock(
            return_value=_response(
                status=200, url="https://www.torrentleech.org/login"
            )
        ),
    )
    assert not await tracker._validate_cookie_session(False)

    error = httpx.RequestError(
        "offline", request=httpx.Request("GET", tracker.base_url)
    )
    monkeypatch.setattr(tracker, "_login_probe", AsyncMock(side_effect=error))
    assert not await tracker._validate_cookie_session(True)


def test_torrentleech_category_disc_and_platform_branches() -> None:
    tracker = _tracker()
    assert tracker.get_category(_meta(is_disc="DVD")) == 12
    assert (
        tracker.get_category(_meta(category="GAME", platform="unknown")) == 17
    )
    assert tracker.get_category(_meta(category="MUSIC")) == 31
    assert tracker.get_category(_meta(category="UNKNOWN")) == 0


@pytest.mark.asyncio
async def test_torrentleech_search_existing_login_fail_and_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker, "login", AsyncMock(return_value=False))
    item = _meta()
    assert await tracker.search_existing(item) == []
    assert item.skipping == "TORRENTLEECH"

    monkeypatch.setattr(tracker, "login", AsyncMock(return_value=True))
    monkeypatch.setattr(
        tracker, "_search_url", AsyncMock(return_value=[{"name": "Dupe"}])
    )
    results = await tracker.search_existing(
        _meta(category="TV", tv_pack=False)
    )
    assert results == [{"name": "Dupe"}, {"name": "Dupe"}]
    assert tracker._search_url.await_count == 2


@pytest.mark.asyncio
async def test_torrentleech_search_url_filters_forbidden() -> None:
    tracker = _tracker()
    tracker.session.get = AsyncMock(
        return_value=httpx.Response(
            200,
            request=httpx.Request(
                "GET", "https://www.torrentleech.org/search"
            ),
            json={
                "torrentList": [
                    {"name": "Movie WEB-DL", "fid": 1, "size": 10},
                    {"name": "Movie WEBRip", "fid": 2, "size": 11},
                ]
            },
        )
    )  # type: ignore[method-assign]
    results = await tracker._search_url(
        "https://www.torrentleech.org/search", ["webrip"]
    )
    assert results == [
        {
            "name": "Movie WEB-DL",
            "size": 10,
            "link": "https://www.torrentleech.org/torrent/1",
        }
    ]


@pytest.mark.asyncio
async def test_torrentleech_upload_routes_api_and_cookie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _tracker(api_upload=True)
    api.common.create_torrent_for_upload = AsyncMock()  # type: ignore[method-assign]
    monkeypatch.setattr(api, "upload_api", AsyncMock(return_value=True))
    assert await api.upload(_meta())
    api.upload_api.assert_awaited_once()

    cookie = _tracker(api_upload=False)
    cookie.common.create_torrent_for_upload = AsyncMock()  # type: ignore[method-assign]
    monkeypatch.setattr(cookie, "cookie_upload", AsyncMock(return_value=True))
    assert await cookie.upload(_meta())
    cookie.cookie_upload.assert_awaited_once()


@pytest.mark.asyncio
async def test_torrentleech_api_torrent_file(tmp_path: Path) -> None:
    tracker = _tracker(api_upload=True)
    root = tmp_path / "tmp" / "release"
    root.mkdir(parents=True)
    (root / "[TORRENTLEECH].torrent").write_bytes(b"torrent")
    files = await tracker._api_torrent_file(_meta(tmp_path))
    assert files["torrent"][1] == b"torrent"


@pytest.mark.asyncio
async def test_torrentleech_upload_api_debug_and_normal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker(api_upload=True)
    monkeypatch.setattr(
        tracker, "_api_torrent_file", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        tracker,
        "_api_upload_data",
        AsyncMock(return_value={"name": "Release"}),
    )
    monkeypatch.setattr(
        tracker, "_debug_api_upload", AsyncMock(return_value=True)
    )
    assert await tracker.upload_api(_meta(debug=True))

    tracker.session.post = AsyncMock(return_value=_response(text="123"))  # type: ignore[method-assign]
    monkeypatch.setattr(
        tracker, "_handle_api_upload_response", AsyncMock(return_value=True)
    )
    assert await tracker.upload_api(_meta(debug=False))
    tracker._handle_api_upload_response.assert_awaited_once()


@pytest.mark.asyncio
async def test_torrentleech_api_response_error_and_success() -> None:
    tracker = _tracker(api_upload=True)
    item = _meta()
    assert not await tracker._handle_api_upload_response(
        item, _response(text="error")
    )
    assert (
        item.tracker_status["TORRENTLEECH"]["status_message"]
        == "data error: error"
    )

    tracker.common.create_torrent_ready_to_seed = AsyncMock()  # type: ignore[method-assign]
    item = _meta()
    assert await tracker._handle_api_upload_response(
        item, _response(text="77")
    )
    assert item.tracker_status["TORRENTLEECH"]["torrent_id"] == "77"
    tracker.common.create_torrent_ready_to_seed.assert_awaited_once()


@pytest.mark.asyncio
async def test_torrentleech_cookie_upload_data_tvmaze_anonymous_and_screens() -> (
    None
):
    tracker = _tracker(anon=True, img_rehost=True)
    item = _meta(
        category="TV",
        tvmaze_id=55,
        image_list=[{"raw_url": "https://img/1"}],
    )
    data = await tracker.get_cookie_upload_data(item)
    assert data["tvMazeURL"] == "https://www.tvmaze.com/shows/55"
    assert data["is_anonymous_upload"] == "on"
    assert data["screenshots[]"] == ["https://img/1"]


@pytest.mark.asyncio
async def test_torrentleech_cookie_upload_login_fail_and_debug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        tracker, "generate_description", AsyncMock(return_value="description")
    )
    monkeypatch.setattr(tracker, "login", AsyncMock(return_value=False))
    item = _meta()
    assert await tracker.cookie_upload(item) is None
    assert (
        "Login with cookies failed"
        in item.tracker_status["TORRENTLEECH"]["status_message"]
    )

    monkeypatch.setattr(tracker, "login", AsyncMock(return_value=True))
    monkeypatch.setattr(
        tracker,
        "get_cookie_upload_data",
        AsyncMock(return_value={"name": "Release"}),
    )
    monkeypatch.setattr(
        tracker, "_debug_cookie_upload", AsyncMock(return_value=True)
    )
    assert await tracker.cookie_upload(_meta(debug=True))


@pytest.mark.asyncio
async def test_torrentleech_submit_cookie_upload_request_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    error = httpx.RequestError(
        "offline", request=httpx.Request("POST", tracker.http_upload_url)
    )
    monkeypatch.setattr(
        tracker, "_cookie_upload_response", AsyncMock(side_effect=error)
    )
    item = _meta()
    assert await tracker._submit_cookie_upload(item, {}, "description") is None
    assert "offline" in item.tracker_status["TORRENTLEECH"]["status_message"]


@pytest.mark.asyncio
async def test_torrentleech_cookie_upload_response_reads_torrent(
    tmp_path: Path,
) -> None:
    tracker = _tracker()
    root = tmp_path / "tmp" / "release"
    root.mkdir(parents=True)
    (root / "[TORRENTLEECH].torrent").write_bytes(b"torrent")
    tracker.session.post = AsyncMock(return_value=_response(status=302))  # type: ignore[method-assign]
    response = await tracker._cookie_upload_response(
        _meta(tmp_path), {}, "description"
    )
    assert response.status_code == 302
    files = tracker.session.post.await_args.kwargs["files"]
    assert files["torrent"][1] == b"torrent"


@pytest.mark.asyncio
async def test_torrentleech_cookie_upload_response_failure_and_success() -> (
    None
):
    tracker = _tracker()
    tracker.common.save_html_file = AsyncMock(return_value="failed.html")  # type: ignore[method-assign]
    item = _meta()
    assert not await tracker._handle_cookie_upload_response(
        item, _response(status=200, text="bad")
    )
    tracker.common.save_html_file.assert_awaited_once()

    tracker.common.create_torrent_ready_to_seed = AsyncMock()  # type: ignore[method-assign]
    success = httpx.Response(
        302,
        request=httpx.Request("POST", tracker.http_upload_url),
        headers={"location": "/successfulupload?torrentID=88"},
    )
    item = _meta()
    assert await tracker._handle_cookie_upload_response(item, success)
    assert item.tracker_status["TORRENTLEECH"]["torrent_id"] == "88"
    tracker.common.create_torrent_ready_to_seed.assert_awaited_once()


@pytest.mark.asyncio
async def test_torrentleech_submit_cookie_upload_success_delegates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    response = _response(status=200)
    monkeypatch.setattr(
        tracker, "_cookie_upload_response", AsyncMock(return_value=response)
    )
    monkeypatch.setattr(
        tracker, "_handle_cookie_upload_response", AsyncMock(return_value=True)
    )
    item = _meta()
    assert await tracker._submit_cookie_upload(item, {}, "description")
    tracker._handle_cookie_upload_response.assert_awaited_once_with(
        item, response
    )
