from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from bs4 import BeautifulSoup

from src.domain_models.release import Meta
from src.integrations.trackers.AVISTAZ import AZTrackerBase

az_module = importlib.import_module("src.integrations.trackers.AVISTAZ")


def _config(name: str = "AVISTAZ", **values: object) -> dict[str, Any]:
    tracker: dict[str, Any] = {
        "base_url": f"https://{name.lower()}.invalid",
        "requests_url": f"https://{name.lower()}.invalid/requests",
        "announce_url": f"https://{name.lower()}.invalid/announce",
        "source_flag": name,
        "check_for_rules": True,
        "anon": False,
        "internal": False,
        "add_audio_spectrogram": False,
        "add_dynamic_hdr_plot": False,
    }
    tracker.update(values)
    return {"TRACKERS": {name: tracker}, "DEFAULT": {"search_requests": True}}


def _tracker(name: str = "AVISTAZ", **values: object) -> AZTrackerBase:
    return AZTrackerBase(_config(name, **values), name)


def _meta(tmp_path: Path | None = None, **values: object) -> Meta:
    root = tmp_path or Path()
    state: dict[str, object] = {
        "base_dir": str(root),
        "uuid": "release",
        "path": str(root / "movie.mkv"),
        "category": "MOVIE",
        "title": "Example",
        "name": "Example 2024 1080p WEB-DL H.264-GROUP",
        "aka": "",
        "manual_episode_title": "",
        "daily_episode_title": "",
        "has_encode_settings": False,
        "tag": "GROUP",
        "year": 2024,
        "no_year": False,
        "search_year": "",
        "season_int": 1,
        "episode_int": 1,
        "season": "S01",
        "episode": "E01",
        "imdb_info": {"imdbID": "tt123", "seasons_summary": []},
        "tmdb": 456,
        "tvdb": 789,
        "tv_pack": 0,
        "source": "WEB",
        "audio": "DD 5.1",
        "type": "WEBDL",
        "is_disc": "",
        "region": "",
        "resolution": "1080p",
        "video_codec": "AVC",
        "edition": "",
        "webdv": False,
        "sd": False,
        "unattended": False,
        "unattended_confirm": False,
        "skipping": "",
        "language_checked": True,
        "subtitle_languages": ["English"],
        "audio_languages": ["English"],
        "keywords": ["drama"],
        "personalrelease": False,
        "search_requests": False,
        "video_width": 1920,
        "video_height": 1080,
        "anon": 0,
        "debug": False,
        "current_version": "1.0",
        "tracker_status": {"AVISTAZ": {}, "CINEMAZ": {}, "PRIVATEHD": {}},
        "dynamic_hdr_plot": False,
        "menu_images": [],
        "image_list": [],
        "spectrograms_images": [],
        "dynamic_hdr_plot_images": [],
        "screens": 6,
        "ua_name": "Upload Assistant",
    }
    state.update(values)
    return Meta(state)


