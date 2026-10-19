from __future__ import annotations

import asyncio
import copy
from unittest.mock import AsyncMock

from data.example_config import config as example_config
from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.theoldschool import TheOldSchool


def _config() -> dict:
    config = copy.deepcopy(example_config)
    config.setdefault("DEFAULT", {}).setdefault("tmdb_api", "test-key")
    values = config.setdefault("TRACKERS", {}).setdefault("THEOLDSCHOOL", {})
    values.setdefault("api_key", "test-key")
    values.setdefault("announce_url", "https://tracker.invalid/announce")
    return config


def _tracker() -> TheOldSchool:
    return TheOldSchool(_config())


def test_theoldschool_category_mapping_edges() -> None:
    tracker = _tracker()
    assert asyncio.run(
        tracker.get_category_id(Meta(category="MOVIE", tag="-VOSTFR"))
    ) == {"category_id": "6"}
    assert asyncio.run(
        tracker.get_category_id(Meta(category="TV", tag="-SubFrench"))
    ) == {"category_id": "7"}
    assert asyncio.run(
        tracker.get_category_id(Meta(category="TV", tv_pack=True))
    ) == {"category_id": "8"}
    assert asyncio.run(tracker.get_category_id(Meta(category="MOVIE"))) == {
        "category_id": "1"
    }
    assert asyncio.run(tracker.get_category_id(Meta(category="TV"))) == {
        "category_id": "2"
    }
    assert asyncio.run(tracker.get_category_id(Meta(category="OTHER"))) == {
        "category_id": "0"
    }


def test_theoldschool_type_mapping_edges() -> None:
    tracker = _tracker()
    assert asyncio.run(tracker.get_type_id(Meta(is_disc="DVD"))) == {
        "type_id": "7"
    }
    assert asyncio.run(tracker.get_type_id(Meta(three_d="3D"))) == {
        "type_id": "8"
    }
    for release_type, expected in (
        ("DISC", "1"),
        ("REMUX", "2"),
        ("ENCODE", "3"),
        ("WEBDL", "4"),
        ("WEBRIP", "5"),
        ("HDTV", "6"),
        ("UNKNOWN", "0"),
    ):
        assert asyncio.run(tracker.get_type_id(Meta(type=release_type))) == {
            "type_id": expected
        }


def test_theoldschool_name_scene_and_non_scene_without_rehash() -> None:
    tracker = _tracker()
    scene = Meta(scene=True, scene_name="Scene.Release-GRP", keep_nfo=False)
    assert asyncio.run(tracker.get_name(scene)) == {
        "name": "Scene.Release-GRP"
    }

    regular = Meta(
        scene=False,
        basename_no_ext="Example Release.mkv",
        keep_nfo=False,
    )
    assert asyncio.run(tracker.get_name(regular)) == {
        "name": "Example.Release"
    }

    none_scene = Meta(
        scene=None,
        basename_no_ext="Example Release.mkv",
        keep_nfo=False,
    )
    assert asyncio.run(tracker.get_name(none_scene)) == {
        "name": "Example Release.mkv"
    }


def test_theoldschool_additional_checks_scene_nfo_paths() -> None:
    tracker = _tracker()
    tracker.common.check_language_requirements = AsyncMock(return_value=True)  # type: ignore[method-assign]

    assert not asyncio.run(
        tracker.get_additional_checks(
            Meta(scene=True, nfo=False, auto_nfo=False)
        )
    )
    assert asyncio.run(
        tracker.get_additional_checks(
            Meta(scene=True, nfo=True, auto_nfo=False)
        )
    )
    assert asyncio.run(
        tracker.get_additional_checks(
            Meta(scene=False, nfo=False, auto_nfo=False)
        )
    )
