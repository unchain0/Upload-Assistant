from __future__ import annotations

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.NEXUSPHP.lemonhd import LemonHD


def _tracker() -> LemonHD:
    return LemonHD({"DEFAULT": {"tmdb_api": "dummy"}, "TRACKERS": {}})


def test_lemonhd_animation_category() -> None:
    assert (
        _tracker().get_category(Meta(category="MOVIE", genres=["Animation"]))
        == 403
    )


@pytest.mark.parametrize(
    ("meta", "expected"),
    (
        (Meta(is_disc="BDMV", resolution="1080p"), 1),
        (Meta(is_disc="BDMV", resolution="2160p"), 6),
        (Meta(is_disc="DVD"), 8),
        (Meta(type="REMUX"), 3),
        (Meta(type="WEBDL"), 4),
        (Meta(type="WEBRIP"), 4),
        (Meta(type="HDTV"), 5),
        (Meta(type="ENCODE"), 2),
        (Meta(type="OTHER"), 2),
    ),
)
def test_lemonhd_type_mappings(meta: Meta, expected: int) -> None:
    assert _tracker().get_type(meta) == expected


def test_lemonhd_default_codec_resolution_and_audio() -> None:
    tracker = _tracker()
    assert tracker.get_codec(Meta(video_codec="UNKNOWN")) == 5
    assert tracker.get_resolution(Meta(resolution="576p", sd=0)) == 5
    assert tracker.get_audio_codec(Meta(audio="UNKNOWN")) == 13
