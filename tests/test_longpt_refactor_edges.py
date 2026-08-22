from __future__ import annotations

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.NEXUSPHP.longpt import LongPT


def _tracker() -> LongPT:
    return LongPT({"DEFAULT": {"tmdb_api": "dummy"}, "TRACKERS": {}})


def test_longpt_category_fallback_and_animation() -> None:
    tracker = _tracker()
    assert tracker.get_category(Meta(category="MOVIE")) == 401
    assert (
        tracker.get_category(Meta(category="MOVIE", genres=["Animation"]))
        == 405
    )


@pytest.mark.parametrize(
    ("meta", "expected"),
    (
        (Meta(is_disc="BDMV", resolution="1080p"), 1),
        (Meta(is_disc="DVD"), 6),
        (Meta(type="REMUX", resolution="1080p"), 3),
        (Meta(type="REMUX", resolution="2160p"), 11),
        (Meta(type="WEBDL"), 4),
        (Meta(type="WEBRIP"), 4),
        (Meta(type="HDTV"), 5),
        (Meta(type="ENCODE"), 7),
        (Meta(type="OTHER"), 7),
    ),
)
def test_longpt_type_mappings(meta: Meta, expected: int) -> None:
    assert _tracker().get_type(meta) == expected


@pytest.mark.parametrize(
    ("codec", "expected"),
    (
        ("HEVC", 2),
        ("AVC", 1),
        ("VC-1", 3),
        ("MPEG-2", 4),
        ("AV1", 5),
        ("Unknown", 6),
    ),
)
def test_longpt_codec_mappings(codec: str, expected: int) -> None:
    assert _tracker().get_codec(Meta(video_codec=codec)) == expected


@pytest.mark.parametrize(
    ("resolution", "sd", "expected"),
    (
        ("4320p", 0, 6),
        ("2160p", 0, 5),
        ("1440p", 0, 1),
        ("1080p", 0, 2),
        ("720p", 0, 3),
        ("576p", 1, 4),
        ("576p", 0, 7),
    ),
)
def test_longpt_resolution_mappings(
    resolution: str, sd: int, expected: int
) -> None:
    assert (
        _tracker().get_resolution(Meta(resolution=resolution, sd=sd))
        == expected
    )


def test_longpt_group_tag_known_and_fallback() -> None:
    tracker = _tracker()
    assert tracker.get_group_tag(Meta(tag="-LONGPT")) == 3
    assert tracker.get_group_tag(Meta(tag="-UNKNOWN")) == 5
    assert tracker.get_group_tag(Meta(tag="")) == 5


def test_longpt_checkbox_options_and_values() -> None:
    tracker = _tracker()
    meta = Meta(
        exclusive=True,
        audio_languages=["Chinese", "English"],
        subtitle_languages=["Mandarin"],
        hdr="HDR10",
        diy_disc=True,
    )
    assert tracker._has_chinese(meta.audio_languages)
    assert tracker._checkbox_options(meta)
    assert tracker.get_checkboxes(meta) == ["1", "5", "9", "6", "7", "4"]
