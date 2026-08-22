from __future__ import annotations

import asyncio
import json
import pickle
from pathlib import Path
from types import TracebackType
from typing import Any, ClassVar, Self
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from src.domain_models.release import Meta
from src.integrations.trackers import filelist as filelist_module
from src.integrations.trackers.filelist import FileList


def _config(tmp_path: Path, **tracker_values: object) -> dict[str, Any]:
    tracker = {
        "username": "user",
        "password": "".join(("pass", "word")),
        "fltools": {"user": "fluser", "pass": "flpass"},
        "uploader_name": "Uploader",
        "anon": "False",
        **tracker_values,
    }
    return {
        "DEFAULT": {},
        "TRACKERS": {"FILELIST": tracker},
        "TORRENT_CLIENTS": {},
        "base_dir": str(tmp_path),
    }


def _meta(tmp_path: Path, **values: object) -> Meta:
    media = tmp_path / "Movie.mkv"
    media.write_bytes(b"video")
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "uuid": "filelist",
        "path": str(media),
        "filename": media.name,
        "basename_no_ext": "Movie.DDP.Atmos",
        "filelist": [str(media)],
        "category": "MOVIE",
        "name": "Title 2024 1080p WEB-DL DD+ Atmos H.264-GROUP",
        "title": "Title",
        "aka": "",
        "year": 2024,
        "resolution": "1080p",
        "sd": 0,
        "type": "WEBDL",
        "is_disc": "",
        "anime": False,
        "tag": "-GROUP",
        "hdr": "",
        "audio": "DD+ 5.1 Atmos",
        "imdb_id": 1234567,
        "imdb": "tt1234567",
        "imdb_info": {"aka": "AKA", "year": 2023, "genres": "Drama"},
        "tv_pack": 0,
        "freeleech": 0,
        "bdinfo": {},
        "mediainfo": {"media": {"track": []}},
        "unattended": True,
        "debug": False,
        "tracker_status": {"FILELIST": {}},
    }
    state.update(values)
    target = tmp_path / "tmp" / str(state["uuid"])
    target.mkdir(parents=True, exist_ok=True)
    return Meta(state)


class Response:
    def __init__(
        self,
        *,
        status: int = 200,
        text: str = "",
        content: bytes = b"",
        url: str = "https://filelist.io/index.php",
    ) -> None:
        self.status_code = status
        self.text = text
        self.content = content
        self.url = httpx.URL(url)
        self.request = httpx.Request("GET", url)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            response = httpx.Response(self.status_code, request=self.request)
            raise httpx.HTTPStatusError(
                "failed", request=self.request, response=response
            )


