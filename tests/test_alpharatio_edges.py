from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.alpharatio import AlphaRatio

ar_module = importlib.import_module("src.integrations.trackers.alpharatio")


def _config(*, images: bool = False) -> dict[str, Any]:
    config: dict[str, Any] = {
        "DEFAULT": {},
        "TRACKERS": {"ALPHARATIO": {"username": " user ", "password": " pass "}},
    }
    if images:
        config["IMAGES"] = {
            "imdb_75": "https://img.invalid/imdb.png",
            "tmdb_75": "https://img.invalid/tmdb.png",
            "tvdb_75": "https://img.invalid/tvdb.png",
            "tvmaze_75": "https://img.invalid/tvmaze.png",
            "mal_75": "https://img.invalid/mal.png",
        }
    return config


def _tracker(*, images: bool = False) -> AlphaRatio:
    return AlphaRatio(_config(images=images))


def _meta(tmp_path: Path | None = None, **values: object) -> Meta:
    base = str(tmp_path or Path())
    state: dict[str, object] = {
        "base_dir": base,
        "uuid": "Release.Name.1080p.mkv",
        "name": "Release Name 1080p",
        "title": "Release Name",
        "category": "MOVIE",
        "type": "WEBDL",
        "source": "WEB",
        "resolution": "1080p",
        "anime": False,
        "sd": False,
        "adult_media": False,
        "tv_pack": False,
        "is_disc": "",
        "imdb_id": 123,
        "imdb": 123,
        "imdb_info": {"imdb_url": "https://imdb.invalid/title/tt123", "cover": "https://cover.invalid/poster.jpg"},
        "tmdb": 456,
        "tvdb_id": 789,
        "tvmaze_id": 10,
        "mal_id": 11,
        "year": 2024,
        "discs": [],
        "filelist": ["release.mkv"],
        "path": "release.mkv",
        "overview": "Plot",
        "genres": ["Drama"],
        "image_list": [],
        "youtube": "https://youtube.invalid/watch?v=1",
        "bdinfo": {},
        "scene": False,
        "scene_name": "",
        "tag": "-GROUP",
        "artwork_url": "https://cover.invalid/poster.jpg",
        "unattended": False,
        "unattended_confirm": False,
        "ua_name": "Upload Assistant",
        "current_version": "1.0",
        "tracker_status": {"ALPHARATIO": {}},
    }
    state.update(values)
    return Meta(state)


def _response(payload: Any = None, *, text: str | None = None, status: int = 200, url: str = "https://alpharatio.cc/ajax.php") -> httpx.Response:
    request = httpx.Request("GET", url)
    if text is not None:
        return httpx.Response(status, request=request, text=text)
    return httpx.Response(status, request=request, json=payload)


@pytest.mark.asyncio
async def test_alpharatio_type_matrix_remaining_paths() -> None:
    tracker = _tracker()
    assert await tracker.get_type(_meta(type="DISC", source="Blu-ray")) == "14"
    assert await tracker.get_type(_meta(anime=True, sd=True)) == "15"
    assert await tracker.get_type(_meta(anime=True, resolution="2160p")) == "16"
    assert await tracker.get_type(_meta(category="TV", tv_pack=True, sd=True)) == "4"
    assert await tracker.get_type(_meta(category="TV", tv_pack=True, resolution="2160p")) == "6"
    assert await tracker.get_type(_meta(category="TV", tv_pack=True, resolution="1080p")) == "5"
    assert await tracker.get_type(_meta(category="TV", tv_pack=False, sd=True)) == "0"
    assert await tracker.get_type(_meta(category="TV", tv_pack=False, resolution="2160p")) == "2"
    assert await tracker.get_type(_meta(category="TV", tv_pack=False, resolution="1080p")) == "1"
    assert await tracker.get_type(_meta(category="MOVIE", sd=True)) == "7"
    assert await tracker.get_type(_meta(category="MOVIE", adult_media=True)) == "13"
    assert await tracker.get_type(_meta(category="MOVIE", resolution="2160p")) == "9"
    assert await tracker.get_type(_meta(category="OTHER")) == "7"


