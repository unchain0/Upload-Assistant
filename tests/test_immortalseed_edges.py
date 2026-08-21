from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.immortalseed import ImmortalSeed


def _config() -> dict[str, Any]:
    return {"DEFAULT": {}, "TRACKERS": {"IMMORTALSEED": {"anon": False}}}


def _tracker() -> ImmortalSeed:
    return ImmortalSeed(_config())


def _meta(**values: object) -> Meta:
    state: dict[str, object] = {
        "category": "MOVIE",
        "title": "Example",
        "season": "S01",
        "artist": "Artist",
        "imdb_tt": "tt1234567",
        "resolution": "1080p",
        "sd": 0,
        "genres": [],
        "keywords": [],
        "anime": False,
        "original_language": "en",
        "tv_pack": False,
        "video_encode": "x264",
        "audiobook": False,
        "comic": False,
        "manga": False,
        "magazine": False,
        "format": "",
        "platform": "PC",
        "base_dir": ".",
        "uuid": "immortalseed",
        "scene_name": "",
        "basename_no_ext": "Example",
        "hosted_artwork": [],
        "artwork_url": "",
        "overview": "Overview",
        "youtube": "",
        "anon": 0,
        "imdb_info": {},
        "tracker_status": {},
        "clean_name": "Example",
    }
    state.update(values)
    return Meta(state)


def _response(
    text: str, *, url: str = "https://immortalseed.me/browse.php"
) -> httpx.Response:
    return httpx.Response(200, request=httpx.Request("GET", url), text=text)


@pytest.mark.asyncio
async def test_immortalseed_validate_credentials_loads_cookies() -> None:
    tracker = _tracker()
    tracker.cookie_validator.load_session_cookies = AsyncMock(
        return_value=httpx.Cookies({"sid": "value"})
    )  # type: ignore[method-assign]
    assert await tracker.validate_credentials(_meta())
    assert tracker.session.cookies.get("sid") == "value"


@pytest.mark.asyncio
async def test_immortalseed_search_unsupported_and_login_failure() -> None:
    tracker = _tracker()
    tracker.cookie_validator.load_session_cookies = AsyncMock(
        return_value=None
    )  # type: ignore[method-assign]
    assert await tracker.search_existing(_meta(category="PODCAST")) == []

    tracker.cookie_validator.handle_validation_failure = AsyncMock()  # type: ignore[method-assign]
    tracker.session.get = AsyncMock(
        return_value=_response(
            "login.php", url="https://immortalseed.me/login.php"
        )
    )  # type: ignore[method-assign]
    meta = _meta(category="MOVIE")
    assert await tracker.search_existing(meta) == []
    assert meta.skipping == "IMMORTALSEED"
    tracker.cookie_validator.handle_validation_failure.assert_awaited_once()


def test_immortalseed_search_entries_guard_and_success() -> None:
    assert ImmortalSeed._search_entries("<html></html>") == []
    html = """
    <table id='sortabletable'><tbody>
      <tr><td>header</td></tr>
      <tr><td><a href='details.php?id=7'>Release</a></td><td></td><td></td><td></td><td>1.5 GB</td></tr>
      <tr><td>No link</td></tr>
    </tbody></table>
    """
    assert ImmortalSeed._search_entries(html) == [
        {"name": "Release", "size": "1.5 GB", "link": "details.php?id=7"}
    ]


def test_immortalseed_special_categories() -> None:
    tracker = _tracker()
    assert (
        tracker.get_category_id(
            _meta(category="MOVIE", genres=["Documentary"], sd=0)
        )
        == 54
    )
    assert (
        tracker.get_category_id(
            _meta(category="TV", genres=["Documentary"], sd=1)
        )
        == 53
    )
    assert tracker.get_category_id(_meta(category="TV", anime=True)) == 32
    assert (
        tracker.get_category_id(_meta(category="TV", keywords=["cartoon"]))
        == 31
    )


@pytest.mark.asyncio
async def test_immortalseed_existing_nfo_and_hosted_cover(
    tmp_path: Path,
) -> None:
    tracker = _tracker()
    root = tmp_path / "tmp" / "release"
    root.mkdir(parents=True)
    (root / "release.nfo").write_bytes(b"nfo")
    meta = _meta(
        base_dir=str(tmp_path),
        uuid="release",
        hosted_artwork=[{"raw_url": "https://images.invalid/cover.jpg"}],
        artwork_url="https://images.invalid/fallback.jpg",
    )
    assert await tracker.get_nfo(meta) == {
        "nfofile": ("release.nfo", b"nfo", "application/octet-stream")
    }
    assert await tracker.get_cover(meta) == "https://images.invalid/cover.jpg"
