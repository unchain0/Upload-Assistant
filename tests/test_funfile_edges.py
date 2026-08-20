from __future__ import annotations

from pathlib import Path
from typing import Any, Self
from unittest.mock import AsyncMock

import httpx
import pytest

from src.domain_models.release import Meta
from src.integrations.trackers import cookie_auth as cookie_auth_module
from src.integrations.trackers import funfile as funfile_module
from src.integrations.trackers.funfile import FunFile


def _config(*, check_requests: bool = False) -> dict[str, Any]:
    return {
        "TRACKERS": {
            "FUNFILE": {
                "username": "user",
                "password": "pass",
                "check_requests": check_requests,
            }
        }
    }


def _tracker(*, check_requests: bool = False) -> FunFile:
    return FunFile(_config(check_requests=check_requests))


def _meta(**values: object) -> Meta:
    state: dict[str, object] = {
        "category": "MOVIE",
        "title": "Example Movie",
        "season": "S01",
        "episode": "E01",
        "base_dir": ".",
        "uuid": "funfile",
        "anime": False,
        "scene": False,
        "scene_name": "",
        "basename_no_ext": "Example Movie",
        "clean_name": "Example Movie",
        "language_checked": True,
        "audio_languages": [],
        "subtitle_languages": [],
        "audio": "",
        "channels": "",
        "artwork_url": "",
        "name": "Example Movie",
        "tvmaze_episode_data": {},
        "mediainfo": {"media": {"track": []}},
        "is_disc": "",
        "sd": 0,
        "video_codec": "H264",
        "video_encode": "x264",
        "source": "WEB",
        "type": "WEBDL",
        "imdb_info": {},
        "tv_pack": False,
    }
    state.update(values)
    return Meta(state)


@pytest.mark.asyncio
async def test_funfile_validate_credentials_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    cookie_file = tmp_path / "funfile.txt"
    cookie_file.write_text("cookie", encoding="utf-8")
    monkeypatch.setattr(cookie_auth_module, "find_cookie_file", lambda *_args, **_kwargs: str(cookie_file))
    tracker.cookie_validator.load_session_cookies = AsyncMock(return_value=httpx.Cookies())  # type: ignore[method-assign]
    assert await tracker.validate_credentials(_meta(base_dir=str(tmp_path)))


@pytest.mark.asyncio
async def test_funfile_login_success_and_cookie_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    cookie_file = tmp_path / "cookies.txt"
    monkeypatch.setattr(cookie_auth_module, "find_cookie_file", lambda *_args, **_kwargs: str(cookie_file))
    response = httpx.Response(302, request=httpx.Request("POST", "https://www.funfile.org/takelogin.php"))
    tracker.session.post = AsyncMock(return_value=response)  # type: ignore[method-assign]
    tracker.session.cookies.set("sid", "value", domain=".funfile.org", path="/")
    await tracker.login(_meta(base_dir=str(tmp_path)))
    text = cookie_file.read_text(encoding="utf-8")
    assert "Netscape HTTP Cookie File" in text
    assert ".funfile.org\tTRUE" in text


@pytest.mark.asyncio
async def test_funfile_login_failure_returns_without_cookie_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    cookie_file = tmp_path / "cookies.txt"
    monkeypatch.setattr(cookie_auth_module, "find_cookie_file", lambda *_args, **_kwargs: str(cookie_file))
    tracker.session.post = AsyncMock(return_value=httpx.Response(403, request=httpx.Request("POST", "https://www.funfile.org/takelogin.php")))  # type: ignore[method-assign]
    await tracker.login(_meta(base_dir=str(tmp_path)))
    assert not cookie_file.exists()


@pytest.mark.asyncio
async def test_funfile_search_existing_success_and_login_failure() -> None:
    tracker = _tracker()
    tracker.cookie_validator.load_session_cookies = AsyncMock(return_value=httpx.Cookies())  # type: ignore[method-assign]
    success = httpx.Response(200, request=httpx.Request("GET", "https://www.funfile.org/suggest.php"), text=" One \n\nTwo\n")
    tracker.session.get = AsyncMock(return_value=success)  # type: ignore[method-assign]
    assert await tracker.search_existing(_meta()) == ["One", "Two"]

    tracker.login = AsyncMock()  # type: ignore[method-assign]
    tracker.cookie_validator.handle_validation_failure = AsyncMock()  # type: ignore[method-assign]
    login_response = httpx.Response(200, request=httpx.Request("GET", "https://www.funfile.org/login.php"), text="login")
    tracker.session.get = AsyncMock(return_value=login_response)  # type: ignore[method-assign]
    meta = _meta()
    assert await tracker.search_existing(meta) == []
    assert meta.skipping == "FUNFILE"


@pytest.mark.asyncio
async def test_funfile_get_requests_error_cookie_none_and_success() -> None:
    tracker = _tracker(check_requests=True)
    tracker._fetch_requests = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
    assert await tracker.get_requests(_meta()) == []

    tracker = _tracker(check_requests=True)
    tracker.cookie_validator.load_session_cookies = AsyncMock(return_value=None)  # type: ignore[method-assign]
    assert await tracker._fetch_requests(_meta()) == []

    tracker.cookie_validator.load_session_cookies = AsyncMock(return_value=httpx.Cookies())  # type: ignore[method-assign]
    tracker._request_search_text = AsyncMock(return_value="<html></html>")  # type: ignore[method-assign]
    assert await tracker._fetch_requests(_meta()) == []


