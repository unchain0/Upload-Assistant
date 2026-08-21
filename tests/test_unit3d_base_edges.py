from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D import UNIT3D

unit3d_module = importlib.import_module("src.integrations.trackers.UNIT3D")


def _config(**tracker_values: object) -> dict[str, Any]:
    return {"DEFAULT": {}, "TRACKERS": {"TEST": dict(tracker_values)}}


def _tracker(**tracker_values: object) -> UNIT3D:
    return UNIT3D(_config(**tracker_values), "TEST")


def _meta(tmp_path: Path | None = None, **values: object) -> Meta:
    base = str(tmp_path or Path())
    state: dict[str, object] = {
        "base_dir": base,
        "uuid": "release",
        "name": "Release.Name",
        "title": "Release Title",
        "category": "MOVIE",
        "type": "WEBDL",
        "resolution": "1080p",
        "tmdb": 123,
        "tmdb_id": 123,
        "imdb_id": 456,
        "tvdb_id": 789,
        "mal_id": 0,
        "igdb_id": 0,
        "stream": 0,
        "sd": 0,
        "anon": 0,
        "keywords": [],
        "personalrelease": False,
        "tag": "-GROUP",
        "season_int": 1,
        "episode_int": 2,
        "freeleech": 0,
        "exclusive": False,
        "distributor": "",
        "region": "",
        "bdinfo": {},
        "audiobook": False,
        "path": str((tmp_path or Path()) / "release.mkv"),
        "filelist": [],
        "scene_nfo_file": "",
        "keep_nfo": False,
        "keep_folder": False,
        "isdir": False,
        "artwork_path": "",
        "artwork_banner_path": "",
        "debug": False,
        "ua_name": "Upload Assistant",
        "current_version": "1.0",
        "tracker_status": {"TEST": {}},
    }
    state.update(values)
    return Meta(state)


@pytest.mark.asyncio
async def test_unit3d_base_defaults_remote_errors_and_urls() -> None:
    tracker = _tracker()
    meta = _meta()
    assert await tracker.get_additional_checks(meta)
    assert await tracker.get_name(meta) == {"name": "Release.Name"}
    assert tracker._remote_error({"message": "bad"})

    tracker.expose_remote_error_details = False
    assert tracker._remote_error("secret") == "[tracker response omitted]"

    tracker.pending_url = "https://tracker.invalid/pending"
    urls = await tracker.get_search_urls(meta, [("name", "Release")])
    assert urls == [
        ("", [("name", "Release")], False),
        ("https://tracker.invalid/pending", [("name", "Release")], True),
    ]


def test_unit3d_search_name_and_book_title_shortening() -> None:
    tracker = _tracker()
    assert tracker.get_search_name(_meta(title="")) == "Release.Name"
    assert (
        tracker._book_search_name(
            _meta(category="BOOK"), "A Long Title: Subtitle"
        )
        == "A Long Title"
    )
    assert (
        tracker._book_search_name(_meta(category="BOOK"), "One: Subtitle")
        == "One: Subtitle"
    )


@pytest.mark.asyncio
async def test_unit3d_search_params_video_and_nonvideo() -> None:
    tracker = _tracker()
    movie = await tracker._search_params_dict(_meta(tmdb=None, tmdb_id=None))
    assert movie["categories[]"] == "1"
    tv = await tracker._search_params_dict(
        _meta(category="TV", title="Show", season="S01", tmdb=99)
    )
    assert tv["name"] == "Show S01"
    assert tv["tmdbId"] == "99"
    book = await tracker._search_params_dict(
        _meta(category="BOOK", title="Long Book Title: A Subtitle", tmdb=None)
    )
    assert book["name"] == "Long Book Title"
    assert book["categories[]"] == "0"


