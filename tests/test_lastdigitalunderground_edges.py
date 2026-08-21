from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D import (
    lastdigitalunderground as ldu_module,
)
from src.integrations.trackers.UNIT3D.lastdigitalunderground import (
    LastDigitalUnderground,
)


def _tracker() -> LastDigitalUnderground:
    return LastDigitalUnderground(
        {"DEFAULT": {}, "TRACKERS": {"LASTDIGITALUNDERGROUND": {}}}
    )


def _meta(**values: object) -> Meta:
    state: dict[str, object] = {
        "category": "MOVIE",
        "keywords": [],
        "combined_genres": "Drama",
        "imdb_info": {"runtime": 120, "sound_mixes": []},
        "subtitle_languages": ["English"],
        "audio_languages": ["English"],
        "audiobook": False,
        "anime": False,
        "mal_id": 0,
        "three_d": False,
        "edition": "",
        "silent": False,
        "audio": "AAC 2.0",
        "tv_pack": False,
        "type": "WEBDL",
        "name": "Example Release",
        "original_language": "en",
        "no_subs": False,
    }
    state.update(values)
    return Meta(state)


@pytest.mark.asyncio
async def test_ldu_non_video_adult_categories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        ldu_module.languages_manager,
        "has_english_language",
        AsyncMock(return_value=True),
    )
    assert (
        await tracker._non_video_category(_meta(category="BOOK"), "adult", "7")
        == "6"
    )

    ldu_module.languages_manager.has_english_language = AsyncMock(
        return_value=False
    )
    assert (
        await tracker._non_video_category(_meta(category="BOOK"), "adult", "7")
        == "45"
    )


@pytest.mark.asyncio
async def test_ldu_movie_language_and_dubbed_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker, "_movie_static_category", lambda *_args: "")
    monkeypatch.setattr(
        tracker, "_has_english_audio_or_subs", AsyncMock(return_value=False)
    )
    assert await tracker._movie_category(_meta(), "drama") == "22"

    tracker._has_english_audio_or_subs = AsyncMock(return_value=True)  # type: ignore[method-assign]
    assert (
        await tracker._movie_category(_meta(audio="Dubbed AAC"), "drama")
        == "27"
    )
    assert await tracker._movie_category(_meta(audio="AAC"), "drama") == "1"


def test_ldu_runtime_invalid_value_defaults_to_zero() -> None:
    assert (
        LastDigitalUnderground._runtime_minutes(
            _meta(imdb_info={"runtime": "bad"})
        )
        == 0
    )


@pytest.mark.asyncio
async def test_ldu_tv_language_pack_and_dubbed_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker, "_tv_static_category", lambda *_args: "")
    monkeypatch.setattr(
        tracker, "_has_english_audio_or_subs", AsyncMock(return_value=False)
    )
    assert await tracker._tv_category(_meta(category="TV"), "drama") == "29"

    tracker._has_english_audio_or_subs = AsyncMock(return_value=True)  # type: ignore[method-assign]
    assert (
        await tracker._tv_category(_meta(category="TV", tv_pack=True), "drama")
        == "2"
    )
    assert (
        await tracker._tv_category(
            _meta(category="TV", audio="Dubbed AAC"), "drama"
        )
        == "31"
    )
    assert (
        await tracker._tv_category(_meta(category="TV", audio="AAC"), "drama")
        == "41"
    )


@pytest.mark.asyncio
async def test_ldu_english_subtitle_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter((False, True))

    async def has_english(_value: object) -> bool:
        return next(responses)

    monkeypatch.setattr(
        ldu_module.languages_manager, "has_english_language", has_english
    )
    assert await LastDigitalUnderground._has_english_audio_or_subs(_meta())


@pytest.mark.asyncio
async def test_ldu_audio_language_empty_and_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    assert await tracker._audio_language(_meta(audio_languages=[])) == (
        "",
        False,
    )

    monkeypatch.setattr(tracker, "_alpha3", lambda *_args: "")
    assert await tracker._audio_language(
        _meta(audio_languages=["Invalid"])
    ) == ("", False)


def test_ldu_alpha3_error_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ldu_module.langcodes,
        "find",
        lambda _value: (_ for _ in ()).throw(LookupError("bad language")),
    )
    assert _tracker()._alpha3("Unknown", "audio") == ""


def test_ldu_silent_category_prefers_subtitle_label() -> None:
    assert (
        LastDigitalUnderground._language_decorated_name(
            "Silent Movie",
            "18",
            non_english_original=True,
            non_english_audio=True,
            audio_iso="FRA",
            subtitle_label="Subs ENG",
        )
        == "Silent Movie [Subs ENG]"
    )
