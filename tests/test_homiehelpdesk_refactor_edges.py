from __future__ import annotations

import asyncio

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.homiehelpdesk import HomieHelpDesk


def _tracker() -> HomieHelpDesk:
    return HomieHelpDesk({"DEFAULT": {}, "TRACKERS": {"HOMIEHELPDESK": {}}})


def test_homiehelpdesk_additional_data_non_music_and_discogs_disabled() -> (
    None
):
    tracker = _tracker()
    assert (
        asyncio.run(tracker.get_additional_data(Meta(category="MOVIE"))) == {}
    )
    assert (
        tracker._discogs_reference(
            Meta(category="MUSIC", music_discogs_enabled=False)
        )
        == ""
    )


@pytest.mark.parametrize(
    ("meta", "expected"),
    (
        (Meta(category="BOOK"), "BOOKS"),
        (Meta(category="BOOK", audiobook=True), "AUDIOBOOK"),
        (Meta(category="BOOK", comic=True), "COMICS"),
        (Meta(category="BOOK", manga=True), "MANGA"),
        (Meta(category="BOOK", magazine=True), "MAGAZINE"),
    ),
)
def test_homiehelpdesk_book_category_variants(
    meta: Meta, expected: str
) -> None:
    assert _tracker()._book_category(meta) == expected


def test_homiehelpdesk_category_mapping_modes_and_explicit_category() -> None:
    tracker = _tracker()
    meta = Meta(category="MOVIE")
    mapping = asyncio.run(tracker.get_category_id(meta, mapping_only=True))
    assert mapping["MOVIE"] == "1"
    reverse = asyncio.run(tracker.get_category_id(meta, reverse=True))
    assert reverse["1"] == "MOVIE"
    assert asyncio.run(tracker.get_category_id(meta, category="TV")) == {
        "category_id": "2"
    }


def test_homiehelpdesk_game_and_standard_type_resolution() -> None:
    tracker = _tracker()
    assert (
        tracker._game_type(Meta(category="GAME", console_game=True))
        == "CONSOLE"
    )
    assert (
        tracker._game_type(
            Meta(category="GAME", console_game=False, platform="windows")
        )
        == "WINDOWS"
    )

    mapping = asyncio.run(tracker.get_type_id(Meta(), mapping_only=True))
    assert mapping["REMUX"] == "2"
    reverse = asyncio.run(tracker.get_type_id(Meta(), reverse=True))
    assert reverse["2"] == "REMUX"
    assert asyncio.run(
        tracker.get_type_id(Meta(category="GAME", console_game=True))
    ) == {"type_id": "28"}
    assert asyncio.run(
        tracker.get_type_id(Meta(category="MOVIE"), type="REMUX")
    ) == {"type_id": "2"}


def test_homiehelpdesk_resolution_mapping_modes_and_fallbacks() -> None:
    tracker = _tracker()
    meta = Meta(resolution="1080p")
    mapping = asyncio.run(tracker.get_resolution_id(meta, mapping_only=True))
    assert mapping["2160p"] == "2"
    reverse = asyncio.run(tracker.get_resolution_id(meta, reverse=True))
    assert reverse["2"] == "2160p"
    assert asyncio.run(tracker.get_resolution_id(meta)) == {
        "resolution_id": "3"
    }
    assert asyncio.run(tracker.get_resolution_id(meta, resolution="480i")) == {
        "resolution_id": "9"
    }
    assert asyncio.run(
        tracker.get_resolution_id(meta, resolution="unknown")
    ) == {"resolution_id": "10"}
