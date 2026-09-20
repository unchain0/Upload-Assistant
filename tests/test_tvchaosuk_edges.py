from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.tvchaosuk import TVChaosUK

tvc_module = importlib.import_module("src.integrations.trackers.tvchaosuk")


def _config(**tracker_values: object) -> dict[str, Any]:
    tracker: dict[str, Any] = {
        "api_key": "key",
        "anon": False,
        "image_count": 2,
        "announce_url": "https://tracker.invalid/announce",
    }
    tracker.update(tracker_values)
    return {
        "DEFAULT": {
            "tmdb_api": "0123456789abcdef0123456789abcdef",
            "logo_size": "250",
        },
        "TRACKERS": {"TVCHAOSUK": tracker},
        "IMAGES": {
            "imdb_75": "https://img.invalid/imdb.png",
            "tmdb_75": "https://img.invalid/tmdb.png",
            "tvdb_75": "https://img.invalid/tvdb.png",
            "tvmaze_75": "https://img.invalid/tvmaze.png",
            "mal_75": "https://img.invalid/mal.png",
        },
    }


def _tracker(**tracker_values: object) -> TVChaosUK:
    return TVChaosUK(_config(**tracker_values))


def _meta(tmp_path: Path | None = None, **values: object) -> Meta:
    root = tmp_path or Path()
    state: dict[str, object] = {
        "base_dir": str(root),
        "uuid": "release",
        "path": str(root / "movie.mkv"),
        "video": str(root / "movie.mkv"),
        "filelist": [str(root / "movie.mkv")],
        "name": "Example Movie 2010",
        "title": "Example Movie",
        "original_title": "Original Title",
        "category": "TV",
        "year": 2010,
        "search_year": 2010,
        "no_year": False,
        "type": "WEBDL",
        "resolution": "1080p",
        "source": "WEB",
        "video_codec": "AVC",
        "season": "S01",
        "episode": "E01",
        "season_int": 1,
        "episode_int": 1,
        "tv_pack": False,
        "season_air_first_date": "2010-01-01",
        "season_name": "Season One",
        "episode_airdate": "2010-01-02",
        "episode_name": "Episode One",
        "episode_overview": "Episode overview",
        "episodes": [],
        "networks": "BBC",
        "logo": "https://img.invalid/logo.png",
        "overview": "Overview",
        "release_date": "2010-01-01",
        "release_dates": {},
        "tmdb": 123,
        "tmdb_id": 123,
        "imdb": 456,
        "imdb_id": 456,
        "imdb_info": {"imdb_url": "https://imdb.invalid/title/tt456"},
        "tvdb_id": 789,
        "tvmaze_id": 10,
        "mal_id": 11,
        "origin_country": [],
        "origin_country_code": [],
        "production_countries": [],
        "production_companies": [],
        "original_language": "en",
        "genres": ["Drama"],
        "keywords": ["drama"],
        "bdinfo": {},
        "discs": [],
        "screens": 2,
        "image_list": [],
        "comparison": False,
        "description": "Notes",
        "ua_signature": "UA",
        "ua_name": "Upload Assistant",
        "current_version": "1.0",
        "anon": 0,
        "stream": 0,
        "sd": 0,
        "personalrelease": False,
        "unattended": True,
        "unattended_confirm": False,
        "debug": False,
        "draft": False,
        "is_disc": "",
        "tmdb_episode_data": {},
        "tmdb_season_data": {},
        "tracker_status": {"TVCHAOSUK": {}},
        "eng_subs": 0,
        "sdh_subs": 0,
        "has_subs": 0,
    }
    state.update(values)
    return Meta(state)


def _response(
    payload: Any = None,
    *,
    status: int = 200,
    text: str | None = None,
    url: str = "https://tvchaosuk.com/api/torrents/upload",
) -> httpx.Response:
    request = httpx.Request("POST", url)
    if text is not None:
        return httpx.Response(status, request=request, text=text)
    return httpx.Response(status, request=request, json=payload)


