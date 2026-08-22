from __future__ import annotations

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.NEXUSPHP.ptcafe import PTCafe


def _tracker() -> PTCafe:
    return PTCafe({"DEFAULT": {"tmdb_api": "dummy"}, "TRACKERS": {}})


def test_ptcafe_category_fallback_and_animation() -> None:
    tracker = _tracker()
    assert tracker.get_category(Meta(category="MOVIE")) == 401
    assert (
        tracker.get_category(Meta(category="MOVIE", genres=["Animation"]))
        == 405
    )


@pytest.mark.parametrize(
    ("meta", "expected"),
    (
        (Meta(is_disc="BDMV", resolution="1080p", diy_disc=False), 4),
        (Meta(is_disc="BDMV", resolution="1080p", diy_disc=True), 5),
        (Meta(is_disc="BDMV", resolution="2160p", diy_disc=False), 1),
        (Meta(is_disc="BDMV", resolution="2160p", diy_disc=True), 2),
        (Meta(type="REMUX", resolution="1080p"), 6),
        (Meta(type="REMUX", resolution="2160p"), 3),
        (Meta(type="WEBDL"), 8),
        (Meta(type="WEBRIP"), 8),
        (Meta(type="HDTV"), 9),
        (Meta(type="ENCODE"), 7),
        (Meta(type="OTHER"), 7),
    ),
)
def test_ptcafe_type_mappings(meta: Meta, expected: int) -> None:
    assert _tracker().get_type(meta) == expected


@pytest.mark.parametrize(
    ("resolution", "sd", "expected"),
    (
        ("1080p", 0, 3),
        ("720p", 0, 4),
        ("576p", 1, 5),
        ("2160p", 0, 2),
        ("4320p", 0, 1),
        ("576p", 0, 6),
    ),
)
def test_ptcafe_resolution_mappings(
    resolution: str, sd: int, expected: int
) -> None:
    assert (
        _tracker().get_resolution(Meta(resolution=resolution, sd=sd))
        == expected
    )


@pytest.mark.parametrize(
    ("audio", "expected"),
    (
        ("DTS:X 7.1", 1),
        ("DTS-HD MA", 2),
        ("DTS-HD HR", 3),
        ("DTS-HD", 4),
        ("DTS:X", 5),
        ("LPCM", 6),
        ("DD 5.1", 7),
        ("Atmos", 8),
        ("AAC", 9),
        ("TrueHD", 10),
        ("DTS", 11),
        ("FLAC", 12),
        ("APE", 13),
        ("MP3", 14),
        ("WAV", 15),
        ("OPUS", 16),
        ("OGG", 17),
        ("Unknown", 18),
    ),
)
def test_ptcafe_audio_codec_mappings(audio: str, expected: int) -> None:
    assert _tracker().get_audio_codec(Meta(audio=audio)) == expected


def test_ptcafe_group_tag_known_and_fallback() -> None:
    tracker = _tracker()
    assert tracker.get_group_tag(Meta(tag="-PTCAFE")) == 25
    assert tracker.get_group_tag(Meta(tag="-UNKNOWN")) == 30
    assert tracker.get_group_tag(Meta(tag="")) == 30


def test_ptcafe_checkbox_options_and_values() -> None:
    tracker = _tracker()
    meta = Meta(
        exclusive=True,
        audio_languages=["Mandarin", "Cantonese"],
        subtitle_languages=["Chinese"],
        hdr="HDR DV",
        diy_disc=True,
    )
    assert tracker._has_chinese(meta.audio_languages)
    assert tracker._checkbox_options(meta)
    assert tracker.get_checkboxes(meta) == [
        "5",
        "7",
        "8",
        "9",
        "12",
        "11",
        "13",
    ]
