from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from bs4 import BeautifulSoup

from src.domain_models.release import Meta
from src.integrations.trackers import hdspace as hdspace_module
from src.integrations.trackers.hdspace import HDSpace


def _config(*, search_requests: bool = False, anon: bool = False) -> dict[str, Any]:
    return {
        "DEFAULT": {"search_requests": search_requests},
        "TRACKERS": {"HDSPACE": {"anon": anon}},
    }


def _tracker(**kwargs: object) -> HDSpace:
    return HDSpace(_config(**kwargs))


def _meta(**values: object) -> Meta:
    state: dict[str, object] = {
        "category": "MOVIE",
        "resolution": "1080p",
        "video_codec": "H.264",
        "video_encode": "x264",
        "filelist": ["Movie.2024.mkv"],
        "image_list": [],
        "name": "Example Movie 2024 1080p WEB-DL H.264-GROUP",
        "imdb": 1234567,
        "imdb_tt": "tt1234567",
        "is_disc": "",
        "type": "WEBDL",
        "genres": [],
        "keywords": [],
        "anime": False,
        "search_requests": False,
        "title": "Example Movie",
        "anon": 0,
        "three_d": "",
        "youtube": "",
        "base_dir": ".",
        "uuid": "hdspace",
        "tracker_status": {},
    }
    state.update(values)
    return Meta(state)


def _response(text: str, *, url: str = "https://hd-space.org/index.php", status: int = 200) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("GET", url), text=text)


@pytest.mark.asyncio
async def test_hdspace_load_cookies_and_empty_payload_rejection() -> None:
    tracker = _tracker()
    tracker.cookie_validator.load_session_cookies = AsyncMock(return_value=httpx.Cookies({"sid": "value"}))  # type: ignore[method-assign]
    assert await tracker.validate_credentials(_meta())
    assert tracker.session.cookies.get("sid") == "value"
    assert not await tracker.get_additional_checks(_meta(filelist=[]))


