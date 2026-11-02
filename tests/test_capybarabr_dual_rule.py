from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.meta import Meta
from src.trackers.UNIT3D.capybarabr import CapybaraBR


def _tracker(api_key: str = "test-key") -> CapybaraBR:
    return CapybaraBR({"TRACKERS": {"CAPYBARABR": {"api_key": api_key}}})


def _meta(**values: object) -> Meta:
    defaults: dict[str, object] = {
        "category": "TV",
        "dual_audio": False,
        "name": "Show.S03.1080p.AMZN.WEB-DL.DDP5.1.H.264-FLUX",
        "path": "Show.S03.1080p.AMZN.WEB-DL.DDP5.1.H.264-FLUX.mkv",
    }
    defaults.update(values)
    return Meta(**defaults)


@pytest.mark.asyncio
async def test_mono_release_is_rejected_when_a_dual_exists() -> None:
    tracker = _tracker()
    tracker.search_existing = AsyncMock(  # type: ignore[method-assign]
        return_value=[{"name": "Show.S03.1080p.BluRay.DUAL-FLUX"}]
    )
    assert await tracker._dual_release_allowed(_meta()) is False


@pytest.mark.asyncio
async def test_dual_release_skips_the_search() -> None:
    tracker = _tracker()
    tracker.search_existing = AsyncMock()  # type: ignore[method-assign]
    assert await tracker._dual_release_allowed(_meta(dual_audio=True)) is True
    tracker.search_existing.assert_not_awaited()


@pytest.mark.asyncio
async def test_release_passes_without_a_dual_match() -> None:
    tracker = _tracker()
    tracker.search_existing = AsyncMock(  # type: ignore[method-assign]
        return_value=[{"name": "Show.S03.1080p.BluRay-GROUP"}]
    )
    assert await tracker._dual_release_allowed(_meta()) is True


@pytest.mark.asyncio
async def test_search_failure_fails_open() -> None:
    tracker = _tracker()
    tracker.search_existing = AsyncMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("search down")
    )
    assert await tracker._dual_release_allowed(_meta()) is True


@pytest.mark.asyncio
async def test_missing_api_key_fails_open_without_searching() -> None:
    tracker = _tracker(api_key="")
    tracker.search_existing = AsyncMock()  # type: ignore[method-assign]
    assert await tracker._dual_release_allowed(_meta()) is True
    tracker.search_existing.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name",
    [
        "Show.S03.1080p.BluRay.Dual.Audio-FLUX",
        "Show.S03.1080p.BluRay.DUAL-FLUX",
        "Show.S03.1080p.BluRay.MULTI-FLUX",
    ],
)
async def test_dual_and_multi_separators_are_detected(name: str) -> None:
    tracker = _tracker()
    tracker.search_existing = AsyncMock(  # type: ignore[method-assign]
        return_value=[{"name": name}]
    )
    assert await tracker._dual_release_allowed(_meta()) is False
