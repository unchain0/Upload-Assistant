"""Regression tests for TorrentHR's UNIT3D mappings."""

import asyncio

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.registry import tracker_class_map
from src.integrations.trackers.UNIT3D.torrenthr import TorrentHR


def _tracker() -> TorrentHR:
    return TorrentHR({"DEFAULT": {}, "TRACKERS": {"TORRENTHR": {}}})


@pytest.mark.parametrize(
    ("meta", "expected"),
    [
        (Meta(category="MOVIE", sd=1), "4"),
        (Meta(category="MOVIE", is_disc="DVD"), "14"),
        (Meta(category="MOVIE", is_disc="BDMV"), "40"),
        (Meta(category="TV", sd=1), "7"),
        (Meta(category="TV"), "34"),
        (Meta(category="TV", anime=True), "31"),
        (Meta(category="MOVIE", combined_genres="Animation"), "18"),
        (Meta(category="MOVIE", combined_genres="Documentary"), "12"),
    ],
)
def test_torrenthr_category_mappings(meta: Meta, expected: str) -> None:
    assert asyncio.run(_tracker().get_category_id(meta)) == {
        "category_id": expected
    }


def test_torrenthr_mapping_modes_and_category_edges() -> None:
    tracker = _tracker()
    mapping = asyncio.run(
        tracker.get_category_id(Meta(category="MOVIE"), mapping_only=True)
    )
    assert mapping["MOVIE_SD"] == "4"

    reverse = asyncio.run(
        tracker.get_category_id(Meta(category="MOVIE"), reverse=True)
    )
    assert reverse["4"] == "MOVIE_SD"

    assert asyncio.run(
        tracker.get_category_id(Meta(category="OTHER"), category="TV")
    ) == {"category_id": "34"}
    assert asyncio.run(tracker.get_category_id(Meta(category="OTHER"))) == {
        "category_id": "0"
    }


def test_torrenthr_is_registered() -> None:
    assert tracker_class_map["TORRENTHR"] is TorrentHR