def test_tvchaos_disc_info_all_variants() -> None:
    tracker = _tracker()
    text = tracker._build_disc_info(
        [
            {"type": "BDMV", "name": "DISC", "summary": "BDINFO"},
            {
                "type": "DVD",
                "name": "DVD1",
                "vob": "/x/VTS_01.VOB",
                "vob_mi": "VOBMI",
                "ifo": "/x/VTS_01.IFO",
                "ifo_mi": "IFOMI",
            },
        ]
    )
    assert "BDINFO" in text
    assert "VOBMI" in text

    first_dvd = tracker._build_disc_info([{"type": "DVD", "vob_mi": "FIRST"}])
    assert "VOB MediaInfo" in first_dvd
    assert "FIRST" in first_dvd


def test_tvchaos_episode_list_all_fields() -> None:
    tracker = _tracker()
    text = tracker._build_episode_list(
        [
            {
                "code": "S01E01",
                "title": "Pilot",
                "airdate": "2010-01-02",
                "overview": "Plot",
            },
            {"code": "S01E02", "title": "", "airdate": "", "overview": ""},
        ]
    )
    assert "[b]S01E01[/b] - Pilot (02-01-2010)" in text
    assert "Plot" in text
    assert "S01E02" in text


def test_tvchaos_category_and_country_fallbacks() -> None:
    tracker = _tracker()
    assert (
        asyncio.run(tracker.get_cat_id([]))
        == tracker.tv_type_map["holding bin"]
    )
    assert (
        asyncio.run(tracker.get_cat_id(["Unknown"]))
        == tracker.tv_type_map["holding bin"]
    )
    assert (
        asyncio.run(
            tracker.append_country_code(
                _meta(origin_country_code=["IE"]), "Show"
            )
        )
        == "Show [IRL]"
    )


@pytest.mark.asyncio
async def test_tvchaos_write_description_file_oserror(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        Path,
        "open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("broken")),
    )
    await tracker._write_description_file(str(tmp_path / "desc.txt"), "text")


def test_tvchaos_tracker_screenshots_tuple_and_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        tvc_module,
        "get_tracker_image_collection",
        lambda *_args: ({"web_url": "w", "img_url": "i"},),
    )
    assert tracker._tracker_screenshots(_meta()) == [
        {"web_url": "w", "img_url": "i"}
    ]
    monkeypatch.setattr(
        tvc_module, "get_tracker_image_collection", lambda *_args: "bad"
    )
    assert tracker._tracker_screenshots(_meta()) == []


@pytest.mark.asyncio
async def test_tvchaos_load_mediainfo_missing(tmp_path: Path) -> None:
    assert await _tracker()._load_mediainfo_json(_meta(tmp_path)) == {}


@pytest.mark.asyncio
async def test_tvchaos_upload_category_foreign_audio() -> None:
    tracker = _tracker()
    mi = {"media": {"track": [{"@type": "Audio", "Language": "fr"}]}}
    item = _meta(original_language="")
    assert (
        await tracker._upload_category(item, mi)
        == tracker.tv_type_map["foreign"]
    )


def test_tvchaos_release_type_brrip() -> None:
    item = _meta(type="ENCODE", path="/media/Movie.BluRay.mkv")
    assert TVChaosUK._release_type(item) == "BRRip"


def test_tvchaos_localized_upload_name() -> None:
    item = _meta(title="Title", original_title="Original")
    name = TVChaosUK._localized_upload_name(
        item, TVChaosUK.tv_type_map["foreign"], "Title [1080p]"
    )
    assert name == "Title (Original) [1080p]"


def test_tvchaos_confirm_upload_name_second_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = iter((False, True))
    monkeypatch.setattr(
        tvc_module.cli_ui,
        "ask_yes_no",
        lambda *_args, **_kwargs: next(answers),
    )
    monkeypatch.setattr(
        tvc_module.cli_ui, "ask_string", lambda *_args, **_kwargs: "Renamed"
    )
    assert TVChaosUK._confirm_upload_name(_meta(unattended=False), "Original")


def test_tvchaos_tracker_config_guard() -> None:
    tracker = TVChaosUK(
        {
            "TRACKERS": "bad",
            "DEFAULT": {"tmdb_api": "0123456789abcdef0123456789abcdef"},
        }
    )
    assert tracker._tracker_config() == {}


