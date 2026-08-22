from __future__ import annotations

import asyncio
import copy

from data.example_config import config as example_config
from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.rastastugan import Rastastugan


def _tracker() -> Rastastugan:
    config = copy.deepcopy(example_config)
    config.setdefault("DEFAULT", {})["tmdb_api"] = (
        "0123456789abcdef0123456789abcdef"
    )
    values = config.setdefault("TRACKERS", {}).setdefault("RASTASTUGAN", {})
    values.setdefault("api_key", "test-key")
    values.setdefault("announce_url", "https://tracker.invalid/announce")
    return Rastastugan(config)


def test_rastastugan_category_mapping_reverse_and_selection() -> None:
    tracker = _tracker()

    mapping = asyncio.run(tracker.get_category_id(Meta(), mapping_only=True))
    assert mapping["AUDIOBOOK"] == "7"
    reverse = asyncio.run(tracker.get_category_id(Meta(), reverse=True))
    assert reverse["7"] == "AUDIOBOOK"
    assert asyncio.run(
        tracker.get_category_id(Meta(category="MOVIE"), category="MUSIC")
    ) == {"category_id": "3"}
    assert asyncio.run(
        tracker.get_category_id(Meta(category="BOOK", audiobook=True))
    ) == {"category_id": "7"}
    assert asyncio.run(tracker.get_category_id(Meta(category="TV"))) == {
        "category_id": "2"
    }


def test_rastastugan_type_mapping_reverse_and_explicit_type() -> None:
    tracker = _tracker()

    mapping = asyncio.run(tracker.get_type_id(Meta(), mapping_only=True))
    assert mapping["M4B"] == "20"
    reverse = asyncio.run(tracker.get_type_id(Meta(), reverse=True))
    assert reverse["20"] == "M4B"
    assert asyncio.run(tracker.get_type_id(Meta(), type=".epub")) == {
        "type_id": "15"
    }
    assert asyncio.run(tracker.get_type_id(Meta(), type="missing")) == {
        "type_id": "0"
    }


def test_rastastugan_category_specific_type_resolution() -> None:
    tracker = _tracker()

    assert asyncio.run(
        tracker.get_type_id(Meta(category="MUSIC", format="FLAC"))
    ) == {"type_id": "7"}
    assert asyncio.run(
        tracker.get_type_id(
            Meta(category="GAME", type="MAC", platform="Unknown")
        )
    ) == {"type_id": "9"}
    assert asyncio.run(
        tracker.get_type_id(Meta(category="BOOK", type="UNKNOWN"))
    ) == {"type_id": "19"}
    assert asyncio.run(
        tracker.get_type_id(Meta(category="MOVIE", type="REMUX"))
    ) == {"type_id": "2"}
