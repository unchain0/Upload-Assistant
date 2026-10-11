from __future__ import annotations

import asyncio
import copy
from unittest.mock import AsyncMock

from data.example_config import config as example_config
from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.locadora import Locadora


def _config() -> dict:
    config = copy.deepcopy(example_config)
    config.setdefault("DEFAULT", {}).setdefault("tmdb_api", "test-key")
    tracker = config.setdefault("TRACKERS", {}).setdefault("LOCADORA", {})
    tracker.setdefault("api_key", "test-key")
    tracker.setdefault("announce_url", "https://tracker.invalid/announce")
    return config


def test_locadora_name_uses_bdmv_name_and_preserves_valid_group() -> None:
    tracker = Locadora(_config())
    meta = Meta(
        is_disc="BDMV",
        name="Movie.H.265.DDP5.1-GROUP",
        basename_no_ext="Wrong.Basename",
        tag="-GROUP",
    )

    assert asyncio.run(tracker.get_name(meta)) == {
        "name": "Movie H.265 DDP5.1-GROUP"
    }


def test_locadora_name_normalizes_invalid_group_to_nogroup() -> None:
    tracker = Locadora(_config())
    meta = Meta(
        basename_no_ext="Movie.H.265-Unknown.mkv",
        tag="-Unknown",
    )

    assert asyncio.run(tracker.get_name(meta)) == {
        "name": "Movie H.265-NoGroup"
    }
    assert tracker._requires_no_group("")


def test_locadora_region_fallback_edges() -> None:
    tracker = Locadora(_config())
    assert asyncio.run(tracker.get_region_id(Meta(region="EUR"))) == {}

    tracker.common.unit3d_region_ids = AsyncMock(return_value=None)  # type: ignore[method-assign]
    assert asyncio.run(tracker.get_region_id(Meta(region="ZZZ"))) == {}


def test_locadora_type_and_resolution_mapping_edges() -> None:
    tracker = Locadora(_config())
    assert asyncio.run(tracker.get_type_id(Meta(type="REMUX"))) == {
        "type_id": "2"
    }
    assert asyncio.run(tracker.get_type_id(Meta(type="UNKNOWN"))) == {
        "type_id": "0"
    }
    assert asyncio.run(
        tracker.get_resolution_id(Meta(resolution="2160p"))
    ) == {"resolution_id": "2"}
    assert asyncio.run(
        tracker.get_resolution_id(Meta(resolution="UNKNOWN"))
    ) == {"resolution_id": "10"}


def test_locadora_additional_checks_delegate_to_portuguese_policy() -> None:
    tracker = Locadora(_config())
    tracker.common.check_portuguese_video_requirements = AsyncMock(  # type: ignore[method-assign]
        return_value=True
    )
    meta = Meta()

    assert asyncio.run(tracker.get_additional_checks(meta))
    tracker.common.check_portuguese_video_requirements.assert_awaited_once_with(
        meta, "LOCADORA"
    )
