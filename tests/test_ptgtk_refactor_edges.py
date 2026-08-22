from __future__ import annotations

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.NEXUSPHP.ptgtk import PTGTK


def _tracker(config: dict | None = None) -> PTGTK:
    return PTGTK(
        config or {"DEFAULT": {"tmdb_api": "dummy"}, "TRACKERS": {"PTGTK": {}}}
    )


def test_ptgtk_category_fallback_and_animation() -> None:
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
        (Meta(is_disc="BDMV", resolution="2160p"), 10),
        (Meta(is_disc="DVD"), 6),
        (Meta(is_disc="HDDVD"), 2),
        (Meta(type="REMUX"), 3),
        (Meta(type="WEBDL"), 11),
        (Meta(type="WEBRIP"), 11),
        (Meta(type="HDTV"), 5),
        (Meta(type="ENCODE"), 7),
        (Meta(type="OTHER"), 7),
    ),
)
def test_ptgtk_type_mappings(meta: Meta, expected: int) -> None:
    assert _tracker().get_type(meta) == expected


@pytest.mark.parametrize(
    ("codec", "expected"),
    (
        ("AV1", 7),
        ("HEVC", 6),
        ("AVC", 1),
        ("MPEG-2", 4),
        ("VC-1", 2),
        ("VP9", 8),
        ("XviD", 3),
        ("Unknown", 5),
    ),
)
def test_ptgtk_codec_mappings(codec: str, expected: int) -> None:
    assert _tracker().get_codec(Meta(video_codec=codec)) == expected


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
def test_ptgtk_resolution_mappings(
    resolution: str, sd: int, expected: int
) -> None:
    assert (
        _tracker().get_resolution(Meta(resolution=resolution, sd=sd))
        == expected
    )


def test_ptgtk_group_tag_known_and_fallback() -> None:
    tracker = _tracker()
    assert tracker.get_group_tag(Meta(tag="-BEAST")) == 11
    assert tracker.get_group_tag(Meta(tag="-UNKNOWN")) == 5
    assert tracker.get_group_tag(Meta(tag="")) == 5


def test_ptgtk_checkbox_options_and_values() -> None:
    tracker = _tracker()
    meta = Meta(
        exclusive=True,
        audio_languages=["Mandarin"],
        subtitle_languages=["Chinese"],
        hdr="HDR10",
    )
    assert tracker._has_chinese(meta.audio_languages)
    assert tracker._checkbox_options(meta)
    assert tracker.get_checkboxes(meta) == ["1", "5", "6", "7"]


def test_ptgtk_anonymous_branches() -> None:
    tracker = _tracker()
    assert not tracker.get_anonymous(Meta(anon=0))
    assert tracker.get_anonymous(Meta(anon=1))
    configured = _tracker(
        {
            "DEFAULT": {"tmdb_api": "dummy"},
            "TRACKERS": {"PTGTK": {"anon": True}},
        }
    )
    assert configured.get_anonymous(Meta(anon=0))