def test_funfile_request_row_and_logging() -> None:
    tracker = _tracker()
    html = """
    <td class='mf_content'><table><tr>
      <td class='row3'><nobr><a href='/requests/1'><b>Request One</b></a></nobr></td>
      <td class='row3'>unused</td><td class='row3'>10 GB</td>
    </tr></table></td>
    """
    results = tracker._parse_requests(html)
    assert results == [{"Name": "Request One", "Link": "/requests/1", "Reward": "10 GB"}]
    tracker._log_requests(results)
    tracker._log_requests([])


def test_funfile_type_helpers_remaining_branches() -> None:
    tracker = _tracker()
    tracker.video_source = "dvd"
    tracker.video_codec = "hevc"
    tracker.video_encode = "h.264"
    assert tracker.movie_type(_meta()) == "DVDR"
    assert tracker.tv_type(_meta(category="TV")) == "DVDR"
    assert tracker.anime_type(_meta(anime=True, tvmaze_episode_data={"season_number": 0})) == "TVSpecial"
    assert tracker.anime_v_codec(_meta()) == "h264"

    tracker.video_source = "web"
    assert tracker.tv_type(_meta(category="TV", sd=1)) == "Web-SD"
    tracker.video_source = "bluray"
    assert tracker.tv_type(_meta(category="TV", sd=0)) == "x265-HD"
    tracker.video_codec = "vc-1"
    assert tracker.anime_v_codec(_meta()) == "VC1"


def test_funfile_display_aspect_and_media_track_guards() -> None:
    assert FunFile._display_aspect_ratio({"DisplayAspectRatio": "bad"}) == "16_9"
    assert FunFile._display_aspect_ratio({"DisplayAspectRatio": "1.33"}) == "4_3"
    assert FunFile._media_tracks(_meta(mediainfo={"media": {"track": "bad"}})) == []


class _PosterClient:
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, _url: str) -> httpx.Response:
        return httpx.Response(200, content=b"poster", request=httpx.Request("GET", "https://example.com/poster.jpg"))


@pytest.mark.asyncio
async def test_funfile_get_poster_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(funfile_module.httpx, "AsyncClient", lambda *_args, **_kwargs: _PosterClient())
    poster = await _tracker().get_poster(_meta(artwork_url="https://example.com/poster.jpg", name="Movie"))
    assert poster == ("Movie.jpg", b"poster", "image/jpeg")


def test_funfile_get_nfo_success(tmp_path: Path) -> None:
    root = tmp_path / "tmp" / "release"
    root.mkdir(parents=True)
    (root / "release.nfo").write_text("nfo", encoding="utf-8")
    result = _tracker().get_nfo(_meta(base_dir=str(tmp_path), uuid="release"))
    assert result["nfo"][0] == "release.nfo"
    result["nfo"][1].close()


@pytest.mark.asyncio
async def test_funfile_upload_success(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    cookies = httpx.Cookies()
    tracker.cookie_validator.load_session_cookies = AsyncMock(return_value=cookies)  # type: ignore[method-assign]
    tracker.get_data = AsyncMock(return_value={"type": "19"})  # type: ignore[method-assign]
    tracker.get_name = AsyncMock(return_value="Release")  # type: ignore[method-assign]
    tracker.get_poster = AsyncMock(return_value=("poster.jpg", b"poster", "image/jpeg"))  # type: ignore[method-assign]
    monkeypatch.setattr(tracker, "get_nfo", lambda _meta: {"nfo": ("release.nfo", b"nfo", "application/octet-stream")})
    tracker.cookie_auth_uploader.handle_upload = AsyncMock(return_value=True)  # type: ignore[method-assign]
    assert await tracker.upload(_meta())
    call = tracker.cookie_auth_uploader.handle_upload.await_args.kwargs
    assert set(call["additional_files"]) == {"poster", "nfo"}


@pytest.mark.asyncio
async def test_funfile_authenticated_search_retry_succeeds() -> None:
    tracker = _tracker()
    tracker.login = AsyncMock()  # type: ignore[method-assign]
    login_response = httpx.Response(200, request=httpx.Request("GET", "https://www.funfile.org/login.php"), text="login")
    success = httpx.Response(200, request=httpx.Request("GET", "https://www.funfile.org/suggest.php"), text="Release")
    tracker.session.get = AsyncMock(return_value=success)  # type: ignore[method-assign]
    assert await tracker._authenticated_search_response(_meta(), "https://www.funfile.org/suggest.php", login_response) is success


def test_funfile_request_row_without_name_returns_none() -> None:
    soup = funfile_module.BeautifulSoup("<tr><td class='row3'>missing</td></tr>", "html.parser")
    row = soup.find("tr")
    assert row is not None
    assert FunFile._request_row(row) is None


def test_funfile_remaining_type_fallbacks() -> None:
    tracker = _tracker()
    tracker.video_source = "bluray"
    tracker.video_codec = "hevc"
    tracker.video_encode = "x265"
    assert tracker.movie_type(_meta()) == "x265"
    tracker.video_source = "dvd"
    assert tracker.anime_type(_meta(anime=True, tvmaze_episode_data={"season_number": 1})) == "DVDSpecial"
    tracker.video_source = "bluray"
    tracker.video_codec = "h264"
    tracker.video_encode = "x265"
    assert tracker.anime_v_codec(_meta()) == "x264"
