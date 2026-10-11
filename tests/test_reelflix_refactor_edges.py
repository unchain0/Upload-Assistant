from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.reelflix import ReelFlix


def _tracker() -> ReelFlix:
    return ReelFlix({"DEFAULT": {}, "TRACKERS": {"REELFLIX": {}}})


@pytest.mark.asyncio
async def test_reelflix_adult_media_check_delegates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    check = AsyncMock(return_value=True)
    monkeypatch.setattr(
        tracker.common, "check_and_confirm_adult_media_upload", check
    )

    meta = Meta(category="MOVIE")
    assert await tracker.get_additional_checks(meta)
    check.assert_awaited_once_with(meta, "REELFLIX")


@pytest.mark.asyncio
async def test_reelflix_name_normalization_paths() -> None:
    tracker = _tracker()

    assert await tracker.get_name(Meta(name="Movie.1080p", tag="")) == {
        "name": "Movie.1080p-NoGroup"
    }
    assert await tracker.get_name(
        Meta(name="Movie.1080p-NoGrp", tag="-NoGrp")
    ) == {"name": "Movie.1080p-NoGroup"}
    assert await tracker.get_name(
        Meta(name="Movie.1080p-GROUP", tag="-GROUP")
    ) == {"name": "Movie.1080p-GROUP"}


@pytest.mark.asyncio
async def test_reelflix_type_mapping_modes() -> None:
    tracker = _tracker()
    meta = Meta(type="WEBDL")

    mapping = await tracker.get_type_id(meta, mapping_only=True)
    assert mapping["DISC"] == "43"
    reverse = await tracker.get_type_id(meta, reverse=True)
    assert reverse["42"] == "WEBDL"
    assert await tracker.get_type_id(meta) == {"type_id": "42"}
    assert await tracker.get_type_id(meta, type="UNKNOWN") == {"type_id": "0"}


@pytest.mark.asyncio
async def test_reelflix_resolution_mapping_modes() -> None:
    tracker = _tracker()
    meta = Meta(resolution="1080p")

    mapping = await tracker.get_resolution_id(meta, mapping_only=True)
    assert mapping["2160p"] == "2"
    reverse = await tracker.get_resolution_id(meta, reverse=True)
    assert reverse["3"] == "1080p"
    assert await tracker.get_resolution_id(meta) == {"resolution_id": "3"}
    assert await tracker.get_resolution_id(meta, resolution="UNKNOWN") == {
        "resolution_id": "10"
    }
