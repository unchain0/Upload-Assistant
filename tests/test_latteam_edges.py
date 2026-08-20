from __future__ import annotations

import asyncio

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.latteam import LatTeam


def _tracker() -> LatTeam:
    return LatTeam({"DEFAULT": {}, "TRACKERS": {"LATTEAM": {}}})


def test_latteam_book_categories_cover_comic_and_magazine() -> None:
    tracker = _tracker()
    assert asyncio.run(tracker.get_category_id(Meta(category="BOOK", comic=True))) == {"category_id": "30"}
    assert asyncio.run(tracker.get_category_id(Meta(category="BOOK", magazine=True))) == {"category_id": "29"}


def test_latteam_tv_categories_cover_soap_and_asian_drama() -> None:
    tracker = _tracker()
    soap = Meta(category="TV", keywords=["telenovela"], genres=[], overview="", origin_country=[])
    assert asyncio.run(tracker.get_category_id(soap)) == {"category_id": "8"}

    asian = Meta(category="TV", keywords=[], genres=["Drama"], overview="", origin_country=["KR"])
    assert asyncio.run(tracker.get_category_id(asian)) == {"category_id": "20"}


def test_latteam_book_type_normalization_and_unknown_fallback() -> None:
    tracker = _tracker()
    assert asyncio.run(tracker.get_type_id(Meta(category="BOOK", type="CBR"))) == {"type_id": "25"}
    assert asyncio.run(tracker.get_type_id(Meta(category="BOOK", type="MOBI"))) == {"type_id": "26"}
    assert asyncio.run(tracker.get_type_id(Meta(category="BOOK", type="DOCX"))) == {"type_id": "21"}


def test_latteam_book_name_existing_edition_and_narration_languages() -> None:
    tracker = _tracker()
    cases = (
        ("Spain Spanish", "Narración en Castellano"),
        ("Latin America Spanish", "Narración en Latino"),
        ("Portuguese", "Narración en Portugués"),
    )
    for language, marker in cases:
        meta = Meta(
            category="BOOK",
            audiobook=True,
            author="Author",
            title="Book",
            type="M4B",
            edition="Second Edition",
            book_language=language,
        )
        name = asyncio.run(tracker.get_name(meta))["name"]
        assert "Second Edition" in name
        assert marker in name


def _video_meta(**values: object) -> Meta:
    state: dict[str, object] = {
        "category": "MOVIE",
        "name": "Original 2025 1080p WEB-DL-GROUP",
        "title": "Original",
        "aka": "AKA Titulo",
        "original_language": "es",
        "type": "WEBDL",
        "audio": "AAC 2.0",
        "tag": "-GROUP",
        "mediainfo": {"media": {"track": [{"@type": "General"}, {"@type": "Video"}]}},
    }
    state.update(values)
    return Meta(state)


def test_latteam_spanish_original_uses_aka_title() -> None:
    name = asyncio.run(_tracker().get_name(_video_meta()))["name"]
    assert "Titulo" in name
    assert "Original" not in name


def test_latteam_ignores_non_mapping_and_commentary_tracks() -> None:
    meta = _video_meta(
        original_language="en",
        aka="",
        mediainfo={
            "media": {
                "track": [
                    {"@type": "General"},
                    {"@type": "Video"},
                    "bad-track",
                    {"@type": "Audio", "Language": "es", "Title": "Director Commentary"},
                ]
            }
        },
    )
    name = asyncio.run(_tracker().get_name(meta))["name"]
    assert "[SUBS]" in name


def test_latteam_latino_audio_needs_no_cast_tag() -> None:
    meta = _video_meta(
        original_language="en",
        aka="",
        mediainfo={
            "media": {
                "track": [
                    {"@type": "General"},
                    {"@type": "Video"},
                    {"@type": "Audio", "Language": "es-419", "Title": "Latino"},
                ]
            }
        },
    )
    name = asyncio.run(_tracker().get_name(meta))["name"]
    assert "[CAST]" not in name
    assert "[SUBS]" not in name


def test_latteam_castilian_audio_adds_cast_tag() -> None:
    meta = _video_meta(
        original_language="en",
        aka="",
        mediainfo={
            "media": {
                "track": [
                    {"@type": "General"},
                    {"@type": "Video"},
                    {"@type": "Audio", "Language": "es", "Title": "Castellano"},
                ]
            }
        },
    )
    assert "[CAST]-GROUP" in asyncio.run(_tracker().get_name(meta))["name"]