@pytest.mark.asyncio
async def test_alpharatio_validate_credentials_paths() -> None:
    tracker = _tracker()
    tracker.cookie_validator.load_session_cookies = AsyncMock(return_value=None)  # type: ignore[method-assign]
    assert not await tracker.validate_credentials(_meta())
    tracker.cookie_validator.load_session_cookies = AsyncMock(return_value=httpx.Cookies({"sid": "1"}))  # type: ignore[method-assign]
    assert await tracker.validate_credentials(_meta())


def test_alpharatio_metadata_links_plain_and_images() -> None:
    item = _meta()
    plain = _tracker().get_links(item, "<h>", "</h>")
    assert "https://imdb.invalid" in plain
    assert "themoviedb.org" in plain
    assert "thetvdb.com" in plain
    assert "tvmaze.com" in plain
    assert "myanimelist.net" in plain

    imaged = _tracker(images=True).get_links(item, "<h>", "</h>")
    assert "[img]https://img.invalid/imdb.png[/img]" in imaged
    assert "[url=https://www.themoviedb.org/movie/456]" in imaged

    empty = _meta(imdb_id=0, tmdb=0, tvdb_id=0, tvmaze_id=0, mal_id=0)
    assert _tracker()._plain_metadata_links(empty) == []
    assert AlphaRatio._imdb_url(_meta(imdb_info="bad")) == ""


