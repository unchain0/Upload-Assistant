from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import TracebackType
from typing import Any, ClassVar, Self
from unittest.mock import AsyncMock

import httpx
import pytest

from src.domain_models.errors import OperationAbortedError
from src.domain_models.release import Meta
from src.integrations.external_apis import bluray


class _Response:
    def __init__(self, status_code: int = 200, text: str = "", content: bytes = b"") -> None:
        self.status_code = status_code
        self.text = text
        self.content = content


class _Client:
    queue: ClassVar[list[object]] = []

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        return None

    async def get(self, *_args: object, **_kwargs: object) -> _Response:
        value = type(self).queue.pop(0)
        if isinstance(value, BaseException):
            raise value
        assert isinstance(value, _Response)
        return value

    @classmethod
    def reset(cls, *values: object) -> None:
        cls.queue = list(values)


def _request_error() -> httpx.RequestError:
    return httpx.RequestError("network", request=httpx.Request("GET", "https://www.blu-ray.com/"))


def _meta(tmp_path: Path, **values: object) -> Meta:
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "uuid": "bluray-edge",
        "imdb_id": 1234567,
        "title": "Example Film",
        "name": "Example Film 2026 1080p BluRay",
        "three_d": "no",
        "resolution": "1080p",
        "is_disc": "BDMV",
        "unattended": True,
        "unattended_confirm": False,
        "use_bluray_images": False,
        "bluray_score": 75,
        "bluray_single_score": 75,
        "debug": True,
        "discs": [],
        "region": "",
        "distributor": "",
        "release_url": "",
    }
    state.update(values)
    target = tmp_path / "tmp" / str(state["uuid"])
    target.mkdir(parents=True, exist_ok=True)
    return Meta(state)


@pytest.fixture(autouse=True)
def _network(monkeypatch: pytest.MonkeyPatch) -> None:
    _Client.reset()
    monkeypatch.setattr(bluray.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(bluray.random, "uniform", lambda *_args: 0.0)

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(bluray.asyncio, "sleep", no_sleep)


def test_style_release_type_product_and_image_helpers() -> None:
    assert bluray._style_contains("font-size: 12px", "12px")
    assert not bluray._style_contains(None, "12px")
    assert bluray._style_green("color: green")
    assert bluray._style_gray("color: #999999")
    assert bluray._style_specs("font-size: 12px")

    assert bluray._derive_release_type(Meta(three_d="yes", resolution="1080p", is_disc="BDMV")) == ("3D", True, False, False)
    assert bluray._derive_release_type(Meta(three_d="yes", resolution="2160p", is_disc="BDMV")) == ("4K", True, True, False)
    assert bluray._derive_release_type(Meta(three_d="no", resolution="480p", is_disc="DVD")) == ("DVD", False, False, True)
    assert asyncio.run(bluray.extract_product_id("https://www.blu-ray.com/movies/Example/12345/")) == "12345"
    assert asyncio.run(bluray.extract_product_id("invalid")) is None

    assert bluray.clean_image_url(None) is None
    assert bluray.clean_image_url("") == ""
    assert bluray.clean_image_url("https://x/a.JPG?size=large") == "https://x/a.JPG"
    assert bluray.clean_image_url("https://x/no-extension") == "https://x/no-extension"


def test_search_bluray_cache_success_invalid_fetch_save_and_read_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    meta = _meta(tmp_path)
    cached = tmp_path / "tmp" / meta.uuid / "debug_bluray_search_tt1234567.html"
    cached.write_text("cached valid html", encoding="utf-8")
    assert asyncio.run(bluray.search_bluray(meta)) == "cached valid html"

    cached.write_text("No index cached", encoding="utf-8")
    _Client.reset(_Response(200, "fresh html"))
    assert asyncio.run(bluray.search_bluray(meta)) == "fresh html"
    assert cached.read_text() == "fresh html"

    cached.write_text("stale", encoding="utf-8")
    original_read = Path.read_text

    def fail_read(path: Path, *_args: object, **_kwargs: object) -> str:
        if path == cached:
            raise OSError("read failed")
        return original_read(path, *_args, **_kwargs)

    monkeypatch.setattr(Path, "read_text", fail_read)
    _Client.reset(_Response(200, "after read failure"))
    assert asyncio.run(bluray.search_bluray(meta)) == "after read failure"
    monkeypatch.setattr(Path, "read_text", original_read)

    original_write = Path.write_text

    def fail_write(path: Path, *_args: object, **_kwargs: object) -> int:
        if path == cached:
            raise OSError("write failed")
        return original_write(path, *_args, **_kwargs)

    cached.unlink()
    monkeypatch.setattr(Path, "write_text", fail_write)
    _Client.reset(_Response(200, "unsaved html"))
    assert asyncio.run(bluray.search_bluray(meta)) == "unsaved html"


def test_search_bluray_block_status_request_retries_and_failure(tmp_path: Path) -> None:
    meta = _meta(tmp_path)
    _Client.reset(_Response(200, "No index"), _Response(200, "No index"), _Response(200, "No index"))
    assert asyncio.run(bluray.search_bluray(meta)) is None

    _Client.reset(_Response(500, "failure"), _Response(502, "failure"), _Response(503, "failure"))
    assert asyncio.run(bluray.search_bluray(meta)) is None

    _Client.reset(_request_error(), _request_error(), _request_error())
    assert asyncio.run(bluray.search_bluray(meta)) is None


def test_extract_bluray_links_full_empty_invalid_and_parser_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    assert bluray.extract_bluray_links(None) is None
    assert bluray.extract_bluray_links("<html></html>") is None
    html = """
    <div class="figure">
      <a class="alphaborder" href="https://www.blu-ray.com/movies/Example/100/"></a>
      <div class="figurecaptionbottom"><div style="font-weight: bold">Example Film</div><div style="margin-top: 2px">2026</div></div>
    </div>
    <div class="figure"><span>No valid anchor</span></div>
    <div class="figure"><a class="alphaborder" href="https://www.blu-ray.com/movies/Unknown/101/"></a></div>
    """
    assert bluray.extract_bluray_links(html) == [
        {"title": "Example Film", "year": "2026", "releases_url": "https://www.blu-ray.com/movies/Example/100/#Releases"},
        {"title": "Unknown Title", "year": "Unknown Year", "releases_url": "https://www.blu-ray.com/movies/Unknown/101/#Releases"},
    ]
    monkeypatch.setattr(bluray, "BeautifulSoup", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("parse failed")))
    assert bluray.extract_bluray_links(html) is None