class Client:
    queue: ClassVar[list[object]] = []
    instances: ClassVar[list[Client]] = []

    def __init__(self, *_args: object, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.cookies = SimpleCookieJar()
        type(self).instances.append(self)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        return None

    def _next(self) -> Response:
        value = type(self).queue.pop(0)
        if isinstance(value, BaseException):
            raise value
        assert isinstance(value, Response)
        return value

    async def get(self, *_args: object, **_kwargs: object) -> Response:
        return self._next()

    async def post(self, *_args: object, **_kwargs: object) -> Response:
        return self._next()

    @classmethod
    def reset(cls, *values: object) -> None:
        cls.queue = list(values)
        cls.instances = []


class PickleCookie:
    def __init__(self, name: str, value: str) -> None:
        self.name = name
        self.value = value


class SimpleCookieJar:
    jar: tuple[()] = ()


@pytest.fixture(autouse=True)
def reset_client(monkeypatch: pytest.MonkeyPatch) -> None:
    Client.reset()
    monkeypatch.setattr(filelist_module.httpx, "AsyncClient", Client)


def test_init_category_and_name_matrix(tmp_path: Path) -> None:
    tracker = FileList(_config(tmp_path, fltools="bad", uploader_name=""))
    assert tracker.fltools == {} and tracker.uploader_name is None
    assert (
        tracker._is_true(True)
        and tracker._is_true(" yes ")
        and not tracker._is_true("no")
    )

    tracker.get_ro_tracks = AsyncMock(return_value=(False, False))  # type: ignore[method-assign]
    cases = [
        (
            _meta(
                tmp_path, category="MOVIE", is_disc="BDMV", resolution="2160p"
            ),
            26,
        ),
        (
            _meta(
                tmp_path,
                uuid="remux",
                category="MOVIE",
                type="REMUX",
                resolution="1080p",
            ),
            20,
        ),
        (_meta(tmp_path, uuid="uhd", category="MOVIE", resolution="2160p"), 6),
        (_meta(tmp_path, uuid="sd", category="MOVIE", sd=1), 1),
        (_meta(tmp_path, uuid="tv4k", category="TV", resolution="2160p"), 27),
        (_meta(tmp_path, uuid="tvsd", category="TV", sd=1), 23),
        (_meta(tmp_path, uuid="tvhd", category="TV"), 21),
        (_meta(tmp_path, uuid="dvd", category="MOVIE", is_disc="DVD"), 2),
        (_meta(tmp_path, uuid="anime", category="MOVIE", anime=True), 24),
    ]
    for meta, expected in cases:
        assert asyncio.run(tracker.get_category_id(meta)) == expected

    tracker.get_ro_tracks = AsyncMock(return_value=(False, True))  # type: ignore[method-assign]
    assert (
        asyncio.run(
            tracker.get_category_id(
                _meta(tmp_path, uuid="ro", category="MOVIE")
            )
        )
        == 19
    )
    assert (
        asyncio.run(
            tracker.get_category_id(
                _meta(tmp_path, uuid="dvd-ro", is_disc="DVD")
            )
        )
        == 3
    )

    meta = _meta(
        tmp_path,
        name="Title AKA 2024 1080p WEB-DL DD+ 5.1 Atmos DV HEVC Dual-Audio-GROUP",
        title="Title",
        aka="AKA",
        hdr="DV HDR10+ PQ10",
        audio="DD+ 5.1 Atmos",
        basename_no_ext="Title.DDP.2024",
        imdb_info={"aka": "Other", "year": 2023},
        year=2024,
    )
    name = asyncio.run(tracker.get_name(meta))
    assert "Other" in name and "2023" in name and "DDP" in name
    assert "Dual-Audio" not in name and "Atmos" not in name


def test_cookie_loading_all_formats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = FileList(_config(tmp_path))
    assert tracker._load_cookie_dict(str(tmp_path / "missing.json")) == {}

    netscape = tmp_path / "cookies.txt"
    netscape.write_text(
        "# comment\n.example.com TRUE / FALSE 0 session value\nshort\n",
        encoding="utf-8",
    )
    assert tracker._load_cookie_dict(str(netscape)) == {"session": "value"}

    pickled = tmp_path / "cookies.pkl"
    pickled.write_bytes(pickle.dumps([PickleCookie("session", "legacy")]))
    save = Mock()
    monkeypatch.setattr(tracker.cookie_validator, "_save_cookies_secure", save)
    assert tracker._load_cookie_dict(str(pickled)) == {"session": "legacy"}
    save.assert_called_once()

    secure = tmp_path / "secure.json"
    secure.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        tracker.cookie_validator,
        "_load_cookies_dict_secure",
        lambda _path: {"session": {"value": "secure"}},
    )
    assert tracker._load_cookie_dict(str(secure)) == {"session": "secure"}

    monkeypatch.setattr(
        tracker.cookie_validator,
        "_load_cookies_dict_secure",
        lambda _path: (_ for _ in ()).throw(ValueError("secure failed")),
    )
    secure.write_text(
        json.dumps({"session": {"value": "nested"}}), encoding="utf-8"
    )
    assert tracker._load_cookie_dict(str(secure)) == {"session": "nested"}
    secure.write_text(json.dumps({"session": "flat"}), encoding="utf-8")
    assert tracker._load_cookie_dict(str(secure)) == {"session": "flat"}
    secure.write_text("not-json", encoding="utf-8")
    assert tracker._load_cookie_dict(str(secure)) == {}

    original_open = Path.open

    def broken_open(path: Path, *args: object, **kwargs: object):
        if path == netscape:
            raise OSError("read failed")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", broken_open)
    assert tracker._load_cookie_dict(str(netscape)) == {}


def _prepare_upload_files(meta: Meta) -> None:
    target = Path(meta.base_dir) / "tmp" / meta.uuid
    (target / "[FILELIST]DESCRIPTION.txt").write_text(
        "description", encoding="utf-8"
    )
    (target / "MEDIAINFO_CLEANPATH.txt").write_text(
        "mediainfo", encoding="utf-8"
    )
    (target / "BD_SUMMARY_00.txt").write_text("bdinfo", encoding="utf-8")
    (target / "[FILELIST].torrent").write_bytes(b"torrent")


