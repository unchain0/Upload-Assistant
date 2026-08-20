from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any
from unittest.mock import ANY, AsyncMock

import httpx
import pytest
from bs4 import BeautifulSoup

from src.domain_models.processing import UploadError
from src.domain_models.release import Meta
from src.integrations.trackers.totheglory import ToTheGlory

ttg_module = importlib.import_module("src.integrations.trackers.totheglory")


def _config(**values: object) -> dict[str, Any]:
    tracker: dict[str, Any] = {
        "username": "user",
        "password": "pass",
        "login_question": "1",
        "login_answer": "answer",
        "user_id": "42",
        "announce_url": "https://tracker.invalid/passkey",
        "anon": False,
    }
    tracker.update(values)
    return {"TRACKERS": {"TOTHEGLORY": tracker}, "DEFAULT": {}}


def _tracker(**values: object) -> ToTheGlory:
    return ToTheGlory(_config(**values))


def _meta(tmp_path: Path | None = None, **values: object) -> Meta:
    root = tmp_path or Path()
    state: dict[str, object] = {
        "base_dir": str(root),
        "uuid": "release",
        "name": "Example.Movie.2010.1080p.WEB-DL-GROUP",
        "title": "Example Movie",
        "category": "MOVIE",
        "resolution": "1080p",
        "original_language": "EN",
        "tv_pack": 0,
        "is_disc": "",
        "genres": ["Drama"],
        "keywords": [],
        "sd": 0,
        "anon": 0,
        "imdb_id": 123,
        "imdb": 123,
        "bdinfo": {},
        "filelist": [str(root / "movie.mkv")],
        "video": str(root / "movie.mkv"),
        "path": str(root / "movie.mkv"),
        "debug": False,
        "tracker_status": {"TOTHEGLORY": {}},
        "type": "WEBDL",
        "service_longname": "Netflix",
        "description": "",
        "discs": [],
        "image_list": [],
        "screens": 0,
        "unattended": False,
        "unattended_confirm": False,
    }
    state.update(values)
    return Meta(state)


def _response(*, status: int = 200, text: str = "", url: str = "https://totheglory.im/") -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("GET", url), text=text)


@pytest.mark.asyncio
async def test_ttg_type_matrix_documentary_animation_and_uhd() -> None:
    tracker = _tracker()
    assert await tracker.get_type_id(_meta(genres=["Documentary"], resolution="720p")) == 62
    assert await tracker.get_type_id(_meta(genres=["Documentary"], resolution="1080p")) == 63
    assert await tracker.get_type_id(_meta(genres=["Documentary"], resolution="480p")) == 0
    assert await tracker.get_type_id(_meta(genres=["Documentary"], is_disc="BDMV")) == 64
    assert await tracker.get_type_id(_meta(genres=["Animation"], resolution="1080p")) == 58
    assert await tracker.get_type_id(_meta(genres=["Animation"], resolution="2160p")) == 108
    assert await tracker.get_type_id(_meta(genres=["Animation"], resolution="2160p", is_disc="BDMV")) == 109
    assert tracker._tv_episode_type_id("480p", "EN") == 0


def test_ttg_movie_tv_and_search_resolution_helpers() -> None:
    tracker = _tracker()
    assert tracker._movie_type_id(_meta(is_disc="BDMV")) == 54
    assert tracker._tv_episode_type_id("1080p", "ZH") == 75
    assert tracker._tv_episode_type_id("720p", "ZH") == 76
    assert tracker._tv_pack_type_id("KR") == 99
    assert tracker._tv_pack_type_id("JA") == 88
    assert tracker._tv_pack_type_id("ZH") == 90
    assert tracker._search_resolution(_meta(is_disc="BDMV")) == "1080p Blu-ray"
    assert tracker._search_resolution(_meta(is_disc="DVD")) == "DVD"


@pytest.mark.asyncio
async def test_ttg_upload_non_debug_delegates_response(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class FakeCommon:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def create_torrent_for_upload(self, *_args: object, **_kwargs: object) -> None:
            return None

    tracker = _tracker()
    monkeypatch.setattr(ttg_module, "Common", FakeCommon)
    monkeypatch.setattr(tracker, "edit_desc", AsyncMock())
    torrent_path = str(tmp_path / "release.torrent")
    monkeypatch.setattr(tracker, "_upload_parts", AsyncMock(return_value=({}, {}, torrent_path)))
    response = _response(url="https://totheglory.im/details.php?id=55")
    monkeypatch.setattr(tracker, "_post_upload", AsyncMock(return_value=response))
    monkeypatch.setattr(tracker, "_handle_upload_response", AsyncMock(return_value=True))
    assert await tracker.upload(_meta())
    tracker._handle_upload_response.assert_awaited_once_with(ANY, response, torrent_path)


@pytest.mark.asyncio
async def test_ttg_post_upload_uses_cookie_payload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tracker = _tracker()
    tracker.cookie_validator._load_cookies_dict_secure = lambda _path: {"sid": {"value": "cookie"}}  # type: ignore[method-assign,reportPrivateUsage]

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, **kwargs: object) -> httpx.Response:
            assert kwargs["data"] is data
            return _response(url="https://totheglory.im/details.php?id=55")

    monkeypatch.setattr(ttg_module.httpx, "AsyncClient", lambda *_args, **_kwargs: Client())
    data: dict[str, Any] = {}
    response = await tracker._post_upload(_meta(tmp_path), data, {})
    assert response.status_code == 200
    assert data["type"] == 53