def _release_sections_html() -> str:
    return """
    <table><tr><td>
      <h3>Blu-ray Editions</h3>
      <img width="18" height="12" title="United States" />
      <a href="https://www.blu-ray.com/movies/Standard/1001/" title="Standard Edition">Standard</a>
      <small style="color: green">$19.99</small><small style="color: #999999">Criterion</small>
      <a href="https://www.blu-ray.com/invalid/" title="Invalid">Invalid</a>
      <h3>4K Blu-ray Editions</h3>
      <img width="18" height="12" title="United Kingdom" />
      <a href="https://www.blu-ray.com/movies/UHD/1002/" title="UHD Edition">UHD</a>
      <small style="color: green">$29.99</small><small style="color: #999999">Arrow</small>
      <h3>3D Blu-ray Editions</h3>
      <a href="https://www.blu-ray.com/movies/ThreeD/1003/">3D Edition</a>
      <h3>DVD Editions</h3>
      <a href="https://www.blu-ray.com/dvd/DVD/1004/">DVD Edition</a>
      <h3>End</h3>
    </td></tr></table>
    """


@pytest.mark.parametrize(
    ("values", "release_id"),
    [
        ({"three_d": "no", "resolution": "1080p", "is_disc": "BDMV"}, "1001"),
        ({"three_d": "no", "resolution": "2160p", "is_disc": "BDMV"}, "1002"),
        ({"three_d": "yes", "resolution": "1080p", "is_disc": "BDMV"}, "1003"),
        ({"three_d": "no", "resolution": "480p", "is_disc": "DVD"}, "1004"),
    ],
)
def test_extract_release_info_all_types(tmp_path: Path, values: dict[str, object], release_id: str) -> None:
    meta = _meta(tmp_path, **values)
    releases = asyncio.run(bluray.extract_bluray_release_info(_release_sections_html(), meta, "product"))
    assert releases[0]["release_id"] == release_id
    if release_id == "1001":
        assert releases[0]["country"] == "United States" and releases[0]["price"] == "$19.99"


def test_extract_release_info_empty_missing_parent_save_and_parser_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    meta = _meta(tmp_path)
    assert asyncio.run(bluray.extract_bluray_release_info("", meta, "1")) == []
    assert asyncio.run(bluray.extract_bluray_release_info("<h3>Blu-ray Editions</h3>", meta, "1")) == []

    original_write = Path.write_text

    def fail_write(path: Path, *_args: object, **_kwargs: object) -> int:
        if "debug_bluray" in path.name:
            raise OSError("write failed")
        return original_write(path, *_args, **_kwargs)

    monkeypatch.setattr(Path, "write_text", fail_write)
    assert asyncio.run(bluray.extract_bluray_release_info(_release_sections_html(), meta, "1"))
    monkeypatch.setattr(Path, "write_text", original_write)
    monkeypatch.setattr(bluray, "BeautifulSoup", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("parse failed")))
    assert asyncio.run(bluray.extract_bluray_release_info("html", meta, "1")) == []


def _specs_html(*, short: bool = False, multi: str = "Two-disc set (2 BD-50)") -> str:
    audio_id = "shortaudio" if short else "longaudio"
    subs_id = "shortsubs" if short else "longsubs"
    return f"""
    <table><tr><td width="228px" style="font-size: 12px">
      <span class="subheading">Video</span>Codec: HEVC<br/>
      Resolution: 2160p<br/>
      <span class="subheading">Audio</span>
      <div id="{audio_id}">English: Dolby Atmos<br/>English: Dolby TrueHD 7.1<br/>Note: immersive<br/>French: DTS-HD MA 5.1</div>
      <span class="subheading">Subtitles</span><div id="{subs_id}">English, French (less), Spanish</div>
      <span class="subheading">Discs</span>Ultra HD Blu-ray {multi}<br/>
      <span class="subheading">Playback</span>4K Blu-ray: Region B (locked)<br/>
    </td></tr></table>
    <script>box.append("<img id='frontimage' src='https://images.invalid/front.jpg?x=1' />");</script>
    """


