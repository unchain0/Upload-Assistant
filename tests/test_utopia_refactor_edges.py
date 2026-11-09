from __future__ import annotations

import asyncio
import copy

from data.example_config import config as example_config
from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.utopia import Utopia


def _tracker() -> Utopia:
    config = copy.deepcopy(example_config)
    config.setdefault("DEFAULT", {}).setdefault("tmdb_api", "test-key")
    values = config.setdefault("TRACKERS", {}).setdefault("UTOPIA", {})
    values.setdefault("api_key", "test-key")
    values.setdefault("announce_url", "https://tracker.invalid/announce")
    return Utopia(config)


def test_utopia_mapping_edges() -> None:
    tracker = _tracker()
    assert asyncio.run(tracker.get_category_id(Meta(category="TV"))) == {
        "category_id": "2"
    }
    assert asyncio.run(tracker.get_category_id(Meta(category="OTHER"))) == {
        "category_id": "1"
    }
    assert asyncio.run(
        tracker.get_resolution_id(Meta(resolution="2160p"))
    ) == {"resolution_id": "2"}
    assert asyncio.run(tracker.get_resolution_id(Meta(resolution="720p"))) == {
        "resolution_id": "11"
    }
    assert asyncio.run(tracker.get_type_id(Meta(type="REMUX"))) == {
        "type_id": "2"
    }
    assert asyncio.run(tracker.get_type_id(Meta(type="OTHER"))) == {
        "type_id": "3"
    }


def test_utopia_webdl_name_components() -> None:
    tracker = _tracker()
    meta = Meta(
        category="MOVIE",
        type="WEBDL",
        title="Movie",
        aka="",
        year=2026,
        service="AMZN",
        resolution="1080p",
        video_encode="H.264",
        video_codec="AVC",
        audio="AAC 2.0",
        tag="-GROUP",
    )

    name = asyncio.run(tracker.get_name(meta))["name"]

    assert "AMZN WEB-DL 1080p" in name
    assert "H.264" in name
    assert "AAC" not in name
    assert name.endswith("-GROUP")