def test_unit3d_search_result_helpers() -> None:
    tracker = _tracker()
    pending = {
        "id": 1,
        "tmdb_id": 123,
        "name": "Pending",
        "files": [{"name": "a.mkv"}],
        "size": 10,
    }
    assert (
        tracker._search_result(_meta(is_disc=""), pending, True)["link"]
        == "/torrents/pending"
    )  # type: ignore[index]
    assert tracker._search_result(_meta(tmdb=999), pending, True) is None

    regular = {
        "id": 2,
        "attributes": {
            "name": "Release",
            "files": [{"name": "one.mkv"}, "bad"],
            "size": 20,
            "bd_info": "BDINFO",
            "description": "DESC",
        },
    }
    result = tracker._search_result(_meta(is_disc="BDMV"), regular, False)
    assert result is not None
    assert result["bd_info"] == "BDINFO"
    assert result["description"] == "DESC"
    assert tracker._result_attributes({"attributes": "bad"}, False) == {}
    assert tracker._file_count({"files": "bad"}) == 0
    assert tracker._file_names({"files": "bad"}) == []


def test_unit3d_response_entries_guard() -> None:
    request = httpx.Request("GET", "https://tracker.invalid")
    assert (
        UNIT3D._response_entries(httpx.Response(200, request=request, json=[]))
        == []
    )
    assert (
        UNIT3D._response_entries(
            httpx.Response(200, request=request, json={"data": "bad"})
        )
        == []
    )


@pytest.mark.asyncio
async def test_unit3d_description_and_media_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Builder:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def general_description_generator(
            self, *_args: object, **_kwargs: object
        ) -> str:
            return "description"

    monkeypatch.setattr(unit3d_module, "DescriptionBuilder", Builder)
    tracker = _tracker()
    assert await tracker.get_description(_meta()) == {
        "description": "description"
    }

    root = tmp_path / "tmp" / "release"
    root.mkdir(parents=True)
    (root / "MEDIAINFO_CLEANPATH.txt").write_text("MI", encoding="utf-8")
    (root / "BD_SUMMARY_00.txt").write_text("BD", encoding="utf-8")
    assert await tracker.get_mediainfo(_meta(tmp_path)) == {"mediainfo": "MI"}
    assert await tracker.get_mediainfo(_meta(tmp_path, bdinfo={"x": 1})) == {
        "mediainfo": ""
    }
    assert await tracker.get_mediainfo(_meta(tmp_path, category="BOOK")) == {
        "mediainfo": ""
    }
    assert await tracker.get_bdinfo(_meta(tmp_path, bdinfo={"x": 1})) == {
        "bdinfo": "BD"
    }
    assert await tracker.get_bdinfo(_meta(tmp_path, bdinfo={})) == {
        "bdinfo": ""
    }


