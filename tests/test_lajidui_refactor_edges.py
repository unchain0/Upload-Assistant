from __future__ import annotations

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.NEXUSPHP.lajidui import Lajidui


def _tracker() -> Lajidui:
    return Lajidui({"DEFAULT": {"tmdb_api": "dummy"}, "TRACKERS": {}})


def test_lajidui_category_fallback_and_animation() -> None:
    tracker = _tracker()
    assert tracker.get_category(Meta(category="MOVIE")) == 401
    assert (
        tracker.get_category(Meta(category="MOVIE", genres=["Animation"]))
        == 405
    )


@pytest.mark.parametrize(
    ("meta", "expected"),
    (
        (Meta(is_disc="BDMV"), 16),
        (Meta(container="mp4"), 11),
        (Meta(container="mkv"), 10),
        (Meta(container="avi"), 17),
    ),
)
def test_lajidui_container_mappings(meta: Meta, expected: int) -> None:
    assert _tracker().get_container(meta) == expected


@pytest.mark.parametrize(
    ("meta", "expected"),
    (
        (Meta(is_disc="BDMV"), 1),
        (Meta(is_disc="DVD"), 6),
        (Meta(is_disc="HDDVD"), 2),
        (Meta(type="REMUX"), 3),
        (Meta(type="WEBDL"), 10),
        (Meta(type="WEBRIP"), 10),
        (Meta(type="HDTV"), 5),
        (Meta(type="ENCODE"), 7),
        (Meta(type="OTHER"), 11),
    ),
)
def test_lajidui_type_mappings(meta: Meta, expected: int) -> None:
    assert _tracker().get_type(meta) == expected


@pytest.mark.parametrize(
    ("codec", "expected"),
    (
        ("HEVC", 7),
        ("x265", 7),
        ("AVC", 1),
        ("x264", 1),
        ("VC-1", 2),
        ("MPEG-2", 4),
        ("AV1", 6),
        ("XviD", 3),
        ("Unknown", 5),
    ),
)
def test_lajidui_codec_mappings(codec: str, expected: int) -> None:
    assert _tracker().get_codec(Meta(video_codec=codec)) == expected


@pytest.mark.parametrize(
    ("resolution", "sd", "expected"),
    (
        ("1080p", 0, 1),
        ("1080i", 0, 2),
        ("720p", 0, 3),
        ("576p", 1, 4),
        ("2160p", 0, 6),
        ("4320p", 0, 7),
        ("576p", 0, 8),
    ),
)
def test_lajidui_resolution_mappings(
    resolution: str, sd: int, expected: int
) -> None:
    assert (
        _tracker().get_resolution(Meta(resolution=resolution, sd=sd))
        == expected
    )


@pytest.mark.parametrize(
    ("audio", "expected"),
    (
        ("FLAC", 1),
        ("APE", 2),
        ("DTS-HD MA", 9),
        ("DTS", 3),
        ("MP3", 4),
        ("OGG", 5),
        ("AAC", 6),
        ("WAV", 8),
        ("TrueHD", 10),
        ("LPCM", 11),
        ("DDP", 12),
        ("DD", 13),
        ("Unknown", 7),
    ),
)
def test_lajidui_audio_codec_mappings(audio: str, expected: int) -> None:
    assert _tracker().get_audio_codec(Meta(audio=audio)) == expected


def test_lajidui_group_tag_known_and_fallback() -> None:
    tracker = _tracker()
    assert tracker.get_group_tag(Meta(tag="-CHD")) == 2
    assert tracker.get_group_tag(Meta(tag="-UNKNOWN")) == 5
    assert tracker.get_group_tag(Meta(tag="")) == 5