@pytest.mark.asyncio
async def test_ttg_handle_upload_response_success_and_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker, "download_new_torrent", AsyncMock())
    item = _meta(tracker_status={"TOTHEGLORY": {}})
    response = _response(url="https://totheglory.im/details.php?id=77")
    torrent_path = str(tmp_path / "x.torrent")
    assert await tracker._handle_upload_response(item, response, torrent_path)
    assert item.tracker_status["TOTHEGLORY"]["status_message"].endswith("id=77")
    tracker.download_new_torrent.assert_awaited_once_with("77", torrent_path)

    with pytest.raises(UploadError):
        await tracker._handle_upload_response(_meta(), _response(url="https://totheglory.im/upload.php"), str(tmp_path / "x"))

    bad_url = _response(url="https://totheglory.im/details.php?id=")
    with pytest.raises(UploadError, match="torrent id missing"):
        await tracker._handle_upload_response(_meta(), bad_url, str(tmp_path / "x"))


def test_ttg_search_release_name_parser() -> None:
    link = BeautifulSoup('<a href="/t/1"><b><font>Release.Name<br></font></b></a>', "html.parser").find("a")
    assert link is not None
    assert ToTheGlory._release_name_from_link(link) == "Release.Name"
    unrelated = BeautifulSoup('<a href="/foo">Foo</a>', "html.parser").find("a")
    assert unrelated is not None
    assert ToTheGlory._release_name_from_link(unrelated) == ""


@pytest.mark.asyncio
async def test_ttg_search_existing_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    cookie = tmp_path / "data" / "cookies" / "TOTHEGLORY.json"
    cookie.parent.mkdir(parents=True)
    cookie.write_text("{}", encoding="utf-8")
    tracker.cookie_validator._load_cookies_dict_secure = lambda _path: {"sid": {"value": "x"}}  # type: ignore[method-assign,reportPrivateUsage]

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, _url: str) -> httpx.Response:
            html = '<a href="/t/1"><b><font>Release.Name<br></font></b></a>'
            return _response(text=html, url="https://totheglory.im/browse.php")

    monkeypatch.setattr(ttg_module.httpx, "AsyncClient", lambda *_args, **_kwargs: Client())
    monkeypatch.setattr(ttg_module.asyncio, "sleep", AsyncMock())
    assert await tracker.search_existing(_meta(tmp_path)) == ["Release.Name"]


@pytest.mark.asyncio
async def test_ttg_validate_cookies_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    cookie = tmp_path / "cookies.pkl"
    cookie.write_text("cookie", encoding="utf-8")
    tracker.cookie_validator._load_cookies_dict_secure = lambda _path: {"sid": {"value": "x"}}  # type: ignore[method-assign,reportPrivateUsage]

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, **_kwargs: object) -> httpx.Response:
            return _response(text='<a href="/logout.php">Logout</a>')

    monkeypatch.setattr(ttg_module.httpx, "AsyncClient", lambda *_args, **_kwargs: Client())
    assert await tracker.validate_cookies(_meta(), str(cookie))


@pytest.mark.asyncio
async def test_ttg_validate_credentials_and_recreate_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    cookie = tmp_path / "data" / "cookies" / "TOTHEGLORY.pkl"
    cookie.parent.mkdir(parents=True)
    cookie.write_text("old", encoding="utf-8")
    monkeypatch.setattr(tracker, "validate_cookies", AsyncMock(return_value=False))
    monkeypatch.setattr(tracker, "login", AsyncMock())

    assert not await tracker.validate_credentials(_meta(tmp_path, unattended=True, unattended_confirm=False))

    monkeypatch.setattr(ttg_module.cli_ui, "ask_yes_no", lambda *_args, **_kwargs: False)
    assert not await tracker._maybe_recreate_session(_meta(tmp_path), cookie)

    monkeypatch.setattr(ttg_module.cli_ui, "ask_yes_no", lambda *_args, **_kwargs: True)
    tracker.validate_cookies = AsyncMock(return_value=True)  # type: ignore[method-assign]
    cookie.write_text("old", encoding="utf-8")
    assert await tracker._maybe_recreate_session(_meta(tmp_path), cookie)
    tracker.login.assert_awaited()


