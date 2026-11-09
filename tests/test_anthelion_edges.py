from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from src.domain_models.release import Meta
from src.integrations.trackers import anthelion as anthelion_module
from src.integrations.trackers.anthelion import Anthelion


def _config(**tracker_values: object) -> dict[str, Any]:
    values: dict[str, Any] = {
        "api_key": "api-key",
        "announce_url": "https://tracker.invalid/announce",
    }
    values.update(tracker_values)
    return {"TRACKERS": {"ANTHELION": values}, "DEFAULT": {}}


def _tracker(**tracker_values: object) -> Anthelion:
    return Anthelion(_config(**tracker_values))


def _meta(tmp_path: Path | None = None, **values: object) -> Meta:
    base = str(tmp_path or Path())
    state: dict[str, object] = {
        "base_dir": base,
        "uuid": "release",
        "path": str((tmp_path or Path()) / "release.mkv"),
        "title": "Example Movie",
        "name": "Example.Movie.2020.1080p",
        "category": "MOVIE",
        "type": "ENCODE",
        "source": "Blu-ray",
        "resolution": "1080p",
        "edition": "",
        "audio": "DD+ 5.1",
        "has_commentary": False,
        "manual_commentary": False,
        "three_d": "",
        "hdr": "",
        "distributor": "",
        "tag": "-GROUP",
        "genres": ["Drama"],
        "imdb_info": {},
        "keywords": [],
        "tmdb_type": "movie",
        "runtime": 120,
        "unattended": False,
        "unattended_confirm": False,
        "mkbrr": False,
        "tmdb": 123,
        "imdb_id": 456,
        "imdb": 456,
        "is_disc": "",
        "scene": False,
        "adult_media": False,
        "image_list": [{"raw_url": "https://img.invalid/1.png"}],
        "ant_user_tags": False,
        "ua_name": "Upload Assistant",
        "current_version": "1.0",
        "debug": False,
        "tracker_status": {"ANTHELION": {}},
        "filelist": [str((tmp_path or Path()) / "release.mkv")],
        "valid_mi": True,
    }
    state.update(values)
    return Meta(state)


def _response(
    payload: Any = None, *, status: int = 200, text: str | None = None
) -> httpx.Response:
    request = httpx.Request("POST", Anthelion.api_url)
    if text is not None:
        return httpx.Response(status, request=request, text=text)
    return httpx.Response(status, request=request, json=payload)


@pytest.mark.asyncio
async def test_anthelion_flags_cover_all_features() -> None:
    tracker = _tracker()
    flags = await tracker.get_flags(
        _meta(
            edition="Director's Extended Uncut Unrated 4KRemaster IMAX",
            audio="Dual-Audio Atmos",
            has_commentary=True,
            three_d="3D",
            hdr="HDR DV",
            distributor="Criterion",
            type="REMUX",
        )
    )
    assert set(flags) >= {
        "Directors",
        "Extended",
        "Uncut",
        "Unrated",
        "4KRemaster",
        "IMAX",
        "DualAudio",
        "Atmos",
        "Commentary",
        "3D",
        "HDR10",
        "DV",
        "Criterion",
        "Remux",
    }


@pytest.mark.asyncio
async def test_anthelion_tags_imdb_and_missing_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    sleep = AsyncMock()
    monkeypatch.setattr(anthelion_module.asyncio, "sleep", sleep)
    imdb_meta = _meta(
        genres=[], imdb_info={"genres": ["Drama", "Not Allowed"]}
    )
    assert await tracker.get_tags(imdb_meta) == ""
    assert imdb_meta.ant_user_tags is True
    sleep.assert_awaited()

    unattended = _meta(
        genres=[], imdb_info={}, unattended=True, unattended_confirm=False
    )
    assert await tracker.get_tags(unattended) == ""
    assert unattended.skipping == "ANTHELION"

    async def prompt_empty(*_args: object, **_kwargs: object) -> str:
        return ""

    monkeypatch.setattr(anthelion_module, "prompt_in_thread", prompt_empty)
    assert await tracker.get_tags(_meta(genres=[], imdb_info={})) == []


