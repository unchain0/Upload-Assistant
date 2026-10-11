from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from bs4 import BeautifulSoup

from src.domain_models.processing import LoginError, UploadError
from src.domain_models.release import Meta
from src.integrations.trackers.pterclub import PTerClub

pter_module = importlib.import_module("src.integrations.trackers.pterclub")


def _config(*, img_rehost: bool = False, anon: bool = False) -> dict[str, Any]:
    return {
        "DEFAULT": {},
        "TRACKERS": {
            "PTERCLUB": {
                "passkey": "passkey",
                "username": "user",
                "password": "password",
                "img_rehost": img_rehost,
                "ptgen_api": "https://ptgen.invalid",
                "anon": anon,
            }
        },
    }


def _tracker(*, img_rehost: bool = False, anon: bool = False) -> PTerClub:
    return PTerClub(_config(img_rehost=img_rehost, anon=anon))


def _meta(tmp_path: Path | None = None, **values: object) -> Meta:
    root = tmp_path or Path()
    state: dict[str, object] = {
        "base_dir": str(root),
        "uuid": "release",
        "filename": "release",
        "name": "Example.Movie.2010.1080p.H.264-GROUP",
        "title": "Example Movie",
        "aka": "",
        "category": "MOVIE",
        "genres": ["Drama"],
        "keywords": [],
        "is_disc": "",
        "resolution": "1080p",
        "type": "WEBDL",
        "imdb_id": 123,
        "imdb": 123,
        "ptgen": {"region": [], "trans_title": [""], "genre": ["剧情"]},
        "discs": [],
        "image_list": [],
        "screens": 2,
        "mediainfo": {"media": {"track": []}},
        "bdinfo": {},
        "has_encode_settings": False,
        "personalrelease": False,
        "anon": 0,
        "filelist": [str(root / "movie.mkv")],
        "video": str(root / "movie.mkv"),
        "path": str(root / "movie.mkv"),
        "debug": False,
        "tracker_status": {"PTERCLUB": {}},
    }
    state.update(values)
    return Meta(state)


def _response(
    *,
    status: int = 200,
    text: str = "",
    url: str = "https://pterclub.net/",
    json_data: Any | None = None,
) -> httpx.Response:
    request = httpx.Request("GET", url)
    if json_data is not None:
        return httpx.Response(status, request=request, json=json_data)
    return httpx.Response(status, request=request, text=text)


@pytest.mark.asyncio
async def test_pterclub_validate_credentials_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        tracker, "validate_cookies", AsyncMock(return_value=False)
    )
    assert not await tracker.validate_credentials(_meta())


@pytest.mark.asyncio
async def test_pterclub_validate_cookies_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = _tracker()
    cookie = tmp_path / "PTERCLUB.txt"
    cookie.write_text("cookie", encoding="utf-8")
    monkeypatch.setattr(
        pter_module, "find_cookie_file", lambda *_args, **_kwargs: str(cookie)
    )
    tracker.common.parse_cookie_file = AsyncMock(return_value={"sid": "x"})  # type: ignore[method-assign]

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, **_kwargs: object) -> httpx.Response:
            return _response(
                text='<a href="#" data-url="logout.php" id="logout-confirm">'
            )

    monkeypatch.setattr(
        pter_module.httpx, "AsyncClient", lambda *_args, **_kwargs: Client()
    )
    assert await tracker.validate_cookies(_meta(tmp_path))


@pytest.mark.asyncio
async def test_pterclub_search_existing_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = _tracker()
    cookie = tmp_path / "PTERCLUB.txt"
    cookie.write_text("cookie", encoding="utf-8")
    monkeypatch.setattr(
        pter_module, "find_cookie_file", lambda *_args, **_kwargs: str(cookie)
    )
    tracker.common.parse_cookie_file = AsyncMock(return_value={"sid": "x"})  # type: ignore[method-assign]
    html = """
    <table class='torrents'>
      <tr><td><table class='torrentname'><tr><td><a href='details.php?id=1' title='Release.One'>R1</a></td></tr></table></td></tr>
      <tr><td><table class='torrentname'><tr><td>No link</td></tr></table></td></tr>
    </table>
    """
    monkeypatch.setattr(
        tracker,
        "_search_response",
        AsyncMock(return_value=_response(text=html)),
    )
    assert await tracker.search_existing(_meta(tmp_path)) == ["Release.One"]