@pytest.mark.asyncio
async def test_ttg_two_factor_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    direct = _response(url="https://totheglory.im/my.php")
    client = AsyncMock()
    assert await tracker._maybe_complete_two_factor(client, direct, _meta()) is direct

    missing = _response(text="<html></html>", url="https://totheglory.im/2fa.php")
    with pytest.raises(UploadError, match="authenticity token"):
        await tracker._maybe_complete_two_factor(client, missing, _meta())

    page = _response(text='<input name="authenticity_token" value="token">', url="https://totheglory.im/2fa.php")
    unattended = _meta(unattended=True, unattended_confirm=False)
    assert await tracker._maybe_complete_two_factor(client, page, unattended) is page

    completed = _response(url="https://totheglory.im/my.php")
    client.post = AsyncMock(return_value=completed)
    monkeypatch.setattr(ttg_module, "prompt_in_thread", AsyncMock(return_value="123456"))
    monkeypatch.setattr(ttg_module.asyncio, "sleep", AsyncMock())
    assert await tracker._submit_two_factor(client, "token") is completed
    assert tracker._authenticity_token('<input name="authenticity_token" value="x">') == "x"
    assert tracker._authenticity_token("<html></html>") == ""


@pytest.mark.asyncio
async def test_ttg_save_login_result_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tracker = _tracker()
    client = AsyncMock()
    monkeypatch.setattr(ttg_module.asyncio, "sleep", AsyncMock())
    await tracker._save_login_result(client, _response(text="bad", url="https://totheglory.im/login.php"), str(tmp_path / "cookie"))


@pytest.mark.asyncio
async def test_ttg_description_preamble_technical_and_screens(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    common = AsyncMock()
    common.ptgen = AsyncMock(return_value="PTGEN")
    monkeypatch.setattr(ttg_module, "Common", lambda *_args, **_kwargs: common)
    item = _meta(tmp_path, type="WEBDL", service_longname="Netflix", description="")
    preamble = await tracker._description_preamble(item)
    assert preamble[0] == "PTGEN"
    assert "Netflix" in preamble[1]

    assert tracker._web_source_note(_meta(type="REMUX")) == ""
    assert tracker._disc_description({"type": "BDMV", "name": "DISC", "summary": "BDINFO"}).startswith("[quote=DISC]")
    assert "VOB" in tracker._disc_description({"type": "DVD", "vob": "VOB", "vob_mi": "VOBMI", "ifo": "IFO", "ifo_mi": "IFOMI"})
    assert tracker._disc_description({"type": "OTHER"}) == ""

    assert tracker._screenshot_description(_meta(image_list=[])) == []
    assert tracker._screenshot_link("bad") == ""
    assert tracker._screenshot_link({"web_url": "", "img_url": "x"}) == ""
    assert tracker._screen_limit("bad", 3) == 0


@pytest.mark.asyncio
async def test_ttg_technical_description_reads_mediainfo(tmp_path: Path) -> None:
    tracker = _tracker()
    root = tmp_path / "tmp" / "release"
    root.mkdir(parents=True)
    (root / "MEDIAINFO_CLEANPATH.txt").write_text("MEDIAINFO", encoding="utf-8")
    result = await tracker._technical_description(_meta(tmp_path, discs=[]))
    assert "MEDIAINFO" in result[0]


@pytest.mark.asyncio
async def test_ttg_download_new_torrent_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tracker = _tracker()

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, **_kwargs: object) -> httpx.Response:
            return _response(status=500, text="error")

    monkeypatch.setattr(ttg_module.httpx, "AsyncClient", lambda *_args, **_kwargs: Client())
    await tracker.download_new_torrent("1", str(tmp_path / "unused.torrent"))


@pytest.mark.asyncio
async def test_ttg_remaining_type_and_validate_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    assert await tracker.get_type_id(_meta(category="TV", tv_pack=0, original_language="KR", resolution="1080p")) == 75

    cookie = tmp_path / "data" / "cookies" / "TOTHEGLORY.pkl"
    cookie.parent.mkdir(parents=True)
    cookie.write_text("cookie", encoding="utf-8")
    monkeypatch.setattr(tracker, "validate_cookies", AsyncMock(return_value=True))
    assert await tracker.validate_credentials(_meta(tmp_path))


@pytest.mark.asyncio
async def test_ttg_maybe_complete_two_factor_submits(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    page = _response(text='<input name="authenticity_token" value="token">', url="https://totheglory.im/2fa.php")
    completed = _response(url="https://totheglory.im/my.php")
    monkeypatch.setattr(tracker, "_submit_two_factor", AsyncMock(return_value=completed))
    assert await tracker._maybe_complete_two_factor(AsyncMock(), page, _meta()) is completed
    tracker._submit_two_factor.assert_awaited_once_with(ANY, "token")


@pytest.mark.asyncio
async def test_ttg_save_login_result_success() -> None:
    tracker = _tracker()

    class Validator:
        def __init__(self) -> None:
            self.saved = False

        def _save_cookies_secure(self, _jar: object, _cookiefile: str) -> None:
            self.saved = True

    validator = Validator()
    tracker.cookie_validator = validator  # type: ignore[assignment]
    client = AsyncMock()
    client.cookies.jar = object()
    await tracker._save_login_result(client, _response(url="https://totheglory.im/my.php"), "cookie.pkl")
    assert validator.saved


@pytest.mark.asyncio
async def test_ttg_technical_description_disc_branch() -> None:
    tracker = _tracker()
    item = _meta(discs=[{"type": "BDMV", "name": "DISC", "summary": "BDINFO"}])
    result = await tracker._technical_description(item)
    assert result == ["[quote=DISC]BDINFO[/quote]\n\n"]