def _response(
    *,
    status: int = 200,
    text: str = "",
    url: str = "https://avistaz.invalid/",
    json_data: Any | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    request = httpx.Request("GET", url)
    if json_data is not None:
        return httpx.Response(status, request=request, json=json_data, headers=headers)
    return httpx.Response(status, request=request, text=text, headers=headers)


def test_avistaz_tracker_config_guard_and_default_rules() -> None:
    tracker = AZTrackerBase({"TRACKERS": "bad"}, "AVISTAZ")
    assert tracker._tracker_config() == {}
    assert tracker.rules(_meta()) == ""


@pytest.mark.asyncio
async def test_avistaz_get_media_code_unsupported_and_lookup_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    assert not await tracker.get_media_code(_meta(category="BOOK"))

    monkeypatch.setattr(tracker, "_media_lookup_attempt", AsyncMock(return_value=True))
    assert await tracker.get_media_code(_meta())

    tracker._media_lookup_attempt = AsyncMock(side_effect=[False, True])  # type: ignore[method-assign]
    monkeypatch.setattr(tracker, "_handle_missing_media", AsyncMock(return_value=True))
    assert await tracker.get_media_code(_meta())


@pytest.mark.asyncio
async def test_avistaz_media_lookup_attempt_delayed_match(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    monkeypatch.setattr(az_module.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(tracker, "_media_lookup_data", AsyncMock(return_value={"data": [{"id": 42, "imdb": "tt123"}]}))
    identifiers = {"imdb": "tt123", "tmdb": "456", "title": "Example"}
    assert await tracker._media_lookup_attempt(_meta(), "1", identifiers, {}, delayed=True)
    assert tracker.media_code == "42"
    assert tracker._matching_media_item({"data": [{"id": 1, "tmdb": "456"}]}, identifiers) == {"id": 1, "tmdb": "456"}


@pytest.mark.asyncio
async def test_avistaz_missing_media_unattended_and_user_decline(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    identifiers = {"imdb": "tt123", "tmdb": "456", "title": "Example"}
    item = _meta(unattended=True, unattended_confirm=False)
    assert not await tracker._handle_missing_media(item, "1", identifiers)
    assert item.skipping == "AVISTAZ"

    monkeypatch.setattr(az_module, "prompt_in_thread", AsyncMock(return_value=False))
    assert not await tracker._handle_missing_media(_meta(), "1", identifiers)


@pytest.mark.asyncio
async def test_avistaz_add_media_success_and_exception() -> None:
    tracker = _tracker()
    assert await tracker._record_add_media_response(_meta(), _response(status=302))
    tracker.session.post = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
    assert not await tracker.add_media_to_db(_meta(), "Example", "1", "tt123", "456")


@pytest.mark.asyncio
async def test_avistaz_validate_credentials_success(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    cookies = httpx.Cookies({"sid": "x"})
    monkeypatch.setattr(tracker.cookie_validator, "load_session_cookies", AsyncMock(return_value=cookies))
    monkeypatch.setattr(tracker.cookie_validator, "cookie_validation", AsyncMock(return_value=True))
    assert await tracker.validate_credentials(_meta())


@pytest.mark.asyncio
async def test_avistaz_additional_checks_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker, "_rules_policy", AsyncMock(return_value=False))
    assert not await tracker.get_additional_checks(_meta())

    monkeypatch.setattr(tracker, "_rules_policy", AsyncMock(return_value=True))
    monkeypatch.setattr(tracker, "_privatehd_group_policy", AsyncMock(return_value=False))
    assert not await tracker.get_additional_checks(_meta())

    monkeypatch.setattr(tracker, "_privatehd_group_policy", AsyncMock(return_value=True))
    monkeypatch.setattr(tracker, "_apply_session_cookies", AsyncMock())
    monkeypatch.setattr(tracker, "get_media_code", AsyncMock(return_value=True))
    assert await tracker.get_additional_checks(_meta())

    monkeypatch.setattr(tracker, "get_media_code", AsyncMock(return_value=False))
    assert not await tracker.get_additional_checks(_meta())


@pytest.mark.asyncio
async def test_avistaz_policy_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker(check_for_rules=False)
    assert await tracker._rules_policy(_meta())
    assert not await tracker._confirm_policy_override(_meta(unattended=True, unattended_confirm=False))

    private = _tracker("PRIVATEHD")
    monkeypatch.setattr(private, "_confirm_policy_override", AsyncMock(return_value=True))
    assert await private._privatehd_group_policy(_meta(type="ENCODE", tag="EVO"))

    cookies = httpx.Cookies({"sid": "x"})
    monkeypatch.setattr(tracker.cookie_validator, "load_session_cookies", AsyncMock(return_value=cookies))
    await tracker._apply_session_cookies(_meta())
    assert tracker.session.cookies is not None


@pytest.mark.asyncio
async def test_avistaz_page_duplicate_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    soup = BeautifulSoup(
        "<table class='table-bordered'><tbody><tr><td></td></tr></tbody></table>",
        "html.parser",
    )
    monkeypatch.setattr(tracker, "_row_duplicate", AsyncMock(return_value={"name": "Dupe", "size": "1 GB", "link": "x"}))
    assert await tracker._page_duplicates(_meta(), soup, "WEB-DL") == [{"name": "Dupe", "size": "1 GB", "link": "x"}]

    no_body = BeautifulSoup("<table class='table-bordered'></table>", "html.parser")
    assert tracker._torrent_rows(no_body) == []


@pytest.mark.asyncio
async def test_avistaz_row_duplicate_match_and_bdinfo(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    row = BeautifulSoup(
        "<tr><td><a class='torrent-filename' href='/torrent/7'>Name</a></td><td></td><td></td><td></td><td><span>1 GB</span></td><td><span class='badge-extra'>WEB-DL</span></td></tr>",
        "html.parser",
    ).find("tr")
    assert row is not None
    assert await tracker._row_duplicate(_meta(), row, "BluRay") is None
    monkeypatch.setattr(tracker, "get_dupe_bdinfo", AsyncMock(return_value="BDINFO"))
    entry = await tracker._row_duplicate(_meta(is_disc="BDMV"), row, "WEB-DL")
    assert entry is not None and entry["bd_info"] == "BDINFO"
    assert tracker._row_matches_rip_type(row, "")

    row_small = BeautifulSoup("<tr><td><a class='torrent-filename'>Name</a></td></tr>", "html.parser").find("tr")
    assert row_small is not None
    assert tracker._base_row_duplicate(row_small)["size"] == ""


@pytest.mark.asyncio
async def test_avistaz_dupe_bdinfo_success_and_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    html = "<div id='collapseMediaInfo'><pre>BDINFO\nLINE</pre></div>"
    tracker.session.get = AsyncMock(return_value=_response(text=html))  # type: ignore[method-assign]
    assert await tracker.get_dupe_bdinfo("https://x") == "BDINFO\nLINE"

    request = httpx.Request("GET", "https://x")
    status_response = httpx.Response(500, request=request)
    monkeypatch.setattr(tracker.session, "get", AsyncMock(side_effect=httpx.HTTPStatusError("bad", request=request, response=status_response)))
    assert await tracker.get_dupe_bdinfo("https://x") == ""

    monkeypatch.setattr(tracker.session, "get", AsyncMock(side_effect=httpx.RequestError("offline", request=request)))
    assert await tracker.get_dupe_bdinfo("https://x") == ""

    monkeypatch.setattr(tracker.session, "get", AsyncMock(side_effect=ValueError("bad parse")))
    assert await tracker.get_dupe_bdinfo("https://x") == ""

    monkeypatch.setattr(tracker.session, "get", AsyncMock(side_effect=RuntimeError("unexpected")))
    with pytest.raises(RuntimeError):
        await tracker.get_dupe_bdinfo("https://x")


def test_avistaz_log_dupe_error_helpers() -> None:
    tracker = _tracker()
    request = httpx.Request("GET", "https://x")
    response = httpx.Response(500, request=request)
    tracker._log_dupe_bdinfo_error("https://x", httpx.HTTPStatusError("bad", request=request, response=response))
    tracker._log_dupe_bdinfo_error("https://x", httpx.RequestError("bad", request=request))
    tracker._log_dupe_bdinfo_error("https://x", ValueError("bad"))
    assert tracker._bdinfo_from_html("<div id='collapseMediaInfo'><pre>INFO</pre></div>") == "INFO"


@pytest.mark.asyncio
async def test_avistaz_file_languages_and_prompt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tracker = _tracker()
    tracker.language_map(_meta())
    path = tmp_path / "tmp" / "release"
    path.mkdir(parents=True)
    (path / "MediaInfo.json").write_text(
        json.dumps({"media": {"track": [{"@type": "Audio", "Language": "en-US"}, {"@type": "Text", "Language": "fr"}]}}),
        encoding="utf-8",
    )
    audio, subs = await tracker._file_language_ids(_meta(tmp_path))
    assert audio and subs
    assert tracker._language_target_id("en-US") == tracker.lang_map["en"]

    missing = _meta(tmp_path / "missing")
    assert await tracker._file_language_ids(missing) == (set(), set())

    item = _meta(unattended=True, unattended_confirm=False)
    assert await tracker._prompt_missing_audio_languages(item) == set()
    assert item.skipping == "AVISTAZ"

    monkeypatch.setattr(tracker, "_ask_missing_audio_languages", AsyncMock(return_value="English, French"))
    ids = await tracker._prompt_missing_audio_languages(_meta())
    assert tracker.lang_map["english"] in ids


def test_avistaz_classify_track_language_missing_and_text() -> None:
    tracker = _tracker()
    tracker.language_map(_meta())
    audio: set[str] = set()
    subs: set[str] = set()
    missing: list[dict[str, Any]] = []
    tracker._classify_track_language({"@type": "Audio"}, audio, subs, missing)
    assert missing
    tracker._classify_track_language({"@type": "Text", "Language": "en"}, audio, subs, missing)
    assert tracker.lang_map["en"] in subs


@pytest.mark.asyncio
async def test_avistaz_img_host_success_failure_and_exception() -> None:
    tracker = _tracker()
    tracker.session.post = AsyncMock(return_value=_response(json_data={"success": True, "imageId": 9}))  # type: ignore[method-assign]
    assert await tracker.img_host(_meta(), "ref", b"x", "x.png") == "9"

    tracker.session.post = AsyncMock(return_value=_response(json_data={"success": False, "error": "bad"}))  # type: ignore[method-assign]
    assert await tracker.img_host(_meta(), "ref", b"x", "x.png") is None

    tracker.session.post = AsyncMock(return_value=_response(status=500, text="error"))  # type: ignore[method-assign]
    assert await tracker.img_host(_meta(), "ref", b"x", "x.png") is None

    tracker.session.post = AsyncMock(side_effect=RuntimeError("offline"))  # type: ignore[method-assign]
    assert await tracker.img_host(_meta(), "ref", b"x", "x.png") is None


@pytest.mark.asyncio
async def test_avistaz_screenshot_helpers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tracker = _tracker(add_audio_spectrogram=True)
    root = tmp_path / "tmp" / "release" / "screenshots"
    root.mkdir(parents=True)
    local = root / "screen.png"
    local.write_bytes(b"png")
    monkeypatch.setattr(tracker, "img_host", AsyncMock(return_value="image-id"))
    assert await tracker._upload_local_screenshot(_meta(tmp_path), local) == "image-id"

    tracker.session.get = AsyncMock(side_effect=RuntimeError("offline"))  # type: ignore[method-assign]
    assert await tracker._upload_remote_screenshot(_meta(), "https://x/image.png") is None

    meta = _meta(spectrograms_images=[{"raw_url": "https://x/spec.png"}])
    assert tracker._audio_plot_urls(meta) == ["https://x/spec.png"]

    monkeypatch.setattr(tracker, "_upload_local_screenshot", AsyncMock(return_value="local-id"))
    results = await tracker._append_local_images(meta, [], [local], 1)
    assert results == ["local-id"]
    monkeypatch.setattr(tracker, "_upload_remote_screenshot", AsyncMock(return_value="remote-id"))
    assert await tracker._append_remote_images(meta, [], ["https://x"], 1) == ["remote-id"]


@pytest.mark.asyncio
async def test_avistaz_requests_disabled_error_and_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    tracker.config["DEFAULT"]["search_requests"] = False
    assert await tracker.get_requests(_meta(search_requests=False)) == []

    tracker.config["DEFAULT"]["search_requests"] = True
    monkeypatch.setattr(tracker, "_apply_session_cookies", AsyncMock(side_effect=RuntimeError("bad")))
    assert await tracker.get_requests(_meta()) == []

    assert tracker._request_row(BeautifulSoup("<tr><td>none</td></tr>", "html.parser").find("tr")) is None
    row = BeautifulSoup("<tr><td><a class='torrent-filename' href='/r/1'>Req</a></td><td></td></tr>", "html.parser").find("tr")
    assert row is not None
    assert tracker._request_row(row) == {"Name": "Req", "Link": "/r/1", "Reward": "N/A"}
    tracker._log_request_results([{"Name": "Req", "Link": "/r/1", "Reward": "10"}])


@pytest.mark.asyncio
async def test_avistaz_fetch_tag_and_get_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    tracker.session.get = AsyncMock(return_value=_response(json_data={"data": [{"tag": "drama", "id": "12"}]}))  # type: ignore[method-assign]
    assert await tracker.fetch_tag_id("drama") == 12

    tracker.session.get = AsyncMock(return_value=_response(json_data={"data": [{"tag": "drama", "id": "bad"}]}))  # type: ignore[method-assign]
    assert await tracker.fetch_tag_id("drama") == 0

    tracker.session.get = AsyncMock(side_effect=RuntimeError("bad"))  # type: ignore[method-assign]
    assert await tracker.fetch_tag_id("drama") == 0

    monkeypatch.setattr(tracker, "fetch_tag_id", AsyncMock(return_value=5))
    tags = await tracker.get_tags(_meta(personalrelease=True))
    assert "5" in tags and "3773" in tags


@pytest.mark.asyncio
async def test_avistaz_edit_desc_and_render_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tracker = _tracker()
    (tmp_path / "tmp" / "release").mkdir(parents=True)
    monkeypatch.setattr(tracker, "_raw_description", AsyncMock(return_value="[b]Hello[/b] https://x"))
    result = await tracker.edit_desc(_meta(tmp_path))
    assert "Hello" in result
    assert (tmp_path / "tmp" / "release" / "[AVISTAZ]DESCRIPTION.txt").exists()

    monkeypatch.setattr(az_module.bbcode, "render_html", None, raising=False)

    class Parser:
        @staticmethod
        def format(value: str) -> str:
            return f"PARSED:{value}"

    monkeypatch.setattr(az_module.bbcode, "Parser", lambda: Parser())
    assert tracker._render_description("x") == "PARSED:x"


@pytest.mark.asyncio
async def test_avistaz_create_task_debug_invalid_redirect_and_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker, "get_media_code", AsyncMock(return_value=True))
    monkeypatch.setattr(tracker, "_task_payload", AsyncMock(return_value={"x": 1}))
    assert await tracker.create_task_id(_meta(debug=True)) == {}

    response = _response(status=302, headers={"Location": "https://x/not-a-number"})
    item = _meta()
    assert await tracker._task_from_response(item, response) == {}
    assert item.skipping == "AVISTAZ"
    assert tracker._task_redirect(_response(status=200)) is None

    monkeypatch.setattr(tracker, "_submit_task_step_one", AsyncMock(side_effect=RuntimeError("bad")))
    item = _meta(debug=False)
    assert await tracker.create_task_id(item) == {}
    assert item.skipping == "AVISTAZ"


def test_avistaz_name_helper_edge_paths() -> None:
    tracker = _tracker("CINEMAZ")
    item = _meta(webdv=True, title="Example")
    assert tracker._reposition_cinemaz_hybrid(item, "Example 2024 WEB-DL") == "Example 2024 WEB-DL"
    assert tracker._reposition_cinemaz_hybrid(item, "Example 2024 Hybrid WEB-DL") == "Example 2024 Hybrid WEB-DL"
    assert tracker._tv_year(_meta(category="TV", no_year=True, year=2020)) == 2020


@pytest.mark.asyncio
async def test_avistaz_fetch_data_skip_and_task_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker, "_apply_session_cookies", AsyncMock())
    monkeypatch.setattr(tracker, "get_lang", AsyncMock(return_value={}))
    item = _meta(skipping="AVISTAZ")
    assert await tracker.fetch_data(item) == {}

    monkeypatch.setattr(tracker, "get_screenshots", AsyncMock(side_effect=RuntimeError("bad")))
    data: dict[str, Any] = {}
    await tracker._apply_task_upload_fields(data, _meta(debug=False), {"redirect_url": "url"})
    assert tracker.upload_url_step2 == "url"


def test_avistaz_check_data_mapping_issue() -> None:
    tracker = _tracker()
    data = {"screenshots[]": ["1", "2", "3"], "task_id": "1", "info_hash": "h", "rip_type_id": "1", "type_id": "1", "video_quality_id": "0"}
    tracker.upload_url_step2 = "url"
    assert "resolution" in str(tracker.check_data(_meta(), data)).lower()


@pytest.mark.asyncio
async def test_avistaz_upload_skip_debug_and_live(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker, "fetch_data", AsyncMock(return_value={}))
    assert not await tracker.upload(_meta(skipping="AVISTAZ"))

    monkeypatch.setattr(tracker, "check_data", lambda *_args: False)
    monkeypatch.setattr(tracker, "_debug_upload", AsyncMock(return_value=True))
    assert await tracker.upload(_meta(debug=True))

    monkeypatch.setattr(tracker, "_live_upload", AsyncMock(return_value=True))
    assert await tracker.upload(_meta(debug=False))


@pytest.mark.asyncio
async def test_avistaz_live_upload_failure_and_success(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker, "_record_step_two_failure", AsyncMock())
    tracker.session.post = AsyncMock(return_value=_response(status=500))  # type: ignore[method-assign]
    assert not await tracker._live_upload(_meta(), {})

    tracker.session.post = AsyncMock(return_value=_response(status=302, headers={"Location": "https://avistaz.invalid/torrent/7"}))  # type: ignore[method-assign]
    monkeypatch.setattr(tracker, "_record_successful_upload", AsyncMock(return_value=True))
    assert await tracker._live_upload(_meta(), {})


@pytest.mark.asyncio
async def test_avistaz_record_successful_upload_register_failure_and_success() -> None:
    tracker = _tracker()
    response = _response(status=302, headers={"Location": "https://avistaz.invalid/torrent/77"})
    tracker.session.get = AsyncMock(return_value=_response(status=500))  # type: ignore[method-assign]
    item = _meta()
    assert not await tracker._record_successful_upload(item, response)

    tracker.session.get = AsyncMock(return_value=_response(status=200))  # type: ignore[method-assign]
    tracker.common.create_torrent_ready_to_seed = AsyncMock()  # type: ignore[method-assign]
    item = _meta()
    assert await tracker._record_successful_upload(item, response)
    assert item.tracker_status["AVISTAZ"]["torrent_id"] == "77"


@pytest.mark.asyncio
async def test_avistaz_debug_upload() -> None:
    tracker = _tracker()
    tracker.common.create_torrent_for_upload = AsyncMock()  # type: ignore[method-assign]
    item = _meta()
    assert await tracker._debug_upload(item, {"x": 1})
    tracker.common.create_torrent_for_upload.assert_awaited_once()


@pytest.mark.asyncio
async def test_avistaz_file_languages_invalid_json(tmp_path: Path) -> None:
    tracker = _tracker()
    tracker.language_map(_meta())
    root = tmp_path / "tmp" / "release"
    root.mkdir(parents=True)
    (root / "MediaInfo.json").write_text("{invalid", encoding="utf-8")
    assert await tracker._file_language_ids(_meta(tmp_path)) == (set(), set())


@pytest.mark.asyncio
async def test_avistaz_language_ids_prompt_for_missing_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker, "_prompt_missing_audio_languages", AsyncMock(return_value={"9"}))
    audio, subtitles = await tracker._language_ids_from_tracks(_meta(), [{"@type": "Audio"}])
    assert audio == {"9"}
    assert subtitles == set()


def test_avistaz_audio_plot_urls_disabled() -> None:
    tracker = _tracker(add_audio_spectrogram=False)
    item = _meta(spectrograms_images=[{"raw_url": "https://x/spec.png"}])
    assert tracker._audio_plot_urls(item) == []


@pytest.mark.asyncio
async def test_avistaz_raw_description_includes_episode_overview(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    monkeypatch.setattr(az_module.DescriptionBuilder, "get_tv_info", AsyncMock(return_value=("Pilot", "Episode overview")))
    monkeypatch.setattr(az_module.DescriptionBuilder, "get_user_description", AsyncMock(return_value="User notes"))
    monkeypatch.setattr(az_module.DescriptionBuilder, "get_tonemapped_header", AsyncMock(return_value="Tone"))
    value = await tracker._raw_description(_meta(category="TV"))
    assert "[b]Episode:[/b] Pilot" in value
    assert "[b]Overview:[/b] Episode overview" in value


@pytest.mark.asyncio
async def test_avistaz_create_task_returns_successful_task(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    response = _response(status=302, headers={"Location": "https://avistaz.invalid/upload/123"})
    expected = {"task_id": "123", "info_hash": "hash", "redirect_url": "https://avistaz.invalid/upload/123"}
    monkeypatch.setattr(tracker, "get_media_code", AsyncMock(return_value=True))
    monkeypatch.setattr(tracker, "_task_payload", AsyncMock(return_value={"x": 1}))
    monkeypatch.setattr(tracker, "_submit_task_step_one", AsyncMock(return_value=response))
    monkeypatch.setattr(tracker, "_task_from_response", AsyncMock(return_value=expected))
    assert await tracker.create_task_id(_meta(debug=False)) == expected


@pytest.mark.asyncio
async def test_avistaz_task_from_response_success() -> None:
    tracker = _tracker()
    tracker.common.get_torrent_hash = AsyncMock(return_value="hash")  # type: ignore[method-assign]
    response = _response(status=302, headers={"Location": "https://avistaz.invalid/upload/123"})
    assert await tracker._task_from_response(_meta(), response) == {
        "task_id": "123",
        "info_hash": "hash",
        "redirect_url": "https://avistaz.invalid/upload/123",
    }


def test_avistaz_raw_urls_ignores_non_mapping_items() -> None:
    assert AZTrackerBase._raw_urls(["bad", {"raw_url": "https://x/image.png"}]) == ["https://x/image.png"]


def test_avistaz_season_summary_entries_rejects_non_list() -> None:
    assert AZTrackerBase._season_summary_entries({"seasons_summary": "bad"}) == []
