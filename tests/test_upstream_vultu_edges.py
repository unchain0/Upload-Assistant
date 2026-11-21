from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from data.example_config import config as example_config
from src.domain_models.release import Meta
from src.domain_models.tracker_catalog import TRACKER_DEFINITIONS
from src.integrations.trackers import registry
from src.integrations.trackers.vultu import Vultu


def _tracker() -> Vultu:
    return Vultu({"DEFAULT": {}, "TRACKERS": {"VULTU": {}}})


def test_vultu_is_registered_with_its_auth_and_categories() -> None:
    assert registry.tracker_class_map["VULTU"] is Vultu
    assert TRACKER_DEFINITIONS["VULTU"].auth_type == "other_api"
    assert example_config["TRACKERS"]["VULTU"]["api_key"] == ""


def test_music_work_uses_valid_musicbrainz_release_group() -> None:
    meta = Meta(
        category="MUSIC",
        music_release={
            "external_ids": {
                "musicbrainz_release_group": "01234567-89ab-cdef-0123-456789abcdef"
            }
        },
    )

    assert asyncio.run(_tracker().get_work(meta)) == (
        "musicbrainz:release-group:01234567-89ab-cdef-0123-456789abcdef"
    )


def test_music_upload_uses_lame_preset_and_bbcode_notes() -> None:
    tracker = _tracker()
    tracker.get_notes = AsyncMock(return_value="[b]Synthetic notes[/b]")  # type: ignore[method-assign]
    tracker.get_mediainfo = AsyncMock(return_value="")  # type: ignore[method-assign]
    meta = Meta(
        category="MUSIC",
        name="Synthetic Artist - Synthetic Album FLAC",
        tag="-GROUP",
        music_release={
            "tracks": [
                {
                    "encoder_settings": "-V 0",
                    "bitrate_mode": "VBR",
                    "bitrate": 230000,
                }
            ]
        },
    )

    data = asyncio.run(
        tracker.get_data(meta, "musicbrainz:release:release-id")
    )

    assert data["name"].endswith("FLAC")
    assert data["notes"] == "[b]Synthetic notes[/b]"
    assert data["notes_format"] == "bbcode"


def test_music_upload_requires_an_external_album_id() -> None:
    assert not asyncio.run(
        _tracker().get_additional_checks(Meta(category="MUSIC"))
    )


def _mock_client(
    monkeypatch: pytest.MonkeyPatch,
    handler: Any,
) -> list[httpx.Request]:
    requests: list[httpx.Request] = []
    client_type = httpx.AsyncClient

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(record)
        return client_type(*args, **kwargs)

    monkeypatch.setattr(
        "src.integrations.trackers.vultu.httpx.AsyncClient", factory
    )
    return requests