@pytest.mark.asyncio
async def test_tvchaos_upload_non_debug_delegates_submit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    tracker.common.create_torrent_for_upload = AsyncMock()  # type: ignore[method-assign]
    monkeypatch.setattr(tracker, "get_tmdb_data", AsyncMock(return_value={}))
    monkeypatch.setattr(
        tracker, "_load_mediainfo_json", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        tracker, "_upload_category", AsyncMock(return_value="11")
    )
    monkeypatch.setattr(
        tracker, "_technical_dumps", AsyncMock(return_value=("MI", ""))
    )
    monkeypatch.setattr(tracker, "edit_desc", AsyncMock(return_value="DESC"))
    monkeypatch.setattr(
        tracker, "_upload_name", AsyncMock(return_value="NAME")
    )
    monkeypatch.setattr(tracker, "_confirm_upload_name", lambda *_args: True)
    monkeypatch.setattr(
        tracker, "_upload_data", lambda *_args: {"name": "NAME"}
    )
    monkeypatch.setattr(
        tracker, "_submit_upload", AsyncMock(return_value=True)
    )
    assert await tracker.upload(_meta())
    tracker._submit_upload.assert_awaited_once()


@pytest.mark.asyncio
async def test_tvchaos_submit_upload_transport_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    item = _meta()
    cases = (
        httpx.TimeoutException("timeout"),
        httpx.RequestError(
            "offline", request=httpx.Request("POST", tracker.upload_url)
        ),
        RuntimeError("unexpected"),
    )
    for error in cases:
        monkeypatch.setattr(
            tracker, "_upload_response", AsyncMock(side_effect=error)
        )
        item.tracker_status["TVCHAOSUK"] = {}
        assert not await tracker._submit_upload(item, {})
        assert item.tracker_status["TVCHAOSUK"]["status_message"]


@pytest.mark.asyncio
async def test_tvchaos_handle_upload_response_http_and_success() -> None:
    tracker = _tracker()
    item = _meta()
    assert not await tracker._handle_upload_response(
        item, _response({}, status=403)
    )
    assert "Forbidden" in item.tracker_status["TVCHAOSUK"]["status_message"]

    tracker.common.create_torrent_ready_to_seed = AsyncMock()  # type: ignore[method-assign]
    text = 'application/x-bittorrent\n{"data":"https://tvchaosuk.com/torrents/77"}'
    response = _response(text=text, status=200)
    assert await tracker._handle_upload_response(item, response)
    assert item.tracker_status["TVCHAOSUK"]["torrent_id"] == "77"
    tracker.common.create_torrent_ready_to_seed.assert_awaited_once()


def test_tvchaos_http_error_message_redirect() -> None:
    assert "Redirect (302)" in TVChaosUK._http_error_message(
        _response({}, status=302)
    )
    assert "HTTP 500" in TVChaosUK._http_error_message(
        _response({}, status=500)
    )


def test_tvchaos_upload_json_and_id_errors() -> None:
    with pytest.raises(ValueError, match="JSON payload must be an object"):
        TVChaosUK._upload_json(_response(text="[]"))
    with pytest.raises(ValueError, match="missing or not a string"):
        TVChaosUK._uploaded_torrent_id({"data": None})
    with pytest.raises(ValueError, match="no path segments"):
        TVChaosUK._uploaded_torrent_id({"data": "https://tvchaosuk.com/"})


def test_tvchaos_origin_country_fallbacks_and_network_normalization() -> None:
    tracker = _tracker()
    assert tracker._origin_country_codes(
        _meta(origin_country=[], production_countries=[{"iso_3166_1": "GB"}])
    ) == ["GB"]
    assert tracker._origin_country_codes(
        _meta(
            origin_country=[],
            production_countries=[],
            production_companies=[{"origin_country": "IE"}],
        )
    ) == ["IE"]
    assert (
        tracker._origin_country_codes(
            _meta(
                origin_country=[],
                production_countries=[],
                production_companies=[],
            )
        )
        == []
    )
    item = _meta(networks=[{"name": "BBC"}])
    tracker._normalize_network(item)
    assert item.networks == "BBC"


