from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.beyondhd import BEYONDHD

bhd_module = importlib.import_module("src.integrations.trackers.beyondhd")


def _config(**tracker_values: object) -> dict[str, Any]:
    tracker: dict[str, Any] = {
        "api_key": "api-key",
        "announce_url": "https://tracker.invalid/announce",
        "anon": False,
        "draft_default": False,
        "internal": False,
        "internal_groups": [],
        "bhd_rss_key": "",
    }
    tracker.update(tracker_values)
    return {"TRACKERS": {"BEYONDHD": tracker}, "DEFAULT": {}}


def _tracker(**tracker_values: object) -> BEYONDHD:
    return BEYONDHD(_config(**tracker_values))


def _meta(tmp_path: Path | None = None, **values: object) -> Meta:
    root = tmp_path or Path()
    state: dict[str, object] = {
        "base_dir": str(root),
        "uuid": "release",
        "path": str(root / "release.mkv"),
        "filelist": [str(root / "release.mkv")],
        "name": "Example Movie 2010 1080p WEB-DL-GROUP",
        "title": "Example Movie",
        "category": "MOVIE",
        "type": "WEBDL",
        "source": "WEB",
        "resolution": "1080p",
        "imdb": 123,
        "tmdb": 456,
        "sd": 0,
        "anon": 0,
        "is_disc": "",
        "bdinfo": {},
        "dvd_size": "DVD9",
        "uhd": "",
        "tag": "-GROUP",
        "tv_pack": 0,
        "season": "S01",
        "region": "USA",
        "edition": "",
        "three_d": "",
        "audio": "DD+ 5.1",
        "scene": False,
        "personalrelease": False,
        "has_commentary": False,
        "hdr": "",
        "video_codec": "AVC",
        "container": "mkv",
        "valid_mi_settings": True,
        "unattended": True,
        "unattended_confirm": False,
        "adult_media": False,
        "debug": False,
        "draft": False,
        "comparison": False,
        "comparison_groups": {},
        "tonemapped": False,
        "screens": 4,
        "image_list": [],
        "menu_images": [],
        "spectrograms_images": [],
        "dynamic_hdr_plot_images": [],
        "discs": [],
        "description": "base description",
        "ua_signature": "UA",
        "ua_name": "Upload Assistant",
        "current_version": "1.0",
        "tracker_status": {"BEYONDHD": {}},
    }
    state.update(values)
    return Meta(state)


def _response(payload: Any, *, status: int = 200, method: str = "POST", url: str = "https://beyond-hd.me/api/upload") -> httpx.Response:
    return httpx.Response(status, request=httpx.Request(method, url), json=payload)


@pytest.mark.asyncio
async def test_beyondhd_upload_success_delegates_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    tracker.common.create_torrent_for_upload = AsyncMock()  # type: ignore[method-assign]
    monkeypatch.setattr(tracker, "edit_desc", AsyncMock())
    monkeypatch.setattr(tracker, "_upload_request_parts", AsyncMock(return_value=({}, {})))
    monkeypatch.setattr(tracker, "_post_upload", AsyncMock(return_value="https://beyond-hd.me/details/77"))
    monkeypatch.setattr(tracker, "_seed_uploaded_torrent", AsyncMock(return_value=True))
    item = _meta()
    assert await tracker.upload(item)
    tracker._seed_uploaded_torrent.assert_awaited_once_with(item, "https://beyond-hd.me/details/77")


def test_beyondhd_upload_option_helpers() -> None:
    tracker = _tracker(internal=True, internal_groups=["GROUP"])
    data: dict[str, Any] = {}
    item = _meta(tag="-GROUP", tv_pack=1, season="S00", region="USA")
    tracker._apply_internal(data, item)
    tracker._apply_pack_special_region(data, item)
    assert data["internal"] == 1
    assert data["pack"] == 1
    assert data["special"] == 1
    assert data["region"] == "USA"


@pytest.mark.asyncio
async def test_beyondhd_debug_upload() -> None:
    tracker = _tracker()
    tracker.common.create_torrent_for_upload = AsyncMock()  # type: ignore[method-assign]
    item = _meta()
    assert await tracker._debug_upload(item, {"name": "Release"})
    assert item.tracker_status["BEYONDHD"]["status_message"].startswith("Debug mode")
    tracker.common.create_torrent_for_upload.assert_awaited_once()