@pytest.mark.asyncio
async def test_unit3d_mapping_modes_flags_and_identity_getters() -> None:
    tracker = _tracker(modq=True, internal=True, internal_groups=["GROUP"])
    meta = _meta(category="TV")
    assert (await tracker.get_category_id(meta, mapping_only=True))[
        "MOVIE"
    ] == "1"
    assert (await tracker.get_category_id(meta, reverse=True))["2"] == "TV"
    assert await tracker.get_category_id(meta, category="UNKNOWN") == {
        "category_id": "0"
    }
    assert (
        await tracker.get_type_id(meta, mapping_only=True)
        == tracker._type_mapping()
    )
    assert (await tracker.get_type_id(meta, reverse=True))["4"] == "WEBDL"
    assert await tracker.get_type_id(meta, type="UNKNOWN") == {"type_id": "0"}
    assert (await tracker.get_resolution_id(meta, mapping_only=True))[
        "1080p"
    ] == "3"
    assert (await tracker.get_resolution_id(meta, reverse=True))[
        "2"
    ] == "2160p"
    assert await tracker.get_resolution_id(meta, resolution="unknown") == {
        "resolution_id": "10"
    }

    assert await tracker.get_flag(_meta(modq=True), "modq") == "1"
    assert await tracker.get_flag(_meta(), "modq") == "1"
    tracker.tracker_config["draft"] = False
    assert await tracker.get_flag(_meta(), "draft") == "0"
    assert await tracker.get_flag(_meta(), "missing") == "0"

    tracker.common.unit3d_distributor_ids = AsyncMock(side_effect=["5", ""])  # type: ignore[method-assign]
    assert await tracker.get_distributor_id(
        _meta(distributor="Criterion")
    ) == {"distributor_id": "5"}
    assert await tracker.get_distributor_id(_meta(distributor="")) == {}
    tracker.common.unit3d_region_ids = AsyncMock(side_effect=["7", ""])  # type: ignore[method-assign]
    assert await tracker.get_region_id(_meta(region="US")) == {
        "region_id": "7"
    }
    assert await tracker.get_region_id(_meta(region="")) == {}

    assert await tracker.get_tmdb(_meta(tmdb=None)) == {"tmdb": "0"}
    assert await tracker.get_imdb(_meta(category="BOOK", imdb_id=123)) == {
        "imdb": "0"
    }
    assert await tracker.get_tvdb(_meta(category="MOVIE", tvdb_id=123)) == {
        "tvdb": "0"
    }
    assert await tracker.get_mal(_meta(mal_id=55)) == {"mal": "55"}
    assert await tracker.get_igdb(_meta(category="GAME", igdb_id=88)) == {
        "igdb": "88"
    }
    assert await tracker.get_igdb(_meta(category="MOVIE", igdb_id=88)) == {
        "igdb": "0"
    }
    assert await tracker.get_stream(_meta(stream=1)) == {"stream": "1"}
    assert await tracker.get_sd(_meta(sd=1)) == {"sd": "1"}
    assert await tracker.get_anonymous(_meta(anon=0)) == {"anonymous": "0"}
    assert await tracker.get_anonymous(_meta(anon=1)) == {"anonymous": "1"}
    assert await tracker.get_personal_release(_meta(personalrelease=True)) == {
        "personal_release": "1"
    }
    assert await tracker.get_internal(_meta(tag="-GROUP")) == {"internal": "1"}
    assert await tracker.get_internal(_meta(tag="-OTHER")) == {"internal": "0"}
    assert await tracker.get_season_number(meta) == {"season_number": "1"}
    assert await tracker.get_season_number(_meta(category="MOVIE")) == {}
    assert await tracker.get_episode_number(meta) == {"episode_number": "2"}
    assert await tracker.get_episode_number(_meta(category="MOVIE")) == {}
    assert await tracker.get_featured(meta) == {"featured": "0"}
    assert await tracker.get_free(_meta(freeleech=50)) == {"free": "50"}
    assert await tracker.get_free(_meta(freeleech=0)) == {"free": "0"}
    assert await tracker.get_doubleup(meta) == {"doubleup": "0"}
    assert await tracker.get_sticky(meta) == {"sticky": "0"}
    assert await tracker.get_additional_data(meta) == {}


@pytest.mark.asyncio
async def test_unit3d_keywords_limit_and_truncation() -> None:
    tracker = _tracker()
    assert await tracker.get_keywords(
        _meta(keywords=[" one ", "", "two"])
    ) == {"keywords": "one, two"}
    long = "x" * 300
    result = await tracker.get_keywords(_meta(keywords=[long]))
    assert len(result["keywords"]) == 255
    result = await tracker.get_keywords(_meta(keywords=["first", "y" * 300]))
    assert result == {"keywords": "first"}


@pytest.mark.asyncio
async def test_unit3d_get_data_merges_and_sets_exclusive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        tracker,
        "get_description",
        AsyncMock(return_value={"description": "desc"}),
    )
    monkeypatch.setattr(
        tracker, "get_mediainfo", AsyncMock(return_value={"mediainfo": "mi"})
    )
    monkeypatch.setattr(
        tracker, "get_bdinfo", AsyncMock(return_value={"bdinfo": ""})
    )
    monkeypatch.setattr(tracker, "get_region_id", AsyncMock(return_value={}))
    monkeypatch.setattr(
        tracker, "get_distributor_id", AsyncMock(return_value={})
    )
    data = await tracker.get_data(_meta(exclusive=True))
    assert data["name"] == "Release.Name"
    assert data["exclusive"] == "1"
    assert data["description"] == "desc"


