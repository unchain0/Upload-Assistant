from __future__ import annotations

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.NEXUSPHP.ptfans import PTFans


def _tracker() -> PTFans:
    return PTFans({"DEFAULT": {"tmdb_api": "dummy"}, "TRACKERS": {}})


def test_ptfans_category_fallback_and_animation() -> None:
    tracker = _tracker()
    assert tracker.get_category(Meta(category="MOVIE")) == 401
    assert (
        tracker.get_category(Meta(category="MOVIE", genres=["Animation"]))
        == 414
    )


@pytest.mark.parametrize(
    ("meta", "expected"),
    (
        (Meta(is_disc="BDMV"), 6),
        (Meta(is_disc="DVD"), 2),
        (Meta(type="REMUX"), 3),
        (Meta(type="WEBDL"), 5),
        (Meta(type="WEBRIP"), 5),
        (Meta(type="ENCODE"), 8),
        (Meta(type="OTHER"), 9),
    ),
)
def test_ptfans_type_mappings(meta: Meta, expected: int) -> None:
    assert _tracker().get_type(meta) == expected


def test_ptfans_codec_default_family() -> None:
    tracker = _tracker()
    assert tracker._codec_family("unknown") == "other"
    assert tracker.get_codec(Meta(video_codec="unknown", source="bluray")) == 9


@pytest.mark.parametrize(
    ("resolution", "sd", "expected"),
    (
        ("1080p", 0, 1),
        ("1080i", 0, 2),
        ("720p", 0, 3),
        ("576p", 1, 4),
        ("2160p", 0, 5),
        ("4320p", 0, 6),
        ("576p", 0, 1),
    ),
)
def test_ptfans_resolution_mappings(
    resolution: str, sd: int, expected: int
) -> None:
    assert (
        _tracker().get_resolution(Meta(resolution=resolution, sd=sd))
        == expected
    )


def test_ptfans_group_tag_known_and_fallback() -> None:
    tracker = _tracker()
    assert tracker.get_group_tag(Meta(tag="-CHD")) == 2
    assert tracker.get_group_tag(Meta(tag="-UNKNOWN")) == 5
    assert tracker.get_group_tag(Meta(tag="")) == 5
