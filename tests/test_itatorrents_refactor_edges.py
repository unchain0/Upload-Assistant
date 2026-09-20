from __future__ import annotations

import asyncio
import copy
from unittest.mock import AsyncMock

import pytest

from data.example_config import config as example_config
from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D import itatorrents as ita_module
from src.integrations.trackers.UNIT3D.itatorrents import ItaTorrents


def _config() -> dict:
    config = copy.deepcopy(example_config)
    config.setdefault("DEFAULT", {})["tmdb_api"] = (
        "0123456789abcdef0123456789abcdef"
    )
    values = config.setdefault("TRACKERS", {}).setdefault("ITATORRENTS", {})
    values.setdefault("api_key", "test-key")
    values.setdefault("announce_url", "https://tracker.invalid/announce")
    return config


def _tracker() -> ItaTorrents:
    return ItaTorrents(_config())


@pytest.mark.parametrize(
    ("basename", "expected"),
    (
        ("Movie.DLMux-GRP", "DLMux"),
        ("Movie.BDMux-GRP", "BDMux"),
        ("Movie.WEBMux-GRP", "WEBMux"),
        ("Movie.DVDMux-GRP", "DVDMux"),
        ("Movie.BDRip-GRP", "BDRip"),
    ),
)
def test_itatorrents_basename_type_markers(
    basename: str, expected: str
) -> None:
    tracker = _tracker()
    assert (
        asyncio.run(
            tracker.get_type_name(
                Meta(basename_no_ext=basename, type="ENCODE")
            )
        )
        == expected
    )


def test_itatorrents_type_name_and_id_fallback_edges() -> None:
    tracker = _tracker()
    assert tracker._basename_type_name("Movie.Regular-GRP") is None
    assert asyncio.run(tracker.get_type_name(Meta(type="REMUX"))) == "REMUX"
    assert asyncio.run(tracker.get_type_name(Meta(type=None))) is None

    mapping = asyncio.run(
        tracker.get_type_id(Meta(type="REMUX"), mapping_only=True)
    )
    assert mapping["DLMux"] == "27"
    assert mapping["Cinema-MD"] == "14"

    reverse = asyncio.run(tracker.get_type_id(Meta(), reverse=True))
    assert reverse["27"] == "DLMux"
    assert reverse["24"] == "DVDRIP"

    assert asyncio.run(tracker.get_type_id(Meta(), type="WEBMux")) == {
        "type_id": "26"
    }
    assert asyncio.run(tracker.get_type_id(Meta(), type="UNKNOWN")) == {
        "type_id": "0"
    }
    assert asyncio.run(
        tracker.get_type_id(
            Meta(basename_no_ext="Movie.DVDMux", type="ENCODE")
        )
    ) == {"type_id": "39"}


def test_itatorrents_year_episode_and_disc_name_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker, "get_dubs", AsyncMock(return_value="ITA"))

    assert tracker._display_year(Meta(no_year=True, year=2025)) == ""
    assert (
        tracker._display_year(
            Meta(category="TV", year=None, search_year="2025")
        )
        == ""
    )
    assert (
        tracker._display_year(Meta(category="TV", year=2025, search_year=""))
        == ""
    )
    assert (
        tracker._display_year(
            Meta(
                category="TV", year=2025, search_year="2025", manual_year=2024
            )
        )
        == "2024"
    )
    assert tracker._base_year(Meta(year=None, manual_year=None)) == ""
    assert tracker._base_year(Meta(year=2025, manual_year=2024)) == "2024"
    assert tracker._episode_tokens(
        Meta(season="S01", episode="E02", no_season=True)
    ) == ("", "")
    assert tracker._episode_tokens(Meta(season="S01", episode="E02")) == (
        "S01",
        "E02",
    )

    disc = Meta(
        category="MOVIE",
        title="Example",
        year=2025,
        type="DISC",
        source="BluRay",
        resolution="OTHER",
        video_codec="HEVC",
        audio="DTS-HD MA",
        region="A",
        tag="-GROUP",
    )
    disc_name = asyncio.run(tracker.get_name(disc))["name"]
    assert "OTHER" not in disc_name
    assert "REMUX" not in disc_name
    assert disc_name.endswith("-GROUP")

    remux = Meta(
        category="MOVIE",
        title="Example",
        year=2025,
        type="REMUX",
        source="BluRay",
        resolution="2160p",
        video_codec="HEVC",
        audio="TrueHD Atmos",
    )
    assert "REMUX" in asyncio.run(tracker.get_name(remux))["name"]


def test_itatorrents_display_type_and_clean_name() -> None:
    assert ItaTorrents._display_type("WEBDL") == "WEB-DL"
    assert ItaTorrents._display_type("WEBRIP") == "WEBRip"
    assert ItaTorrents._display_type("DVDRIP") == "DVDRip"
    assert ItaTorrents._display_type("ENCODE") == "BluRay"
    assert (
        ItaTorrents._clean_name("Example  Dubbed Dual-Audio 1080p", "-GROUP")
        == "Example 1080p-GROUP"
    )


@pytest.mark.asyncio
async def test_itatorrents_dubs_language_processing_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    process = AsyncMock()
    monkeypatch.setattr(
        ita_module.languages_manager, "process_desc_language", process
    )

    unchecked = Meta(
        language_checked=False, audio_languages=["Italian", "English"]
    )
    label = await tracker.get_dubs(unchecked)
    process.assert_awaited_once_with(unchecked, tracker=tracker.tracker)
    assert set(label.split()) == {"ITA", "ENG"}

    process.reset_mock()
    checked = Meta(language_checked=True, audio_languages="Italian")
    assert await tracker.get_dubs(checked) == ""
    process.assert_not_awaited()