@pytest.mark.asyncio
async def test_anthelion_type_detection_and_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    assert (
        await tracker.get_type(
            _meta(imdb_info={"type": "movie", "runtime": "30"})
        )
        == 1
    )
    assert await tracker.get_type(_meta(imdb_info={"type": "short"})) == 1
    assert (
        await tracker.get_type(_meta(imdb_info={"type": "tv mini series"}))
        == 2
    )
    assert await tracker.get_type(_meta(imdb_info={"type": "comedy"})) == 3
    assert (
        await tracker.get_type(_meta(imdb_info={}, keywords=["short film"]))
        == 1
    )
    assert (
        await tracker.get_type(_meta(imdb_info={}, keywords=["miniseries"]))
        == 2
    )
    assert (
        await tracker.get_type(
            _meta(imdb_info={}, keywords=["stand-up comedy"])
        )
        == 3
    )
    assert (
        await tracker.get_type(
            _meta(imdb_info={}, tmdb_type="movie", runtime=0)
        )
        == 0
    )
    assert (
        await tracker.get_type(
            _meta(imdb_info={}, tmdb_type="other", unattended=True)
        )
        == 0
    )

    async def prompt_choice(*_args: object, **_kwargs: object) -> str:
        return "Other"

    monkeypatch.setattr(anthelion_module, "prompt_in_thread", prompt_choice)
    assert (
        await tracker.get_type(
            _meta(imdb_info={}, tmdb_type="other", unattended=False)
        )
        == 3
    )


def test_anthelion_runtime_type_invalid_defaults_feature() -> None:
    assert Anthelion._runtime_type("invalid") == 0


@pytest.mark.asyncio
async def test_anthelion_prepare_torrent_regenerates_large_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = _tracker()
    root = tmp_path / "tmp" / "release"
    root.mkdir(parents=True)
    base = root / "BASE.torrent"
    base.write_bytes(b"x" * (251 * 1024))
    create = AsyncMock()
    monkeypatch.setattr(
        anthelion_module.TorrentCreator, "create_torrent", create
    )
    item = _meta(tmp_path, mkbrr=True)
    assert await tracker._prepare_torrent(item) == "ANTHELION"
    assert item.max_piece_size == 128
    create.assert_awaited_once()
    assert (
        create.await_args.kwargs["tracker_url"]
        == "https://tracker.invalid/announce"
    )


def test_anthelion_release_group_banned_and_missing() -> None:
    tracker = _tracker()
    data: dict[str, Any] = {}
    asyncio.run(tracker._apply_release_group(_meta(tag="-RARBG"), data))
    assert data == {"noreleasegroup": 1}
    data = {}
    asyncio.run(tracker._apply_release_group(_meta(tag=""), data))
    assert data == {"noreleasegroup": 1}


@pytest.mark.asyncio
async def test_anthelion_adult_screenshot_policies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    data: dict[str, Any] = {}
    unattended = _meta(
        adult_media=True, unattended=True, unattended_confirm=False
    )
    await tracker._apply_adult_screenshots(unattended, data, "screens")
    assert data["screenshots"] == ""

    async def unsafe(*_args: object, **_kwargs: object) -> bool:
        return False

    monkeypatch.setattr(anthelion_module, "prompt_in_thread", unsafe)
    data = {}
    await tracker._apply_adult_screenshots(
        _meta(adult_media=True), data, "screens"
    )
    assert data["screenshots"] == ""


@pytest.mark.asyncio
async def test_anthelion_upload_aborts_audio_and_skipping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        tracker, "_prepare_torrent", AsyncMock(return_value="BASE")
    )
    tracker.common.create_torrent_for_upload = AsyncMock()  # type: ignore[method-assign]
    monkeypatch.setattr(tracker, "get_audio", AsyncMock(return_value=""))
    item = _meta()
    assert not await tracker.upload(item)
    assert (
        "unsupported audio format"
        in item.tracker_status["ANTHELION"]["status_message"]
    )

    monkeypatch.setattr(tracker, "get_audio", AsyncMock(return_value="EAC3"))
    monkeypatch.setattr(
        tracker, "_torrent_upload_file", AsyncMock(return_value={})
    )

    async def data_with_skip(meta: Meta, _audio: str) -> dict[str, Any]:
        meta.skipping = "ANTHELION"
        return {}

    monkeypatch.setattr(tracker, "_upload_data", data_with_skip)
    assert not await tracker.upload(_meta())


@pytest.mark.asyncio
async def test_anthelion_submit_upload_error_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    item = _meta()
    cases = (
        httpx.TimeoutException("timeout"),
        httpx.RequestError(
            "offline", request=httpx.Request("POST", tracker.api_url)
        ),
        RuntimeError("unexpected"),
    )
    for error in cases:
        monkeypatch.setattr(
            tracker, "_post_upload", AsyncMock(side_effect=error)
        )
        item.tracker_status["ANTHELION"] = {}
        assert not await tracker._submit_upload(item, {}, {})
        assert item.tracker_status["ANTHELION"]["status_message"]