def test_parse_release_details_full_short_single_multi_images_and_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    meta = _meta(tmp_path, use_bluray_images=True)
    release: dict[str, Any] = {"title": "UHD"}
    result = asyncio.run(bluray.parse_release_details(_specs_html(), release, meta))
    specs = result["specs"]
    assert specs["video"] == {"codec": "HEVC", "resolution": "2160p"}
    assert specs["audio"][0].startswith("English: Dolby TrueHD Atmos 7.1")
    assert "Note: immersive" in specs["audio"][0]
    assert specs["subtitles"] == ["English", "French", "Spanish"]
    assert specs["discs"]["count"] == 2 and specs["discs"]["format"] == "2 BD-50"
    assert specs["playback"] == {"region": "B", "region_notes": "locked"}
    assert result["cover_images"]["front"].endswith("front.jpg")

    short = asyncio.run(bluray.parse_release_details(_specs_html(short=True, multi="Single disc (1 BD-25)"), {"title": "Short"}, meta))
    assert short["specs"]["discs"] == {"type": "Ultra HD Blu-ray", "count": 1, "format": "BD-25"}

    no_specs: dict[str, Any] = {"title": "No Specs"}
    assert asyncio.run(bluray.parse_release_details("<html></html>", no_specs, meta)) is no_specs
    monkeypatch.setattr(bluray, "BeautifulSoup", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("parse failed")))
    failed: dict[str, Any] = {"title": "Failed"}
    assert asyncio.run(bluray.parse_release_details("html", failed, meta)) is failed


def test_extract_cover_images_scripts_overlay_and_cleaning() -> None:
    html = """
    <script>ignored();</script>
    <script>
      box.append('<img id="frontimage" src="https://x/front.jpg?large=1" />');
      box.append("<img id='backimage' src='https://x/back.png#fragment' />");
      box.append('<img id="slipimage1" src="https://x/slip.webp" />');
      box.append('<img id="booklet" src="https://x/booklet.jpeg" />');
      box.append('<img id="missing" src="" />');
    </script>
    """
    images = bluray.extract_cover_images(html)
    assert images == {
        "front": "https://x/front.jpg",
        "back": "https://x/back.png",
        "slip": "https://x/slip.webp",
        "booklet": "https://x/booklet.jpeg",
    }
    overlay = """
    <div class="simple_overlay"><img id="front-cover" src="https://x/front.jpg" /></div>
    <div class="simple_overlay"><img id="back-cover" src="https://x/back.jpg" /></div>
    <div class="simple_overlay"><img id="slip-cover" src="https://x/slip.jpg" /></div>
    <div class="simple_overlay"><img id="other" src="https://x/other.jpg" /></div>
    <div class="simple_overlay"><img /></div>
    """
    assert bluray.extract_cover_images(overlay) == {
        "front": "https://x/front.jpg",
        "back": "https://x/back.jpg",
        "slip": "https://x/slip.jpg",
        "other": "https://x/other.jpg",
    }


def test_extract_section_mixed_siblings() -> None:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(
        '<td><span class="subheading">Video</span> text <b>bold</b><br/><span class="subheading">Audio</span>end</td>',
        "html.parser",
    )
    assert bluray.extract_section(soup.find("td"), "Video") == " text bold"
    assert bluray.extract_section(soup.find("td"), "Missing") is None


def test_download_cover_images_cache_success_mismatch_corrupt_and_downloads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    meta = _meta(tmp_path, bluray_cover_urls={}, release_url="https://release/1")
    assert not asyncio.run(bluray.download_cover_images(meta))

    covers = tmp_path / "tmp" / meta.uuid / "covers.json"
    covers.write_text(json.dumps([{"release_url": meta.release_url}]), encoding="utf-8")
    meta.bluray_cover_urls = {"front": "https://images/front.jpg"}
    assert asyncio.run(bluray.download_cover_images(meta))

    covers.write_text(json.dumps([{"release_url": "other"}]), encoding="utf-8")
    _Client.reset(
        _Response(200, content=b"front"),
        _Response(404, content=b"missing"),
        _request_error(),
    )
    meta.bluray_cover_urls = {
        "front": "https://images/front.jpg",
        "back": "https://images/back.png",
        "slip": "https://images/slip.webp",
    }
    assert asyncio.run(bluray.download_cover_images(meta))
    assert meta.downloaded_bluray_cover_paths["front"].endswith("cover_front.jpg")
    assert Path(meta.downloaded_bluray_cover_paths["front"]).read_bytes() == b"front"
    assert not covers.exists()

    covers.write_text("not json", encoding="utf-8")
    _Client.reset(_Response(500, content=b""))
    meta.bluray_cover_urls = {"front": "https://images/front.jpg"}
    assert not asyncio.run(bluray.download_cover_images(meta))
    assert not covers.exists()

    covers.write_text("not json", encoding="utf-8")
    original_unlink = Path.unlink

    def fail_unlink(path: Path, *_args: object, **_kwargs: object) -> None:
        if path == covers:
            raise OSError("read only")
        original_unlink(path, *_args, **_kwargs)

    monkeypatch.setattr(Path, "unlink", fail_unlink)
    _Client.reset(_Response(500, content=b""))
    assert not asyncio.run(bluray.download_cover_images(meta))