def test_pterclub_row_release_name_missing_link() -> None:
    row = BeautifulSoup("<tr><td>none</td></tr>", "html.parser").find("tr")
    assert row is not None
    assert PTerClub._row_release_name(row) == ""


@pytest.mark.asyncio
async def test_pterclub_special_category_and_area() -> None:
    tracker = _tracker()
    assert (
        await tracker.get_type_category_id(_meta(genres=["Documentary"]))
        == "402"
    )
    assert (
        await tracker.get_type_category_id(
            _meta(genres=["Animation", "Documentary"])
        )
        == "403"
    )
    assert await tracker.get_area_id(_meta(ptgen={"region": ["日本"]})) == 6


@pytest.mark.asyncio
async def test_pterclub_description_signature_and_discs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    tracker.signature = "SIGNATURE"
    tracker.common.ptgen = AsyncMock(return_value="PTGEN")  # type: ignore[method-assign]
    monkeypatch.setattr(
        tracker, "_description_screenshots", AsyncMock(return_value=[])
    )

    class BBCode:
        @staticmethod
        def convert_code_to_quote(value: str) -> str:
            return value

        @staticmethod
        def convert_spoiler_to_hide(value: str) -> str:
            return value

        @staticmethod
        def convert_comparison_to_centered(value: str, _width: int) -> str:
            return value

    item = _meta(
        discs=[
            {"type": "BDMV", "summary": "BDINFO"},
            {"type": "DVD", "name": "DVD1", "vob_mi": "VOB", "ifo_mi": "IFO"},
        ]
    )
    parts = await tracker._description_parts(item, "BASE", BBCode())
    joined = "".join(parts)
    assert "PTGEN" in joined
    assert "BDINFO" in joined
    assert "VOB" in joined
    assert "SIGNATURE" in joined


@pytest.mark.asyncio
async def test_pterclub_technical_description_reads_mediainfo(
    tmp_path: Path,
) -> None:
    tracker = _tracker()
    root = tmp_path / "tmp" / "release"
    root.mkdir(parents=True)
    (root / "MEDIAINFO_CLEANPATH.txt").write_text(
        "MEDIAINFO", encoding="utf-8"
    )
    result = await tracker._technical_description(_meta(tmp_path, discs=[]))
    assert result == ["[hide=mediainfo]MEDIAINFO[/hide]", "\n"]


@pytest.mark.asyncio
async def test_pterclub_description_rehost_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker(img_rehost=True)
    monkeypatch.setattr(
        tracker,
        "pterimg_upload",
        AsyncMock(return_value=[{"web_url": "w", "img_url": "i"}]),
    )
    assert await tracker._description_image_list(_meta()) == [
        {"web_url": "w", "img_url": "i"}
    ]


@pytest.mark.asyncio
async def test_pterclub_get_auth_token_saved_and_login_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = _tracker()
    cookie = tmp_path / "Pterimg.txt"
    cookie.write_text("cookie", encoding="utf-8")
    monkeypatch.setattr(tracker, "_pterimg_cookie_file", lambda _meta: cookie)
    monkeypatch.setattr(
        tracker, "_saved_cookie_values", lambda _cookie: {"sid": "x"}
    )
    monkeypatch.setattr(
        tracker, "_saved_auth_token", AsyncMock(return_value="saved")
    )
    assert await tracker.get_auth_token(_meta(tmp_path)) == "saved"

    monkeypatch.setattr(
        tracker, "_saved_auth_token", AsyncMock(return_value="")
    )
    monkeypatch.setattr(
        tracker, "_pterimg_login", AsyncMock(return_value="fresh")
    )
    assert await tracker.get_auth_token(_meta(tmp_path)) == "fresh"


