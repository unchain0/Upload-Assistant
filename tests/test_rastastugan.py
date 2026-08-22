"""Regression tests for Rastastugan MUSIC type mappings."""

import asyncio
from unittest.mock import ANY, AsyncMock

import pytest

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


def test_rastastugan_refactor_helper_branches() -> None:
    tracker = _tracker()
    mapping = tracker._type_mapping()

    assert tracker._category_mapping()["GAME"] == "5"
    assert tracker._selected_category(Meta(category="TV"), "GAME") == "GAME"
    assert (
        tracker._selected_category(Meta(category="BOOK", audiobook=True), "")
        == "AUDIOBOOK"
    )
    assert (
        asyncio.run(
            tracker.get_category_id(Meta(category="TV"), mapping_only=True)
        )["TV"]
        == "2"
    )
    assert (
        asyncio.run(
            tracker.get_category_id(Meta(category="TV"), reverse=True)
        )["2"]
        == "TV"
    )

    assert tracker._named_game_platform_type_id("macos") == "9"
    assert tracker._named_game_platform_type_id("linux") == "18"
    assert tracker._named_game_platform_type_id("other") is None
    assert tracker._game_platform_type_id(Meta(platform="windows")) == "10"
    assert (
        tracker._game_platform_type_id(
            Meta(platform="other", console_game=True)
        )
        == "11"
    )
    assert tracker._game_platform_type_id(Meta(platform="other")) is None
    assert (
        tracker._game_type_id(Meta(platform="other"), mapping, "WINDOWS")
        == "10"
    )
    assert (
        tracker._game_type_id(Meta(platform="other"), mapping, "bad") == "19"
    )

    assert (
        tracker._category_type_id(
            Meta(category="MUSIC", format="FLAC"), mapping, ""
        )
        == "7"
    )
    assert (
        tracker._category_type_id(
            Meta(category="GAME", platform="other"), mapping, "WINDOWS"
        )
        == "10"
    )
    assert (
        tracker._category_type_id(Meta(category="BOOK"), mapping, "unknown")
        == "19"
    )
    assert (
        tracker._category_type_id(Meta(category="MOVIE"), mapping, "REMUX")
        == "2"
    )


def test_rastastugan_additional_checks_delegate_nordic_languages() -> None:
    tracker = _tracker()
    check = AsyncMock(return_value=True)
    tracker.common.check_language_requirements = check

    assert asyncio.run(tracker.get_additional_checks(Meta())) is True
    check.assert_awaited_once_with(
        ANY,
        "RASTASTUGAN",
        languages_to_check=[
            "danish",
            "swedish",
            "norwegian",
            "icelandic",
            "finnish",
            "english",
        ],
        check_audio=True,
        check_subtitle=True,
    )


@pytest.mark.parametrize(
    ("meta", "expected"),
    (
        (Meta(category="GAME", platform="macOS", type="OTHER"), "9"),
        (Meta(category="GAME", platform="Linux", type="OTHER"), "18"),
        (Meta(category="GAME", platform="Windows PC", type="OTHER"), "10"),
        (Meta(category="GAME", console_game=True, type="OTHER"), "11"),
        (Meta(category="GAME", type="OTHER"), "19"),
    ),
)
def test_rastastugan_game_type_variants(meta: Meta, expected: str) -> None:
    assert asyncio.run(_tracker().get_type_id(meta)) == {"type_id": expected}
