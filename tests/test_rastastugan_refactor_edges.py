from __future__ import annotations

import asyncio

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.rastastugan import Rastastugan


def _tracker() -> Rastastugan:
    return Rastastugan(
        {"DEFAULT": {"tmdb_api": "test-key"}, "TRACKERS": {"RASTASTUGAN": {}}}
    )


def test_rastastugan_category_mapping_modes() -> None:
    tracker = _tracker()
    audiobook = Meta(category="BOOK", audiobook=True)

    mapping = asyncio.run(
        tracker.get_category_id(audiobook, mapping_only=True)
    )
    assert mapping["AUDIOBOOK"] == "7"
    reverse = asyncio.run(tracker.get_category_id(audiobook, reverse=True))
    assert reverse["7"] == "AUDIOBOOK"
    assert asyncio.run(tracker.get_category_id(audiobook)) == {
        "category_id": "7"
    }
    assert asyncio.run(
        tracker.get_category_id(audiobook, category="MOVIE")
    ) == {"category_id": "1"}
    assert asyncio.run(tracker.get_category_id(Meta(category="UNKNOWN"))) == {
        "category_id": "0"
    }


def test_rastastugan_type_mapping_modes_and_fallbacks() -> None:
    tracker = _tracker()
    movie = Meta(category="MOVIE", type="REMUX")

    mapping = asyncio.run(tracker.get_type_id(movie, mapping_only=True))
    assert mapping["REMUX"] == "2"
    reverse = asyncio.run(tracker.get_type_id(movie, reverse=True))
    assert reverse["2"] == "REMUX"
    assert asyncio.run(tracker.get_type_id(movie, type=".webdl")) == {
        "type_id": "4"
    }
    assert asyncio.run(tracker.get_type_id(movie)) == {"type_id": "2"}
    assert asyncio.run(
        tracker.get_type_id(Meta(category="MOVIE", type="UNKNOWN"))
    ) == {"type_id": "0"}
    assert asyncio.run(
        tracker.get_type_id(Meta(category="BOOK", type="UNKNOWN"))
    ) == {"type_id": "19"}