def test_upload_debug_success_attended_rename_and_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = FileList(_config(tmp_path))
    common_double = SimpleCommon()
    monkeypatch.setattr(
        filelist_module, "Common", lambda **_kwargs: common_double
    )
    tracker.edit_desc = AsyncMock()  # type: ignore[method-assign]
    tracker.get_name = AsyncMock(return_value="Release.Name")  # type: ignore[method-assign]
    tracker.get_category_id = AsyncMock(return_value=4)  # type: ignore[method-assign]
    tracker.get_ro_tracks = AsyncMock(return_value=(True, False))  # type: ignore[method-assign]

    meta = _meta(tmp_path, debug=True, tv_pack=1, freeleech=1)
    _prepare_upload_files(meta)
    assert asyncio.run(tracker.upload(meta))
    assert common_double.create_calls[-1][1] == "FILELIST_DEBUG"

    answers = iter((False, "Manual.Name"))

    async def prompt(
        function: object, *_args: object, **_kwargs: object
    ) -> object:
        del function
        return next(answers)

    monkeypatch.setattr(filelist_module, "prompt_in_thread", prompt)
    import src.integrations.trackers.cookie_auth as cookie_auth

    monkeypatch.setattr(
        cookie_auth,
        "find_cookie_file",
        lambda *_args, **_kwargs: str(tmp_path / "cookies.json"),
    )
    tracker._load_cookie_dict = Mock(return_value={})  # type: ignore[method-assign]
    tracker.download_new_torrent = AsyncMock()  # type: ignore[method-assign]
    meta = _meta(
        tmp_path,
        uuid="upload-success",
        unattended=False,
        anime=True,
        tag="-SubsPlease",
    )
    _prepare_upload_files(meta)
    Client.reset(
        Response(url="https://filelist.io/details.php?id=123&uploaded=1")
    )
    assert asyncio.run(tracker.upload(meta))
    tracker.download_new_torrent.assert_awaited_once()

    answers = iter((False, ""))
    meta = _meta(tmp_path, uuid="upload-abort", unattended=False)
    _prepare_upload_files(meta)
    assert not asyncio.run(tracker.upload(meta))

    meta = _meta(tmp_path, uuid="upload-fail")
    _prepare_upload_files(meta)
    Client.reset(
        Response(status=200, text="failed", url="https://filelist.io/error")
    )
    with pytest.raises(filelist_module.UploadError):
        asyncio.run(tracker.upload(meta))


class SimpleCommon:
    def __init__(self) -> None:
        self.create_calls: list[tuple[Meta, str, str]] = []

    async def create_torrent_for_upload(
        self, meta: Meta, tracker: str, source: str, **_kwargs: object
    ) -> None:
        self.create_calls.append((meta, tracker, source))


def test_search_existing_imdb_title_and_html_filter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = FileList(_config(tmp_path))
    import src.integrations.trackers.cookie_auth as cookie_auth

    monkeypatch.setattr(
        cookie_auth,
        "find_cookie_file",
        lambda *_args, **_kwargs: str(tmp_path / "cookies.json"),
    )
    tracker._load_cookie_dict = Mock(return_value={})  # type: ignore[method-assign]
    tracker.get_category_id = AsyncMock(return_value=4)  # type: ignore[method-assign]
    html = '<a href="details.php?id=1" title="One">One</a><a href="details.php?id=2&x=1" title="Skip">Skip</a><a href="browse.php">Other</a>'
    Client.reset(Response(text=html))
    assert asyncio.run(tracker.search_existing(_meta(tmp_path))) == ["One"]
    Client.reset(Response(text=html))
    assert asyncio.run(
        tracker.search_existing(
            _meta(tmp_path, uuid="title-search", imdb_id=None, title="Title")
        )
    ) == ["One"]


