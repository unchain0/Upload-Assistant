from __future__ import annotations

import asyncio

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.NEXUSPHP.oneptba import OnePTBA


def _tracker(config: dict | None = None) -> OnePTBA:
    return OnePTBA(
        config or {"DEFAULT": {"tmdb_api": "dummy"}, "TRACKERS": {}}
    )


def test_oneptba_animation_category_and_standard_bdmv() -> None:
    tracker = _tracker()
    assert (
        tracker.get_category(Meta(category="MOVIE", genres=["Animation"]))
        == 405
    )
    assert (
        tracker.get_type(
            Meta(is_disc="BDMV", resolution="1080p", diy_disc=False)
        )
        == 1
    )


def test_oneptba_type_and_default_codec_resolution_audio() -> None:
    tracker = _tracker()
    assert tracker.get_type(Meta(type="REMUX")) == 3
    assert tracker.get_codec(Meta(video_codec="UNKNOWN")) == 5
    assert tracker.get_resolution(Meta(resolution="576p", sd=0)) == 1
    assert tracker.get_audio_codec(Meta(audio="UNKNOWN")) == 7


@pytest.mark.parametrize(
    ("release_type", "expected"),
    (
        ("REMUX", 20),
        ("HDTV", 4),
        ("ENCODE", 22),
        ("OTHER", 6),
    ),
)
def test_oneptba_region_fallbacks(release_type: str, expected: int) -> None:
    assert _tracker().get_region(Meta(type=release_type)) == expected


@pytest.mark.parametrize(
    ("meta", "expected"),
    (
        (Meta(is_disc="BDMV"), 1),
        (Meta(type="REMUX"), 1),
        (Meta(type="WEBDL"), 2),
    ),
)
def test_oneptba_container_mappings(meta: Meta, expected: int) -> None:
    assert _tracker().get_container(meta) == expected


def test_oneptba_group_tag_known_and_fallback() -> None:
    tracker = _tracker()
    assert tracker.get_group_tag(Meta(tag="-CHD")) == 2
    assert tracker.get_group_tag(Meta(tag="-UNKNOWN")) == 5
    assert tracker.get_group_tag(Meta(tag="")) == 5


def test_oneptba_anonymous_data_branches() -> None:
    tracker = _tracker()
    assert asyncio.run(tracker.get_anonymous_data(Meta(anon=0))) == {}
    assert asyncio.run(tracker.get_anonymous_data(Meta(anon=1))) == {
        "anonymous": "1"
    }

    configured = _tracker(
        {
            "DEFAULT": {"tmdb_api": "dummy"},
            "TRACKERS": {"1PTBA": {"anon": True}},
        }
    )
    assert asyncio.run(configured.get_anonymous_data(Meta(anon=0))) == {
        "anonymous": "1"
    }
