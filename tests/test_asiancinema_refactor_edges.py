from __future__ import annotations

import asyncio
import copy

from data.example_config import config as example_config
from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.asiancinema import AsianCinema


def _config() -> dict:
    config = copy.deepcopy(example_config)
    config.setdefault("DEFAULT", {}).setdefault("tmdb_api", "test-key")
    tracker = config.setdefault("TRACKERS", {}).setdefault("ASIANCINEMA", {})
    tracker.setdefault("api_key", "test-key")
    tracker.setdefault("announce_url", "https://tracker.invalid/announce")
    return config


def test_asiancinema_allows_asian_origin() -> None:
    tracker = AsianCinema(_config())
    assert asyncio.run(
        tracker.get_additional_checks(Meta(origin_country=["JP"]))
    )
    assert asyncio.run(tracker.get_additional_checks(Meta(origin_country=[])))


def test_asiancinema_resolution_mapping_edges() -> None:
    tracker = AsianCinema(_config())
    meta = Meta(resolution="720p")

    mapping = asyncio.run(tracker.get_resolution_id(meta, mapping_only=True))
    assert mapping["2160p"] == "1"
    reverse = asyncio.run(tracker.get_resolution_id(meta, reverse=True))
    assert reverse["1"] == "2160p"
    assert asyncio.run(
        tracker.get_resolution_id(meta, resolution="2160p")
    ) == {"resolution_id": "1"}
    assert asyncio.run(
        tracker.get_resolution_id(meta, resolution="unknown")
    ) == {"resolution_id": "6"}
    assert asyncio.run(tracker.get_resolution_id(meta)) == {
        "resolution_id": "3"
    }


def test_asiancinema_region_mapping_edges() -> None:
    tracker = AsianCinema(_config())
    assert asyncio.run(tracker.get_region_id(Meta(region="KOR"))) == {
        "region_id": "1"
    }
    assert asyncio.run(tracker.get_region_id(Meta(region="ZZZ"))) == {
        "region_id": ""
    }


def test_asiancinema_name_aka_and_aac_edges() -> None:
    tracker = AsianCinema(_config())
    meta = Meta(
        name="Title Alt 2026 WEB-DL AAC 2.0 H.265 Atmos",
        title="Title",
        aka="Alt",
        original_title="Original",
        audio="AAC 2.0",
        subtitle_languages=["English"],
    )

    result = asyncio.run(tracker.get_name(meta))["name"]

    assert "Original" in result
    assert "Alt" not in result
    assert "AAC2.0" in result
    assert "HEVC" in result
    assert "Atmos" not in result


def test_asiancinema_name_original_title_and_non_dvd_edges() -> None:
    tracker = AsianCinema(_config())
    meta = Meta(
        name="Title 2026 WEB-DL DD+ 5.1",
        title="Title",
        aka="",
        original_title="Original",
        audio="DD+ 5.1",
        is_disc="",
        subtitle_languages=["English"],
    )

    result = asyncio.run(tracker.get_name(meta))["name"]

    assert "Title / Original" in result
    assert "DD+5.1" in result


def test_asiancinema_dvd_without_mpeg_rewrite() -> None:
    tracker = AsianCinema(_config())
    meta = Meta(
        name="Example PAL DVD5 AAC 2.0",
        title="Example",
        original_title="Example",
        audio="AAC 2.0",
        channels="5.1",
        source="PAL",
        is_disc="DVD",
        resolution="576p",
        subtitle_languages=["English"],
    )

    result = asyncio.run(tracker.get_name(meta))["name"]

    assert "576p DVD PAL" in result
    assert "MPEG" not in result
