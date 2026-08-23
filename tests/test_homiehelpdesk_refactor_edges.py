from __future__ import annotations

import asyncio

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.homiehelpdesk import HomieHelpDesk


def _tracker() -> HomieHelpDesk:
    return HomieHelpDesk({"DEFAULT": {}, "TRACKERS": {"HOMIEHELPDESK": {}}})


def test_non_music_and_discogs_disabled_edges() -> None:
    tracker = _tracker()
    disabled = Meta(category="MUSIC", music_discogs_enabled=False)

    assert tracker._discogs_reference(disabled) == ""
    assert asyncio.run(tracker.get_additional_data(disabled)) == {}
    assert (
        asyncio.run(tracker.get_additional_data(Meta(category="MOVIE"))) == {}
    )


def test_category_mapping_and_audiobook_edges() -> None:
    tracker = _tracker()
    audiobook = Meta(category="BOOK", audiobook=True)

    assert tracker._book_category(audiobook) == "AUDIOBOOK"
    mapping = asyncio.run(
        tracker.get_category_id(audiobook, mapping_only=True)
    )
    assert mapping["AUDIOBOOK"] == "8"
    reverse = asyncio.run(tracker.get_category_id(audiobook, reverse=True))
    assert reverse["8"] == "AUDIOBOOK"
    assert asyncio.run(tracker.get_category_id(audiobook)) == {
        "category_id": "8"
    }


def test_game_type_resolution() -> None:
    tracker = _tracker()
    game = Meta(category="GAME", console_game=True, platform="PC")
    mapping = tracker._type_mapping()

    assert tracker._game_type(game) == "CONSOLE"
    assert tracker._resolved_type(game, "", mapping) == "CONSOLE"
    assert asyncio.run(tracker.get_type_id(game)) == {"type_id": "28"}


def test_music_type_uses_release_field_value() -> None:
    tracker = _tracker()
    meta = Meta(
        category="MUSIC",
        format="MP3",
        music_release={"fields": {"format": {"value": "FLAC"}}},
    )

    mapping = tracker._type_mapping()
    assert tracker._resolved_type(meta, "", mapping) == "FLAC"
    assert asyncio.run(tracker.get_type_id(meta)) == {"type_id": "7"}


def test_type_mapping_forward_and_reverse() -> None:
    tracker = _tracker()
    game = Meta(category="GAME", console_game=True, platform="PC")

    forward = asyncio.run(tracker.get_type_id(game, mapping_only=True))
    reverse = asyncio.run(tracker.get_type_id(game, reverse=True))
    assert forward["CONSOLE"] == "28"
    assert reverse["28"] == "CONSOLE"


def test_book_and_movie_resolved_type_fallbacks() -> None:
    tracker = _tracker()
    mapping = tracker._type_mapping()

    assert (
        tracker._resolved_type(
            Meta(category="BOOK", type="UNKNOWN"), "", mapping
        )
        == "OTHER"
    )
    assert (
        tracker._resolved_type(
            Meta(category="MOVIE", type="REMUX"), "", mapping
        )
        == "REMUX"
    )


def test_resolution_mapping_reverse_and_default_edges() -> None:
    tracker = _tracker()
    meta = Meta(resolution="2160p")

    mapping = asyncio.run(tracker.get_resolution_id(meta, mapping_only=True))
    assert mapping["2160p"] == "2"
    reverse = asyncio.run(tracker.get_resolution_id(meta, reverse=True))
    assert reverse["2"] == "2160p"
    assert asyncio.run(tracker.get_resolution_id(meta)) == {
        "resolution_id": "2"
    }
    assert asyncio.run(
        tracker.get_resolution_id(meta, resolution="unknown")
    ) == {"resolution_id": "10"}