def test_vultu_work_lookup_and_duplicate_pagination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = [
        {
            "releases": [
                {
                    "name": "First",
                    "size": 1,
                    "files": [{"path": "a.mkv"}],
                    "id": 1,
                }
            ],
            "next": "next",
        },
        {
            "releases": [
                {
                    "name": "Second",
                    "size": 2,
                    "files": [],
                    "url": "https://vultu.invalid/torrents/2",
                    "id": 2,
                }
            ],
            "next": None,
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/works"):
            assert request.url.params["imdb"] == "tt0000001"
            return httpx.Response(
                200, json={"results": [{"kind": "tv", "work": "tmdb:tv:12"}]}
            )
        assert request.url.path.endswith("/duplicates")
        return httpx.Response(200, json=pages.pop(0))

    calls = _mock_client(monkeypatch, handler)
    tracker = _tracker()
    meta = Meta(category="TV", imdb_tt="tt0000001", season="S02")

    assert asyncio.run(tracker.get_work(meta)) == "tmdb:tv:12"
    meta.tmdb = 12
    assert asyncio.run(tracker.search_existing(meta)) == [
        {
            "name": "First",
            "size": 1,
            "files": ["a.mkv"],
            "file_count": 1,
            "link": None,
            "id": 1,
        },
        {
            "name": "Second",
            "size": 2,
            "files": [],
            "file_count": 0,
            "link": "https://vultu.invalid/torrents/2",
            "id": 2,
        },
    ]
    assert len(calls) == 3


def test_vultu_requests_data_and_filesystem_payloads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_dir = tmp_path / "tmp" / "vultu"
    release_dir.mkdir(parents=True)
    (release_dir / "MEDIAINFO_CLEANPATH.txt").write_text(
        "MediaInfo", encoding="utf-8"
    )
    (release_dir / "release.nfo").write_bytes(b"NFO")
    requests_page = {
        "requests": [
            {"id": 1, "title": "Season two", "bounty": 5, "season": 2},
            {"id": 2, "title": "Season three", "bounty": 6, "season": 3},
        ]
    }
    _mock_client(
        monkeypatch, lambda _request: httpx.Response(200, json=requests_page)
    )
    generator = AsyncMock(return_value=" [b]Notes[/b] ")
    monkeypatch.setattr(
        "src.integrations.trackers.vultu.DescriptionBuilder.general_description_generator",
        generator,
    )
    tracker = _tracker()
    meta = Meta(
        category="TV",
        base_dir=str(tmp_path),
        uuid="vultu",
        tmdb=12,
        season="S02",
        name="Synthetic Show S02 1080p WEB-DL-GROUP",
        image_list=[
            {"raw_url": "https://images.invalid/one.png"},
            {"raw_url": "https://images.invalid/one.png"},
            {"raw_url": "http://images.invalid/insecure.png"},
        ],
        search_requests=True,
        keep_nfo=True,
        keep_folder=True,
        path=str(tmp_path / "release.mkv"),
    )

    data = asyncio.run(tracker.get_data(meta, "tmdb:tv:12"))

    assert data["screenshots"] == ["https://images.invalid/one.png"]
    assert data["mediainfo"] == "MediaInfo"
    assert data["notes"] == "[b]Notes[/b]"
    assert asyncio.run(tracker.get_nfo(meta)) == (
        "release.nfo",
        b"NFO",
        "text/plain",
    )
    assert asyncio.run(tracker.get_requests(meta)) == [
        {
            "Name": "Season two",
            "Reward": "5",
            "Link": "https://vultu.org/pedidos/1",
        }
    ]
    preview = asyncio.run(tracker.get_data(meta, "tmdb:tv:12", preview=True))
    assert preview == {
        "work": "tmdb:tv:12",
        "name": asyncio.run(tracker.get_name(meta)),
        "anime": "false",
        "anonymous": "false",
    }


def test_vultu_upload_debug_and_failure_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    no_work = Meta(category="MOVIE", name="Synthetic")
    assert asyncio.run(tracker.upload(no_work)) is False

    (tmp_path / "tmp" / "debug").mkdir(parents=True)
    debug = Meta(
        category="GAME",
        igdb_id=3,
        base_dir=str(tmp_path),
        uuid="debug",
        name="Synthetic Game",
        debug=True,
    )
    create = AsyncMock()
    monkeypatch.setattr(tracker.common, "create_torrent_for_upload", create)
    assert asyncio.run(tracker.upload(debug)) is True
    create.assert_awaited_once()


def test_vultu_request_search_uses_its_custom_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    requests = [
        {
            "Name": "Synthetic request",
            "Reward": "0",
            "Link": "https://vultu.invalid/pedidos/1",
        }
    ]
    get_requests = AsyncMock(return_value=requests)
    monkeypatch.setattr(tracker, "get_requests", get_requests)
    setup = registry.TrackerSetup({"DEFAULT": {}, "TRACKERS": {"VULTU": {}}})
    monkeypatch.setitem(
        registry.tracker_class_map, "VULTU", lambda _config: tracker
    )
    generic_search = AsyncMock(
        side_effect=AssertionError("generic dispatch used")
    )
    monkeypatch.setattr(setup, "get_tracker_requests", generic_search)

    result = asyncio.run(
        setup._process_tracker_request(Meta(category="MUSIC"), "VULTU")
    )

    assert result is False
    get_requests.assert_awaited_once()
    generic_search.assert_not_awaited()
