from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.hdbits import HDBits

hdb_module = importlib.import_module("src.integrations.trackers.hdbits")


def _config(**tracker_values: object) -> dict[str, Any]:
    tracker: dict[str, Any] = {
        "username": "user",
        "passkey": "pass",
        "announce_url": "https://tracker.invalid/announce",
        "img_rehost": True,
        "internal": False,
        "internal_groups": [],
    }
    tracker.update(tracker_values)
    return {"TRACKERS": {"HDBITS": tracker}, "DEFAULT": {"rehash_cooldown": 0}}


def _tracker(**tracker_values: object) -> HDBits:
    return HDBits(_config(**tracker_values))


def _meta(tmp_path: Path | None = None, **values: object) -> Meta:
    root = tmp_path or Path()
    state: dict[str, object] = {
        "base_dir": str(root),
        "uuid": "release",
        "path": str(root / "movie.mkv"),
        "video": str(root / "movie.mkv"),
        "filelist": [str(root / "movie.mkv")],
        "filename": "movie.mkv",
        "basename_no_ext": "Movie",
        "name": "Movie 2020 AMZN 1080p WEB-DL H.264 DDP 5.1-GROUP",
        "title": "Movie",
        "aka": "",
        "year": 2020,
        "category": "MOVIE",
        "type": "WEBDL",
        "source": "WEB",
        "resolution": "1080p",
        "video_codec": "AVC",
        "video_encode": "H.264",
        "service": "AMZN",
        "service_longname": "Amazon",
        "description": "description",
        "audio": "DDP 5.1",
        "hdr": "",
        "edition": "",
        "distributor": "",
        "silent": False,
        "anime": False,
        "is_disc": "",
        "has_encode_settings": False,
        "base_torrent_piece_mb": 4,
        "nohash": False,
        "tag": "-GROUP",
        "imdb_id": 123,
        "imdb": 123,
        "imdb_info": {
            "aka": "Movie",
            "year": 2020,
            "imdb_url": "https://imdb.invalid/title/tt123",
        },
        "tvdb_id": 0,
        "season_int": 1,
        "episode_int": 1,
        "genres": ["Drama"],
        "keywords": [],
        "region": "",
        "dvd_size": "DVD9",
        "scene": False,
        "tv_pack": False,
        "screens": 6,
        "image_list": [],
        "comparison": False,
        "comparison_groups": {},
        "discs": [],
        "debug": False,
        "tracker_status": {"HDBITS": {}},
    }
    state.update(values)
    return Meta(state)


def _response(
    *,
    status: int = 200,
    text: str = "",
    url: str = "https://hdbits.org/details.php?id=77&uploaded=1",
    payload: Any | None = None,
    content: bytes = b"",
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    request = httpx.Request("GET", url)
    if payload is not None:
        return httpx.Response(
            status,
            request=request,
            json=payload,
            headers=headers,
            content=content or None,
        )
    return httpx.Response(
        status,
        request=request,
        text=text,
        headers=headers,
        content=content or None,
    )


@pytest.mark.asyncio
async def test_hdbits_category_documentary_and_concert() -> None:
    tracker = _tracker()
    assert (
        await tracker.get_type_category_id(_meta(genres=["Documentary"])) == 3
    )
    concert = _meta(imdb_info={"type": "Concert", "genres": ["Music"]})
    assert await tracker.get_type_category_id(concert) == 4


def test_hdbits_strip_service_without_source_or_service() -> None:
    assert (
        HDBits._strip_service("Name", _meta(source="", service="")) == "Name"
    )


def test_hdbits_apply_imdb_name_changes_title_and_year() -> None:
    item = _meta(
        name="Title 2020",
        title="Title",
        year=2020,
        imdb_info={"aka": "AKA Title", "year": 2019},
    )
    assert HDBits._apply_imdb_name("Title 2020", item) == "AKA Title 2019"


@pytest.mark.asyncio
async def test_hdbits_upload_rejects_bad_mapping_and_dual_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker, "edit_desc", AsyncMock())
    monkeypatch.setattr(
        tracker,
        "_upload_identifiers",
        AsyncMock(return_value={"category": 0, "codec": 1, "medium": 1}),
    )
    assert await tracker.upload(_meta()) is None

    monkeypatch.setattr(
        tracker,
        "_upload_identifiers",
        AsyncMock(
            return_value={
                "name": "x",
                "category": 1,
                "codec": 1,
                "medium": 1,
                "tags": [],
            }
        ),
    )
    assert (
        await tracker.upload(
            _meta(audio="Dual-Audio", anime=False, is_disc="BDMV")
        )
        is None
    )


