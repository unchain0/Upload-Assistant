from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.emuwarez import Emuwarez


def _config(*, use_spanish_title: bool = True) -> dict[str, Any]:
    return {
        "DEFAULT": {"tmdb_api": "0123456789abcdef0123456789abcdef"},
        "TRACKERS": {"EMUWAREZ": {"api_key": "test-key", "use_spanish_title": use_spanish_title}},
    }


def _tracker(**kwargs: object) -> Emuwarez:
    return Emuwarez(_config(**kwargs))


def _meta(**values: object) -> Meta:
    state: dict[str, object] = {
        "title": "Original Title",
        "category": "MOVIE",
        "imdb_info": {},
        "tmdb": 0,
        "original_language": "en",
        "mediainfo": {"media": {"track": []}},
        "audio_languages": [],
        "subtitle_languages": [],
        "resolution": "1080p",
        "source": "BluRay",
        "type": "ENCODE",
        "is_disc": "",
        "video_codec": "H.264",
        "video_encode": "x264",
        "hdr": "",
        "language_checked": True,
        "year": 2025,
        "season_int": 0,
        "tag": "-GROUP",
        "season": "",
        "tracker_status": {},
    }
    state.update(values)
    return Meta(state)


def _audio_track(language: str = "English", *, format_name: str = "AAC", channels: int = 2) -> dict[str, Any]:
    return {"@type": "Audio", "Language": language, "Format": format_name, "Channels": channels}


@pytest.mark.asyncio
async def test_emuwarez_spanish_title_from_imdb_and_tmdb(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    imdb_meta = _meta(imdb_info={"akas": [{"country": "Spain", "title": "Título Español"}]})
    assert await tracker._get_title(imdb_meta) == "Título Español"

    assert tracker._imdb_spanish_title(_meta(imdb_info={"akas": "bad"})) == ""
    monkeypatch.setattr(tracker.tmdb_manager, "get_tmdb_translations", AsyncMock(return_value="Título TMDb"))
    tmdb_meta = _meta(tmdb=123)
    assert await tracker._tmdb_spanish_title(tmdb_meta) == "Título TMDb"


def test_emuwarez_source_and_language_fallbacks() -> None:
    tracker = _tracker()
    assert tracker._source_format("BluRay") == "BluRay"
    assert tracker._map_language("") == ""
    meta = _meta(mediainfo={"media": {"track": "bad"}})
    assert tracker._media_tracks(meta) == []
    fallback = _meta(audio_languages=["Spanish"])
    assert tracker._extract_audio_languages([], fallback) == ["ESP"]
    assert tracker._is_spanish_subtitle({"@type": "Text", "Title": "Castellano"})
    assert tracker._search_items([]) == []


@pytest.mark.asyncio
async def test_emuwarez_dual_audio_string() -> None:
    tracker = _tracker()
    tracks = [_audio_track("Spanish"), _audio_track("English")]
    assert tracker._dual_audio_string(tracks) == "DUAL AAC 2.0"
    assert await tracker._special_audio_string(_meta(), tracks, ["ESP", "ING"]) == "DUAL AAC 2.0"


def test_emuwarez_dual_mixed_codecs_rejected() -> None:
    tracker = _tracker()
    tracks = [_audio_track(format_name="AAC"), _audio_track(format_name="AC-3")]
    assert tracker._dual_audio_string(tracks) == ""


@pytest.mark.asyncio
async def test_emuwarez_multi_audio_string() -> None:
    tracker = _tracker()
    tracks = [_audio_track(language) for language in ("Spanish", "English", "French", "German")]
    assert tracker._multi_audio_string(tracks) == "MULTI AAC 2.0"
    assert await tracker._special_audio_string(_meta(), tracks, ["ESP", "ING", "FRA", "ALE"]) == "MULTI AAC 2.0"


def test_emuwarez_multi_mixed_codecs_rejected() -> None:
    tracker = _tracker()
    tracks = [_audio_track(), _audio_track(), _audio_track(), _audio_track(format_name="AC-3")]
    assert tracker._multi_audio_string(tracks) == ""


@pytest.mark.asyncio
async def test_emuwarez_single_original_audio_vose_and_vo() -> None:
    tracker = _tracker()
    track = _audio_track("English")
    vo = _meta(original_language="en", mediainfo={"media": {"track": [track]}})
    assert await tracker._single_original_audio_string(vo, [track], ["ING"]) == "V.O. ING AAC 2.0"

    vose_meta = _meta(
        original_language="en",
        mediainfo={
            "media": {
                "track": [
                    track,
                    {"@type": "Text", "Language": "es"},
                ]
            }
        },
    )
    assert await tracker._single_original_audio_string(vose_meta, [track], ["ING"]) == "VOSE ING AAC 2.0"


@pytest.mark.asyncio
async def test_emuwarez_single_original_requires_one_track() -> None:
    tracker = _tracker()
    assert await tracker._single_original_audio_string(_meta(), [], []) == ""
    assert await tracker._build_audio_string(_meta(audio_languages=["English"])) == ""