def test_unit3d_image_type_signatures() -> None:
    assert UNIT3D._image_type(b"\xff\xd8\xffrest") == (".jpg", "image/jpeg")
    assert UNIT3D._image_type(b"\x89PNG\r\n\x1a\nrest") == (
        ".png",
        "image/png",
    )
    assert UNIT3D._image_type(b"GIF89arest") == (".gif", "image/gif")
    assert UNIT3D._image_type(b"RIFF1234WEBPrest") == (".webp", "image/webp")
    assert UNIT3D._image_type(b"unknown") is None


@pytest.mark.asyncio
async def test_unit3d_get_image_file_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = _tracker()
    missing = tmp_path / "missing.png"
    assert await tracker.get_image_file(missing) is None

    image = tmp_path / "cover.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nrest")
    assert await tracker.get_image_file(image, max_size=1) is None

    monkeypatch.setattr(
        unit3d_module, "is_valid_image_bytes", lambda _data: False
    )
    assert await tracker.get_image_file(image) is None

    monkeypatch.setattr(
        unit3d_module, "is_valid_image_bytes", lambda _data: True
    )
    result = await tracker.get_image_file(image)
    assert result == ("cover.png", b"\x89PNG\r\n\x1a\nrest", "image/png")

    bad = tmp_path / "bad.bin"
    bad.write_bytes(b"unknown")
    assert await tracker.get_image_file(bad) is None


@pytest.mark.asyncio
async def test_unit3d_additional_files_nfo_and_artwork(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = _tracker()
    root = tmp_path / "game"
    root.mkdir()
    nfo = root / "scene.nfo"
    nfo.write_bytes(b"nfo")
    game = _meta(
        tmp_path,
        category="GAME",
        path=str(root / "game.iso"),
        scene_nfo_file="scene.nfo",
        filelist=[],
    )
    files = await tracker.get_additional_files(game)
    assert files["nfo"] == ("scene.nfo", b"nfo", "text/plain")

    temp_root = tmp_path / "tmp" / "release"
    temp_root.mkdir(parents=True, exist_ok=True)
    temp_nfo = temp_root / "temp.nfo"
    temp_nfo.write_bytes(b"temp")
    files = await tracker.get_additional_files(
        _meta(tmp_path, category="MOVIE")
    )
    assert files["nfo"][0] == "temp.nfo"

    image_reader = AsyncMock(
        side_effect=[
            ("cover.png", b"c", "image/png"),
            ("banner.png", b"b", "image/png"),
        ]
    )
    monkeypatch.setattr(tracker, "get_image_file", image_reader)
    book = _meta(
        tmp_path,
        category="BOOK",
        artwork_path="cover.png",
        artwork_banner_path="banner.png",
    )
    files = await tracker.get_additional_files(book)
    assert set(files) >= {"torrent-cover", "torrent-banner"}
    assert (
        image_reader.await_args_list[0].kwargs["max_size"] == 5 * 1024 * 1024
    )
    assert image_reader.await_args_list[1].kwargs["max_size"] is None


@pytest.mark.asyncio
async def test_unit3d_kept_nfo_path(tmp_path: Path) -> None:
    tracker = _tracker()
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    media = media_dir / "release.mkv"
    media.write_bytes(b"video")
    nfo = media_dir / "kept.nfo"
    nfo.write_bytes(b"nfo")
    item = _meta(tmp_path, path=str(media), keep_nfo=True, keep_folder=True)
    assert tracker._kept_nfo_path(item) == nfo


@pytest.mark.asyncio
async def test_unit3d_upload_request_parts_and_debug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = _tracker(api_key="key")
    root = tmp_path / "tmp" / "release"
    root.mkdir(parents=True)
    (root / "BASE.torrent").write_bytes(b"torrent")
    monkeypatch.setattr(
        tracker, "get_upload_torrent_filename", AsyncMock(return_value="BASE")
    )
    monkeypatch.setattr(
        tracker, "get_additional_files", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        tracker, "get_data", AsyncMock(return_value={"name": "Release"})
    )
    data, files, headers = await tracker._upload_request_parts(_meta(tmp_path))
    assert data == {"name": "Release"}
    assert files["torrent"][1] == b"torrent"
    assert headers["authorization"] == "Bearer key"

    tracker.common.create_torrent_for_upload = AsyncMock()  # type: ignore[method-assign]
    item = _meta(tmp_path, tracker_status={"TEST": {}})
    assert await tracker._debug_upload(item, data)
    tracker.common.create_torrent_for_upload.assert_awaited_once()


@pytest.mark.asyncio
async def test_unit3d_post_attempt_success_redirect_and_api_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    success = httpx.Response(
        200,
        request=httpx.Request("POST", "https://tracker.invalid/upload"),
        json={"success": True, "message": "ok", "data": "/123.file"},
    )
    monkeypatch.setattr(
        tracker, "_upload_response", AsyncMock(return_value=success)
    )
    item = _meta(tracker_status={"TEST": {}})
    data = await tracker._post_upload_attempt(item, {}, {}, {}, 40.0)
    assert data["success"] is True
    assert item.tracker_status["TEST"]["torrent_id"] == "123"

    redirect = httpx.Response(
        302,
        request=httpx.Request("POST", "https://tracker.invalid/upload"),
        headers={"location": "/login"},
    )
    tracker.follow_upload_redirects = False
    monkeypatch.setattr(
        tracker, "_upload_response", AsyncMock(return_value=redirect)
    )
    item = _meta(tracker_status={"TEST": {}})
    assert await tracker._post_with_retries(item, {}, {}, {}) is None
    assert (
        item.tracker_status["TEST"]["status_message"]
        == "data error: Upload redirect rejected"
    )

    tracker.follow_upload_redirects = True
    rejected = httpx.Response(
        200,
        request=httpx.Request("POST", "https://tracker.invalid/upload"),
        json={"success": False, "message": "bad"},
    )
    monkeypatch.setattr(
        tracker, "_upload_response", AsyncMock(return_value=rejected)
    )
    item = _meta(tracker_status={"TEST": {}})
    assert await tracker._post_with_retries(item, {}, {}, {}) is None
    assert item.tracker_status["TEST"]["status_message"] == "API error: bad"


@pytest.mark.asyncio
async def test_unit3d_post_with_retries_success_and_value_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        tracker,
        "_post_upload_attempt",
        AsyncMock(return_value={"data": "https://download.invalid/torrent"}),
    )
    assert (
        await tracker._post_with_retries(
            _meta(tracker_status={"TEST": {}}), {}, {}, {}
        )
        == "https://download.invalid/torrent"
    )

    monkeypatch.setattr(
        tracker,
        "_post_upload_attempt",
        AsyncMock(side_effect=ValueError("bad json")),
    )
    item = _meta(tracker_status={"TEST": {}})
    assert await tracker._post_with_retries(item, {}, {}, {}) is None
    assert (
        "Invalid JSON response"
        in item.tracker_status["TEST"]["status_message"]
    )


