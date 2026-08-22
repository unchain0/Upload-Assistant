import asyncio

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.homiehelpdesk import HomieHelpDesk


def _tracker() -> HomieHelpDesk:
    return HomieHelpDesk({"DEFAULT": {}, "TRACKERS": {"HOMIEHELPDESK": {}}})


def test_homiehelpdesk_music_upload_uses_musicbrainz_release_id():
    meta = Meta(
        category="MUSIC",
        music_release={
            "external_ids": {
                "musicbrainz_release": "c0d17e85-3a36-4dc8-9a88-c188a5e78b0d"
            }
        },
    )

    assert asyncio.run(_tracker().get_additional_data(meta)) == {
        "music_exists_on_musicbrainz": "1",
        "musicbrainz": "c0d17e85-3a36-4dc8-9a88-c188a5e78b0d",
    }


def test_homiehelpdesk_music_upload_uses_discogs_url_when_musicbrainz_is_unavailable():
    meta = Meta(
        category="MUSIC",
        music_release={
            "external_ids": {
                "discogs_master_url": "https://www.discogs.com/master/28700-Example"
            }
        },
    )

    assert asyncio.run(_tracker().get_additional_data(meta)) == {
        "music_exists_on_discogs": "1",
        "discogs": "https://www.discogs.com/master/28700-Example",
    }


def test_homiehelpdesk_requires_a_music_external_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "src.integrations.trackers.UNIT3D.homiehelpdesk.logger.info",
        lambda *_args, **_kwargs: None,
    )
    meta = Meta(
        category="MUSIC",
        music_release={"external_ids": {"musicbrainz_release": "invalid"}},
    )

    assert asyncio.run(_tracker().get_additional_checks(meta)) is False


def test_homiehelpdesk_music_type_uses_analyzed_format():
    meta = Meta(
        category="MUSIC",
        music_release={"fields": {"format": {"value": "FLAC"}}},
    )

    assert asyncio.run(_tracker().get_type_id(meta)) == {"type_id": "7"}


def test_homiehelpdesk_refactor_music_and_category_edges() -> None:
    tracker = _tracker()
    disabled = Meta(category="MUSIC", music_discogs_enabled=False)
    assert tracker._discogs_reference(disabled) == ""
    assert (
        asyncio.run(tracker.get_additional_data(Meta(category="MOVIE"))) == {}
    )
    assert (
        tracker._book_category(Meta(category="BOOK", audiobook=True))
        == "AUDIOBOOK"
    )

    category_mapping = asyncio.run(
        tracker.get_category_id(Meta(category="MOVIE"), mapping_only=True)
    )
    assert category_mapping["AUDIOBOOK"] == "8"
    category_reverse = asyncio.run(
        tracker.get_category_id(Meta(category="MOVIE"), reverse=True)
    )
    assert category_reverse["1"] == "MOVIE"


def test_homiehelpdesk_refactor_type_edges() -> None:
    tracker = _tracker()
    game = Meta(category="GAME", platform="PC", console_game=True)
    assert tracker._game_type(game) == "CONSOLE"
    assert asyncio.run(tracker.get_type_id(game)) == {"type_id": "28"}
    assert asyncio.run(
        tracker.get_type_id(Meta(category="MOVIE", type="REMUX"))
    ) == {"type_id": "2"}

    type_mapping = asyncio.run(
        tracker.get_type_id(Meta(category="MOVIE"), mapping_only=True)
    )
    assert type_mapping["OTHER"] == "23"
    type_reverse = asyncio.run(
        tracker.get_type_id(Meta(category="MOVIE"), reverse=True)
    )
    assert type_reverse["28"] == "CONSOLE"


def test_homiehelpdesk_refactor_resolution_edges() -> None:
    tracker = _tracker()
    meta = Meta(category="MOVIE", resolution="1080p")
    mapping = asyncio.run(tracker.get_resolution_id(meta, mapping_only=True))
    assert mapping["2160p"] == "2"
    reverse = asyncio.run(tracker.get_resolution_id(meta, reverse=True))
    assert reverse["5"] == "720p"
    assert asyncio.run(tracker.get_resolution_id(meta)) == {
        "resolution_id": "3"
    }
    assert asyncio.run(
        tracker.get_resolution_id(meta, resolution="unknown")
    ) == {"resolution_id": "10"}


