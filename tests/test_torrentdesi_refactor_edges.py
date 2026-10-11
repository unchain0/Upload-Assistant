from __future__ import annotations

import asyncio
import copy

import pytest

from data.example_config import config as example_config
from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.torrentdesi import DesiTorrents


def _tracker() -> DesiTorrents:
    config = copy.deepcopy(example_config)
    config.setdefault("DEFAULT", {}).setdefault("tmdb_api", "test-key")
    values = config.setdefault("TRACKERS", {}).setdefault("DESITORRENTS", {})
    values.setdefault("api_key", "test-key")
    values.setdefault("announce_url", "https://tracker.invalid/announce")
    return DesiTorrents(config)


def test_torrentdesi_category_mapping_edges() -> None:
    tracker = _tracker()
    meta = Meta(category="MOVIE")
    assert (
        asyncio.run(tracker.get_category_id(meta, mapping_only=True))["TV"]
        == "2"
    )
    assert (
        asyncio.run(tracker.get_category_id(meta, reverse=True))["1"]
        == "MOVIE"
    )
    assert asyncio.run(tracker.get_category_id(meta, category="GAME")) == {
        "category_id": "4"
    }
    assert asyncio.run(tracker.get_category_id(meta)) == {"category_id": "1"}
    assert asyncio.run(tracker.get_category_id(Meta(category="OTHER"))) == {
        "category_id": "0"
    }


@pytest.mark.parametrize(
    ("meta", "expected"),
    (
        (Meta(type="DISC", disctype="BD50"), "3"),
        (Meta(type="DISC", disctype="BD25"), "4"),
        (Meta(type="REMUX", uhd=True), "2"),
        (Meta(type="REMUX", uhd=False), "5"),
        (Meta(type="ENCODE", uhd=True), "1"),
        (Meta(type="ENCODE", uhd=False), "12"),
        (Meta(type="WEBDL"), "11"),
        (Meta(type="WEBRIP"), "12"),
        (Meta(type="DVD"), "8"),
        (Meta(type="HDTV"), "13"),
        (Meta(type="OTHER"), "0"),
    ),
)
def test_torrentdesi_dynamic_type_ids(meta: Meta, expected: str) -> None:
    assert asyncio.run(_tracker().get_type_id(meta)) == {"type_id": expected}


def test_torrentdesi_type_mapping_modes() -> None:
    tracker = _tracker()
    meta = Meta(type="REMUX")
    assert (
        asyncio.run(tracker.get_type_id(meta, mapping_only=True))["DISC"]
        == "3"
    )
    assert asyncio.run(tracker.get_type_id(meta, reverse=True))["5"] == "REMUX"
    assert asyncio.run(tracker.get_type_id(meta, type="DVD")) == {
        "type_id": "8"
    }
    assert asyncio.run(tracker.get_type_id(meta, type="UNKNOWN")) == {
        "type_id": "0"
    }


def test_torrentdesi_resolution_mapping_edges() -> None:
    tracker = _tracker()
    meta = Meta(resolution="1080p")
    mapping = asyncio.run(tracker.get_resolution_id(meta, mapping_only=True))
    assert mapping["2160p"] == "8"
    assert (
        asyncio.run(tracker.get_resolution_id(meta, reverse=True))["8"]
        == "2160p"
    )
    assert asyncio.run(tracker.get_resolution_id(meta, resolution="720p")) == {
        "resolution_id": "6"
    }
    assert asyncio.run(
        tracker.get_resolution_id(meta, resolution="UNKNOWN")
    ) == {"resolution_id": "10"}
    assert asyncio.run(tracker.get_resolution_id(meta)) == {
        "resolution_id": "11"
    }
    assert asyncio.run(
        tracker.get_resolution_id(Meta(resolution="UNKNOWN"))
    ) == {"resolution_id": "10"}