@pytest.mark.asyncio
async def test_unit3d_http_error_decisions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker, "_retry_delay", AsyncMock())

    def error(code: int, text: str = "error") -> httpx.HTTPStatusError:
        request = httpx.Request("POST", "https://tracker.invalid/upload")
        response = httpx.Response(code, request=request, text=text)
        return httpx.HTTPStatusError("bad", request=request, response=response)

    item = _meta(tracker_status={"TEST": {}})
    assert await tracker._http_error_decision(item, error(403), 0, 40.0) == (
        False,
        40.0,
    )
    assert "Forbidden" in item.tracker_status["TEST"]["status_message"]

    item = _meta(tracker_status={"TEST": {}})
    assert await tracker._http_error_decision(item, error(302), 0, 40.0) == (
        False,
        40.0,
    )
    assert "Redirect" in item.tracker_status["TEST"]["status_message"]

    duplicate_text = '{"data":{"name":["The name has already been taken."]}}'
    item = _meta(tracker_status={"TEST": {}})
    assert await tracker._http_error_decision(
        item, error(422, duplicate_text), 0, 40.0
    ) == (False, 40.0)
    assert item.tracker_status["TEST"]["dupe"] is True

    item = _meta(tracker_status={"TEST": {}})
    assert await tracker._http_error_decision(item, error(401), 0, 40.0) == (
        True,
        40.0,
    )
    assert await tracker._http_error_decision(item, error(401), 1, 40.0) == (
        False,
        40.0,
    )

    item = _meta(tracker_status={"TEST": {}})
    assert await tracker._http_error_decision(item, error(500), 0, 40.0) == (
        True,
        40.0,
    )
    assert await tracker._http_error_decision(item, error(520), 1, 40.0) == (
        False,
        40.0,
    )
    assert "cloudflare" in item.tracker_status["TEST"]["status_message"]


