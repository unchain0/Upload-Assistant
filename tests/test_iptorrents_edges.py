from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import bencodepy
import httpx
import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.iptorrents import IPTorrents


def _config(*, anon: bool = False, force_data: bool = False) -> dict[str, Any]:
    return {
        "DEFAULT": {},
        "TRACKERS": {"IPTORRENTS": {"anon": anon, "force_data": force_data}},
    }


def _tracker(*, anon: bool = False, force_data: bool = False) -> IPTorrents:
    return IPTorrents(_config(anon=anon, force_data=force_data))


def _meta(**values: object) -> Meta:
    state: dict[str, object] = {
        "category": "MOVIE",
        "title": "Example Movie",
        "season": "S01",
        "artist": "Artist",
        "resolution": "1080p",
        "is_disc": "",
        "type": "WEBDL",
        "genres": [],
        "source": "WEB",
        "three_d": "",
        "video_codec": "H264",
        "platform": "PC",
        "original_language": "en",
        "tv_pack": False,
        "audiobook": False,
        "comic": False,
        "manga": False,
        "magazine": False,
        "newspaper": False,
        "book_language_iso": "eng",
        "format": "MP3",
        "scene_name": "",
        "clean_name": "Example Movie 1080p WEB-DL-GROUP",
        "scene": False,
        "name": "Example Movie 1080p WEB-DL-GROUP",
        "base_dir": ".",
        "uuid": "release",
        "anon": 0,
        "debug": False,
        "tmdb_id": 123,
        "tracker_status": {
            "IPTORRENTS": {"torrent_id": "42", "status_message": "ok"}
        },
        "ua_signature": "UA",
    }
    state.update(values)
    return Meta(state)


def _response(
    text: str, *, url: str = "https://iptorrents.com/t?72=&q=Example"
) -> httpx.Response:
    return httpx.Response(200, request=httpx.Request("GET", url), text=text)


@pytest.mark.asyncio
async def test_iptorrents_validate_credentials_success() -> None:
    tracker = _tracker()
    cookies = httpx.Cookies()
    cookies.set("sid", "value")
    tracker.cookie_validator.load_session_cookies = AsyncMock(
        return_value=cookies
    )  # type: ignore[method-assign]
    assert await tracker.validate_credentials(_meta())
    assert tracker.session.cookies.get("sid") == "value"


@pytest.mark.asyncio
async def test_iptorrents_search_none_and_login_paths() -> None:
    tracker = _tracker()
    assert await tracker.search_existing(_meta(category="UNKNOWN")) == []

    cookies = httpx.Cookies()
    cookies.set("sid", "value")
    tracker.cookie_validator.load_session_cookies = AsyncMock(
        return_value=cookies
    )  # type: ignore[method-assign]
    tracker.cookie_validator.handle_validation_failure = AsyncMock()  # type: ignore[method-assign]
    tracker.session.get = AsyncMock(
        return_value=_response("login", url="https://iptorrents.com/login.php")
    )  # type: ignore[method-assign]
    meta = _meta()
    assert await tracker.search_existing(meta) == []
    assert meta.skipping == "IPTORRENTS"
    tracker.cookie_validator.handle_validation_failure.assert_awaited_once()


@pytest.mark.asyncio
async def test_iptorrents_search_parses_and_filters_rows() -> None:
    tracker = _tracker()
    tracker.cookie_validator.load_session_cookies = AsyncMock(
        return_value=None
    )  # type: ignore[method-assign]
    html = """
    <table id='torrents'><tbody>
      <tr><td>0</td><td><a class='hv' href='/t/1'>Example Movie 1080p WEB-DL</a></td><td>2</td><td>3</td><td>4</td><td>8.5 GB</td></tr>
      <tr><td>0</td><td><a class='hv' href='/t/2'>Example Movie 1080p WEBRip</a></td><td>2</td><td>3</td><td>4</td><td>No size</td></tr>
      <tr><td>short</td></tr>
    </tbody></table>
    """
    tracker.session.get = AsyncMock(return_value=_response(html))  # type: ignore[method-assign]
    result = await tracker.search_existing(_meta(type="WEBDL"))
    assert result == [
        {
            "name": "Example Movie 1080p WEB-DL",
            "size": "8.5 GB",
            "link": "https://iptorrents.com/t/1",
        }
    ]


def test_iptorrents_search_parser_guards() -> None:
    assert IPTorrents._parse_search_results("<html></html>", []) == []
    assert (
        IPTorrents._parse_search_results("<table id='torrents'></table>", [])
        == []
    )


