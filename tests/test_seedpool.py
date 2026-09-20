"""Regression tests for Seedpool category and type mappings."""

import asyncio

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.seedpool import Seedpool


def _tracker() -> Seedpool:
    return Seedpool({"DEFAULT": {}, "TRACKERS": {"SEEDPOOL": {}}})


def test_seedpool_supports_music_game_and_book_categories():
    tracker = _tracker()

    assert {"MUSIC", "GAME", "BOOK"}.issubset(tracker.supported_categories)
    assert asyncio.run(tracker.get_category_id(Meta(category="MUSIC"))) == {
        "category_id": "5"
    }
    assert asyncio.run(tracker.get_category_id(Meta(category="GAME"))) == {
        "category_id": "3"
    }
    assert asyncio.run(tracker.get_category_id(Meta(category="BOOK"))) == {
        "category_id": "7"
    }
    assert asyncio.run(
        tracker.get_category_id(Meta(category="BOOK", audiobook=True))
    ) == {"category_id": "9"}
    assert asyncio.run(
        tracker.get_category_id(Meta(category="GAME", name="FIFA 26"))
    ) == {"category_id": "3"}


def test_seedpool_maps_music_book_and_game_types_to_current_site_ids():
    tracker = _tracker()

    assert asyncio.run(
        tracker.get_type_id(Meta(category="MUSIC", format="FLAC"))
    ) == {"type_id": "11"}
    assert asyncio.run(
        tracker.get_type_id(Meta(category="BOOK", comic=True, type="CBZ"))
    ) == {"type_id": "40"}
    assert asyncio.run(
        tracker.get_type_id(
            Meta(category="BOOK", audiobook=True, format="MP3")
        )
    ) == {"type_id": "13"}
    assert asyncio.run(
        tracker.get_type_id(Meta(category="GAME", platform="Nintendo Switch"))
    ) == {"type_id": "15"}


def test_seedpool_routes_sports_titles_to_sports_category():
    assert asyncio.run(
        _tracker().get_category_id(
            Meta(category="TV", name="NFL GameNight 2026")
        )
    ) == {"category_id": "8"}


def test_seedpool_maps_remaining_game_platforms():
    tracker = _tracker()
    cases = (
        ("Xbox 360", "53"),
        ("Xbox One", "54"),
        ("Xbox Series X", "35"),
        ("PlayStation 4", "28"),
        ("PlayStation 3", "52"),
        ("PlayStation 2", "51"),
        ("PlayStation", "50"),
        ("Wii", "44"),
        ("NES", "45"),
    )
    for platform, expected in cases:
        assert asyncio.run(
            tracker.get_type_id(Meta(category="GAME", platform=platform))
        ) == {"type_id": expected}


def test_seedpool_removes_known_video_extension_from_generated_name():
    meta = Meta(
        scene=False,
        is_disc=False,
        name="Ignored Name",
        basename_no_ext="Scene File.mkv",
        mal_id=0,
    )
    assert asyncio.run(_tracker().get_name(meta)) == {"name": "Scene.File"}


def test_seedpool_low_resolution_prompt_paths(monkeypatch):
    tracker = _tracker()
    meta = Meta(
        category="MOVIE",
        resolution="720p",
        unattended=False,
        unattended_confirm=False,
        keywords=[],
        combined_genres=[],
    )

    monkeypatch.setattr(
        "src.integrations.trackers.UNIT3D.seedpool.cli_ui.ask_yes_no",
        lambda *_args, **_kwargs: True,
    )
    assert asyncio.run(tracker.get_additional_checks(meta)) is True

    monkeypatch.setattr(
        "src.integrations.trackers.UNIT3D.seedpool.cli_ui.ask_yes_no",
        lambda *_args, **_kwargs: False,
    )
    assert asyncio.run(tracker.get_additional_checks(meta)) is False

    unattended = Meta(
        category="MOVIE",
        resolution="720p",
        unattended=True,
        unattended_confirm=False,
        keywords=[],
        combined_genres=[],
    )
    assert asyncio.run(tracker.get_additional_checks(unattended)) is False


def test_seedpool_adult_keyword_prompt_paths(monkeypatch):
    tracker = _tracker()
    meta = Meta(
        category="MOVIE",
        resolution="1080p",
        unattended=False,
        unattended_confirm=False,
        keywords=["porn"],
        combined_genres=[],
    )

    monkeypatch.setattr(
        "src.integrations.trackers.UNIT3D.seedpool.cli_ui.ask_yes_no",
        lambda *_args, **_kwargs: True,
    )
    assert asyncio.run(tracker.get_additional_checks(meta)) is True

    monkeypatch.setattr(
        "src.integrations.trackers.UNIT3D.seedpool.cli_ui.ask_yes_no",
        lambda *_args, **_kwargs: False,
    )
    assert asyncio.run(tracker.get_additional_checks(meta)) is False

    unattended = Meta(
        category="MOVIE",
        resolution="1080p",
        unattended=True,
        unattended_confirm=False,
        keywords=["porn"],
        combined_genres=[],
    )
    assert asyncio.run(tracker.get_additional_checks(unattended)) is False