@pytest.mark.asyncio
async def test_alpharatio_edit_desc_disc_variants(tmp_path: Path) -> None:
    tracker = _tracker()
    multi = _meta(
        tmp_path,
        is_disc="BDMV",
        discs=[
            {"type": "BDMV", "summary": "MAIN"},
            {"type": "BDMV", "name": "DISC2", "summary": "SECOND"},
            {"type": "DVD", "name": "DVD", "vob": "/x/VTS_01.VOB", "vob_mi": "VOBMI", "ifo": "/x/VTS_01.IFO", "ifo_mi": "IFOMI"},
        ],
    )
    await tracker.edit_desc(multi)
    text = (tmp_path / "tmp" / "Release.Name.1080p.mkv" / "[ALPHARATIO]DESCRIPTION.txt").read_text(encoding="utf-8")
    assert "SECOND" in text
    assert "VOBMI" in text

    single_dvd = _meta(tmp_path, uuid="dvd", is_disc="DVD", discs=[{"type": "DVD", "vob_mi": "DVDMI"}])
    await tracker.edit_desc(single_dvd)
    assert "DVDMI" in (tmp_path / "tmp" / "dvd" / "[ALPHARATIO]DESCRIPTION.txt").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_alpharatio_edit_desc_file_normal_and_template(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    root = tmp_path / "tmp" / "file"
    root.mkdir(parents=True)
    (root / "MEDIAINFO_CLEANPATH.txt").write_text("FULL MI", encoding="utf-8")
    item = _meta(tmp_path, uuid="file", filelist=["movie.mkv"], image_list=[{"raw_url": "https://raw.invalid/1.png", "img_url": "https://thumb.invalid/1.png"}])
    await tracker.edit_desc(item)
    text = (root / "[ALPHARATIO]DESCRIPTION.txt").read_text(encoding="utf-8")
    assert "FULL MI" in text
    assert "Screenshots" in text
    assert "Youtube" in text

    template = tmp_path / "data" / "templates" / "summary-mediainfo.csv"
    template.parent.mkdir(parents=True)
    template.write_text("template", encoding="utf-8")
    monkeypatch.setattr(tracker, "parse_mediainfo_async", AsyncMock(return_value="SHORT MI"))
    await tracker.edit_desc(item)
    text = (root / "[ALPHARATIO]DESCRIPTION.txt").read_text(encoding="utf-8")
    assert "SHORT MI" in text
    assert "FULL MEDIAINFO" in text

    assert tracker._screenshot_block(_meta(image_list=[]), "<h>", "</h>") == ""


@pytest.mark.asyncio
async def test_alpharatio_language_tag_bdmv_and_mediainfo(tmp_path: Path) -> None:
    tracker = _tracker()
    english = _meta(is_disc="BDMV", bdinfo={"audio": [{"language": "English"}, {"language": "French"}]})
    assert await tracker.get_language_tag(english) == ""
    french = _meta(is_disc="BDMV", bdinfo={"audio": [{"language": "French"}]})
    assert await tracker.get_language_tag(french) == "FRENCH"
    assert tracker._bdmv_language_tag(_meta(is_disc="BDMV", bdinfo={"audio": []})) == ""

    root = tmp_path / "tmp" / "mi"
    root.mkdir(parents=True)
    (root / "MediaInfo.json").write_text(
        '{"media":{"track":[{"@type":"General"},{"@type":"Audio","Language":"fr","Language_String":"French"}]}}',
        encoding="utf-8",
    )
    assert await tracker.get_language_tag(_meta(tmp_path, uuid="mi")) == "FRENCH"
    (root / "MediaInfo.json").write_text(
        '{"media":{"track":[{"@type":"Audio","Language":"en-US","Language_String":"English"}]}}',
        encoding="utf-8",
    )
    assert await tracker.get_language_tag(_meta(tmp_path, uuid="mi")) == ""
    (root / "MediaInfo.json").write_text("bad-json", encoding="utf-8")
    assert await tracker.get_language_tag(_meta(tmp_path, uuid="mi")) == ""
    assert tracker._media_mapping([]) == {}


@pytest.mark.asyncio
async def test_alpharatio_search_cookie_title_login_and_results(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    tracker.cookie_validator.load_session_cookies = AsyncMock(return_value=None)  # type: ignore[method-assign]
    assert await tracker.search_existing(_meta()) == []

    cookies = httpx.Cookies({"sid": "1"})
    tracker.cookie_validator.load_session_cookies = AsyncMock(return_value=cookies)  # type: ignore[method-assign]
    assert await tracker.search_existing(_meta(title="")) == []

    login = _response(text="login.php", url="https://alpharatio.cc/login.php")
    tracker.cookie_validator.handle_validation_failure = AsyncMock()  # type: ignore[method-assign]
    monkeypatch.setattr(tracker, "_search_response", AsyncMock(return_value=login))
    item = _meta()
    assert await tracker.search_existing(item) == []
    assert item.skipping == "ALPHARATIO"

    payload = {
        "status": "success",
        "response": {
            "results": [
                {"groupName": "Release", "size": "1 GB", "fileCount": 2, "groupId": 3, "torrentId": 4},
                {"noGroupName": "skip"},
            ]
        },
    }
    monkeypatch.setattr(tracker, "_search_response", AsyncMock(return_value=_response(payload)))
    result = await tracker.search_existing(_meta())
    assert result[0]["name"] == "Release"
    assert result[0]["link"].endswith("?id=3&torrentid=4")
    assert tracker._search_result("bad") is None

    with pytest.raises(RuntimeError, match="invalid response"):
        tracker._successful_search_payload([])
    with pytest.raises(RuntimeError, match="unsuccessful status"):
        tracker._successful_search_payload({"status": "failure", "error": "bad"})


def test_alpharatio_user_agent_and_search_query() -> None:
    assert AlphaRatio._search_query(_meta(title="Example", year=None)) == "Example"
    assert AlphaRatio._user_agent_header(_meta(current_version=None))["User-Agent"].endswith("github.com/wastaken7/Upload-Assistant")


@pytest.mark.asyncio
async def test_alpharatio_auth_key_saved_missing_and_fetched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    tracker.cookie_validator.get_ar_auth_key = AsyncMock(return_value="saved")  # type: ignore[method-assign]
    assert await tracker.get_auth_key(_meta(tmp_path)) == "saved"

    tracker.cookie_validator.get_ar_auth_key = AsyncMock(return_value=None)  # type: ignore[method-assign]
    tracker.cookie_validator.load_session_cookies = AsyncMock(return_value=None)  # type: ignore[method-assign]
    assert await tracker.get_auth_key(_meta(tmp_path)) is None

    tracker.cookie_validator.load_session_cookies = AsyncMock(return_value=httpx.Cookies({"sid": "1"}))  # type: ignore[method-assign]
    monkeypatch.setattr(tracker, "_fetch_auth_key", AsyncMock(return_value="fetched"))
    assert await tracker.get_auth_key(_meta(tmp_path)) == "fetched"


def test_alpharatio_extract_auth_key() -> None:
    assert AlphaRatio._extract_auth_key('<a href="logout.php?auth=secret&x=1">Logout</a>') == "secret"
    assert AlphaRatio._extract_auth_key("<html></html>") is None
    assert AlphaRatio._extract_auth_key('<a href="logout.php">Logout</a>') is None


@pytest.mark.asyncio
async def test_alpharatio_fetch_and_save_auth_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    response = _response(text='<a href="logout.php?auth=fresh&x=1">Logout</a>')
    monkeypatch.setattr(tracker, "_auth_page", AsyncMock(return_value=response))
    monkeypatch.setattr(tracker, "_save_auth_key", AsyncMock())
    assert await tracker._fetch_auth_key(_meta(tmp_path), httpx.Cookies()) == "fresh"
    tracker._save_auth_key.assert_awaited_once()

    monkeypatch.setattr(tracker, "_auth_page", AsyncMock(return_value=_response(text="<html></html>")))
    assert await tracker._fetch_auth_key(_meta(tmp_path), httpx.Cookies()) is None

    monkeypatch.setattr(tracker, "_auth_page", AsyncMock(side_effect=httpx.RequestError("offline")))
    assert await tracker._fetch_auth_key(_meta(tmp_path), httpx.Cookies()) is None


@pytest.mark.asyncio
async def test_alpharatio_save_auth_key_handles_ioerror(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cookie_file = tmp_path / "ALPHARATIO.txt"
    monkeypatch.setattr("src.integrations.trackers.cookie_auth.find_cookie_file", lambda *_args, **_kwargs: str(cookie_file))
    tracker = _tracker()
    await tracker._save_auth_key(_meta(tmp_path), "secret")
    assert (tmp_path / "ALPHARATIO_auth.txt").read_text(encoding="utf-8") == "secret"

    monkeypatch.setattr(ar_module.aiofiles, "open", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("broken")))
    await tracker._save_auth_key(_meta(tmp_path), "secret")


@pytest.mark.asyncio
async def test_alpharatio_upload_failure_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    tracker.common.create_torrent_for_upload = AsyncMock()  # type: ignore[method-assign]
    monkeypatch.setattr(tracker, "edit_desc", AsyncMock())

    item = _meta(tmp_path, uuid="missing", artwork_url="https://cover.invalid/poster.jpg")
    assert not await tracker.upload(item)
    assert "Description file not found" in item.tracker_status["ALPHARATIO"]["status_message"]

    root = tmp_path / "tmp" / "release"
    root.mkdir(parents=True, exist_ok=True)
    (root / "[ALPHARATIO]DESCRIPTION.txt").write_text("description", encoding="utf-8")
    unattended = _meta(tmp_path, uuid="release", artwork_url="", imdb_info={}, unattended=True, unattended_confirm=False)
    assert not await tracker.upload(unattended)
    assert unattended.skipping == "ALPHARATIO"

    monkeypatch.setattr(tracker, "get_auth_key", AsyncMock(return_value=None))
    no_auth = _meta(tmp_path, uuid="release", artwork_url="https://cover.invalid/poster.jpg")
    assert not await tracker.upload(no_auth)
    assert "Failed to extract auth key" in no_auth.tracker_status["ALPHARATIO"]["status_message"]

    monkeypatch.setattr(tracker, "get_auth_key", AsyncMock(return_value="auth"))
    tracker.cookie_validator.load_session_cookies = AsyncMock(return_value=None)  # type: ignore[method-assign]
    no_cookie = _meta(tmp_path, uuid="release", artwork_url="https://cover.invalid/poster.jpg")
    assert not await tracker.upload(no_cookie)
    assert "Failed to load cookies" in no_cookie.tracker_status["ALPHARATIO"]["status_message"]


@pytest.mark.asyncio
async def test_alpharatio_upload_success_and_cover_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    root = tmp_path / "tmp" / "release"
    root.mkdir(parents=True)
    (root / "[ALPHARATIO]DESCRIPTION.txt").write_text("description", encoding="utf-8")
    tracker.common.create_torrent_for_upload = AsyncMock()  # type: ignore[method-assign]
    monkeypatch.setattr(tracker, "edit_desc", AsyncMock())
    monkeypatch.setattr(tracker, "get_auth_key", AsyncMock(return_value="auth"))
    tracker.cookie_validator.load_session_cookies = AsyncMock(return_value=httpx.Cookies({"sid": "1"}))  # type: ignore[method-assign]
    monkeypatch.setattr(tracker, "_submit_upload", AsyncMock(return_value=True))
    item = _meta(tmp_path, uuid="release", genres="Drama & Crime, Sci..Fi")
    assert await tracker.upload(item)
    sent = tracker._submit_upload.await_args.args[1]
    assert sent["auth"] == "auth"
    assert "Drama, Crime, Sci.Fi" in sent["tags"]

    answers = iter(("bad", "https://cover.invalid/valid.jpg"))

    async def prompt(*_args: object, **_kwargs: object) -> str:
        return next(answers)

    monkeypatch.setattr(ar_module, "prompt_in_thread", prompt)
    assert await tracker._prompt_cover() == "https://cover.invalid/valid.jpg"
    assert tracker._valid_cover_url("https://x.invalid/a.PNG")


@pytest.mark.asyncio
async def test_alpharatio_upload_data_and_submit() -> None:
    tracker = _tracker()
    data = await tracker._upload_data(_meta(), "description", "cover", "auth")
    assert data["type"] == "8"
    assert data["image"] == "cover"
    tracker.cookie_uploader.handle_upload = AsyncMock(return_value=True)  # type: ignore[method-assign]
    assert await tracker._submit_upload(_meta(), data, httpx.Cookies())
    call = tracker.cookie_uploader.handle_upload.await_args.kwargs
    assert call["torrent_field_name"] == "file_input"


def test_alpharatio_genre_and_name_helpers() -> None:
    assert AlphaRatio._genre_tags(["Drama", " ", "Crime"]) == "Drama, Crime"
    assert AlphaRatio._genre_tags("Drama & Crime, Sci..Fi") == "Drama, Crime, Sci.Fi"
    assert AlphaRatio._valid_group_tag(None) is False
    assert AlphaRatio._ensure_group_name("Release-NoGrp", "-NoGrp") == "Release-NoGRP"
    assert asyncio.run(_tracker().get_name(_meta(scene=True, scene_name="Scene.Release-GROUP", tag="-GROUP"))) == "Scene.Release-GROUP"
    normalized = asyncio.run(_tracker().get_name(_meta(uuid="Movie Name (2024).mkv", tag="")))
    assert normalized.endswith("-NoGRP")
    assert "Movie.Name.2024" in normalized


def test_alpharatio_extract_auth_key_rejects_non_string_href(monkeypatch: pytest.MonkeyPatch) -> None:
    class Link:
        def get(self, _key: str) -> list[str]:
            return ["logout.php?auth=x"]

    class Soup:
        def find(self, *_args: object, **_kwargs: object) -> Link:
            return Link()

    monkeypatch.setattr(ar_module, "BeautifulSoup", lambda *_args, **_kwargs: Soup())
    assert AlphaRatio._extract_auth_key("ignored") is None