def test_pterclub_saved_cookie_values(tmp_path: Path) -> None:
    tracker = _tracker()
    cookie = tmp_path / "cookie.txt"
    assert tracker._saved_cookie_values(cookie) == {}
    cookie.write_text("cookie", encoding="utf-8")
    tracker.cookie_validator._load_cookies_dict_secure = lambda _path: {
        "sid": {"value": "abc"}
    }  # type: ignore[method-assign,reportPrivateUsage]
    assert tracker._saved_cookie_values(cookie) == {"sid": "abc"}


@pytest.mark.asyncio
async def test_pterclub_saved_auth_token_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, _url: str) -> httpx.Response:
            return _response(
                text='auth_token = "token" <a href="https://s3.pterclub.com/logout/?x">logout</a>'
            )

    monkeypatch.setattr(
        pter_module.httpx, "AsyncClient", lambda *_args, **_kwargs: Client()
    )
    assert await tracker._saved_auth_token({"sid": "x"}) == "token"


@pytest.mark.asyncio
async def test_pterclub_pterimg_login_success_and_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = _tracker()
    cookie = tmp_path / "Pterimg.txt"
    saved: list[str] = []
    tracker.cookie_validator._save_cookies_secure = lambda _jar, path: (
        saved.append(path)
    )  # type: ignore[method-assign,reportPrivateUsage]

    class Client:
        def __init__(self, success: bool) -> None:
            self.success = success
            self.cookies = type("Cookies", (), {"jar": object()})()

        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, _url: str) -> httpx.Response:
            return _response(text='auth_token = "first"')

        async def post(self, **_kwargs: object) -> httpx.Response:
            return _response(
                status=200 if self.success else 403,
                text='auth_token = "fresh"',
            )

    monkeypatch.setattr(
        pter_module.httpx,
        "AsyncClient",
        lambda *_args, **_kwargs: Client(True),
    )
    assert await tracker._pterimg_login(cookie, {}) == "fresh"
    assert saved == [str(cookie)]

    monkeypatch.setattr(
        pter_module.httpx,
        "AsyncClient",
        lambda *_args, **_kwargs: Client(False),
    )
    with pytest.raises(LoginError):
        await tracker._pterimg_login(cookie, {})


@pytest.mark.asyncio
async def test_pterclub_pterimg_upload_wrapper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = _tracker()
    cookie = tmp_path / "Pterimg.txt"
    cookie.write_text("cookie", encoding="utf-8")
    monkeypatch.setattr(
        pter_module, "find_cookie_file", lambda *_args, **_kwargs: str(cookie)
    )
    monkeypatch.setattr(
        tracker, "get_auth_token", AsyncMock(return_value="auth")
    )
    monkeypatch.setattr(
        tracker, "_saved_cookie_values", lambda _cookie: {"sid": "x"}
    )
    monkeypatch.setattr(
        tracker, "_screenshot_paths", lambda _meta: ["one.png"]
    )
    monkeypatch.setattr(
        tracker,
        "_upload_pterimg_images",
        AsyncMock(return_value=[{"web_url": "u", "img_url": "u"}]),
    )

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(
        pter_module.httpx, "AsyncClient", lambda *_args, **_kwargs: Client()
    )
    assert await tracker.pterimg_upload(_meta(tmp_path)) == [
        {"web_url": "u", "img_url": "u"}
    ]


@pytest.mark.asyncio
async def test_pterclub_upload_pterimg_images_filters_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        tracker,
        "_upload_pterimg_image",
        AsyncMock(side_effect=[None, {"web_url": "u", "img_url": "u"}]),
    )
    assert await tracker._upload_pterimg_images(
        AsyncMock(), ["a.png", "b.png"], {}
    ) == [{"web_url": "u", "img_url": "u"}]