@pytest.mark.asyncio
async def test_tvchaos_cached_tmdb_episode_and_season() -> None:
    tracker = _tracker()
    episode = {
        "air_date": "2010-01-02",
        "name": "Episode",
        "overview": "Overview",
    }
    item = _meta(tmdb_episode_data=episode)
    assert await tracker._episode_info(item) == episode

    season = {"air_date": "2010-01-01", "name": "Season", "episodes": []}
    item = _meta(tmdb_season_data=season)
    assert await tracker._season_info(item) == season


@pytest.mark.asyncio
async def test_tvchaos_additional_checks_forbidden() -> None:
    tracker = _tracker()
    assert not await tracker.get_additional_checks(_meta(resolution="2160p"))
    assert await tracker.get_additional_checks(
        _meta(resolution="1080p", video_codec="HEVC")
    )


def test_tvchaos_finalize_description_empty_and_links_empty() -> None:
    tracker = _tracker()
    text = tracker._finalize_description("", False, "")
    assert "No description available" in text
    empty = _meta(imdb_id=0, tmdb_id=0, tvdb_id=0, tvmaze_id=0, mal_id=0)
    assert tracker.get_links(empty) == ""
    assert tracker._imdb_link(_meta(imdb_id=0)) == ""
    assert tracker._tmdb_link(_meta(tmdb_id=0)) == ""


def test_tvchaos_subtitle_sdh_flag() -> None:
    tracker = _tracker()
    item = _meta()
    mi = {
        "media": {
            "track": [
                {"@type": "Text", "Language": "eng", "Title": "English SDH"}
            ]
        }
    }
    tracker.get_subs_info(item, mi)
    assert item.eng_subs == 1
    assert item.sdh_subs == 1
    assert item.has_subs == 1


def test_tvchaos_normalized_audio_language_empty_and_english() -> None:
    assert TVChaosUK._normalized_audio_language({}) == ""
    assert (
        TVChaosUK._normalized_audio_language({"Language/String": "en-US"})
        == "English"
    )


def test_tvchaos_remaining_description_and_origin_branches() -> None:
    tracker = _tracker()
    assert (
        tracker._build_fallback_desc(_meta(category="OTHER", overview=""))
        == ""
    )
    assert (
        tracker._add_screenshots(
            _meta(screens=1), [{"web_url": "w", "img_url": "i"}]
        )
        == ""
    )
    assert tracker._localized_upload_name(_meta(), "11", "Name") == "Name"
    assert (
        tracker._localized_upload_name(
            _meta(title="Same", original_title="Same"),
            tracker.tv_type_map["foreign"],
            "Same",
        )
        == "Same"
    )
    assert tracker._origin_country_codes(_meta(origin_country=["IE"])) == [
        "IE"
    ]


@pytest.mark.asyncio
async def test_tvchaos_read_file_and_valid_mediainfo(tmp_path: Path) -> None:
    tracker = _tracker()
    path = tmp_path / "value.txt"
    path.write_text("hello", encoding="utf-8")
    assert await tracker.read_file(str(path)) == "hello"

    root = tmp_path / "tmp" / "release"
    root.mkdir(parents=True)
    (root / "MediaInfo.json").write_text(
        '{"media":{"track":[]}}', encoding="utf-8"
    )
    assert await tracker._load_mediainfo_json(_meta(tmp_path)) == {
        "media": {"track": []}
    }


@pytest.mark.asyncio
async def test_tvchaos_upload_stops_when_name_not_confirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    tracker.common.create_torrent_for_upload = AsyncMock()  # type: ignore[method-assign]
    monkeypatch.setattr(tracker, "get_tmdb_data", AsyncMock(return_value={}))
    monkeypatch.setattr(
        tracker, "_load_mediainfo_json", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        tracker, "_upload_category", AsyncMock(return_value="11")
    )
    monkeypatch.setattr(
        tracker, "_technical_dumps", AsyncMock(return_value=("MI", ""))
    )
    monkeypatch.setattr(tracker, "edit_desc", AsyncMock(return_value="DESC"))
    monkeypatch.setattr(
        tracker, "_upload_name", AsyncMock(return_value="NAME")
    )
    monkeypatch.setattr(tracker, "_confirm_upload_name", lambda *_args: False)
    assert await tracker.upload(_meta()) is None
