from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.hdtorrents import HDTorrents


def _config() -> dict:
    return {
        "TRACKERS": {
            "HDTORRENTS": {
                "url": "https://hd-torrents.org",
                "announce_url": "https://hdts-announce.ru/announce.php",
                "anon": False,
            }
        }
    }


def _tracker() -> HDTorrents:
    tracker = HDTorrents(_config())
    tracker.session = SimpleNamespace(cookies=None)
    return tracker


def _response(text: str, *, url: str = "https://hd-torrents.org/torrents.php") -> httpx.Response:
    return httpx.Response(200, text=text, request=httpx.Request("GET", url))


@pytest.mark.asyncio
async def test_hdtorrents_validate_credentials_domain_mismatch() -> None:
    tracker = _tracker()
    cookies = [SimpleNamespace(domain="other.invalid")]
    tracker.cookie_validator.load_session_cookies = AsyncMock(return_value=cookies)  # type: ignore[method-assign]

    assert not await tracker.validate_credentials(Meta())


@pytest.mark.asyncio
async def test_hdtorrents_validate_credentials_success() -> None:
    tracker = _tracker()
    cookies = [SimpleNamespace(domain=".hd-torrents.org")]
    tracker.cookie_validator.load_session_cookies = AsyncMock(return_value=cookies)  # type: ignore[method-assign]

    assert await tracker.validate_credentials(Meta())
    assert tracker.session.cookies == cookies


@pytest.mark.asyncio
async def test_hdtorrents_search_login_redirect_marks_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    cookies = [SimpleNamespace(domain="hd-torrents.org")]
    tracker.cookie_validator.load_session_cookies = AsyncMock(return_value=cookies)  # type: ignore[method-assign]
    tracker.cookie_validator.handle_validation_failure = AsyncMock()  # type: ignore[method-assign]
    monkeypatch.setattr(tracker, "_search_response", AsyncMock(return_value=_response("login.php", url="https://hd-torrents.org/login.php")))
    meta = Meta()

    assert await tracker.search_existing(meta) == []
    assert meta.skipping == "HDTORRENTS"
    tracker.cookie_validator.handle_validation_failure.assert_awaited_once()


@pytest.mark.asyncio
async def test_hdtorrents_search_parses_rows_and_updates_token(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    tracker.cookie_validator.load_session_cookies = AsyncMock(return_value=[])  # type: ignore[method-assign]
    html = """
    <input name="csrfToken" value="token-123" />
    <table>
      <tr><td class="mainblockcontent">Filename</td></tr>
      <tr><td class="mainblockcontent"><a href="details.php?id=10">Release One</a></td><td class="mainblockcontent">1.5 GiB</td></tr>
      <tr><td class="mainblockcontent"><a href="details.php?id=11">Release Two</a></td><td class="mainblockcontent">No size</td></tr>
      <tr><td class="mainblockcontent">No release link</td></tr>
    </table>
    """
    monkeypatch.setattr(tracker, "_search_response", AsyncMock(return_value=_response(html)))

    results = await tracker.search_existing(Meta())

    assert HDTorrents.secret_token == "token-123"
    assert results == [
        {"name": "Release One", "size": "1.5 GiB", "link": "https://hd-torrents.org/details.php?id=10"},
        {"name": "Release Two", "size": None, "link": "https://hd-torrents.org/details.php?id=11"},
    ]


def test_hdtorrents_row_result_header_and_missing_link() -> None:
    tracker = _tracker()
    assert tracker._search_results('<tr><td class="mainblockcontent">Filename</td></tr>') == []
    assert tracker._search_results('<tr><td class="mainblockcontent">Other</td></tr>') == []


@pytest.mark.asyncio
async def test_hdtorrents_get_nfo_reads_first_file(tmp_path: Path) -> None:
    root = tmp_path / "tmp" / "release"
    root.mkdir(parents=True)
    (root / "release.nfo").write_bytes(b"nfo-data")
    meta = Meta(base_dir=str(tmp_path), uuid="release")

    result = await _tracker().get_nfo(meta)

    assert result == {"nfos": ("release.nfo", b"nfo-data", "application/octet-stream")}


@pytest.mark.asyncio
async def test_hdtorrents_upload_applies_loaded_cookies(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    cookies = [SimpleNamespace(domain="hd-torrents.org")]
    tracker.cookie_validator.load_session_cookies = AsyncMock(return_value=cookies)  # type: ignore[method-assign]
    tracker.cookie_auth_uploader.handle_upload = AsyncMock(return_value=True)  # type: ignore[method-assign]
    monkeypatch.setattr(tracker, "get_data", AsyncMock(return_value={"category": 1}))
    monkeypatch.setattr(tracker, "get_nfo", AsyncMock(return_value={}))

    assert await tracker.upload(Meta())
    assert tracker.session.cookies == cookies
    tracker.cookie_auth_uploader.handle_upload.assert_awaited_once()