@pytest.mark.asyncio
async def test_hdbits_upload_success_delegates_response(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tracker = _tracker()
    identifiers = {
        "name": "x",
        "category": 1,
        "codec": 1,
        "medium": 1,
        "tags": [],
    }
    torrent_path = tmp_path / "torrent"
    response = _response()
    monkeypatch.setattr(tracker, "edit_desc", AsyncMock())
    monkeypatch.setattr(
        tracker, "_upload_identifiers", AsyncMock(return_value=identifiers)
    )
    monkeypatch.setattr(tracker, "_prepare_upload_torrent", AsyncMock())
    monkeypatch.setattr(
        tracker,
        "_upload_request_parts",
        AsyncMock(return_value=({}, {}, torrent_path)),
    )
    monkeypatch.setattr(
        tracker, "_post_upload", AsyncMock(return_value=response)
    )
    monkeypatch.setattr(
        tracker, "_handle_upload_response", AsyncMock(return_value=True)
    )
    item = _meta(tmp_path)
    assert await tracker.upload(item)
    tracker._handle_upload_response.assert_awaited_once_with(
        item, response, torrent_path
    )


@pytest.mark.asyncio
async def test_hdbits_rehash_cooldown_and_config_guards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    tracker.config["DEFAULT"]["rehash_cooldown"] = 1
    sleep = AsyncMock()
    monkeypatch.setattr(hdb_module.asyncio, "sleep", sleep)
    monkeypatch.setattr(
        hdb_module.TorrentCreator, "create_torrent", AsyncMock()
    )
    tracker.common.create_torrent_for_upload = AsyncMock()  # type: ignore[method-assign]
    await tracker._rehash_upload_torrent(_meta())
    sleep.assert_awaited_once_with(1)

    bad = HDBits({"TRACKERS": "bad", "DEFAULT": {"rehash_cooldown": "bad"}})
    assert bad._tracker_config() == {}
    assert bad._rehash_cooldown() == 0


@pytest.mark.asyncio
async def test_hdbits_debug_upload() -> None:
    tracker = _tracker()
    tracker.common.create_torrent_for_upload = AsyncMock()  # type: ignore[method-assign]
    item = _meta()
    assert await tracker._debug_upload(item, {"name": "x"})
    tracker.common.create_torrent_for_upload.assert_awaited_once()


@pytest.mark.asyncio
async def test_hdbits_post_upload_uses_cookie_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        "src.integrations.trackers.cookie_auth.find_cookie_file",
        lambda *_args, **_kwargs: str(tmp_path / "cookie"),
    )
    tracker.common.parse_cookie_file = AsyncMock(return_value={"sid": "x"})  # type: ignore[method-assign]
    expected = _response()

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, **_kwargs: object) -> httpx.Response:
            return expected

    monkeypatch.setattr(
        hdb_module.httpx, "AsyncClient", lambda *_args, **_kwargs: Client()
    )
    assert await tracker._post_upload(_meta(tmp_path), {}, {}) is expected


@pytest.mark.asyncio
async def test_hdbits_handle_upload_response_records_and_downloads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker, "download_new_torrent", AsyncMock())
    item = _meta(tmp_path)
    response = _response(url="https://hdbits.org/details.php?id=77&uploaded=1")
    assert await tracker._handle_upload_response(
        item, response, tmp_path / "x.torrent"
    )
    assert item.tracker_status["HDBITS"]["torrent_id"] == "77"
    tracker.download_new_torrent.assert_awaited_once()