@pytest.mark.asyncio
async def test_beyondhd_post_upload_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker, "_submit_request", AsyncMock(side_effect=RuntimeError("boom")))
    item = _meta()
    assert await tracker._post_upload(item, {}, {}) is None
    assert "boom" in item.tracker_status["BEYONDHD"]["status_message"]


@pytest.mark.asyncio
async def test_beyondhd_invalid_imdb_retry_and_invalid_name(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    retry = _response({"status_code": 1, "status_message": "https://beyond-hd.me/torrent/download/release.55.torrent"})
    monkeypatch.setattr(tracker, "_submit_request", AsyncMock(return_value=retry))
    data = {"imdb_id": 123, "name": "Release"}
    payload = {"status_code": 0, "status_message": "Invalid imdb_id 123"}
    result = await tracker._maybe_retry_invalid_imdb(_meta(), data, {}, payload)
    assert result["status_code"] == 1
    assert data["imdb_id"] == 1
    tracker._log_invalid_name({"status_code": 0, "status_message": "Invalid name value"}, "Release")


@pytest.mark.asyncio
async def test_beyondhd_details_link_paths() -> None:
    tracker = _tracker()
    item = _meta()
    assert tracker._details_link_from_payload(item, {}) is None
    assert item.tracker_status["BEYONDHD"]["status_message"].startswith("data error")

    item = _meta()
    assert tracker._details_link_from_payload(item, {"status_message": "uploaded but no download"}) == ""
    assert "No valid details link" in item.tracker_status["BEYONDHD"]["status_message"]

    item = _meta()
    payload = {"status_message": "https://beyond-hd.me/torrent/download/release.88.torrent"}
    assert tracker._details_link_from_payload(item, payload) == "https://beyond-hd.me/details/88"
    assert item.tracker_status["BEYONDHD"]["torrent_id"] == "88"


@pytest.mark.asyncio
async def test_beyondhd_seed_uploaded_torrent_failure() -> None:
    tracker = _tracker()
    tracker.common.create_torrent_ready_to_seed = AsyncMock(side_effect=RuntimeError("seed failure"))  # type: ignore[method-assign]
    assert not await tracker._seed_uploaded_torrent(_meta(), "https://beyond-hd.me/details/1")


@pytest.mark.asyncio
async def test_beyondhd_description_content_exercises_disc_comparison_header_and_screens(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    tracker.config["DEFAULT"]["tonemapped_header"] = "TONE HEADER"
    monkeypatch.setattr(bhd_module, "base_description", lambda _meta: "before https://old.invalid/a.png after [img]cover[/img]")

    def image_collection(_meta: Meta, _tracker: str, name: str) -> Any:
        if name == "menu_images":
            return [{"raw_url": "https://new.invalid/a.png"}]
        if name == "screenshots":
            return [
                {"web_url": "https://web/1", "img_url": "https://img/1"},
                {"web_url": "https://web/2", "img_url": "https://img/2"},
            ]
        return []

    monkeypatch.setattr(bhd_module, "get_tracker_image_collection", image_collection)
    item = _meta(
        menu_images=[{"raw_url": "https://old.invalid/a.png"}],
        discs=[
            {"type": "DVD", "vob_mi": "FIRST DVD"},
            {"type": "BDMV", "name": "DISC2", "summary": "BDINFO"},
            {"type": "HDDVD", "name": "DISC3", "largest_evo": "/x/movie.evo", "evo_mi": "EVOINFO"},
        ],
        comparison=True,
        comparison_groups={
            "1": {"name": "Source A", "urls": [{"raw_url": "https://cmp/a1"}]},
            "2": {"name": "Source B", "urls": [{"raw_url": "https://cmp/b1"}]},
        },
        tonemapped=True,
        screens=2,
    )
    text = await tracker._description_content(item)
    assert "https://new.invalid/a.png" in text
    assert "FIRST DVD" in text
    assert "BDINFO" in text
    assert "EVOINFO" in text
    assert "comparison=Source A, Source B" in text
    assert "https://cmp/a1" in text
    assert "TONE HEADER" in text
    assert "https://img/1" in text
    assert "[img width=300]cover" in text


def test_beyondhd_description_helper_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    item = _meta(menu_images="bad")
    monkeypatch.setattr(bhd_module, "get_tracker_image_collection", lambda *_args: "bad")
    assert tracker._replace_rehosted_collection(item, "text", "menu_images") == "text"
    assert tracker._replace_rehosted_url("text", {}, {}) == "text"
    assert tracker._disc_description_blocks(_meta(discs=[])) == []
    assert tracker._comparison_block(_meta(comparison=False)) == ""
    assert tracker._comparison_urls({}, []) == []
    assert tracker._group_raw_url({"urls": []}, 0) == ""
    assert tracker._tonemapped_header(_meta(tonemapped=False)) == ""
    assert tracker._screenshot_block(_meta()) == ""
    assert tracker._screenshot_links(_meta()) == []


@pytest.mark.asyncio
async def test_beyondhd_additional_check_failure_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    tracker.common.check_and_confirm_adult_media_upload = AsyncMock(return_value=True)  # type: ignore[method-assign]

    internal = _meta(name="Movie.1080p-FraMeSToR", unattended=True, unattended_confirm=False)
    assert not await tracker.get_additional_checks(internal)

    assert not await tracker.get_additional_checks(_meta(valid_mi_settings=False))
    assert not await tracker.get_additional_checks(_meta(type="REMUX", container="avi"))
    assert not await tracker.get_additional_checks(_meta(type="ENCODE", tag="-EVO", unattended=True, unattended_confirm=False))

    monkeypatch.setattr(bhd_module.cli_ui, "ask_yes_no", lambda *_args, **_kwargs: True)
    assert tracker._optional_policy_override(_meta(unattended=False))


@pytest.mark.asyncio
async def test_beyondhd_search_existing_rss_and_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker(bhd_rss_key="rss")
    payload = {
        "status_code": 1,
        "results": [
            {
                "name": "Release",
                "url": "https://beyond-hd.me/details/1",
                "size": 10,
                "dv": 1,
                "hdr10+": 1,
                "download_url": "https://download.invalid/1",
            }
        ],
    }

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, _url: str, *, params: dict[str, Any]) -> httpx.Response:
            assert params["rsskey"] == "rss"
            return _response(payload, url="https://beyond-hd.me/api/torrents/key")

    monkeypatch.setattr(bhd_module.httpx, "AsyncClient", lambda *_args, **_kwargs: Client())
    results = await tracker.search_existing(_meta())
    assert results[0]["flags"] == ["DV", "HDR"]
    assert results[0]["download"] == "https://download.invalid/1"

    params, has_rss = await tracker._search_params(_meta(category="TV", season="S02", sd=1, is_disc="DVD"))
    assert has_rss
    assert params["categories"] is None
    assert params["types"] is None
    assert params["search"] == "S02"

    with pytest.raises(RuntimeError, match="BEYONDHD API Error"):
        tracker._search_payload(_response({"status_code": 0, "message": "bad"}))
    assert tracker._search_items({"results": "bad"}) == []


@pytest.mark.asyncio
async def test_beyondhd_get_edition_custom_branch() -> None:
    custom, edition = await _tracker().get_edition(_meta(edition="Fan Cut"), [])
    assert custom is True
    assert edition == "Fan Cut"


@pytest.mark.asyncio
async def test_beyondhd_upload_returns_false_without_details_link(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    tracker.common.create_torrent_for_upload = AsyncMock()  # type: ignore[method-assign]
    monkeypatch.setattr(tracker, "edit_desc", AsyncMock())
    monkeypatch.setattr(tracker, "_upload_request_parts", AsyncMock(return_value=({}, {})))
    monkeypatch.setattr(tracker, "_post_upload", AsyncMock(return_value=None))
    assert not await tracker.upload(_meta())


@pytest.mark.asyncio
async def test_beyondhd_invalid_imdb_non_matching_error_returns_payload() -> None:
    tracker = _tracker()
    payload = {"status_code": 0, "status_message": "Different API error"}
    assert await tracker._maybe_retry_invalid_imdb(_meta(), {}, {}, payload) is payload


def test_beyondhd_tonemapped_header_rejects_non_mapping_default() -> None:
    tracker = _tracker()
    tracker.config["DEFAULT"] = "bad"
    assert tracker._tonemapped_header(_meta(tonemapped=True)) == ""


@pytest.mark.asyncio
async def test_beyondhd_additional_checks_reaches_adult_policy() -> None:
    tracker = _tracker()
    tracker.common.check_and_confirm_adult_media_upload = AsyncMock(return_value=True)  # type: ignore[method-assign]
    assert await tracker.get_additional_checks(_meta())
    tracker.common.check_and_confirm_adult_media_upload.assert_awaited_once()


@pytest.mark.asyncio
async def test_beyondhd_get_edition_known_edition_branch() -> None:
    custom, edition = await _tracker().get_edition(_meta(edition="extended"), [])
    assert custom is False
    assert edition == "extended"