def test_homiehelpdesk_refactor_mapping_edges() -> None:
    tracker = _tracker()

    assert tracker._discogs_reference(Meta(music_discogs_enabled=False)) == ""
    assert (
        asyncio.run(tracker.get_additional_data(Meta(category="MOVIE"))) == {}
    )
    assert asyncio.run(
        tracker.get_category_id(Meta(category="BOOK", audiobook=True))
    ) == {"category_id": "8"}
    assert (
        asyncio.run(tracker.get_category_id(Meta(), mapping_only=True))[
            "MOVIE"
        ]
        == "1"
    )
    assert (
        asyncio.run(tracker.get_category_id(Meta(), reverse=True))["1"]
        == "MOVIE"
    )

    game = Meta(category="GAME", platform="PC", console_game=False)
    assert asyncio.run(tracker.get_type_id(game)) == {"type_id": "25"}
    assert asyncio.run(
        tracker.get_type_id(Meta(type="ENCODE", category="MOVIE"))
    ) == {"type_id": "3"}
    assert (
        asyncio.run(tracker.get_type_id(Meta(), mapping_only=True))["FLAC"]
        == "7"
    )
    assert (
        asyncio.run(tracker.get_type_id(Meta(), reverse=True))["7"] == "FLAC"
    )

    assert (
        asyncio.run(tracker.get_resolution_id(Meta(), mapping_only=True))[
            "2160p"
        ]
        == "2"
    )
    assert (
        asyncio.run(tracker.get_resolution_id(Meta(), reverse=True))["2"]
        == "2160p"
    )
    assert asyncio.run(
        tracker.get_resolution_id(Meta(resolution="1080i"))
    ) == {"resolution_id": "4"}
    assert asyncio.run(
        tracker.get_resolution_id(Meta(), resolution="unknown")
    ) == {"resolution_id": "10"}


def test_homiehelpdesk_non_music_and_discogs_disabled_edges() -> None:
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


def test_homiehelpdesk_category_and_type_mapping_edges() -> None:
    tracker = _tracker()
    audiobook = Meta(category="BOOK", audiobook=True)
    assert tracker._book_category(audiobook) == "AUDIOBOOK"
    category_mapping = asyncio.run(
        tracker.get_category_id(audiobook, mapping_only=True)
    )
    assert category_mapping["AUDIOBOOK"] == "8"
    assert asyncio.run(tracker.get_category_id(audiobook, reverse=True))[
        "8"
    ] == ("AUDIOBOOK")

    game = Meta(category="GAME", console_game=True, platform="PC")
    assert tracker._game_type(game) == "CONSOLE"
    type_mapping = asyncio.run(tracker.get_type_id(game, mapping_only=True))
    assert type_mapping["CONSOLE"] == "28"
    assert (
        asyncio.run(tracker.get_type_id(game, reverse=True))["28"] == "CONSOLE"
    )
    assert asyncio.run(tracker.get_type_id(game)) == {"type_id": "28"}

    book = Meta(category="BOOK", type="UNKNOWN")
    assert tracker._resolved_type(book, "", tracker._type_mapping()) == "OTHER"
    assert (
        tracker._resolved_type(
            Meta(category="MOVIE", type="REMUX"),
            "",
            tracker._type_mapping(),
        )
        == "REMUX"
    )


def test_homiehelpdesk_resolution_mapping_edges() -> None:
    tracker = _tracker()
    meta = Meta(resolution="2160p")
    mapping = asyncio.run(tracker.get_resolution_id(meta, mapping_only=True))
    assert mapping["2160p"] == "2"
    assert asyncio.run(tracker.get_resolution_id(meta, reverse=True))["2"] == (
        "2160p"
    )
    assert asyncio.run(tracker.get_resolution_id(meta)) == {
        "resolution_id": "2"
    }
    assert asyncio.run(
        tracker.get_resolution_id(meta, resolution="unknown")
    ) == {"resolution_id": "10"}