@pytest.mark.asyncio
async def test_hdbits_search_existing_fallback_terms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        tracker, "_search_payload", AsyncMock(return_value={"base": 1})
    )
    monkeypatch.setattr(
        tracker,
        "_search_api",
        AsyncMock(
            side_effect=[
                [{"name": "one"}],
                [{"name": "two"}],
                [{"name": "three"}],
            ]
        ),
    )
    item = _meta(
        imdb_id=0, filename="Movie.mkv", aka="AKA Alt", basename_no_ext="Movie"
    )
    result = await tracker.search_existing(item)
    assert result == [{"name": "one"}, {"name": "two"}, {"name": "three"}]
    assert tracker._search_api.await_count == 3
    assert tracker._search_terms(item) == ["Movie.mkv", "Alt", "Movie"]


def test_hdbits_search_results_non_mapping() -> None:
    assert _tracker()._search_results([]) == []


@pytest.mark.asyncio
async def test_hdbits_validate_credentials_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        tracker, "validate_cookies", AsyncMock(return_value=True)
    )
    assert await tracker.validate_credentials(_meta())


@pytest.mark.asyncio
async def test_hdbits_validate_cookies_existing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tracker = _tracker()
    cookie = tmp_path / "HDBITS.txt"
    cookie.write_text("cookie", encoding="utf-8")
    monkeypatch.setattr(
        "src.integrations.trackers.cookie_auth.find_cookie_file",
        lambda *_args, **_kwargs: str(cookie),
    )

    class FakeCommon:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        @staticmethod
        async def parse_cookie_file(_path: str) -> dict[str, str]:
            return {"sid": "x"}

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, **_kwargs: object) -> httpx.Response:
            return _response(text='<a href="/logout.php">Logout</a>')

    monkeypatch.setattr(hdb_module, "Common", FakeCommon)
    monkeypatch.setattr(
        hdb_module.httpx, "AsyncClient", lambda *_args, **_kwargs: Client()
    )
    assert await tracker.validate_cookies(_meta(tmp_path))


