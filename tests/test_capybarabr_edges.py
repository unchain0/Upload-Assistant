from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.capybarabr import CapybaraBR


def _tracker() -> CapybaraBR:
    return CapybaraBR({"TRACKERS": {"CAPYBARABR": {}}})


@pytest.mark.asyncio
async def test_capybarabr_game_platform_ids_cover_playstation_and_console() -> None:
    tracker = _tracker()
    assert await tracker.get_type_id(Meta(category="GAME", type="GAME", platform="PlayStation 5")) == {"type_id": "48"}
    assert await tracker.get_type_id(Meta(category="GAME", type="GAME", platform="Switch")) == {"type_id": "50"}


def test_capybarabr_game_language_tags_cover_multi_and_fallback() -> None:
    assert CapybaraBR._game_language_tag(Meta(languages=["Portuguese", "English"], language="Portuguese")) == "[MULTI]"
    assert CapybaraBR._game_language_tag(Meta(languages=["Spanish"], language="Spanish")) == "[SPANISH]"
    assert CapybaraBR._is_multilingual_portuguese(["Portuguese", "French"])


def test_capybarabr_preserves_source_group_when_adding_audio_tag() -> None:
    meta = Meta(path="Movie-OLD.DUAL-GROUP.mkv", uuid="", tag="-GROUP")
    assert CapybaraBR._source_group(meta) == "OLD"
    assert CapybaraBR._insert_audio_tag("Movie-GROUP", " DUAL", meta) == "Movie-OLD DUAL-GROUP"


def test_capybarabr_missing_group_tag_adds_nogroup() -> None:
    assert not CapybaraBR._valid_group_tag(None)
    assert CapybaraBR._ensure_nogroup("Movie-UNKNOWN", None) == "Movie-NoGroup"


@pytest.mark.asyncio
async def test_capybarabr_audiobook_requires_narrator() -> None:
    meta = Meta(category="BOOK", audiobook=True, narrator="")
    assert not await _tracker().get_additional_checks(meta)


@pytest.mark.asyncio
async def test_capybarabr_bluray_remux_encode_settings_require_bdinfo(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker.common, "has_bdinfo", lambda _text: False)
    tracker.common.check_portuguese_video_requirements = AsyncMock(return_value=True)  # type: ignore[method-assign]
    meta = Meta(
        category="MOVIE",
        type="REMUX",
        source="BluRay",
        has_encode_settings=True,
        description="",
        description_link_content="",
        description_file_content="",
    )
    assert not await tracker.get_additional_checks(meta)
    tracker.common.check_portuguese_video_requirements.assert_not_awaited()
