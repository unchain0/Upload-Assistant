from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

import src.integrations.trackers.passthepopcorn as ptp_module
from src.domain_models.processing import LoginError, UploadError
from src.integrations.trackers.passthepopcorn import PassThePopcorn
from tests.test_passthepopcorn_contracts import _Client, _meta, _Response


def _config() -> dict[str, Any]:
    return {
        "DEFAULT": {
            "img_host_1": "pixhost",
            "multiScreens": 2,
            "tonemapped_header": "[center]Tone mapped[/center]",
            "rehash_cooldown": 0,
        },
        "TRACKERS": {
            "PASSTHEPOPCORN": {
                "ApiUser": "api-user",
                "api_key": "api-key",
                "announce_url": (
                    "https://please.passthepopcorn.me:2710/passkey/announce"
                ),
                "username": "user",
                "password": "password",
                "add_web_source_to_desc": True,
            }
        },
    }


@pytest.fixture
def tracker(monkeypatch: pytest.MonkeyPatch) -> PassThePopcorn:
    monkeypatch.setattr(ptp_module.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(
        ptp_module.asyncio, "sleep", AsyncMock(return_value=None)
    )
    return PassThePopcorn(_config())


def test_search_and_group_helper_edges(
    tracker: PassThePopcorn, tmp_path: Path
) -> None:
    meta = _meta(tmp_path, tag="")
    assert tracker._group_allowed(meta)
    assert tracker._torrent_match([], "") == (None, None)
    torrents = [
        {"Id": "1", "InfoHash": "a", "ReleaseName": "Alpha"},
        {"Id": "2", "InfoHash": "b", "ReleaseName": "Beta"},
    ]
    assert tracker._torrent_match(torrents, "beta") == ("2", "b")
    assert tracker._torrent_match(torrents, "missing") == ("1", "a")
    assert (
        tracker._movie_search_result({"Torrents": torrents}, "alpha") is None
    )
    assert tracker._torrent_infohash(torrents, "9") is None
    assert tracker._selected_group_id([], "missing") is None
    assert tracker._group_from_response


@pytest.mark.asyncio
async def test_ptp_search_response_status_and_exception_edges(
    tracker: PassThePopcorn,
) -> None:
    _Client.reset(_Response(status=401))
    assert await tracker.get_ptp_id_imdb("x", "", {}) == (None, None, None)
    _Client.reset(_Response(status=503))
    assert await tracker.get_ptp_id_imdb("x", "", {}) == (None, None, None)
    _Client.reset(RuntimeError("down"))
    assert await tracker.get_ptp_id_imdb("x", "", {}) == (None, None, None)
    _Client.reset(_Response(payload={"Movies": []}))
    assert await tracker.get_ptp_id_imdb("x", "", {}) == (None, None, None)


@pytest.mark.asyncio
async def test_imported_description_interactive_edges(
    tracker: PassThePopcorn,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meta = _meta(tmp_path, unattended=False, skipit=False)
    monkeypatch.setattr(ptp_module.click, "edit", lambda _value: " edited ")
    assert tracker._edited_description(meta, "old") == "edited"
    assert meta.description == "edited"
    monkeypatch.setattr(ptp_module.click, "edit", lambda _value: None)
    assert tracker._edited_description(meta, "old") == "old"

    monkeypatch.setattr(
        ptp_module, "prompt_in_thread", AsyncMock(return_value="d")
    )
    meta.saved_description = False
    await tracker._interactive_description(meta, "discard")
    assert meta.saved_description is False

    monkeypatch.setattr(
        ptp_module, "prompt_in_thread", AsyncMock(return_value="")
    )
    await tracker._interactive_description(meta, "keep")
    assert meta.description == "keep"
    assert meta.saved_description is True

    skipped = _meta(tmp_path, skip_tracker_descriptions=True)
    await tracker._apply_ptp_description(skipped, "ignored")
    assert skipped.saved_description is None


@pytest.mark.asyncio
async def test_group_lookup_response_edges(
    tracker: PassThePopcorn,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    non_json = _Response(
        payload=json.JSONDecodeError("bad", "x", 0), text="html"
    )
    assert tracker._group_response_json(non_json) is None

    _Client.reset(_Response(status=500, text="bad"))
    assert await tracker.get_group_by_imdb("1") is None

    assert await tracker._group_from_response({"Page": "Browse"}, "1") is None
    assert await tracker._group_from_response({"Page": "Other"}, "1") is None
    assert (
        await tracker._search_group_result(
            {"TotalResults": 0, "Movies": []}, "1"
        )
        is None
    )
    single = {
        "TotalResults": 1,
        "Movies": [{"GroupId": 7, "Title": "One", "Year": 2020}],
    }
    assert await tracker._search_group_result(single, "1") == "7"

    movies = [
        {"GroupId": 8, "Title": "A", "Year": 2020},
        {"GroupId": 9, "Title": "B", "Year": 2021},
    ]
    monkeypatch.setattr(
        ptp_module,
        "prompt_in_thread",
        AsyncMock(return_value="Skip - Don't use any of these matches"),
    )
    assert await tracker._multiple_group_result(movies, "1") is None
    monkeypatch.setattr(
        ptp_module,
        "prompt_in_thread",
        AsyncMock(return_value=tracker._group_choice(movies[1])),
    )
    assert await tracker._multiple_group_result(movies, "1") == "9"


@pytest.mark.asyncio
async def test_type_tag_quality_and_codec_edges(
    tracker: PassThePopcorn,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert tracker._normalized_tag_item(object()) == []
    assert tracker._keyword_release_type(set()) is None
    assert (
        tracker._tmdb_release_type(_meta(tmp_path, tmdb_type="miniseries"))
        == "Miniseries"
    )
    assert (
        tracker._tmdb_release_type(_meta(tmp_path, tmdb_type="series")) is None
    )
    monkeypatch.setattr(
        ptp_module.cli_ui, "ask_choice", lambda *_args, **_kwargs: "Concert"
    )
    assert tracker._prompt_release_type() == "Live Performance"
    assert tracker._ptp_quality(_meta(tmp_path, sd=1)) == "Standard Definition"
    assert (
        tracker._ptp_quality(_meta(tmp_path, resolution="2160p"))
        == "Ultra High Definition"
    )
    assert tracker._ptp_quality(_meta(tmp_path, resolution="576p")) is None
    assert tracker._existing_release_names([], None) == []
    assert tracker._bd_codec(_meta(tmp_path, bdinfo={})) == "BD25"
    assert tracker._dvd_codec(_meta(tmp_path, dvd_size="UNKNOWN")) == ""
    assert (
        tracker._custom_resolution(
            _meta(tmp_path, video_width=None, video_height=None)
        )
        is None
    )
    cli_meta = _meta(tmp_path, mode="cli", tmdb_type="series", keywords=[])
    cli_meta.imdb_info = {"type": None}
    assert tracker.get_type(cli_meta.imdb_info, cli_meta) == "Live Performance"


@pytest.mark.asyncio
async def test_poster_rehost_helper_edges(
    tracker: PassThePopcorn,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meta = _meta(tmp_path, imghost="pixhost")
    tracker._restore_imghost(meta, None)
    assert meta.imghost == ""
    tracker._restore_imghost(meta, "imgbb")
    assert meta.imghost == "imgbb"

    tracker.uploadscreens_manager.upload_screens = AsyncMock(
        return_value=([], False)
    )
    assert (
        await tracker._upload_poster(meta, tmp_path / "poster.jpg", "pixhost")
        is None
    )
    tracker.uploadscreens_manager.upload_screens = AsyncMock(
        return_value=([{"raw_url": "https://pixhost.to/poster.jpg"}], False)
    )
    assert (
        await tracker._upload_poster(meta, tmp_path / "poster.jpg", "pixhost")
        == "https://pixhost.to/poster.jpg"
    )

    monkeypatch.setattr(
        tracker, "_selected_poster_host", lambda _meta: "pixhost"
    )
    monkeypatch.setattr(
        tracker, "_poster_already_on_selected_host", lambda *_args: False
    )
    monkeypatch.setattr(
        tracker, "_download_poster", AsyncMock(side_effect=OSError("bad"))
    )
    assert (
        await tracker.rehost_poster_to_selected_host(
            meta, "https://other/poster.jpg"
        )
        == "https://other/poster.jpg"
    )


@pytest.mark.asyncio
async def test_subtitle_and_trumpable_edges(
    tracker: PassThePopcorn,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forced = {"Language": "en", "Forced": "Yes"}
    intertitles = {"Language": "en", "Title": "English intertitles"}
    assert tracker._subtitle_language(forced) == "en (Forced)"
    assert tracker._subtitle_language(intertitles) == "en (Intertitles)"
    assert tracker._subtitle_language({"Language": "fr"}) == "fr"
    assert tracker._subtitle_id("zz") is None

    no_text = _meta(
        tmp_path,
        mediainfo={"media": {"track": [{"@type": "Audio", "Language": "en"}]}},
    )
    assert tracker.get_subtitles(no_text) == [44]

    monkeypatch.setattr(
        ptp_module.cli_ui, "ask_string", lambda *_args, **_kwargs: "fr"
    )
    values = [44]
    tracker._apply_hardcoded_language(values)
    assert values != [44]

    subs = [44]
    tracker._normalize_forced_trumpable([14], subs)
    assert 44 not in subs
    trumpable = [15]
    subs = [44]
    tracker._normalize_other_hardcoded_trumpable(trumpable, subs, False)
    assert 4 in trumpable and 14 in trumpable and 44 not in subs
    assert tracker._normalize_hardcoded_trumpable(None, [44], False) == (
        None,
        [44],
    )

    monkeypatch.setattr(
        ptp_module.cli_ui,
        "select_choices",
        lambda *_args, **_kwargs: [
            "English Hardcoded Subs (Full)",
            "English Hardcoded Subs (Forced)",
            "No English Subs",
            "Hardcoded Subs (Non-English)",
        ],
    )
    trumpable, sub_ids = tracker.get_trumpable([44])
    assert trumpable and 4 in trumpable and 14 in trumpable
    assert 44 not in sub_ids


@pytest.mark.asyncio
async def test_login_tfa_and_csrf_edges(
    tracker: PassThePopcorn,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert tracker._csrf_token_from_html("none") is None
    assert tracker._csrf_token_from_html('data-AntiCsrfToken="abc"') == "abc"
    with pytest.raises(LoginError):
        broken = PassThePopcorn(
            {
                "DEFAULT": {},
                "TRACKERS": {
                    "PASSTHEPOPCORN": {
                        "ApiUser": "a",
                        "api_key": "b",
                        "announce_url": "bad",
                        "username": "u",
                        "password": "p",
                    }
                },
            }
        )
        broken._passkey()

    response = _Response(payload=[1, 2], text="not-json")
    with pytest.raises(TypeError):
        tracker._login_payload(response)
    assert "not-json" in tracker._redacted_login_response(response)
    json_response = _Response(text='{"password":"secret"}')
    assert tracker._redacted_login_response(json_response)

    unattended = _meta(tmp_path, unattended=True, unattended_confirm=False)
    with pytest.raises(LoginError):
        await tracker._complete_tfa(
            unattended,
            _Client(),
            {},
            {},
            _Response(),
            {"Result": "TfaRequired"},
        )

    monkeypatch.setattr(
        ptp_module, "prompt_in_thread", AsyncMock(return_value="123456")
    )
    _Client.reset(_Response(payload={"Result": "Ok", "AntiCsrfToken": "t"}))
    response, payload = await tracker._complete_tfa(
        _meta(tmp_path, unattended=False),
        _Client(),
        {},
        {},
        _Response(),
        {"Result": "TfaRequired"},
    )
    assert payload["Result"] == "Ok"
    assert response.status_code == 200

    fake_client = _Client()
    tracker.cookie_validator._save_cookies_secure = Mock()
    assert (
        tracker._successful_login_token(
            fake_client,
            tmp_path / "cookies.json",
            {"Result": "Ok", "AntiCsrfToken": "token"},
        )
        == "token"
    )
    with pytest.raises(LoginError):
        tracker._successful_login_token(
            fake_client, tmp_path / "x", {"Result": "Bad"}
        )


@pytest.mark.asyncio
async def test_csrf_saved_and_fresh_session_edges(
    tracker: PassThePopcorn,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meta = _meta(tmp_path)
    cookie_file = tracker._cookie_file(meta)
    cookie_file.write_text("{}", encoding="utf-8")
    tracker.cookie_validator._load_cookies_dict_secure = Mock(
        return_value={"session": {"value": "abc"}}
    )
    _Client.reset(_Response(text='<div data-AntiCsrfToken="saved"></div>'))
    assert await tracker.get_anti_csrf_token(meta) == "saved"

    cookie_file.write_text("{}", encoding="utf-8")
    _Client.reset(
        _Response(text='<a href="login.php?act=recover">login</a>'),
        _Response(payload={"Result": "Ok", "AntiCsrfToken": "fresh"}),
    )
    tracker.cookie_validator._save_cookies_secure = Mock()
    assert await tracker.get_anti_csrf_token(meta) == "fresh"
    assert not cookie_file.exists()

    monkeypatch.setattr(
        tracker, "_fresh_session_token", AsyncMock(return_value="new")
    )
    missing_meta = _meta(tmp_path, uuid="missing-cookie")
    assert await tracker.get_anti_csrf_token(missing_meta) == "new"


@pytest.mark.asyncio
async def test_pack_image_filter_load_and_save_edges(
    tracker: PassThePopcorn,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meta = _meta(tmp_path, debug=True)
    tracker.config["DEFAULT"]["multiScreens"] = 1
    assert tracker._multi_screen_count() == 2
    assert (
        tracker._description_images(_meta(tmp_path, skip_imghost_upload=True))
        == []
    )

    monkeypatch.setattr(
        ptp_module, "urlparse", Mock(side_effect=ValueError("bad"))
    )
    assert not tracker._approved_pack_image(meta, {"raw_url": "x"})
    monkeypatch.undo()

    filtered = tracker._filtered_pack_keys(
        meta,
        {
            "bad": {"images": [{"raw_url": "https://other/image.png"}]},
            "junk": "not-a-dict",
        },
    )
    assert filtered == {}
    assert tracker._filtered_pack_data(meta, {"keys": {}}) == {}
    assert tracker._pack_key_images({"keys": "bad"}, "x") == []

    pack_path = tracker._pack_image_path(meta)
    pack_path.write_text("not-json", encoding="utf-8")
    assert await tracker._load_pack_images(meta) == {}
    assert await tracker.save_image_links(meta, "x", None) is None

    image_path = await tracker.save_image_links(
        meta,
        "x",
        [{"raw_url": "r", "img_url": "i", "web_url": "w"}],
    )
    assert image_path is not None and image_path.exists()
    loaded = json.loads(image_path.read_text())
    assert loaded["total_count"] == 1


@pytest.mark.asyncio
async def test_disc_description_helper_edges(
    tracker: PassThePopcorn,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meta = _meta(tmp_path, discs=[], retry_count=0)
    desc = io.StringIO()
    disc = {
        "type": "BDMV",
        "summary": "SUMMARY",
        "bdinfo": {"edition": "Main"},
        "bdinfo_1": {"edition": "Alt"},
        "summary_1": "ALT SUMMARY",
    }
    meta.bdinfo = {"edition": "Main"}
    keys = tracker._write_initial_disc_media(desc, meta, disc)
    assert len(keys) == 2

    tracker._restore_pack_images = Mock(return_value=False)
    tracker._upload_extra_images = AsyncMock(return_value=[])
    tracker._persist_meta = AsyncMock(return_value=None)
    monkeypatch.setattr(
        ptp_module, "manifest_files", lambda *_args, **_kwargs: []
    )
    await tracker._write_playlist_block(desc, meta, disc, {}, 1, "bdinfo_1", 2)

    tracker._upload_extra_images = AsyncMock(
        return_value=[{"raw_url": "https://pixhost.to/x"}]
    )
    meta.new_images_disc_1 = []
    await tracker._multi_bdmv_screens(desc, meta, "new_images_disc_1", 1, 2)
    assert "pixhost" in desc.getvalue()

    tracker._restore_pack_images = Mock(return_value=True)
    meta.new_images_disc_1 = [{"raw_url": "https://pixhost.to/saved"}]
    tracker._multi_bdmv_screens = AsyncMock(return_value=None)
    await tracker._write_multi_bdmv_disc(
        desc, meta, disc, "BASE", [], {}, 1, 2
    )
    tracker._multi_bdmv_screens.assert_awaited_once()


@pytest.mark.asyncio
async def test_form_cover_tag_and_trumpable_edges(
    tracker: PassThePopcorn,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert tracker._first_audio_language([]) == ""
    assert tracker._audio_flags_from_tracks([], True) == (True, False)
    assert tracker._can_prompt_trumpable(_meta(tmp_path, unattended=False))

    unattended = _meta(tmp_path, unattended=True, unattended_confirm=False)
    result = await tracker._prompt_trumpable(unattended, "message", [44])
    assert result == (None, [44])

    monkeypatch.setattr(
        ptp_module, "prompt_in_thread", AsyncMock(return_value=False)
    )
    interactive = _meta(tmp_path, unattended=False)
    assert await tracker._prompt_trumpable(interactive, "message", [44]) == (
        None,
        [44],
    )

    assert tracker._valid_cover_url("https://x/poster.webp")
    with pytest.raises(UploadError):
        tracker._ensure_cover_prompt_allowed(unattended)
    assert unattended.skipping == tracker.tracker

    monkeypatch.setattr(
        ptp_module,
        "prompt_in_thread",
        AsyncMock(side_effect=["bad.txt", "https://x/good.jpg"]),
    )
    monkeypatch.setattr(
        tracker,
        "rehost_poster_to_selected_host",
        AsyncMock(return_value="https://host/good.jpg"),
    )
    assert await tracker._prompt_cover(interactive) == "https://host/good.jpg"

    non_cli = _meta(tmp_path, mode="non_cli")
    with pytest.raises(UploadError):
        tracker._ensure_tag_prompt_allowed(non_cli)
    cli_unattended = _meta(
        tmp_path, mode="cli", unattended=True, unattended_confirm=False
    )
    with pytest.raises(UploadError):
        tracker._ensure_tag_prompt_allowed(cli_unattended)
    monkeypatch.setattr(
        ptp_module, "prompt_in_thread", AsyncMock(side_effect=["", "action"])
    )
    assert await tracker._prompt_required_tags() == "action"


@pytest.mark.asyncio
async def test_form_new_group_helpers(
    tracker: PassThePopcorn,
    tmp_path: Path,
) -> None:
    meta = _meta(tmp_path, youtube=None, manual_year=1999)
    assert tracker._new_group_year(meta, {"year": 0}) == 1999
    tracker.get_torrent_info_tmdb = AsyncMock(return_value={"title": "TMDB"})
    assert await tracker._new_group_torrent_info(meta, "0") == {
        "title": "TMDB"
    }
    tracker.get_torrent_info = AsyncMock(return_value={"title": "IMDb"})
    assert await tracker._new_group_torrent_info(meta, "123") == {
        "title": "IMDb"
    }

    unattended = _meta(tmp_path, youtube=None, unattended=True)
    await tracker._ensure_youtube(unattended)
    assert unattended.youtube == ""
    assert (
        tracker._director_names(
            _meta(tmp_path, imdb_info={"directors": "bad"})
        )
        == ()
    )

    tracker._required_cover = AsyncMock(return_value="cover")
    tracker._required_tags = AsyncMock(return_value=["action"])
    tracker._new_group_torrent_info = AsyncMock(
        return_value={
            "title": "Title",
            "year": 2024,
            "plot": "Plot",
            "tags": ["action"],
        }
    )
    tracker._ensure_youtube = AsyncMock(return_value=None)
    fields = await tracker._new_group_fields(meta, {"imdb": "123"})
    assert fields["image"] == "cover"


@pytest.mark.asyncio
async def test_upload_helper_edges(
    tracker: PassThePopcorn,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meta = _meta(tmp_path, base_torrent_piece_mb=0, nohash=False)
    common = SimpleNamespace(
        create_torrent_for_upload=AsyncMock(return_value=None),
        create_torrent_ready_to_seed=AsyncMock(return_value=None),
    )
    await tracker._prepare_ptp_torrent(common, meta)
    common.create_torrent_for_upload.assert_awaited_once()

    tracker.config["DEFAULT"]["rehash_cooldown"] = "bad"
    assert tracker._rehash_cooldown() == 0
    tracker.config["DEFAULT"]["rehash_cooldown"] = 1
    monkeypatch.setattr(
        ptp_module.TorrentCreator, "create_torrent", AsyncMock()
    )
    await tracker._rehash_for_ptp(common, meta)

    debug_meta = _meta(tmp_path, debug=True)
    data = {"AntiCsrfToken": "secret"}
    assert await tracker._debug_upload(common, debug_meta, "url", data)
    assert data["AntiCsrfToken"] == "secret"

    failure = _Response(text="bad", url="https://passthepopcorn.me/upload.php")
    assert not await tracker._complete_upload_response(common, meta, failure)
    success = _Response(
        text="ok",
        url="https://passthepopcorn.me/torrents.php?id=1&torrentid=2",
    )
    assert await tracker._complete_upload_response(common, meta, success)
    common.create_torrent_ready_to_seed.assert_awaited_once()


@pytest.mark.asyncio
async def test_remaining_description_interaction_branches(
    tracker: PassThePopcorn,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meta = _meta(tmp_path, unattended=False, skipit=False)
    monkeypatch.setattr(
        ptp_module, "prompt_in_thread", AsyncMock(return_value="e")
    )
    monkeypatch.setattr(ptp_module.click, "edit", lambda _value: "edited")
    await tracker._interactive_description(meta, "original")
    assert meta.description == "edited"

    tracker._interactive_description = AsyncMock(return_value=None)
    await tracker._apply_ptp_description(meta, "interactive")
    tracker._interactive_description.assert_awaited_once()


@pytest.mark.asyncio
async def test_remaining_group_lookup_and_runtime_edges(
    tracker: PassThePopcorn,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    movies = [
        {"GroupId": 1, "Title": "A", "Year": 2020},
        {"GroupId": 2, "Title": "B", "Year": 2021},
    ]
    monkeypatch.setattr(
        ptp_module,
        "prompt_in_thread",
        AsyncMock(side_effect=KeyboardInterrupt),
    )
    assert await tracker._multiple_group_result(movies, "x") is None

    _Client.reset(_Response(payload={"Page": "Details", "GroupId": 1}))
    monkeypatch.setattr(
        tracker,
        "_group_from_response",
        AsyncMock(side_effect=ValueError("boom")),
    )
    assert await tracker.get_group_by_imdb("1") is None

    assert tracker._runtime_value(object()) == 60
    assert tracker._runtime_value("bad") == 60
    assert tracker._dvd_codec(_meta(tmp_path, dvd_size="DVD9")) == "DVD9"
    assert tracker._keyword_release_type({"other"}) is None


@pytest.mark.asyncio
async def test_remaining_subtitle_audio_form_branches(
    tracker: PassThePopcorn,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert tracker._subtitle_language({"Language": "en"}) == "en"
    monkeypatch.setattr(
        ptp_module.cli_ui, "ask_string", lambda *_args, **_kwargs: ""
    )
    values = [44]
    tracker._apply_hardcoded_language(values)
    assert values == [44]

    bdmv = _meta(
        tmp_path,
        is_disc="BDMV",
        bdinfo={"audio": []},
    )
    assert tracker._audio_flags(bdmv) == (False, False)

    no_audio = _meta(
        tmp_path,
        mediainfo={"media": {"track": []}},
    )
    assert tracker._audio_flags(no_audio) == (True, False)
    assert tracker._audio_flags_from_tracks([{"Language": "en"}], False) == (
        False,
        True,
    )

    tracker._prompt_trumpable = AsyncMock(return_value=([14], [44]))
    assert await tracker._missing_english_subtitle_fields(
        _meta(tmp_path), [44], True, False
    ) == ([14], [44])
    assert await tracker._missing_english_subtitle_fields(
        _meta(tmp_path), [44], False, False
    ) == ([14], [44])
    assert await tracker._missing_english_subtitle_fields(
        _meta(tmp_path), [3], False, False
    ) == (None, [3])
    assert await tracker._missing_english_subtitle_fields(
        _meta(tmp_path), [44], False, True
    ) == (None, [44])

    tracker.get_trumpable = Mock(return_value=([15], [44]))
    normalized = tracker._hardcoded_subtitle_fields([44], False)
    assert normalized[0] is not None
    tracker.get_subtitles = Mock(return_value=[44])
    hardcoded = _meta(tmp_path, hardcoded_subs=True)
    assert (await tracker._upload_subtitle_fields(hardcoded))[0] is not None

    data = {"remaster_year": "", "remaster_title": ""}
    tracker._apply_common_form_flags(
        data,
        _meta(tmp_path, personalrelease=True),
        "1080p",
        None,
    )
    assert data["internalrip"] == "on"


@pytest.mark.asyncio
async def test_remaining_cover_and_tag_edges(
    tracker: PassThePopcorn,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meta = _meta(tmp_path, artwork_url="https://art/poster.jpg")
    meta.imdb_info = {"cover": None}
    tracker.rehost_poster_to_selected_host = AsyncMock(return_value="rehoused")
    assert await tracker._initial_cover(meta) == "rehoused"

    blank = _meta(tmp_path, artwork_url="")
    blank.imdb_info = {"cover": ""}
    assert await tracker._initial_cover(blank) is None

    non_string = _meta(tmp_path)
    non_string.imdb_info = {"cover": 123}
    assert await tracker._initial_cover(non_string) == 123

    monkeypatch.setattr(
        ptp_module, "prompt_in_thread", AsyncMock(return_value="")
    )
    assert (
        await tracker._prompt_cover_candidate(
            _meta(tmp_path, unattended=False)
        )
        is None
    )

    allowed = _meta(
        tmp_path, mode="cli", unattended=False, unattended_confirm=False
    )
    tracker._ensure_tag_prompt_allowed(allowed)
    assert await tracker._required_tags(allowed, ["action"]) == ["action"]
    tracker._prompt_required_tags = AsyncMock(return_value="action")
    assert await tracker._required_tags(allowed, None) == "action"

    existing_youtube = _meta(tmp_path, youtube="https://youtube.com/watch?v=x")
    await tracker._ensure_youtube(existing_youtube)
    assert existing_youtube.youtube.endswith("v=x")


@pytest.mark.asyncio
async def test_remaining_pack_and_comparison_edges(
    tracker: PassThePopcorn,
    tmp_path: Path,
) -> None:
    assert (
        tracker._description_base(_meta(tmp_path, scene_nfo_file="nfo")) == ""
    )

    tracker.config["DEFAULT"] = None
    desc = io.StringIO()
    tracker._write_tonemapped_header(desc, _meta(tmp_path, tonemapped=True))
    tracker.config["DEFAULT"] = _config()["DEFAULT"]

    assert (
        tracker._pack_key_images({"keys": {"x": {"images": "bad"}}}, "x") == []
    )
    assert tracker._comparison_groups(_meta(tmp_path, comparison=False)) == {}
    groups = {
        "1": {"name": "A", "urls": [{"raw_url": ""}]},
        "2": {"name": "B", "urls": []},
    }
    assert tracker._comparison_row(groups, ["1", "2"], 0) == ""
    comparison_meta = _meta(tmp_path, comparison=False)
    tracker._write_comparison(io.StringIO(), comparison_meta)


@pytest.mark.asyncio
async def test_remaining_disc_and_file_description_edges(
    tracker: PassThePopcorn,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dvd = {
        "type": "DVD",
        "name": "DVD1",
        "ifo_mi_full": "IFO",
        "vob_mi_full": "VOB",
    }
    bdmv = {"type": "BDMV", "summary": "SUMMARY"}
    meta = _meta(tmp_path, discs=[dvd, bdmv], retry_count=0)
    assert tracker._write_initial_disc_media(io.StringIO(), meta, dvd) == []

    tracker._write_multi_bdmv_disc = AsyncMock(return_value=None)
    tracker._write_multi_dvd_disc = AsyncMock(return_value=None)
    await tracker._write_multiple_discs(
        io.StringIO(), meta, [bdmv, dvd], "BASE", [], {}, 2
    )
    tracker._write_multi_bdmv_disc.assert_awaited_once()
    tracker._write_multi_dvd_disc.assert_awaited_once()

    tracker = PassThePopcorn(_config())
    monkeypatch.setattr(ptp_module.httpx, "AsyncClient", _Client)
    tracker._restore_pack_images = Mock(return_value=False)
    tracker._multi_bdmv_screens = AsyncMock(return_value=None)
    desc = io.StringIO()
    meta = _meta(tmp_path, retry_count=0)
    await tracker._write_multi_bdmv_disc(
        desc, meta, bdmv, "BASE", [], {}, 0, 2
    )
    await tracker._write_multi_bdmv_disc(
        desc, meta, bdmv, "BASE", [], {}, 1, 2
    )
    assert meta.retry_count == 1

    meta = _meta(tmp_path, discs=[dvd], retry_count=0)
    tracker._dvd_screen_names = Mock(side_effect=[[], []])
    tracker.takescreens_manager.dvd_screenshots = AsyncMock(
        side_effect=RuntimeError("capture")
    )
    assert await tracker._ensure_dvd_screens(meta, 0, 2) == []
    tracker._dvd_screen_names = Mock(return_value=["existing.png"])
    assert await tracker._ensure_dvd_screens(meta, 0, 2) == ["existing.png"]

    tracker._persist_meta = AsyncMock(return_value=None)
    tracker._restore_pack_images = Mock(return_value=False)
    tracker._ensure_dvd_screens = AsyncMock(return_value=["screen.png"])
    tracker._upload_extra_images = AsyncMock(
        return_value=[{"raw_url": "https://pixhost.to/dvd"}]
    )
    meta = _meta(tmp_path, discs=[dvd, dvd], retry_count=0)
    await tracker._write_multi_dvd_disc(
        io.StringIO(), meta, dvd, "BASE", [], {}, 1, 2
    )
    assert meta.new_images_disc_1

    tracker._restore_pack_images = Mock(return_value=True)
    meta.new_images_disc_1 = [{"raw_url": "https://saved"}]
    await tracker._write_multi_dvd_disc(
        io.StringIO(), meta, dvd, "BASE", [], {}, 1, 2
    )

    monkeypatch.setattr(
        ptp_module, "manifest_files", lambda *_args, **_kwargs: []
    )
    tracker.takescreens_manager.screenshots = AsyncMock(
        side_effect=RuntimeError("capture")
    )
    assert await tracker._ensure_file_screens(meta, "file.mkv", 1, 2) == []

    existing = SimpleNamespace(name="existing.png")
    monkeypatch.setattr(
        ptp_module, "manifest_files", lambda *_args, **_kwargs: [existing]
    )
    assert await tracker._ensure_file_screens(meta, "file.mkv", 1, 2) == [
        "existing.png"
    ]


@pytest.mark.asyncio
async def test_remaining_upload_extra_and_additional_file_edges(
    tracker: PassThePopcorn,
    tmp_path: Path,
) -> None:
    meta = _meta(tmp_path)
    meta.test_images = []
    assert await tracker._upload_extra_images(meta, "test_images", [], 2) == []
    skip = _meta(tmp_path, skip_imghost_upload=True)
    skip.test_images = []
    assert (
        await tracker._upload_extra_images(skip, "test_images", ["x.png"], 2)
        == []
    )

    tracker.uploadscreens_manager.upload_screens = AsyncMock(
        return_value=([{"raw_url": "https://pixhost.to/x"}], False)
    )
    tracker.save_image_links = AsyncMock(return_value=None)
    uploaded = await tracker._upload_extra_images(
        meta, "test_images", ["x.png"], 2
    )
    assert uploaded
    tracker.save_image_links.assert_awaited_once()

    tracker._additional_file_mediainfo = AsyncMock(return_value="MI")
    tracker._persist_meta = AsyncMock(return_value=None)
    tracker._restore_pack_images = Mock(return_value=True)
    meta.new_images_file_1 = [{"raw_url": "https://saved"}]
    await tracker._write_additional_file(
        io.StringIO(), meta, "file.mkv", 1, {}, 2
    )

    tracker._restore_pack_images = Mock(return_value=False)
    tracker._ensure_file_screens = AsyncMock(return_value=[])
    tracker._upload_extra_images = AsyncMock(return_value=[])
    meta.retry_count = 0
    await tracker._write_additional_file(
        io.StringIO(), meta, "file.mkv", 2, {}, 2
    )
    assert meta.retry_count == 1


@pytest.mark.asyncio
async def test_remaining_image_link_login_and_description_file_edges(
    tracker: PassThePopcorn,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = tmp_path / "missing.json"
    assert await tracker._existing_image_links(missing) == {
        "keys": {},
        "total_count": 0,
    }

    class BadWriter:
        async def __aenter__(self) -> Any:
            raise OSError("write fail")

        async def __aexit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(
        ptp_module.aiofiles,
        "open",
        lambda *_args, **_kwargs: BadWriter(),
    )
    assert (
        await tracker._write_image_links(
            tmp_path / "out.json", {"total_count": 0}, "x", 1
        )
        is None
    )
    monkeypatch.undo()

    response = _Response(text="not-json")
    error = tracker._login_response_error(response)
    assert isinstance(error, LoginError)

    tracker._post_login = AsyncMock(
        return_value=_Response(payload=["not", "object"], text="bad")
    )
    with pytest.raises(LoginError):
        await tracker._fresh_session_token(
            _meta(tmp_path), tmp_path / "cookie.json", {}
        )

    missing_meta = _meta(tmp_path, uuid="missing-description")
    assert await tracker._upload_description(missing_meta) == ""


@pytest.mark.asyncio
async def test_final_eight_coverage_branches(
    tracker: PassThePopcorn,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert tracker._keyword_release_type({"short"}) == "Short Film"

    dvd_meta = _meta(
        tmp_path,
        is_disc="DVD",
        discs=[{"ifo": "VIDEO_TS.IFO", "name": "DVD"}],
    )
    monkeypatch.setattr(
        ptp_module.MediaInfo,
        "parse",
        lambda *_args, **_kwargs: json.dumps(
            {"media": {"track": [{"@type": "Text", "Language": "en"}]}}
        ),
    )
    assert tracker._subtitle_tracks(dvd_meta)[0]["@type"] == "Text"

    assert tracker._pack_key_images({"keys": {"x": "bad"}}, "x") == []

    meta = _meta(tmp_path)
    meta.bucket = []
    tracker._upload_extra_images = AsyncMock(return_value=[])
    monkeypatch.setattr(
        ptp_module, "manifest_files", lambda *_args, **_kwargs: []
    )
    await tracker._multi_bdmv_screens(io.StringIO(), meta, "bucket", 1, 2)

    missing_retry = _meta(tmp_path)
    missing_retry.retry_count = None  # type: ignore[assignment]
    await tracker._write_multiple_discs(
        io.StringIO(), missing_retry, [], "", [], {}, 2
    )
    assert missing_retry.retry_count == 0

    tracker._additional_file_mediainfo = AsyncMock(return_value="MI")
    tracker._restore_pack_images = Mock(return_value=False)
    tracker._ensure_file_screens = AsyncMock(return_value=["screen.png"])
    tracker._upload_extra_images = AsyncMock(
        return_value=[{"raw_url": "https://pixhost.to/new"}]
    )
    tracker._persist_meta = AsyncMock(return_value=None)
    file_meta = _meta(tmp_path, retry_count=0)
    await tracker._write_additional_file(
        io.StringIO(), file_meta, "file.mkv", 1, {}, 2
    )

    assert tracker._audio_flags_from_tracks([{}], False) == (True, False)

    form_data = {"remaster_year": "", "remaster_title": ""}
    tracker._apply_common_form_flags(
        form_data,
        _meta(tmp_path),
        "Other",
        "1920x800",
    )
    assert form_data["other_resolution"] == "1920x800"