def test_fetch_release_details_cache_success_invalid_network_retries_and_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    meta = _meta(tmp_path)
    release: dict[str, Any] = {
        "title": "Release",
        "url": "https://www.blu-ray.com/movies/Release/123/",
        "release_id": "123",
    }
    parse = AsyncMock(side_effect=lambda _html, value, _meta: {**value, "parsed": True})
    monkeypatch.setattr(bluray, "parse_release_details", parse)
    cached = tmp_path / "tmp" / meta.uuid / "debug_release_123.html"
    cached.write_text("cached details", encoding="utf-8")
    result = asyncio.run(bluray.fetch_release_details(dict(release), meta))
    assert result["parsed"] and parse.await_args.args[0] == "cached details"

    cached.write_text("No index", encoding="utf-8")
    _Client.reset(_Response(200, "fresh details"))
    result = asyncio.run(bluray.fetch_release_details(dict(release), meta))
    assert result["parsed"] and cached.read_text() == "fresh details"

    cached.write_text("stale", encoding="utf-8")
    original_read = Path.read_text

    def fail_read(path: Path, *_args: object, **_kwargs: object) -> str:
        if path == cached:
            raise OSError("read failed")
        return original_read(path, *_args, **_kwargs)

    monkeypatch.setattr(Path, "read_text", fail_read)
    _Client.reset(_Response(200, "after read error"))
    assert asyncio.run(bluray.fetch_release_details(dict(release), meta))["parsed"]
    monkeypatch.setattr(Path, "read_text", original_read)

    cached.unlink(missing_ok=True)
    original_write = Path.write_text

    def fail_write(path: Path, *_args: object, **_kwargs: object) -> int:
        if path == cached:
            raise OSError("write failed")
        return original_write(path, *_args, **_kwargs)

    monkeypatch.setattr(Path, "write_text", fail_write)
    _Client.reset(_Response(200, "unsaved"))
    assert asyncio.run(bluray.fetch_release_details(dict(release), meta))["parsed"]
    monkeypatch.setattr(Path, "write_text", original_write)

    for responses in (
        (_Response(200, "No index"), _Response(200, "No index"), _Response(200, "No index")),
        (_Response(500, "failure"), _Response(502, "failure"), _Response(503, "failure")),
        (_request_error(), _request_error(), _request_error()),
    ):
        cached.unlink(missing_ok=True)
        _Client.reset(*responses)
        unchanged = dict(release)
        assert asyncio.run(bluray.fetch_release_details(unchanged, meta)) is unchanged