@pytest.mark.asyncio
async def test_unit3d_timeout_request_and_dispatch_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker, "_retry_delay", AsyncMock())
    item = _meta(tracker_status={"TEST": {}})
    assert await tracker._timeout_error_decision(item, 0, 40.0) == (True, 60.0)
    assert await tracker._timeout_error_decision(item, 1, 60.0) == (
        False,
        60.0,
    )
    assert "timed out" in item.tracker_status["TEST"]["status_message"]

    request_error = httpx.RequestError(
        "offline", request=httpx.Request("POST", "https://tracker.invalid")
    )
    item = _meta(tracker_status={"TEST": {}})
    assert await tracker._request_error_decision(
        item, request_error, 0, 40.0, {}
    ) == (True, 40.0)
    assert await tracker._request_error_decision(
        item, request_error, 1, 40.0, {"message": "x"}
    ) == (False, 40.0)
    assert "offline" in item.tracker_status["TEST"]["status_message"]

    item = _meta(tracker_status={"TEST": {}})
    retry, _ = await tracker._upload_error_decision(
        item, httpx.TimeoutException("timeout"), 1, 40.0, {}
    )
    assert not retry


@pytest.mark.asyncio
async def test_unit3d_download_uploaded_torrent_paths() -> None:
    tracker = _tracker()
    item = _meta(tracker_status={"TEST": {}})
    assert not await tracker._download_uploaded_torrent(item, {}, "")

    tracker.download_url_hosts = ("tracker.invalid",)
    tracker.common.download_tracker_torrent = AsyncMock(return_value=None)  # type: ignore[method-assign]
    item = _meta(tracker_status={"TEST": {}})
    assert not await tracker._download_uploaded_torrent(
        item, {}, "https://tracker.invalid/file"
    )

    tracker.download_url_hosts = ()
    tracker.common.download_tracker_torrent = AsyncMock(return_value=None)  # type: ignore[method-assign]
    assert await tracker._download_uploaded_torrent(
        _meta(tracker_status={"TEST": {}}), {}, "https://tracker.invalid/file"
    )


@pytest.mark.asyncio
async def test_unit3d_upload_wrapper_success_and_debug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        tracker, "_upload_request_parts", AsyncMock(return_value=({}, {}, {}))
    )
    monkeypatch.setattr(
        tracker,
        "_post_with_retries",
        AsyncMock(return_value="https://download.invalid/file"),
    )
    monkeypatch.setattr(
        tracker, "_download_uploaded_torrent", AsyncMock(return_value=True)
    )
    assert await tracker.upload(_meta(tracker_status={"TEST": {}}))

    monkeypatch.setattr(tracker, "_debug_upload", AsyncMock(return_value=True))
    assert await tracker.upload(_meta(debug=True, tracker_status={"TEST": {}}))


@pytest.mark.asyncio
async def test_unit3d_bounded_response_limits() -> None:
    request = httpx.Request("GET", "https://tracker.invalid")
    too_large_header = httpx.Response(
        200, request=request, headers={"content-length": "100"}, content=b"x"
    )
    with pytest.raises(ValueError, match="exceeds"):
        await UNIT3D._bounded_response(too_large_header, 10)

    too_large_body = httpx.Response(
        200, request=request, content=b"12345678901"
    )
    with pytest.raises(ValueError, match="exceeds"):
        await UNIT3D._bounded_response(too_large_body, 10)

    ok = httpx.Response(200, request=request, content=b"1234")
    bounded = await UNIT3D._bounded_response(ok, 10)
    assert bounded.content == b"1234"