def test_iptorrents_category_edge_paths() -> None:
    tracker = _tracker()
    assert tracker.get_category_id(_meta(category="UNKNOWN")) == 0
    assert (
        tracker._movie_secondary_special(_meta(type="BDRIP", source="WEB"))
        == 90
    )
    assert (
        tracker._movie_secondary_special(
            _meta(type="OTHER", resolution="480p", source="WEB")
        )
        == 77
    )
    assert (
        tracker._movie_secondary_special(
            _meta(type="OTHER", resolution="1080p", source="CAM")
        )
        == 96
    )
    assert tracker._movie_language_category(_meta(genres=["Family"])) == 54
    assert (
        tracker._movie_language_category(
            _meta(genres=[], original_language="fr")
        )
        == 38
    )
    assert (
        tracker._book_special_category(_meta(category="BOOK", comic=True))
        == 94
    )
    assert (
        tracker._book_special_category(_meta(category="BOOK", magazine=True))
        == 92
    )


def test_iptorrents_torrent_multifile_size() -> None:
    payload = bencodepy.encode(
        {b"info": {b"files": [{b"length": 5}, {b"length": 7}]}}
    )
    assert IPTorrents._torrent_size_bytes(payload) == 12


@pytest.mark.asyncio
async def test_iptorrents_freeleech_missing_invalid_and_large_torrent(
    tmp_path: Path,
) -> None:
    tracker = _tracker()
    meta = _meta(base_dir=str(tmp_path), uuid="release")
    assert not await tracker.get_is_freeleech(meta)

    root = tmp_path / "tmp" / "release"
    root.mkdir(parents=True, exist_ok=True)
    (root / "BASE.torrent").write_bytes(b"invalid")
    assert not await tracker.get_is_freeleech(meta)

    (root / "BASE.torrent").write_bytes(
        bencodepy.encode({b"info": {b"length": 9 * 1024**3}})
    )
    assert await tracker.get_is_freeleech(meta)


@pytest.mark.asyncio
async def test_iptorrents_get_data_freeleech_and_anonymous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker(anon=True)
    monkeypatch.setattr(
        tracker, "generate_description", AsyncMock(return_value="description")
    )
    monkeypatch.setattr(
        tracker, "get_is_freeleech", AsyncMock(return_value=True)
    )
    data = await tracker.get_data(_meta())
    assert data["freeleech"] == "on"
    assert data["anonymous"] == "on"


@pytest.mark.asyncio
async def test_iptorrents_upload_force_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker(force_data=True)
    cookies = httpx.Cookies()
    cookies.set("sid", "value")
    tracker.cookie_validator.load_session_cookies = AsyncMock(
        return_value=cookies
    )  # type: ignore[method-assign]
    monkeypatch.setattr(
        tracker, "get_data", AsyncMock(return_value={"type": 72})
    )
    monkeypatch.setattr(tracker, "get_name", AsyncMock(return_value="Release"))
    tracker.cookie_auth_uploader.handle_upload = AsyncMock(return_value=True)  # type: ignore[method-assign]
    monkeypatch.setattr(tracker, "edit_post_upload", AsyncMock())
    meta = _meta()
    assert await tracker.upload(meta)
    tracker.edit_post_upload.assert_awaited_once_with(meta)


@pytest.mark.asyncio
async def test_iptorrents_edit_post_upload_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        tracker, "generate_description", AsyncMock(return_value="description")
    )
    tracker.session.post = AsyncMock(
        return_value=httpx.Response(
            200, request=httpx.Request("POST", "https://iptorrents.com")
        )
    )  # type: ignore[method-assign]
    meta = _meta()
    await tracker.edit_post_upload(meta)
    assert meta.tracker_status["IPTORRENTS"]["status_message"].endswith(
        "Failed to edit torrent."
    )


def test_iptorrents_search_row_without_link_returns_none() -> None:
    soup = __import__("bs4").BeautifulSoup(
        "<tr><td>0</td><td>name only</td><td>2</td><td>3</td><td>4</td><td>1 GB</td></tr>",
        "html.parser",
    )
    row = soup.find("tr")
    assert row is not None
    assert IPTorrents._search_row(row, []) is None


def test_iptorrents_documentary_genre_category() -> None:
    assert _tracker().get_category_id(_meta(genres=["Documentary"])) == 26
