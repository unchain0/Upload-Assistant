"""Regression tests for Rastastugan MUSIC type mappings."""

import asyncio

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.rastastugan import Rastastugan


def _tracker() -> Rastastugan:
    return Rastastugan(
        {"DEFAULT": {"tmdb_api": "test-key"}, "TRACKERS": {"RASTASTUGAN": {}}}
    )


def test_rastastugan_music_types_use_music_format():
    tracker = _tracker()

    assert asyncio.run(
        tracker.get_type_id(Meta(category="MUSIC", format="FLAC"))
    ) == {"type_id": "7"}
    assert asyncio.run(
        tracker.get_type_id(Meta(category="MUSIC", format="MP3"))
    ) == {"type_id": "8"}
    assert asyncio.run(
        tracker.get_type_id(Meta(category="MUSIC", format="M4A"))
    ) == {"type_id": "14"}
    assert asyncio.run(
        tracker.get_type_id(Meta(category="MUSIC", format="M4B"))
    ) == {"type_id": "20"}


def test_rastastugan_unknown_music_format_uses_other():
    assert asyncio.run(
        _tracker().get_type_id(Meta(category="MUSIC", format="OPUS"))
    ) == {"type_id": "19"}


def test_rastastugan_category_mapping_edges() -> None:
    tracker = _tracker()
    mapping = asyncio.run(
        tracker.get_category_id(Meta(category="MOVIE"), mapping_only=True)
    )
    assert mapping["AUDIOBOOK"] == "7"
    assert (
        asyncio.run(
            tracker.get_category_id(Meta(category="MOVIE"), reverse=True)
        )["7"]
        == "AUDIOBOOK"
    )
    assert asyncio.run(
        tracker.get_category_id(Meta(category="MOVIE"), category="GAME")
    ) == {"category_id": "5"}
    assert asyncio.run(
        tracker.get_category_id(Meta(category="BOOK", audiobook=True))
    ) == {"category_id": "7"}
    assert asyncio.run(tracker.get_category_id(Meta(category="TV"))) == {
        "category_id": "2"
    }
    assert asyncio.run(tracker.get_category_id(Meta(category="UNKNOWN"))) == {
        "category_id": "0"
    }


def test_rastastugan_type_mapping_edges() -> None:
    tracker = _tracker()
    mapping = asyncio.run(
        tracker.get_type_id(Meta(category="MOVIE"), mapping_only=True)
    )
    assert mapping["M4B"] == "20"
    assert (
        asyncio.run(tracker.get_type_id(Meta(category="MOVIE"), reverse=True))[
            "20"
        ]
        == "M4B"
    )
    assert asyncio.run(
        tracker.get_type_id(Meta(category="BOOK"), type=".epub")
    ) == {"type_id": "15"}
    assert asyncio.run(
        tracker.get_type_id(Meta(category="MOVIE", type="REMUX"))
    ) == {"type_id": "2"}


def test_rastastugan_category_mapping_modes_and_selection() -> None:
    tracker = _tracker()
    mapping = asyncio.run(
        tracker.get_category_id(Meta(category="MOVIE"), mapping_only=True)
    )
    assert mapping["AUDIOBOOK"] == "7"
    reverse = asyncio.run(
        tracker.get_category_id(Meta(category="MOVIE"), reverse=True)
    )
    assert reverse["1"] == "MOVIE"
    assert asyncio.run(
        tracker.get_category_id(Meta(category="MOVIE"), category="GAME")
    ) == {"category_id": "5"}
    assert asyncio.run(
        tracker.get_category_id(Meta(category="BOOK", audiobook=True))
    ) == {"category_id": "7"}
    assert asyncio.run(tracker.get_category_id(Meta(category="TV"))) == {
        "category_id": "2"
    }


def test_rastastugan_type_mapping_modes_and_regular_fallback() -> None:
    tracker = _tracker()
    mapping = asyncio.run(
        tracker.get_type_id(Meta(category="MOVIE"), mapping_only=True)
    )
    assert mapping["REMUX"] == "2"
    reverse = asyncio.run(
        tracker.get_type_id(Meta(category="MOVIE"), reverse=True)
    )
    assert reverse["20"] == "M4B"
    assert asyncio.run(
        tracker.get_type_id(Meta(category="MOVIE"), type=".webrip")
    ) == {"type_id": "5"}
    assert asyncio.run(
        tracker.get_type_id(Meta(category="MOVIE"), type="UNKNOWN")
    ) == {"type_id": "0"}
    assert asyncio.run(
        tracker.get_type_id(Meta(category="MOVIE", type="REMUX"))
    ) == {"type_id": "2"}