@pytest.mark.asyncio
async def test_unit3d_response_parsers() -> None:
    tracker = _tracker()
    assert await tracker.get_torrent_id({"data": "/12345.hash"}) == "12345"
    assert await tracker.get_torrent_id({}) == ""
    assert (
        await tracker.process_response_data({"success": True, "message": "ok"})
        == "ok"
    )
    assert (
        await tracker.process_response_data(
            {"success": False, "message": "bad"}
        )
        == "API response: bad"
    )
    assert (
        await tracker.process_response_data({"success": False, "data": 1})
        == "API response: {'success': False, 'data': 1}"
    )


def test_unit3d_book_search_name_nonbook_passthrough() -> None:
    assert (
        UNIT3D._book_search_name(_meta(category="MOVIE"), "Title: Subtitle")
        == "Title: Subtitle"
    )


@pytest.mark.asyncio
async def test_unit3d_search_redirect_and_failed_status_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    tracker.follow_search_redirects = False
    redirect = httpx.Response(
        302, request=httpx.Request("GET", "https://tracker.invalid/search")
    )
    monkeypatch.setattr(
        tracker, "_request_search_response", AsyncMock(return_value=redirect)
    )
    with pytest.raises(ValueError, match="redirect rejected"):
        await tracker._search_response(
            AsyncMock(), "https://tracker.invalid/search", [], {}, False
        )

    failed = httpx.Response(
        500,
        request=httpx.Request("GET", "https://tracker.invalid/search"),
        json={"data": []},
    )
    dupes: list[dict[str, Any]] = []
    tracker._append_search_results(_meta(), failed, False, dupes)
    assert dupes == []

    ok = httpx.Response(
        200,
        request=httpx.Request("GET", "https://tracker.invalid/search"),
        json={
            "data": [
                {
                    "id": 1,
                    "attributes": {"name": "Release", "size": 10, "files": []},
                }
            ]
        },
    )
    tracker._append_search_results(_meta(), ok, False, dupes)
    assert dupes[0]["name"] == "Release"


class _StreamContext:
    def __init__(self, response: httpx.Response) -> None:
        self.response = response

    async def __aenter__(self) -> httpx.Response:
        return self.response

    async def __aexit__(self, *_args: object) -> None:
        return None


class _SearchStreamClient:
    def __init__(self, response: httpx.Response) -> None:
        self.response = response

    def stream(self, *_args: object, **_kwargs: object) -> _StreamContext:
        return _StreamContext(self.response)


@pytest.mark.asyncio
async def test_unit3d_bounded_search_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    tracker.max_json_response_size = 1024
    response = httpx.Response(
        200,
        request=httpx.Request("GET", "https://tracker.invalid/search"),
        json={"data": []},
    )
    bounded = httpx.Response(200, request=response.request, json={"data": []})
    monkeypatch.setattr(
        tracker, "_bounded_response", AsyncMock(return_value=bounded)
    )
    result = await tracker._request_search_response(
        _SearchStreamClient(response), "https://tracker.invalid/search", [], {}
    )  # type: ignore[arg-type]
    assert result is bounded
    tracker._bounded_response.assert_awaited_once()


@pytest.mark.asyncio
async def test_unit3d_read_image_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = _tracker()
    image = tmp_path / "cover.png"
    image.write_bytes(b"png")
    monkeypatch.setattr(
        tracker,
        "_image_path_allowed",
        lambda *_args: (_ for _ in ()).throw(OSError("stat failed")),
    )
    assert await tracker._read_image_bytes(image, None) is None


@pytest.mark.asyncio
async def test_unit3d_get_upload_torrent_filename_delegates() -> None:
    tracker = _tracker()
    tracker.common.get_torrent_filename = AsyncMock(return_value="CUSTOM")  # type: ignore[method-assign]
    assert await tracker.get_upload_torrent_filename(_meta()) == "CUSTOM"


@pytest.mark.asyncio
async def test_unit3d_upload_returns_false_when_post_never_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        tracker, "_upload_request_parts", AsyncMock(return_value=({}, {}, {}))
    )
    monkeypatch.setattr(
        tracker, "_post_with_retries", AsyncMock(return_value=None)
    )
    assert not await tracker.upload(_meta(tracker_status={"TEST": {}}))