def test_get_bluray_releases_guards_cache_and_unattended(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    meta = _meta(tmp_path)
    monkeypatch.setattr(bluray, "search_bluray", AsyncMock(return_value=None))
    assert asyncio.run(bluray.get_bluray_releases(meta)) == []

    monkeypatch.setattr(bluray, "search_bluray", AsyncMock(return_value="search"))
    monkeypatch.setattr(bluray, "extract_bluray_links", lambda _html: None)
    assert asyncio.run(bluray.get_bluray_releases(meta)) == []

    movies = [
        {"title": "Invalid", "year": "2025", "releases_url": "invalid"},
        {"title": "Valid", "year": "2026", "releases_url": "https://www.blu-ray.com/movies/Valid/123/#Releases"},
    ]
    monkeypatch.setattr(bluray, "extract_bluray_links", lambda _html: movies)
    extract_id = AsyncMock(side_effect=[None, "123"])
    monkeypatch.setattr(bluray, "extract_product_id", extract_id)
    cached = tmp_path / "tmp" / meta.uuid / "debug_bluray_BD_123.html"
    cached.write_text("cached releases", encoding="utf-8")
    release = {"title": "Edition", "url": "https://release", "country": "Canada", "publisher": "Publisher", "price": "$1"}
    monkeypatch.setattr(bluray, "extract_bluray_release_info", AsyncMock(return_value=[release]))
    process = AsyncMock(return_value=[release])
    monkeypatch.setattr(bluray, "process_all_releases", process)
    result = asyncio.run(bluray.get_bluray_releases(meta))
    assert result == [release]
    assert release["movie_title"] == "Valid" and release["movie_year"] == "2026"
    process.assert_awaited_once()


def test_get_bluray_releases_network_retries_and_outer_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    meta = _meta(tmp_path, unattended=True, unattended_confirm=False)
    movie = {"title": "Valid", "year": "2026", "releases_url": "https://www.blu-ray.com/movies/Valid/123/#Releases"}
    monkeypatch.setattr(bluray, "search_bluray", AsyncMock(return_value="search"))
    monkeypatch.setattr(bluray, "extract_bluray_links", lambda _html: [movie])
    monkeypatch.setattr(bluray, "extract_product_id", AsyncMock(return_value="123"))
    release = {"title": "Edition", "url": "https://release", "country": "Canada", "publisher": "Publisher", "price": "$1"}
    monkeypatch.setattr(bluray, "extract_bluray_release_info", AsyncMock(return_value=[release]))
    monkeypatch.setattr(bluray, "process_all_releases", AsyncMock(return_value=[release]))

    _Client.reset(_Response(200, "No index"), _Response(200, "No index"), _Response(200, "No index"))
    assert asyncio.run(bluray.get_bluray_releases(meta)) == []
    _Client.reset(_Response(500, "failure"), _Response(500, "failure"), _Response(500, "failure"))
    assert asyncio.run(bluray.get_bluray_releases(meta)) == []
    _Client.reset(_request_error(), _request_error(), _request_error())
    assert asyncio.run(bluray.get_bluray_releases(meta)) == []

    class BrokenClient:
        async def __aenter__(self) -> BrokenClient:
            raise RuntimeError("client setup failed")

        async def __aexit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(bluray.httpx, "AsyncClient", lambda *_args, **_kwargs: BrokenClient())
    assert asyncio.run(bluray.get_bluray_releases(meta)) == []


def test_get_bluray_releases_interactive_all_skip_invalid_selected_images_and_cancel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    meta = _meta(tmp_path, unattended=False, unattended_confirm=False, use_bluray_images=True)
    releases = [
        {"title": "One", "url": "https://one", "country": "United States", "publisher": "Criterion", "price": "$1"},
        {"title": "Two", "url": "https://two", "country": "United Kingdom", "publisher": "Arrow", "price": "$2"},
    ]
    movie = {"title": "Movie", "year": "2026", "releases_url": "https://www.blu-ray.com/movies/Movie/123/#Releases"}
    monkeypatch.setattr(bluray, "search_bluray", AsyncMock(return_value="search"))
    monkeypatch.setattr(bluray, "extract_bluray_links", lambda _html: [movie])
    monkeypatch.setattr(bluray, "extract_product_id", AsyncMock(return_value="123"))
    monkeypatch.setattr(bluray, "extract_bluray_release_info", AsyncMock(return_value=[dict(value) for value in releases]))
    _Client.reset(_Response(200, "release html"))
    process = AsyncMock(return_value=releases)
    monkeypatch.setattr(bluray, "process_all_releases", process)
    monkeypatch.setattr(bluray.cli_ui, "ask_string", lambda *_args, **_kwargs: "a")
    assert asyncio.run(bluray.get_bluray_releases(meta)) == releases

    _Client.reset(_Response(200, "release html"))
    monkeypatch.setattr(bluray.cli_ui, "ask_string", lambda *_args, **_kwargs: "n")
    assert asyncio.run(bluray.get_bluray_releases(meta)) == []

    _Client.reset(_Response(200, "release html"))
    answers = iter(("bad", "9", "2"))
    monkeypatch.setattr(bluray.cli_ui, "ask_string", lambda *_args, **_kwargs: next(answers))
    detailed = {**releases[1], "cover_images": {"front": "https://cover"}}
    monkeypatch.setattr(bluray, "fetch_release_details", AsyncMock(return_value=detailed))
    download = AsyncMock(return_value=True)
    monkeypatch.setattr(bluray, "download_cover_images", download)
    result = asyncio.run(bluray.get_bluray_releases(meta))
    assert result == [detailed]
    assert meta.region == "GBR" and meta.distributor == "ARROW" and meta.release_url == "https://two"
    assert meta.bluray_cover_urls == {"front": "https://cover"}
    download.assert_awaited_once_with(meta)

    _Client.reset(_Response(200, "release html"))
    monkeypatch.setattr(bluray.cli_ui, "ask_string", lambda *_args, **_kwargs: (_ for _ in ()).throw(EOFError()))
    with pytest.raises(OperationAbortedError, match="cancelled"):
        asyncio.run(bluray.get_bluray_releases(meta))


def test_get_bluray_releases_no_matches_debug_save_and_write_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    meta = _meta(tmp_path, imdb_id=None, title=None)
    monkeypatch.setattr(bluray, "search_bluray", AsyncMock(return_value="search"))
    monkeypatch.setattr(bluray, "extract_bluray_links", lambda _html: [{"title": "Invalid", "year": "", "releases_url": "invalid"}])
    monkeypatch.setattr(bluray, "extract_product_id", AsyncMock(return_value=None))
    assert asyncio.run(bluray.get_bluray_releases(meta)) == []

    original_write = Path.write_text

    def fail_write(path: Path, *_args: object, **_kwargs: object) -> int:
        if "bluray_results" in path.name:
            raise OSError("write failed")
        return original_write(path, *_args, **_kwargs)

    monkeypatch.setattr(Path, "write_text", fail_write)
    assert asyncio.run(bluray.get_bluray_releases(meta)) == []


def _match_meta(
    tmp_path: Path,
    *,
    key: str,
    size: float = 23.2,
    video: dict[str, str] | None = None,
    audio: list[dict[str, str]] | None = None,
    subtitles: tuple[str, ...] = ("English",),
    interactive: bool = False,
    score: float = -1000,
    single_score: float = -1000,
) -> Meta:
    uuid = f"match-{key}"
    temp = tmp_path / "tmp" / uuid
    temp.mkdir(parents=True, exist_ok=True)
    if subtitles:
        (temp / "BD_SUMMARY_00.txt").write_text(
            "\n".join(f"Subtitle: {language} / 10.0 kbps" for language in subtitles),
            encoding="utf-8",
        )
    return Meta(
        base_dir=str(tmp_path),
        uuid=uuid,
        category="MOVIE",
        title="Example",
        name="Example",
        debug=True,
        unattended=not interactive,
        unattended_confirm=interactive,
        bluray_score=score,
        bluray_single_score=single_score,
        use_bluray_images=True,
        discs=[
            {
                "type": "BDMV",
                "bdinfo": {
                    "size": size,
                    "video": [video or {"codec": "AVC", "res": "1080p"}],
                    "audio": audio
                    if audio is not None
                    else [
                        {
                            "language": "English",
                            "codec": "DTS-HD MA",
                            "channels": "5.1",
                            "sample_rate": "48 kHz",
                            "bit_depth": "24-bit",
                            "bitrate": "3500 kbps",
                        }
                    ],
                },
            }
        ],
    )


def _match_release(
    title: str,
    *,
    release_format: str = "BD-25",
    codec: str = "H.264 AVC",
    resolution: str = "1080p",
    audio: list[str] | None = None,
    subtitles: list[str] | None = None,
    specs: bool = True,
    cover: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "title": title,
        "country": "United States",
        "publisher": "Publisher",
        "url": f"https://release.invalid/{title}",
    }
    if cover:
        result["cover_images"] = {"front": "https://images.invalid/front.jpg"}
    if specs:
        result["specs"] = {
            "video": {"codec": codec, "resolution": resolution},
            "audio": audio if audio is not None else ["English DTS-HD MA 5.1 48 kHz 24-bit 3500 kbps"],
            "subtitles": subtitles if subtitles is not None else ["English"],
            "discs": {"format": release_format},
        }
    return result


def _identity_details(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    fetch = AsyncMock(side_effect=lambda release, _meta: release)
    monkeypatch.setattr(bluray, "fetch_release_details", fetch)
    monkeypatch.setattr(bluray, "download_cover_images", AsyncMock(return_value=True))
    return fetch


def test_remaining_release_info_parse_and_cover_branches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    meta = _meta(tmp_path)
    invalid_html = """
    <table><tr><td><h3>Blu-ray Editions</h3>
      <a href="https://www.blu-ray.com/movies/Invalid/not-a-number/">Invalid ID</a>
      <a href="https://www.blu-ray.com/movies/Broken/123/">Broken</a>
      <h3>End</h3>
    </td></tr></table>
    """
    original_search = bluray.re.search

    def flaky_search(pattern: object, value: object, *args: object, **kwargs: object):
        if "Broken" in str(value):
            raise RuntimeError("release parse failed")
        return original_search(pattern, value, *args, **kwargs)

    monkeypatch.setattr(bluray.re, "search", flaky_search)
    assert asyncio.run(bluray.extract_bluray_release_info(invalid_html, meta, "1")) == []
    monkeypatch.setattr(bluray.re, "search", original_search)

    five_one = _specs_html().replace("English: Dolby TrueHD 7.1", "English: Dolby Digital 5.1")
    parsed = asyncio.run(bluray.parse_release_details(five_one, {"title": "5.1"}, _meta(tmp_path, use_bluray_images=False)))
    assert "5.1" in parsed["specs"]["audio"][0]

    digit = _specs_html(multi="3-disc set")
    parsed = asyncio.run(bluray.parse_release_details(digit, {"title": "Digit"}, meta))
    assert parsed["specs"]["discs"] == {"type": "Ultra HD Blu-ray", "count": 3, "format": "multiple discs"}

    unknown_format = _specs_html(multi="Two-disc set (DVD extras)")
    parsed = asyncio.run(bluray.parse_release_details(unknown_format, {"title": "Unknown"}, meta))
    assert parsed["specs"]["discs"]["format"] == "multiple discs"

    images = bluray.extract_cover_images("<script></script><div class='simple_overlay'><img id='' src='' /></div>")
    assert images == {}


def test_get_releases_invalid_cache_and_cache_read_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    meta = _meta(tmp_path)
    movie = {"title": "Movie", "year": "2026", "releases_url": "https://www.blu-ray.com/movies/Movie/123/#Releases"}
    monkeypatch.setattr(bluray, "search_bluray", AsyncMock(return_value="search"))
    monkeypatch.setattr(bluray, "extract_bluray_links", lambda _html: [movie])
    monkeypatch.setattr(bluray, "extract_product_id", AsyncMock(return_value="123"))
    monkeypatch.setattr(bluray, "extract_bluray_release_info", AsyncMock(return_value=[]))
    cache = tmp_path / "tmp" / meta.uuid / "debug_bluray_BD_123.html"
    cache.write_text("No index", encoding="utf-8")
    _Client.reset(_Response(200, "fresh"))
    assert asyncio.run(bluray.get_bluray_releases(meta)) == []

    cache.write_text("stale", encoding="utf-8")
    original_read = Path.read_text

    def fail_read(path: Path, *_args: object, **_kwargs: object) -> str:
        if path == cache:
            raise OSError("read failed")
        return original_read(path, *_args, **_kwargs)

    monkeypatch.setattr(Path, "read_text", fail_read)
    _Client.reset(_Response(200, "fresh"))
    assert asyncio.run(bluray.get_bluray_releases(meta)) == []


def test_process_local_summary_missing_invalid_and_incomplete_specs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _identity_details(monkeypatch)
    release = _match_release("Incomplete")
    release["specs"] = {"video": {}, "audio": [], "subtitles": [], "discs": {}}

    missing = _match_meta(tmp_path, key="missing-summary", subtitles=())
    result = asyncio.run(bluray.process_all_releases([dict(release)], missing))
    assert result

    low = _match_meta(tmp_path, key="low-summary", subtitles=())
    summary = tmp_path / "tmp" / low.uuid / "BD_SUMMARY_00.txt"
    summary.write_text("Subtitle: English / 0.5 kbps", encoding="utf-8")
    asyncio.run(bluray.process_all_releases([dict(release)], low))

    broken = _match_meta(tmp_path, key="broken-summary")
    summary = tmp_path / "tmp" / broken.uuid / "BD_SUMMARY_00.txt"
    original_read = Path.read_text

    def fail_read(path: Path, *_args: object, **_kwargs: object) -> str:
        if path == summary:
            raise OSError("read failed")
        return original_read(path, *_args, **_kwargs)

    monkeypatch.setattr(Path, "read_text", fail_read)
    asyncio.run(bluray.process_all_releases([dict(release)], broken))


def test_process_disc_size_codec_and_resolution_branches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _identity_details(monkeypatch)
    scenarios = [
        (30.0, {"codec": "AVC", "res": "1080p"}, "BD-25", "H.264 AVC", "1080p"),
        (55.0, {"codec": "HEVC", "res": "2160p"}, "BD-66", "H.265 HEVC", "4K 2160p"),
        (70.0, {"codec": "VC-1", "res": "1080p"}, "BD-100", "VC-1", "1080p"),
        (30.0, {"codec": "MPEG-2", "res": "1080p"}, "BD-50", "MPEG-2", "1080p"),
    ]
    for index, (size, video, release_format, codec, resolution) in enumerate(scenarios):
        meta = _match_meta(tmp_path, key=f"video-{index}", size=size, video=video)
        release = _match_release(
            f"Video {index}",
            release_format=release_format,
            codec=codec,
            resolution=resolution,
            cover=True,
        )
        result = asyncio.run(bluray.process_all_releases([release], meta))
        assert result


def test_process_atmos_partial_missing_audio_and_subtitle_branches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _identity_details(monkeypatch)
    atmos = [
        {
            "language": "English",
            "codec": "Dolby TrueHD",
            "channels": "Atmos",
            "sample_rate": "7.1",
            "bit_depth": "24-bit 4000 kbps",
            "bitrate": "48 kHz",
            "atmos_why_you_be_like_this": "Atmos",
        },
        {
            "language": "French",
            "codec": "Dolby Digital Audio",
            "channels": "Atmos Audio",
            "sample_rate": "5.1",
            "bit_depth": "DN -27 dB",
            "bitrate": "192 kbps",
            "atmos_why_you_be_like_this": "Atmos",
        },
        {
            "language": "Spanish",
            "codec": "DTS-HD MA",
            "channels": "2.0",
            "sample_rate": "48 kHz",
            "bit_depth": "24-bit",
            "bitrate": "128 kbps",
        },
    ]
    meta = _match_meta(tmp_path, key="atmos", audio=atmos, subtitles=("English", "French"))
    release = _match_release(
        "Atmos",
        audio=[
            "English Dolby TrueHD Atmos 7.1 48 kHz 24-bit 4000 kbps",
            "French Dolby Atmos 5.1",
            "Spanish DTS-HD MA mono",
            "Japanese LPCM 2.0",
        ],
        subtitles=["English", "Spanish"],
    )
    assert asyncio.run(bluray.process_all_releases([release], meta))

    single_partial = _match_meta(
        tmp_path,
        key="single-partial",
        audio=[{"language": "English", "codec": "DTS-HD MA", "channels": "5.1", "sample_rate": "", "bit_depth": "", "bitrate": ""}],
    )
    partial = _match_release("Partial", audio=["English DTS-HD MA 2.0"])
    assert asyncio.run(bluray.process_all_releases([partial], single_partial))

    single_missing = _match_meta(
        tmp_path,
        key="single-missing",
        audio=[{"language": "English", "codec": "DTS-HD MA", "channels": "5.1", "sample_rate": "", "bit_depth": "", "bitrate": ""}],
    )
    missing = _match_release("Missing", audio=["French LPCM 2.0"], subtitles=[])
    assert asyncio.run(bluray.process_all_releases([missing], single_missing))

    no_audio = _match_meta(tmp_path, key="no-audio", audio=[], subtitles=())
    no_compare = _match_release("No Compare", audio=[], subtitles=[])
    assert asyncio.run(bluray.process_all_releases([no_compare], no_audio))


def test_process_multiple_close_interactive_error_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _identity_details(monkeypatch)
    first = _match_release("First", specs=False)
    second = _match_release("Second", specs=False)

    # Explicit skip and missing-specs warning.
    meta = _match_meta(tmp_path, key="close-skip", interactive=True)
    monkeypatch.setattr(bluray.cli_ui, "ask_string", lambda *_args, **_kwargs: "n")
    assert asyncio.run(bluray.process_all_releases([dict(first), dict(second)], meta)) == []

    # Log-selection branch: invalid index, non-number, KeyboardInterrupt, then
    # valid release selection with a cover.
    second["cover_images"] = {"front": "https://images.invalid/front.jpg"}
    meta = _match_meta(tmp_path, key="close-errors", interactive=True)
    answers = iter(("p", "9", "p", "bad", "p", "1", "bad", "9", "2"))

    def answer(*_args: object, **_kwargs: object) -> str:
        value = next(answers)
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr(bluray.cli_ui, "ask_string", answer)
    result = asyncio.run(bluray.process_all_releases([dict(first), dict(second)], meta))
    assert result and meta.release_url.endswith("Second")

    cancel_meta = _match_meta(tmp_path, key="close-log-cancel", interactive=True)
    cancel_answers = iter(("p", KeyboardInterrupt()))

    def cancel_answer(*_args: object, **_kwargs: object) -> str:
        value = next(cancel_answers)
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr(bluray.cli_ui, "ask_string", cancel_answer)
    assert asyncio.run(bluray.process_all_releases([dict(first), dict(second)], cancel_meta))
    assert cancel_meta.release_url == ""


def test_process_single_and_best_only_unattended_and_interactive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _identity_details(monkeypatch)
    imperfect = _match_release("Imperfect", release_format="BD-50", cover=True)

    high = _match_meta(tmp_path, key="single-high", single_score=-1000)
    assert asyncio.run(bluray.process_all_releases([dict(imperfect)], high))
    assert high.release_url.endswith("Imperfect")

    low = _match_meta(tmp_path, key="single-low", single_score=101)
    assert asyncio.run(bluray.process_all_releases([dict(imperfect)], low)) == []

    # Two releases more than 40 points apart enter the isolated-best branch.
    best = _match_release("Best", cover=True)
    bad = _match_release("Bad", release_format="BD-100", codec="MPEG2", resolution="480p", audio=[], subtitles=[])
    for suffix, answer, expected in (("yes", True, True), ("no", False, False)):
        meta = _match_meta(tmp_path, key=f"best-{suffix}", interactive=True)
        monkeypatch.setattr(bluray.cli_ui, "ask_yes_no", lambda *_args, value=answer, **_kwargs: value)
        result = asyncio.run(bluray.process_all_releases([dict(best), dict(bad)], meta))
        assert bool(result) is expected

    meta = _match_meta(tmp_path, key="best-cancel", interactive=True)
    monkeypatch.setattr(bluray.cli_ui, "ask_yes_no", lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert asyncio.run(bluray.process_all_releases([dict(best), dict(bad)], meta))

    unattended = _match_meta(tmp_path, key="best-unattended", score=-1000)
    assert asyncio.run(bluray.process_all_releases([dict(best), dict(bad)], unattended))
    assert unattended.release_url.endswith("Best")
    rejected = _match_meta(tmp_path, key="best-rejected", score=101)
    assert asyncio.run(bluray.process_all_releases([dict(best), dict(bad)], rejected)) == []


def test_process_multiple_close_unattended_cover(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _identity_details(monkeypatch)
    one = _match_release("One", release_format="BD", cover=True)
    two = _match_release("Two", release_format="BD", cover=True)
    meta = _match_meta(tmp_path, key="close-unattended", score=-1000)
    assert asyncio.run(bluray.process_all_releases([one, two], meta))
    assert meta.bluray_cover_urls


def test_process_remaining_video_and_keyboard_cancellation_branches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _identity_details(monkeypatch)

    no_video = _match_meta(tmp_path, key="no-video-specs")
    no_video.discs[0]["bdinfo"]["video"] = []
    assert asyncio.run(bluray.process_all_releases([_match_release("No Video")], no_video))

    single = _match_meta(tmp_path, key="single-keyboard", interactive=True)
    imperfect = _match_release("Keyboard", release_format="BD-50")
    monkeypatch.setattr(bluray.cli_ui, "ask_yes_no", lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert asyncio.run(bluray.process_all_releases([imperfect], single))
    assert single.release_url == ""

    class InterruptSelection:
        def strip(self) -> InterruptSelection:
            return self

        def lower(self) -> InterruptSelection:
            return self

        def __eq__(self, _other: object) -> bool:
            return False

        def __int__(self) -> int:
            raise KeyboardInterrupt

    first = _match_release("First", specs=False)
    second = _match_release("Second", specs=False)
    multiple = _match_meta(tmp_path, key="multiple-keyboard", interactive=True)
    monkeypatch.setattr(bluray.cli_ui, "ask_string", lambda *_args, **_kwargs: InterruptSelection())
    assert asyncio.run(bluray.process_all_releases([first, second], multiple))
    assert multiple.release_url == ""
