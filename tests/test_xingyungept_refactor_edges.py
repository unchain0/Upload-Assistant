from __future__ import annotations

import asyncio

from src.domain_models.release import Meta
from src.integrations.trackers.NEXUSPHP.xingyungept import XingyungePT


def _tracker(config: dict | None = None) -> XingyungePT:
    return XingyungePT(
        config
        or {
            "DEFAULT": {"tmdb_api": "dummy"},
            "TRACKERS": {"XINGYUNGEPT": {}},
        }
    )


def test_xingyungept_category_fallback_and_animation() -> None:
    tracker = _tracker()
    assert tracker.get_category(Meta(category="MOVIE")) == 401
    assert (
        tracker.get_category(Meta(category="MOVIE", genres=["Animation"]))
        == 405
    )


def test_xingyungept_disc_and_file_type_edges() -> None:
    tracker = _tracker()
    assert tracker.get_type(Meta(is_disc="BDMV", resolution="2160p")) == 2
    assert tracker.get_type(Meta(type="REMUX")) == 3
    assert tracker.get_type(Meta(type="OTHER")) == 7


def test_xingyungept_default_codec_resolution_audio() -> None:
    tracker = _tracker()
    assert tracker.get_codec(Meta(video_codec="UNKNOWN")) == 6
    assert tracker._fallback_resolution_id(Meta(sd=1), "576p") == 1
    assert tracker.get_resolution(Meta(resolution="480p", sd=0)) == 1
    assert tracker.get_audio_codec(Meta(audio="UNKNOWN")) == 16


def test_xingyungept_group_tag_known_and_fallback() -> None:
    tracker = _tracker()
    assert tracker.get_group_tag(Meta(tag="-CHD")) == 2
    assert tracker.get_group_tag(Meta(tag="-UNKNOWN")) == 5
    assert tracker.get_group_tag(Meta(tag="")) == 5


def test_xingyungept_episode_checkbox_path() -> None:
    tracker = _tracker()
    episode = Meta(category="TV", tv_pack=False)
    assert tracker._tv_checkbox(episode) == 10
    assert tracker.get_checkboxes(episode) == ["10"]


def test_xingyungept_anonymous_data_branches() -> None:
    tracker = _tracker()
    assert asyncio.run(tracker.get_anonymous_data(Meta(anon=0))) == {}
    assert asyncio.run(tracker.get_anonymous_data(Meta(anon=1))) == {
        "anonymous": "1"
    }
    configured = _tracker(
        {
            "DEFAULT": {"tmdb_api": "dummy"},
            "TRACKERS": {"XINGYUNGEPT": {"anon": True}},
        }
    )
    assert asyncio.run(configured.get_anonymous_data(Meta(anon=0))) == {
        "anonymous": "1"
    }
