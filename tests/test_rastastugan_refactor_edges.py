from __future__ import annotations

import asyncio

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.rastastugan import Rastastugan


def _tracker() -> Rastastugan:
    return Rastastugan(
        {
            "DEFAULT": {"tmdb_api": "test-key"},
            "TRACKERS": {"RASTASTUGAN": {}},
        }
    )


def test_rastastugan_category_mapping_modes() -> None:
    tracker = _tracker()
    mapping = asyncio.run(tracker.get_category_id(Meta(), mapping_only=True))
    assert mapping["MOVIE"] == "1"
    assert mapping["AUDIOBOOK"] == "7"

    reverse = asyncio.run(tracker.get_category_id(Meta(), reverse=True))
    assert reverse["1"] == "MOVIE"
    assert reverse["7"] == "AUDIOBOOK"

    assert asyncio.run(
        tracker.get_category_id(Meta(category="MOVIE"), category="TV")
    ) == {"category_id": "2"}
    assert asyncio.run(
        tracker.get_category_id(Meta(category="BOOK", audiobook=True))
    ) == {"category_id": "7"}
    assert asyncio.run(tracker.get_category_id(Meta(category="UNKNOWN"))) == {
        "category_id": "0"
    }


@pytest.mark.parametrize(
    ("platform", "console_game", "release_type", "expected"),
    (
        ("macOS", False, "OTHER", "9"),
        ("Linux", False, "OTHER", "18"),
        ("Windows PC", False, "OTHER", "10"),
        ("", True, "OTHER", "11"),
        ("", False, "OTHER", "19"),
    ),
)
def test_rastastugan_game_type_variants(
    platform: str,
    console_game: bool,
    release_type: str,
    expected: str,
) -> None:
    meta = Meta(
        category="GAME",
        platform=platform,
        console_game=console_game,
        type=release_type,
    )
    assert asyncio.run(_tracker().get_type_id(meta)) == {"type_id": expected}


def test_rastastugan_type_mapping_modes_and_fallbacks() -> None:
    tracker = _tracker()
    mapping = asyncio.run(tracker.get_type_id(Meta(), mapping_only=True))
    assert mapping["REMUX"] == "2"
    assert mapping["M4B"] == "20"

    reverse = asyncio.run(tracker.get_type_id(Meta(), reverse=True))
    assert reverse["2"] == "REMUX"
    assert reverse["20"] == "M4B"

    assert asyncio.run(tracker.get_type_id(Meta(), type=" .webrip ")) == {
        "type_id": "5"
    }
    assert asyncio.run(tracker.get_type_id(Meta(), type="unknown")) == {
        "type_id": "0"
    }
    assert asyncio.run(
        tracker.get_type_id(Meta(category="BOOK", type="EPUB"))
    ) == {"type_id": "15"}
    assert asyncio.run(
        tracker.get_type_id(Meta(category="BOOK", type="UNKNOWN"))
    ) == {"type_id": "19"}
    assert asyncio.run(
        tracker.get_type_id(Meta(category="AUDIOBOOK", type="M4B"))
    ) == {"type_id": "20"}
    assert asyncio.run(
        tracker.get_type_id(Meta(category="MOVIE", type="REMUX"))
    ) == {"type_id": "2"}