@pytest.mark.asyncio
async def test_pterclub_upload_pterimg_image_success_duplicate_and_error(
    tmp_path: Path,
) -> None:
    tracker = _tracker()
    image = tmp_path / "image.png"
    image.write_bytes(b"image")
    client = AsyncMock()
    client.post = AsyncMock(
        return_value=_response(json_data={"image": {"url": "https://img/u"}})
    )
    assert await tracker._upload_pterimg_image(client, str(image), {}) == {
        "web_url": "https://img/u",
        "img_url": "https://img/u",
    }

    client.post = AsyncMock(
        return_value=_response(
            status=409, json_data={"error": {"message": "Duplicated upload"}}
        )
    )
    assert await tracker._upload_pterimg_image(client, str(image), {}) is None

    client.post = AsyncMock(
        return_value=_response(
            status=500, json_data={"error": {"message": "boom"}}
        )
    )
    with pytest.raises(RuntimeError, match="boom"):
        await tracker._upload_pterimg_image(client, str(image), {})


def test_pterclub_pterimg_payload_and_image_result_guards() -> None:
    assert PTerClub._pterimg_payload(_response(text="not-json")) is None
    assert PTerClub._pterimg_payload_message(None) == ""
    assert PTerClub._pterimg_payload_message({"error": "bad"}) == ""
    assert PTerClub._response_fallback_message(_response(text="fallback"))
    with pytest.raises(ValueError, match="Unexpected response"):
        PTerClub._pterimg_image_result(None)
    with pytest.raises(ValueError, match="Missing image data"):
        PTerClub._pterimg_image_result({})
    with pytest.raises(ValueError, match="Missing image url"):
        PTerClub._pterimg_image_result({"image": {}})


@pytest.mark.asyncio
async def test_pterclub_upload_orchestration_debug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    tracker.common.create_torrent_for_upload = AsyncMock()  # type: ignore[method-assign]
    monkeypatch.setattr(tracker, "_ensure_description", AsyncMock())
    monkeypatch.setattr(
        tracker,
        "_upload_parts",
        AsyncMock(return_value=({"name": "Release"}, {}, Path("torrent"))),
    )
    monkeypatch.setattr(tracker, "_debug_upload", AsyncMock(return_value=True))
    assert await tracker.upload(_meta(debug=True))
    tracker._debug_upload.assert_awaited_once()


@pytest.mark.asyncio
async def test_pterclub_ensure_description_calls_edit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker, "edit_desc", AsyncMock())
    await tracker._ensure_description(_meta(tmp_path))
    tracker.edit_desc.assert_awaited_once()


@pytest.mark.asyncio
async def test_pterclub_upload_parts_reads_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = _tracker()
    root = tmp_path / "tmp" / "release"
    root.mkdir(parents=True)
    (root / "[PTERCLUB]DESCRIPTION.txt").write_text(
        "description", encoding="utf-8"
    )
    (root / "[PTERCLUB].torrent").write_bytes(b"torrent")
    monkeypatch.setattr(
        tracker, "_upload_data", AsyncMock(return_value={"name": "Release"})
    )
    data, files, path = await tracker._upload_parts(_meta(tmp_path))
    assert data == {"name": "Release"}
    assert files["file"][1] == b"torrent"
    assert path.name == "[PTERCLUB].torrent"


@pytest.mark.asyncio
async def test_pterclub_small_description_and_debug_upload() -> None:
    tracker = _tracker()
    item = _meta(
        ptgen={"trans_title": ["译名一", "译名二"], "genre": ["剧情"]}
    )
    assert "译名一 / 译名二" in tracker._small_description(item)
    tracker.common.create_torrent_for_upload = AsyncMock()  # type: ignore[method-assign]
    assert await tracker._debug_upload(item, {"name": "Release"})
    assert item.tracker_status["PTERCLUB"]["status_message"].startswith(
        "Debug mode"
    )