def test_validate_credentials_cookies_login_and_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = FileList(_config(tmp_path))
    cookiefile = tmp_path / "cookies.json"
    import src.integrations.trackers.cookie_auth as cookie_auth

    monkeypatch.setattr(
        cookie_auth,
        "find_cookie_file",
        lambda *_args, **_kwargs: str(cookiefile),
    )
    tracker.login = AsyncMock()  # type: ignore[method-assign]
    tracker.validate_cookies = AsyncMock(return_value=True)  # type: ignore[method-assign]
    assert asyncio.run(tracker.validate_credentials(_meta(tmp_path)))
    tracker.login.assert_awaited_once()

    cookiefile.write_text("{}", encoding="utf-8")
    tracker.validate_cookies = AsyncMock(return_value=False)  # type: ignore[method-assign]
    assert not asyncio.run(
        tracker.validate_credentials(
            _meta(tmp_path, unattended=True, unattended_confirm=False)
        )
    )

    async def yes(*_args: object, **_kwargs: object) -> bool:
        return True

    monkeypatch.setattr(filelist_module, "prompt_in_thread", yes)
    tracker.validate_cookies = AsyncMock(side_effect=[False, True])  # type: ignore[method-assign]
    tracker.login.reset_mock()
    assert asyncio.run(
        tracker.validate_credentials(
            _meta(tmp_path, uuid="relogin", unattended=False)
        )
    )
    tracker.login.assert_awaited_once()

    tracker = FileList(_config(tmp_path))
    tracker._load_cookie_dict = Mock(return_value={})  # type: ignore[method-assign]
    assert not asyncio.run(
        tracker.validate_cookies(_meta(tmp_path), str(cookiefile))
    )
    tracker._load_cookie_dict = Mock(return_value={"session": "x"})  # type: ignore[method-assign]
    Client.reset(Response(text="Logout", url="https://filelist.io/index.php"))
    assert asyncio.run(
        tracker.validate_cookies(_meta(tmp_path), str(cookiefile))
    )
    Client.reset(Response(text="Login", url="https://filelist.io/index.php"))
    assert not asyncio.run(
        tracker.validate_cookies(_meta(tmp_path), str(cookiefile))
    )

    save = Mock()
    monkeypatch.setattr(tracker.cookie_validator, "_save_cookies_secure", save)
    login_html = '<input name="validator" value="token">'
    Client.reset(
        Response(text=login_html), Response(), Response(text="Logout")
    )
    asyncio.run(tracker.login(str(cookiefile)))
    save.assert_called_once()

    Client.reset(Response(text="<html></html>"))
    with pytest.raises(filelist_module.LoginError):
        asyncio.run(tracker.login(str(cookiefile)))
    Client.reset(Response(text='<input name="validator">'))
    with pytest.raises(filelist_module.LoginError):
        asyncio.run(tracker.login(str(cookiefile)))

    torrent_path = tmp_path / "downloaded.torrent"
    Client.reset(Response(content=b"torrent"))
    asyncio.run(tracker.download_new_torrent({}, "1", str(torrent_path)))
    assert torrent_path.read_bytes() == b"torrent"
    Client.reset(Response(status=500, text="failed"))
    asyncio.run(tracker.download_new_torrent({}, "1", str(torrent_path)))


def test_edit_desc_web_and_bluray_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = FileList(_config(tmp_path))
    meta = _meta(tmp_path)
    target = tmp_path / "tmp" / meta.uuid
    (target / "MEDIAINFO_CLEANPATH.txt").write_text(
        "mediainfo", encoding="utf-8"
    )
    screen_dir = filelist_module.screenshots_dir(meta.base_dir, meta.uuid)
    screen_dir.mkdir(parents=True, exist_ok=True)
    (screen_dir / f"{meta.filename}-01.png").write_bytes(b"png")
    monkeypatch.setattr(
        filelist_module,
        "base_description",
        lambda _meta: "[spoiler=x]body[/spoiler][code]code[/code]",
    )
    Client.reset(Response(text="WEB DESC\r\nLINE"))
    asyncio.run(tracker.edit_desc(meta))
    desc = (target / "[FILELIST]DESCRIPTION.txt").read_text(encoding="utf-8")
    assert desc == "WEB DESC\nLINE"

    meta = _meta(tmp_path, uuid="bd-desc", is_disc="BDMV")
    target = tmp_path / "tmp" / meta.uuid
    (target / "BD_SUMMARY_EXT.txt").write_text(
        "DISC INFO:\nPLAYLIST REPORT:\nVIDEO:\nAUDIO:\nSUBTITLES:\n[/pre][/quote]",
        encoding="utf-8",
    )
    screen_dir = filelist_module.screenshots_dir(meta.base_dir, meta.uuid)
    screen_dir.mkdir(parents=True, exist_ok=True)
    (screen_dir / f"{meta.filename}-01.png").write_bytes(b"png")
    Client.reset(Response(text="SCREENS"))
    asyncio.run(tracker.edit_desc(meta))
    desc = (target / "[FILELIST]DESCRIPTION.txt").read_text(encoding="utf-8")
    assert "BD_Info" in desc and "SCREENS" in desc

    tracker.signature = "SIGNATURE"
    meta = _meta(tmp_path, uuid="bd-empty", is_disc="BDMV")
    target = tmp_path / "tmp" / meta.uuid
    (target / "BD_SUMMARY_EXT.txt").write_text("", encoding="utf-8")
    asyncio.run(tracker.edit_desc(meta))
    assert (
        (target / "[FILELIST]DESCRIPTION.txt")
        .read_text(encoding="utf-8")
        .endswith("SIGNATURE")
    )


