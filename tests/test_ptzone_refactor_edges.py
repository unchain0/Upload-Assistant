from __future__ import annotations

import asyncio

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.NEXUSPHP.ptzone import PTZone


def _tracker(config: dict | None = None) -> PTZone:
    return PTZone(
        config
        or {"DEFAULT": {"tmdb_api": "dummy"}, "TRACKERS": {"PTZONE": {}}}
    )


def test_ptzone_animation_category() -> None:
    assert (
        _tracker().get_category(Meta(category="MOVIE", genres=["Animation"]))
        == 405
    )


@pytest.mark.parametrize(
    ("meta", "expected"),
    (
        (Meta(type="REMUX"), 3),
        (Meta(type="WEBDL"), 4),
        (Meta(type="WEBRIP"), 4),
        (Meta(type="HDTV"), 5),
        (Meta(type="ENCODE"), 7),
        (Meta(type="OTHER"), 7),
    ),
)
def test_ptzone_file_type_mappings(meta: Meta, expected: int) -> None:
    assert _tracker().get_type(meta) == expected


def test_ptzone_default_codec_resolution_audio() -> None:
    tracker = _tracker()
    assert tracker.get_codec(Meta(video_codec="UNKNOWN")) == 5
    assert tracker.get_resolution(Meta(resolution="576p", sd=0)) == 1
    assert tracker.get_audio_codec(Meta(audio="UNKNOWN")) == 7


def test_ptzone_group_tag_known_and_fallback() -> None:
    tracker = _tracker()
    assert tracker.get_group_tag(Meta(tag="-PTZWEB")) == 6
    assert tracker.get_group_tag(Meta(tag="-UNKNOWN")) == 5
    assert tracker.get_group_tag(Meta(tag="")) == 5


def test_ptzone_checkbox_pack_and_episode_paths() -> None:
    tracker = _tracker()
    pack = Meta(
        category="TV",
        tv_pack=True,
        exclusive=True,
        audio_languages=["Mandarin"],
        subtitle_languages=["Chinese"],
        diy_disc=True,
        hdr="HDR10",
    )
    assert tracker._has_chinese(pack.audio_languages)
    assert tracker._tv_checkbox(pack) == 9
    assert tracker._checkbox_options(pack)
    assert tracker.get_checkboxes(pack) == ["1", "5", "6", "4", "7", "9"]

    episode = Meta(category="TV", tv_pack=False)
    assert tracker._tv_checkbox(episode) == 8
    assert tracker.get_checkboxes(episode) == ["8"]

    movie = Meta(category="MOVIE", tv_pack=False)
    assert tracker._tv_checkbox(movie) is None


def test_ptzone_anonymous_data_branches() -> None:
    tracker = _tracker()
    assert asyncio.run(tracker.get_anonymous_data(Meta(anon=0))) == {}
    assert asyncio.run(tracker.get_anonymous_data(Meta(anon=1))) == {
        "anonymous": "1"
    }
    configured = _tracker(
        {
            "DEFAULT": {"tmdb_api": "dummy"},
            "TRACKERS": {"PTZONE": {"anon": True}},
        }
    )
    assert asyncio.run(configured.get_anonymous_data(Meta(anon=0))) == {
        "anonymous": "1"
    }