@pytest.mark.asyncio
async def test_pterclub_submit_upload_missing_cookie(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        pter_module,
        "find_cookie_file",
        lambda *_args, **_kwargs: str(tmp_path / "missing.txt"),
    )
    assert not await tracker._submit_upload(
        _meta(tmp_path), {}, {}, tmp_path / "torrent"
    )


@pytest.mark.asyncio
async def test_pterclub_handle_upload_response_success_and_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker, "download_new_torrent", AsyncMock())
    item = _meta(tmp_path)
    response = _response(
        url="https://pterclub.net/details.php?id=77&uploaded=1"
    )
    assert await tracker._handle_upload_response(
        item, response, tmp_path / "torrent"
    )
    assert item.tracker_status["PTERCLUB"]["torrent_id"] == "77"

    with pytest.raises(UploadError, match="not expected"):
        await tracker._handle_upload_response(
            _meta(tmp_path),
            _response(url="https://pterclub.net/upload.php"),
            tmp_path / "torrent",
        )

    with pytest.raises(UploadError, match="torrent id"):
        tracker._torrent_id("https://pterclub.net/details.php")


@pytest.mark.asyncio
async def test_pterclub_validate_credentials_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        tracker, "validate_cookies", AsyncMock(return_value=True)
    )
    assert await tracker.validate_credentials(_meta())


@pytest.mark.asyncio
async def test_pterclub_saved_auth_token_invalid_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, _url: str) -> httpx.Response:
            return _response(text="not logged in")

    monkeypatch.setattr(
        pter_module.httpx, "AsyncClient", lambda *_args, **_kwargs: Client()
    )
    assert await tracker._saved_auth_token({"sid": "x"}) == ""


@pytest.mark.asyncio
async def test_pterclub_pterimg_upload_missing_cookie(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tracker = _tracker()
    missing = tmp_path / "missing.txt"
    monkeypatch.setattr(
        pter_module, "find_cookie_file", lambda *_args, **_kwargs: str(missing)
    )
    monkeypatch.setattr(
        tracker, "get_auth_token", AsyncMock(return_value="auth")
    )
    assert await tracker.pterimg_upload(_meta(tmp_path)) == []


@pytest.mark.asyncio
async def test_pterclub_upload_non_debug_delegates_submit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    tracker.common.create_torrent_for_upload = AsyncMock()  # type: ignore[method-assign]
    monkeypatch.setattr(tracker, "_ensure_description", AsyncMock())
    monkeypatch.setattr(
        tracker,
        "_upload_parts",
        AsyncMock(return_value=({"name": "Release"}, {}, Path("torrent"))),
    )
    monkeypatch.setattr(
        tracker, "_submit_upload", AsyncMock(return_value=True)
    )
    item = _meta(debug=False)
    assert await tracker.upload(item)
    tracker._submit_upload.assert_awaited_once_with(
        item, {"name": "Release"}, {}, Path("torrent")
    )


@pytest.mark.asyncio
async def test_pterclub_submit_upload_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = _tracker()
    cookie = tmp_path / "PTERCLUB.txt"
    cookie.write_text("cookie", encoding="utf-8")
    monkeypatch.setattr(
        pter_module, "find_cookie_file", lambda *_args, **_kwargs: str(cookie)
    )
    tracker.common.parse_cookie_file = AsyncMock(return_value={"sid": "x"})  # type: ignore[method-assign]
    monkeypatch.setattr(
        tracker, "_handle_upload_response", AsyncMock(return_value=True)
    )

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, **_kwargs: object) -> httpx.Response:
            return _response(url="https://pterclub.net/details.php?id=1")

    monkeypatch.setattr(
        pter_module.httpx, "AsyncClient", lambda *_args, **_kwargs: Client()
    )
    item = _meta(tmp_path)
    assert await tracker._submit_upload(item, {}, {}, tmp_path / "torrent")
    tracker._handle_upload_response.assert_awaited_once()
