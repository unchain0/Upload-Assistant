from __future__ import annotations

import asyncio

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.homiehelpdesk import HomieHelpDesk


def _tracker() -> HomieHelpDesk:
    return HomieHelpDesk({"DEFAULT": {}, "TRACKERS": {"HOMIEHELPDESK": {}}})


def test_homiehelpdesk_music_helper_fallback_edges() -> None:
    tracker = _tracker()
    assert (
        tracker._discogs_reference(
            Meta(category="MUSIC", music_discogs_enabled=False)
        )
        == ""
    )
    assert (
        asyncio.run(tracker.get_additional_data(Meta(category="MOVIE"))) == {}
    )


def test_homiehelpdesk_additional_checks_edges() -> None:
    tracker = _tracker()
    assert not asyncio.run(
        tracker.get_additional_checks(Meta(category="MOVIE", type="DVDRIP"))
    )
    assert asyncio.run(
        tracker.get_additional_checks(Meta(category="MOVIE", type="REMUX"))
    )


def test_homiehelpdesk_category_mapping_edges() -> None:
    tracker = _tracker()
    audiobook = Meta(category="BOOK", audiobook=True)
    assert tracker._book_category(audiobook) == "AUDIOBOOK"
    assert asyncio.run(tracker.get_category_id(audiobook)) == {
        "category_id": "8"
    }

    mapping = asyncio.run(tracker.get_category_id(Meta(), mapping_only=True))
    assert mapping["MOVIE"] == "1"
    reverse = asyncio.run(tracker.get_category_id(Meta(), reverse=True))
    assert reverse["8"] == "AUDIOBOOK"
    assert asyncio.run(
        tracker.get_category_id(Meta(category="MOVIE"), category="TV")
    ) == {"category_id": "2"}


def test_homiehelpdesk_type_mapping_edges() -> None:
    tracker = _tracker()
    console = Meta(category="GAME", console_game=True, platform="")
    pc = Meta(category="GAME", console_game=False, platform="PC")
    assert tracker._game_type(console) == "CONSOLE"
    assert tracker._game_type(pc) == "PC"
    assert asyncio.run(tracker.get_type_id(console)) == {"type_id": "28"}
    assert asyncio.run(tracker.get_type_id(pc)) == {"type_id": "25"}

    generic = Meta(category="MOVIE", type="REMUX")
    assert (
        tracker._resolved_type(generic, "", tracker._type_mapping()) == "REMUX"
    )
    book = Meta(category="BOOK", type="UNKNOWN")
    assert tracker._resolved_type(book, "", tracker._type_mapping()) == "OTHER"
    mapping = asyncio.run(tracker.get_type_id(generic, mapping_only=True))
    assert mapping["REMUX"] == "2"
    reverse = asyncio.run(tracker.get_type_id(generic, reverse=True))
    assert reverse["2"] == "REMUX"
    assert asyncio.run(tracker.get_type_id(generic, type="ENCODE")) == {
        "type_id": "3"
    }


def test_homiehelpdesk_resolution_mapping_edges() -> None:
    tracker = _tracker()
    meta = Meta(resolution="2160p")
    mapping = asyncio.run(tracker.get_resolution_id(meta, mapping_only=True))
    assert mapping["2160p"] == "2"
    reverse = asyncio.run(tracker.get_resolution_id(meta, reverse=True))
    assert reverse["2"] == "2160p"
    assert asyncio.run(tracker.get_resolution_id(meta)) == {
        "resolution_id": "2"
    }
    assert asyncio.run(tracker.get_resolution_id(meta, resolution="720p")) == {
        "resolution_id": "5"
    }
    assert asyncio.run(
        tracker.get_resolution_id(meta, resolution="unknown")
    ) == {"resolution_id": "10"}