@pytest.mark.asyncio
async def test_hdspace_description_error_is_nonfatal(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenBuilder:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("broken")

    monkeypatch.setattr(hdspace_module, "DescriptionBuilder", BrokenBuilder)
    assert await _tracker().generate_description(_meta()) == ""


@pytest.mark.asyncio
async def test_hdspace_search_login_failure() -> None:
    tracker = _tracker()
    tracker.cookie_validator.handle_validation_failure = AsyncMock()  # type: ignore[method-assign]
    meta = _meta()
    response = _response("Recover password", url="https://hd-space.org/index.php?page=login")
    assert await tracker._search_login_failure(meta, response)
    assert meta.skipping == "HDSPACE"
    tracker.cookie_validator.handle_validation_failure.assert_awaited_once()


def test_hdspace_parse_search_page_and_row_helpers() -> None:
    tracker = _tracker()
    assert tracker._parse_search_page("Show/Hide Categories<html></html>", 0) == ([], False)

    html = """
    Show/Hide Categories
    <table><tbody>
      <tr>
        <td class='lista'><a href='index.php?page=torrent-details&id=7' title='Fallback Title'></a></td>
        <td class='lista'></td><td class='lista'></td><td class='lista'></td>
        <td class='lista'>1.5 GB</td>
      </tr>
      <tr><td class='lista'>No link</td></tr>
    </tbody></table>
    <a href='index.php?page=torrents&pages=1'>Next</a>
    """
    parsed = tracker._parse_search_page(html, 0)
    assert parsed is not None
    entries, has_next = parsed
    assert entries == [{"name": "Fallback Title", "size": "1.5 GB", "link": "https://hd-space.org/index.php?page=torrent-details&id=7"}]
    assert has_next


def test_hdspace_has_next_page_numeric_fallback() -> None:
    soup = BeautifulSoup("<a href='index.php?pages=1'>1</a>", "html.parser")
    assert HDSpace._has_next_page(soup, 0)


def test_hdspace_documentary_category() -> None:
    assert asyncio.run(_tracker().get_category_id(_meta(genres=["Documentary"]))) == 25


@pytest.mark.asyncio
async def test_hdspace_get_requests_success_and_error() -> None:
    tracker = _tracker(search_requests=True)
    tracker.cookie_validator.load_session_cookies = AsyncMock(return_value=httpx.Cookies())  # type: ignore[method-assign]
    html = """
    <form action='index.php?page=takedelreq'>
      <table class='lista'>
        <tr><td class='header'>Header</td></tr>
        <tr><td class='lista'><a href='index.php?page=req&id=1'><b>Request One</b></a></td></tr>
        <tr><td class='lista'>Missing name</td></tr>
      </table>
    </form>
    """
    tracker.session.get = AsyncMock(return_value=_response(html))  # type: ignore[method-assign]
    result = await tracker.get_requests(_meta())
    assert result == [{"Name": "Request One", "Link": "index.php?page=req&id=1"}]

    tracker.session.get = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
    assert await tracker.get_requests(_meta()) == []


def test_hdspace_log_request_entries() -> None:
    _tracker()._log_request_entries([{"Name": "Request", "Link": "index.php?page=req&id=1"}])


@pytest.mark.asyncio
async def test_hdspace_existing_nfo(tmp_path: Path) -> None:
    tracker = _tracker()
    root = tmp_path / "tmp" / "release"
    root.mkdir(parents=True)
    (root / "release.nfo").write_bytes(b"nfo")
    assert await tracker.get_nfo(_meta(base_dir=str(tmp_path), uuid="release")) == {"nfo": ("release.nfo", b"nfo", "application/octet-stream")}


@pytest.mark.asyncio
async def test_hdspace_search_pages_login_and_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker, "_search_page_response", AsyncMock(return_value=_response("login")))
    monkeypatch.setattr(tracker, "_search_login_failure", AsyncMock(return_value=True))
    assert await tracker._search_pages(_meta(), "123") == []

    tracker = _tracker()
    responses = iter((_response("page-one"), _response("page-two")))
    monkeypatch.setattr(tracker, "_search_page_response", AsyncMock(side_effect=lambda *_args: next(responses)))
    monkeypatch.setattr(tracker, "_search_login_failure", AsyncMock(return_value=False))
    parsed = iter((([{"name": "One", "size": None, "link": "link1"}], True), ([{"name": "Two", "size": None, "link": "link2"}], False)))
    monkeypatch.setattr(tracker, "_parse_search_page", lambda *_args: next(parsed))
    assert await tracker._search_pages(_meta(), "123") == [
        {"name": "One", "size": None, "link": "link1"},
        {"name": "Two", "size": None, "link": "link2"},
    ]


def test_hdspace_search_row_missing_link_and_direct_name() -> None:
    soup = BeautifulSoup("<tr><td class='lista'><a href=''>Direct Name</a></td></tr>", "html.parser")
    row = soup.find("tr")
    assert row is not None
    assert HDSpace._search_row(row) is None

    soup = BeautifulSoup("<a href='index.php?page=torrent-details&id=1'>Direct Name</a>", "html.parser")
    tag = soup.find("a")
    assert tag is not None
    assert HDSpace._search_name(tag) == "Direct Name"


@pytest.mark.asyncio
async def test_hdspace_requests_disabled() -> None:
    assert await _tracker().get_requests(_meta(search_requests=False)) is False


def test_hdspace_search_row_rejects_empty_resolved_link(monkeypatch: pytest.MonkeyPatch) -> None:
    soup = BeautifulSoup("<tr><td class='lista'><a href='index.php?page=torrent-details&id=1'>Direct Name</a></td></tr>", "html.parser")
    row = soup.find("tr")
    assert row is not None
    monkeypatch.setattr(HDSpace, "_search_link", classmethod(lambda _cls, _tag: ""))
    assert HDSpace._search_row(row) is None
