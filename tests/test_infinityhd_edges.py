from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D import infinityhd as infinity_module
from src.integrations.trackers.UNIT3D.infinityhd import InfinityHD


def _tracker() -> InfinityHD:
    return InfinityHD({"DEFAULT": {}, "TRACKERS": {"INFINITYHD": {}}})


def test_infinityhd_nested_language_and_lookup_error(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    assert tracker._language_value({"Language": {"String": "English"}}) == "English"
    monkeypatch.setattr(infinity_module.pycountry.languages, "get", lambda **_kwargs: (_ for _ in ()).throw(AttributeError("bad")))
    assert tracker._lookup_language_code("unknown") == "unknown"


def test_infinityhd_original_language_match_and_commentary_rejection() -> None:
    tracker = _tracker()
    meta = Meta(
        original_language=["en"],
        mediainfo={
            "media": {
                "track": [
                    {"@type": "Audio", "Language": "English", "Title": "Main"},
                    {"@type": "Audio", "Language": "English", "Title": "Director Commentary"},
                ]
            }
        },
    )
    assert tracker.original_language_check(meta)
    assert not tracker._track_matches_original({"@type": "Audio", "Language": "English", "Title": "Commentary"}, {"en"})


def test_infinityhd_foreign_name_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    monkeypatch.setattr(infinity_module.languages_manager, "has_english_language", AsyncMock(return_value=False))
    meta = Meta(
        name="Movie 1080p WEB-DL",
        resolution="1080p",
        language_checked=True,
        audio_languages=["French"],
    )
    assert asyncio.run(tracker.get_name(meta)) == {"name": "Movie FRENCH 1080p WEB-DL"}


def test_infinityhd_non_disc_language_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    monkeypatch.setattr(infinity_module.languages_manager, "has_english_language", AsyncMock(return_value=False))
    tracker.common.check_and_confirm_adult_media_upload = AsyncMock(return_value=True)  # type: ignore[method-assign]
    meta = Meta(
        resolution="1080p",
        valid_mi_settings=True,
        is_disc="",
        language_checked=True,
        original_language=[],
        audio_languages=[],
        subtitle_languages=[],
        mediainfo={"media": {"track": []}},
        unattended=False,
        debug=False,
    )
    assert not asyncio.run(tracker.get_additional_checks(meta))
    tracker.common.check_and_confirm_adult_media_upload.assert_not_awaited()


def test_infinityhd_non_disc_english_audio_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()

    async def has_english(value: object) -> bool:
        return value == ["English"]

    monkeypatch.setattr(infinity_module.languages_manager, "has_english_language", has_english)
    tracker.common.check_and_confirm_adult_media_upload = AsyncMock(return_value=True)  # type: ignore[method-assign]
    meta = Meta(
        resolution="1080p",
        valid_mi_settings=True,
        is_disc="",
        language_checked=True,
        original_language=[],
        audio_languages=["English"],
        subtitle_languages=[],
        mediainfo={"media": {"track": []}},
    )
    assert asyncio.run(tracker.get_additional_checks(meta))
    tracker.common.check_and_confirm_adult_media_upload.assert_awaited_once()


def test_infinityhd_subtitle_fallback_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()

    async def has_english(value: object) -> bool:
        return value == ["English"]

    monkeypatch.setattr(infinity_module.languages_manager, "has_english_language", has_english)
    meta = Meta(
        original_language=[],
        audio_languages=[],
        subtitle_languages=["English"],
        mediainfo={"media": {"track": []}},
        language_checked=True,
    )
    assert asyncio.run(tracker._has_allowed_language(meta))


def test_infinityhd_original_language_short_circuits_external_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    monkeypatch.setattr(infinity_module.languages_manager, "has_english_language", AsyncMock(return_value=False))
    meta = Meta(
        original_language=["en"],
        mediainfo={"media": {"track": [{"@type": "Audio", "Language": "English"}]}},
        audio_languages=[],
        subtitle_languages=[],
    )
    assert asyncio.run(tracker._has_allowed_language(meta))
    infinity_module.languages_manager.has_english_language.assert_not_awaited()