@pytest.mark.asyncio
async def test_anthelion_handle_response_json_and_api_failure() -> None:
    tracker = _tracker()
    item = _meta()
    assert not tracker._handle_upload_response(
        item, _response(text="not-json")
    )
    assert (
        "json decode error"
        in item.tracker_status["ANTHELION"]["status_message"]
    )

    item.tracker_status["ANTHELION"] = {}
    assert not tracker._handle_upload_response(
        item, _response({"status": "failure"}, status=200)
    )
    assert "data error" in item.tracker_status["ANTHELION"]["status_message"]


@pytest.mark.asyncio
async def test_anthelion_unknown_audio_returns_other() -> None:
    assert await _tracker().get_audio(_meta(audio="Vorbis")) == "Other"


def test_anthelion_search_result_parser_branches() -> None:
    tracker = _tracker()
    assert tracker._search_items([]) == []
    assert tracker._search_items({"item": "bad"}) == []
    items = tracker._search_items(
        {
            "item": [
                "bad",
                {
                    "fileName": "Release",
                    "resolution": "1080p",
                    "size": "bad",
                    "files": [],
                },
            ]
        }
    )
    assert len(items) == 1

    assert tracker._search_entry(items[0], "720p") is None
    entry = tracker._search_entry(items[0], "")
    assert entry is not None
    assert entry["name"] == "Release"
    assert entry["size"] == 0
    assert tracker._largest_file_name([]) == ""

    results = tracker._search_results(
        _meta(resolution="1080p"),
        {
            "item": [
                {
                    "fileName": "Fallback",
                    "resolution": "1080p",
                    "size": 10,
                    "files": [
                        {"name": "small.mkv", "size": 1},
                        {"name": "large.mkv", "size": 2},
                    ],
                }
            ]
        },
    )
    assert results[0]["name"] == "large.mkv"


@pytest.mark.asyncio
async def test_anthelion_get_data_from_files_guards_and_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    assert await tracker.get_data_from_files(_meta(is_disc="BDMV")) == []
    assert await tracker.get_data_from_files(_meta(filelist=[])) == []

    no_key = _tracker(api_key="")
    assert await no_key.get_data_from_files(_meta()) == []
    assert tracker._configured_api_key() == "api-key"

    for error in (
        httpx.TimeoutException("timeout"),
        httpx.RequestError(
            "offline", request=httpx.Request("GET", tracker.api_url)
        ),
        RuntimeError("unexpected"),
    ):
        monkeypatch.setattr(
            tracker, "_file_search_response", AsyncMock(side_effect=error)
        )
        assert await tracker._safe_file_search("release.mkv", "api-key") == []

    bad_status = httpx.Response(
        500, request=httpx.Request("GET", tracker.api_url), text="error"
    )
    monkeypatch.setattr(
        tracker, "_file_search_response", AsyncMock(return_value=bad_status)
    )
    assert await tracker.get_data_from_files(_meta()) == []


def test_anthelion_file_search_ids_and_matching() -> None:
    tracker = _tracker()
    invalid = httpx.Response(
        200, request=httpx.Request("GET", tracker.api_url), text="not-json"
    )
    assert tracker._file_search_ids("release.mkv", invalid) == []

    direct = tracker._matched_file_item(
        "release.mkv", {"item": [{"imdb": "tt123", "tmdb": "456"}]}
    )
    assert direct == {"imdb": "tt123", "tmdb": "456"}
    assert tracker._file_items([]) == []
    assert tracker._external_ids({"imdb": "tt123", "tmdb": "456"}) == [
        {"imdb_id": 123},
        {"tmdb_id": 456},
    ]
    assert tracker._imdb_numeric_id("tt123") == 123


def test_anthelion_filename_matching_and_multiple_item_no_match() -> None:
    tracker = _tracker()
    assert tracker._filename_matches("Movie.mkv", "movie.mkv")
    assert tracker._filename_matches("Movie.mkv", "movie.mp4")
    payload = {
        "item": [
            {"files": [{"name": "other.mkv"}]},
            {"files": [{"name": "different.mkv"}]},
        ]
    }
    assert tracker._matched_file_item("wanted.mkv", payload) is None