def test_ro_tracks_file_and_bdinfo(tmp_path: Path) -> None:
    tracker = FileList(_config(tmp_path))
    meta = _meta(
        tmp_path,
        mediainfo={
            "media": {
                "track": [
                    {"@type": "Text", "Language": "ro"},
                    {"@type": "Audio", "Audio": "ro"},
                    "bad",
                ]
            }
        },
    )
    assert asyncio.run(tracker.get_ro_tracks(meta)) == (True, True)
    assert asyncio.run(
        tracker.get_ro_tracks(_meta(tmp_path, uuid="bad-mi", mediainfo="bad"))
    ) == (False, False)

    meta = _meta(
        tmp_path,
        uuid="bd-ro",
        is_disc="BDMV",
        bdinfo={
            "subtitles": ["English", "Romanian"],
            "audio": ["bad", {"language": "Romanian"}],
        },
    )
    assert asyncio.run(tracker.get_ro_tracks(meta)) == (True, True)
    assert asyncio.run(
        tracker.get_ro_tracks(
            _meta(tmp_path, uuid="bd-bad", is_disc="BDMV", bdinfo="bad")
        )
    ) == (False, False)


def test_filelist_final_uncovered_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = FileList(_config(tmp_path))
    broken_pickle = tmp_path / "broken.pkl"
    broken_pickle.write_bytes(b"not-pickle")
    assert tracker._load_cookie_dict(str(broken_pickle)) == {}

    common_double = SimpleCommon()
    monkeypatch.setattr(
        filelist_module, "Common", lambda **_kwargs: common_double
    )
    tracker.edit_desc = AsyncMock()  # type: ignore[method-assign]
    tracker.get_name = AsyncMock(return_value="Release.Name")  # type: ignore[method-assign]
    tracker.get_category_id = AsyncMock(return_value=20)  # type: ignore[method-assign]
    tracker.get_ro_tracks = AsyncMock(return_value=(False, False))  # type: ignore[method-assign]
    meta = _meta(tmp_path, uuid="freeleech-disc", debug=True, is_disc="BDMV")
    _prepare_upload_files(meta)
    assert asyncio.run(tracker.upload(meta))

    save = Mock()
    monkeypatch.setattr(tracker.cookie_validator, "_save_cookies_secure", save)
    login_html = '<input name="validator" value="token">'
    Client.reset(
        Response(text=login_html),
        Response(),
        Response(text="Login", url="https://filelist.io/login.php"),
    )
    asyncio.run(tracker.login(str(tmp_path / "cookies.json")))
    save.assert_not_called()


def test_filelist_refactor_pure_helper_edges(tmp_path: Path) -> None:
    tracker = FileList(_config(tmp_path))

    assert tracker._raw_json_cookie_values({}) == {}
    assert (
        tracker._base_category_id(
            _meta(tmp_path, uuid="other-category", category="OTHER"), False
        )
        == 4
    )
    assert (
        tracker._name_imdb_overrides(
            _meta(tmp_path, uuid="invalid-imdb-info", imdb_info="bad"),
            "Release",
        )
        == "Release"
    )
    assert (
        tracker._name_imdb_year(
            _meta(tmp_path, uuid="missing-year", year=None), "Release", {}
        )
        == "Release"
    )

    upload_data: dict[str, Any] = {}
    tracker._add_imdb_upload_data(
        _meta(tmp_path, uuid="missing-imdb", imdb_id=None), upload_data
    )
    assert upload_data == {}

    assert (
        tracker._raw_mediainfo_tracks(
            _meta(
                tmp_path,
                uuid="invalid-media",
                mediainfo={"media": "bad"},
            )
        )
        == []
    )
    assert (
        tracker._raw_mediainfo_tracks(
            _meta(
                tmp_path,
                uuid="invalid-tracks",
                mediainfo={"media": {"track": "bad"}},
            )
        )
        == []
    )
    assert not tracker._ro_bd_audio(
        _meta(
            tmp_path,
            uuid="non-ro-bd-audio",
            is_disc="BDMV",
            bdinfo={"audio": [{"language": "English"}]},
        )
    )


def test_filelist_refactor_prompt_helper_edges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = FileList(_config(tmp_path))
    attended = _meta(tmp_path, uuid="confirmed-name", unattended=False)

    monkeypatch.setattr(
        filelist_module, "prompt_in_thread", AsyncMock(return_value=True)
    )
    assert (
        asyncio.run(tracker._confirmed_upload_name(attended, "Release.Name"))
        == "Release.Name"
    )

    monkeypatch.setattr(
        filelist_module, "prompt_in_thread", AsyncMock(return_value=False)
    )
    assert not asyncio.run(
        tracker._relogin(attended, str(tmp_path / "cookies.json"))
    )