@pytest.mark.asyncio
async def test_unit3d_post_with_retries_exhausts_retry_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        tracker,
        "_post_upload_attempt",
        AsyncMock(side_effect=RuntimeError("retry")),
    )
    monkeypatch.setattr(
        tracker, "_upload_error_decision", AsyncMock(return_value=(True, 40.0))
    )
    assert (
        await tracker._post_with_retries(
            _meta(tracker_status={"TEST": {}}), {}, {}, {}
        )
        is None
    )
    assert tracker._post_upload_attempt.await_count == 2


class _UploadClient:
    def __init__(self, response: httpx.Response, **_kwargs: object) -> None:
        self.response = response

    async def __aenter__(self) -> _UploadClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def post(self, **_kwargs: object) -> httpx.Response:
        return self.response

    def stream(self, *_args: object, **_kwargs: object) -> _StreamContext:
        return _StreamContext(self.response)


@pytest.mark.asyncio
async def test_unit3d_upload_response_direct_and_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    response = httpx.Response(
        200,
        request=httpx.Request("POST", "https://tracker.invalid/upload"),
        json={"success": True},
    )
    monkeypatch.setattr(
        unit3d_module.httpx,
        "AsyncClient",
        lambda **kwargs: _UploadClient(response, **kwargs),
    )
    assert await tracker._upload_response({}, {}, {}, 40.0) is response

    tracker.max_json_response_size = 1024
    bounded = httpx.Response(
        200, request=response.request, json={"success": True}
    )
    monkeypatch.setattr(
        tracker, "_bounded_response", AsyncMock(return_value=bounded)
    )
    assert await tracker._upload_response({}, {}, {}, 40.0) is bounded
    tracker._bounded_response.assert_awaited_once()


def test_unit3d_json_object_rejects_non_mapping() -> None:
    response = httpx.Response(
        200,
        request=httpx.Request("POST", "https://tracker.invalid/upload"),
        json=[],
    )
    with pytest.raises(ValueError, match="JSON object"):
        UNIT3D._json_object(response)


@pytest.mark.asyncio
async def test_unit3d_upload_error_dispatch_http_and_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        tracker, "_http_error_decision", AsyncMock(return_value=(False, 40.0))
    )
    request = httpx.Request("POST", "https://tracker.invalid/upload")
    response = httpx.Response(500, request=request)
    http_error = httpx.HTTPStatusError(
        "bad", request=request, response=response
    )
    assert await tracker._upload_error_decision(
        _meta(), http_error, 0, 40.0, {}
    ) == (False, 40.0)

    monkeypatch.setattr(
        tracker,
        "_request_error_decision",
        AsyncMock(return_value=(False, 40.0)),
    )
    request_error = httpx.RequestError("offline", request=request)
    assert await tracker._upload_error_decision(
        _meta(), request_error, 0, 40.0, {}
    ) == (False, 40.0)


def test_unit3d_final_http_error_non_520() -> None:
    tracker = _tracker()
    request = httpx.Request("POST", "https://tracker.invalid/upload")
    response = httpx.Response(500, request=request, text="server error")
    error = httpx.HTTPStatusError("bad", request=request, response=response)
    item = _meta(tracker_status={"TEST": {}})
    tracker._final_http_error_status(item, error)
    assert "HTTP 500" in item.tracker_status["TEST"]["status_message"]


@pytest.mark.asyncio
async def test_unit3d_retry_delay_calls_sleep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    sleep = AsyncMock()
    monkeypatch.setattr(unit3d_module.asyncio, "sleep", sleep)
    await tracker._retry_delay("Request error", 0, 40.0)
    sleep.assert_awaited_once_with(5)


class _ChunkedResponse:
    status_code = 200
    request = httpx.Request("GET", "https://tracker.invalid/data")

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}

    async def aiter_bytes(self):
        yield b"123456"
        yield b"789012"


@pytest.mark.asyncio
async def test_unit3d_bounded_response_body_limit_without_content_length() -> (
    None
):
    with pytest.raises(ValueError, match="exceeds"):
        await UNIT3D._bounded_response(_ChunkedResponse(), 10)  # type: ignore[arg-type]