@pytest.mark.asyncio
async def test_hdbits_download_new_torrent_writes_content(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tracker = _tracker()
    response = _response(
        content=b"d4:infod",
        headers={"content-type": "application/x-bittorrent"},
    )
    monkeypatch.setattr(
        tracker,
        "_torrent_filename_from_api",
        AsyncMock(return_value="x.torrent"),
    )
    monkeypatch.setattr(
        tracker, "_download_torrent_response", AsyncMock(return_value=response)
    )
    path = tmp_path / "download.torrent"
    await tracker.download_new_torrent("7", str(path))
    assert path.read_bytes() == b"d4:infod"


def test_hdbits_json_object_errors() -> None:
    response = _response(text="not-json")
    with pytest.raises(ValueError, match="Failed to parse JSON"):
        HDBits._json_object(response, "api", {})
    list_response = _response(payload=[])
    with pytest.raises(ValueError, match="expected object"):
        HDBits._json_object(list_response, "api", {})


def test_hdbits_torrent_filename_payload_success() -> None:
    assert (
        HDBits._torrent_filename_from_payload(
            {"data": [{"filename": "x.torrent"}]}, "api", {}
        )
        == "x.torrent"
    )


def test_hdbits_validate_torrent_download_rejects_invalid_body() -> None:
    response = _response(
        content=b"not-bencoded",
        headers={"content-type": "application/x-bittorrent"},
    )
    with pytest.raises(ValueError, match="does not appear"):
        HDBits._validate_torrent_download(response, "x.torrent", "1")


@pytest.mark.asyncio
async def test_hdbits_description_parts_note_images_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    tracker.signature = "SIGNATURE"
    monkeypatch.setattr(
        tracker, "_formatted_base_description", lambda _meta: "BASE"
    )
    monkeypatch.setattr(
        tracker, "_image_description_block", AsyncMock(return_value="IMAGES")
    )
    parts = await tracker._description_parts(_meta())
    assert parts == [
        "[center][quote]This release is sourced from Amazon[/quote][/center]",
        "BASE",
        "IMAGES",
        "SIGNATURE",
    ]


def test_hdbits_disc_description_parts() -> None:
    item = _meta(
        discs=[
            {"type": "DVD", "vob_mi": "FIRST"},
            {"type": "BDMV", "name": "DISC2", "summary": "BDINFO"},
        ]
    )
    text = "".join(HDBits._disc_description_parts(item))
    assert "FIRST" in text and "BDINFO" in text


@pytest.mark.asyncio
async def test_hdbits_image_description_rehost_and_manual(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker(img_rehost=True)
    monkeypatch.setattr(
        tracker, "hdbimg_upload", AsyncMock(return_value="BBCODE")
    )
    assert (
        await tracker._image_description_block(_meta())
        == "[center]BBCODE[/center]"
    )

    manual = _tracker(img_rehost=False)
    item = _meta(
        image_list=[{"web_url": "https://web", "img_url": "https://img"}],
        screens=1,
    )
    assert "https://img" in await manual._image_description_block(item)


def test_hdbits_comparison_header() -> None:
    item = _meta(comparison_groups={"2": {"name": "B"}, "1": {"name": "A"}})
    assert HDBits._comparison_header(item) == "A vs B"


@pytest.mark.asyncio
async def test_hdbits_image_upload_no_loaded_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        tracker,
        "_image_upload_plan",
        AsyncMock(return_value=(["x.png"], "w300", [])),
    )
    monkeypatch.setattr(
        tracker, "_planned_upload_files", AsyncMock(return_value={})
    )
    assert await tracker.hdbimg_upload(_meta()) is None


@pytest.mark.asyncio
async def test_hdbits_safe_image_upload_request_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    error = httpx.RequestError(
        "offline", request=httpx.Request("POST", "https://img.hdbits.org")
    )
    monkeypatch.setattr(
        tracker, "_execute_image_upload", AsyncMock(side_effect=error)
    )
    assert await tracker._safe_image_upload(_meta(), {}, [], {}) is None


def test_hdbits_comparison_image_plan_discovery(tmp_path: Path) -> None:
    tracker = _tracker()
    comparison = tmp_path / "comparison"
    comparison.mkdir()
    (comparison / "1-1-A.png").write_bytes(b"a")
    (comparison / "1-2-B.png").write_bytes(b"b")
    (comparison / "junk.txt").write_text("junk", encoding="utf-8")
    plan = tracker._comparison_image_plan(
        _meta(comparison=str(comparison), comparison_groups=None)
    )
    assert plan is not None
    files, _thumb, groups = plan
    assert len(files) == 2
    assert groups == ["1", "2"]


@pytest.mark.asyncio
async def test_hdbits_upload_image_batch_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(
            self, *_args: object, **_kwargs: object
        ) -> httpx.Response:
            return _response(status=500)

    monkeypatch.setattr(
        hdb_module.httpx, "AsyncClient", lambda *_args, **_kwargs: Client()
    )
    assert (
        await tracker._upload_image_batch(
            {"images_files[0]": ("x.png", b"x", "image/png")}, {}
        )
        is None
    )


@pytest.mark.asyncio
async def test_hdbits_upload_comparison_images_success_and_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    files = {"images_files[0]": ("x.png", b"x", "image/png")}
    monkeypatch.setattr(
        tracker, "_comparison_chunks", lambda *_args: [files, files]
    )
    monkeypatch.setattr(
        tracker, "_upload_comparison_chunk", AsyncMock(side_effect=["A", "B"])
    )
    assert await tracker._upload_comparison_images(files, ["1"], {}) == "AB"

    monkeypatch.setattr(
        tracker, "_upload_comparison_chunk", AsyncMock(return_value=None)
    )
    assert await tracker._upload_comparison_images(files, ["1"], {}) is None


def test_hdbits_chunk_comparison_rows_split() -> None:
    row = [("a.png", b"1234", "image/png")]
    chunks = HDBits._chunk_comparison_rows([row, row], max_size=5)
    assert len(chunks) == 2


@pytest.mark.asyncio
async def test_hdbits_upload_comparison_chunk_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(
            self, *_args: object, **_kwargs: object
        ) -> httpx.Response:
            return _response(status=500)

    monkeypatch.setattr(
        hdb_module.httpx, "AsyncClient", lambda *_args, **_kwargs: Client()
    )
    assert await tracker._upload_comparison_chunk(0, 1, {}, {}) is None


@pytest.mark.asyncio
async def test_hdbits_get_info_errors_and_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    request_error = httpx.RequestError(
        "offline", request=httpx.Request("POST", tracker.base_url)
    )
    monkeypatch.setattr(
        tracker, "_torrent_info_payload", AsyncMock(side_effect=request_error)
    )
    assert await tracker.get_info_from_torrent_id(1) == (
        None,
        None,
        None,
        None,
        None,
    )

    monkeypatch.setattr(
        tracker,
        "_torrent_info_payload",
        AsyncMock(side_effect=ValueError("bad")),
    )
    assert await tracker.get_info_from_torrent_id(1) == (
        None,
        None,
        None,
        None,
        None,
    )

    payload = {
        "status": 0,
        "data": [
            {
                "imdb": {"id": 11},
                "tvdb": {"id": 12},
                "name": "Name",
                "hash": "hash",
                "descr": "desc",
            }
        ],
    }
    monkeypatch.setattr(
        tracker, "_torrent_info_payload", AsyncMock(return_value=payload)
    )
    assert await tracker.get_info_from_torrent_id(1) == (
        11,
        12,
        "Name",
        "hash",
        "desc",
    )


@pytest.mark.asyncio
async def test_hdbits_torrent_info_payload_rejects_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(
            self, *_args: object, **_kwargs: object
        ) -> httpx.Response:
            return _response(payload=[])

    monkeypatch.setattr(
        hdb_module.httpx, "AsyncClient", lambda *_args, **_kwargs: Client()
    )
    with pytest.raises(ValueError, match="JSON object"):
        await tracker._torrent_info_payload(1)


@pytest.mark.asyncio
async def test_hdbits_search_filename_disc_and_request_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tracker = _tracker()
    temp = tmp_path / "tmp" / "release"
    temp.mkdir(parents=True)
    (temp / "BD_SUMMARY_00.txt").write_text(
        "Disc Title: DISC TITLE\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        tracker,
        "_filename_search_response",
        AsyncMock(return_value={"data": []}),
    )
    result = await tracker.search_filename(
        "ignored", "folder", _meta(tmp_path, is_disc="BDMV")
    )
    assert result == (None, None, None, None, None, None)

    request_error = httpx.RequestError(
        "offline", request=httpx.Request("POST", tracker.base_url)
    )
    monkeypatch.setattr(
        tracker,
        "_filename_search_response",
        AsyncMock(side_effect=request_error),
    )
    assert await tracker.search_filename(
        "movie.mkv", "file", _meta(tmp_path)
    ) == (None, None, None, None, None, None)


@pytest.mark.asyncio
async def test_hdbits_disc_search_title_missing_file(tmp_path: Path) -> None:
    assert (
        await _tracker()._disc_search_title(_meta(tmp_path, is_disc="BDMV"))
        == ""
    )


def test_hdbits_disc_title_from_lines() -> None:
    assert (
        HDBits._disc_title_from_lines(["x\n", "Disc Title: MY DISC\n"])
        == "MY DISC"
    )


@pytest.mark.asyncio
async def test_hdbits_filename_search_response_failure_and_nonobject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()

    class Client:
        response: httpx.Response

        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(
            self, *_args: object, **_kwargs: object
        ) -> httpx.Response:
            return self.response

    client = Client()
    client.response = _response(status=500)
    monkeypatch.setattr(
        hdb_module.httpx, "AsyncClient", lambda *_args, **_kwargs: client
    )
    assert await tracker._filename_search_response({}) == {}

    client.response = _response(payload=[])
    with pytest.raises(ValueError, match="JSON object"):
        await tracker._filename_search_response({})


@pytest.mark.asyncio
async def test_hdbits_search_filename_stops_without_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        tracker, "_filename_search_payload", AsyncMock(return_value=None)
    )
    assert await tracker.search_filename(
        "ignored", "folder", _meta(is_disc="BDMV")
    ) == (
        None,
        None,
        None,
        None,
        None,
        None,
    )
