from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.lst import LST


def _tracker() -> LST:
    return LST({"DEFAULT": {}, "TRACKERS": {"LST": {}}})


def test_lst_category_mapping_and_selection_edges() -> None:
    tracker = _tracker()
    meta = Meta(category="MOVIE")
    assert (
        asyncio.run(tracker.get_category_id(meta, mapping_only=True))["BOOK"]
        == "9"
    )
    assert (
        asyncio.run(tracker.get_category_id(meta, reverse=True))["1"]
        == "MOVIE"
    )
    assert asyncio.run(tracker.get_category_id(meta, category="TV")) == {
        "category_id": "2"
    }
    assert asyncio.run(tracker.get_category_id(Meta(category="OTHER"))) == {
        "category_id": "0"
    }
    assert tracker._selected_category(meta, "BOOK") == "BOOK"
    assert tracker._selected_category(meta, "") == "MOVIE"


def test_lst_type_mapping_edges() -> None:
    tracker = _tracker()
    meta = Meta(category="MOVIE", type="REMUX")
    mapping = asyncio.run(tracker.get_type_id(meta, mapping_only=True))
    assert mapping["REMUX"] == "2"
    reverse = asyncio.run(tracker.get_type_id(meta, reverse=True))
    assert reverse["2"] == "REMUX"
    assert asyncio.run(tracker.get_type_id(meta, type=".webdl")) == {
        "type_id": "4"
    }
    assert tracker._selected_type(meta, "ENCODE") == "ENCODE"


@pytest.mark.asyncio
async def test_lst_additional_check_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    assert await tracker.get_additional_checks(Meta(category="BOOK"))
    assert not await tracker.get_additional_checks(
        Meta(category="MOVIE", valid_mi_settings=False)
    )
    assert await tracker.get_additional_checks(
        Meta(category="MOVIE", valid_mi_settings=True, is_disc="BDMV")
    )
    monkeypatch.setattr(
        tracker.common,
        "check_language_requirements",
        AsyncMock(return_value=True),
    )
    assert await tracker.get_additional_checks(
        Meta(category="TV", valid_mi_settings=True, is_disc="")
    )


@pytest.mark.asyncio
async def test_lst_additional_payload_book_and_edition_edges() -> None:
    tracker = _tracker()
    meta = Meta(
        category="BOOK",
        edition="Director's Cut",
        openlibrary="OL123M",
        isbn="9781234567897",
        extra_openlibrary_ids="OL1,OL2",
    )
    data = await tracker.get_additional_data(meta)
    assert data["edition_id"] == 2
    assert data["book_exists_on_openlibrary"] == "1"
    assert data["openlibrary_book_id"] == "OL123M"
    assert data["openlibrary_isbn"] == "9781234567897"
    assert await tracker.get_edition(Meta(edition="Unknown")) is None

    fallback = Meta(category="BOOK", openlibrary_book_id="OL999M")
    assert tracker._openlibrary_id(fallback) == "OL999M"


def test_lst_discogs_and_special_name_fallback_edges() -> None:
    tracker = _tracker()
    assert tracker._discogs_id_value(None, "release") == ""
    assert tracker._first_music_track({}) == {}
    assert tracker._special_name(Meta(category="MOVIE")) is None


@pytest.mark.asyncio
async def test_lst_dvdrip_movie_and_tv_name_edges() -> None:
    tracker = _tracker()
    movie = Meta(
        category="MOVIE",
        type="DVDRIP",
        name="Film PAL DVDx264 AAC-GRP",
        source="PAL DVD",
        resolution="576p",
        video_encode="x264",
        audio="AAC",
        trump_reason="exact_match",
    )
    movie_name = (await tracker.get_name(movie))["name"]
    assert "576p" in movie_name
    assert "AACx264" in movie_name
    assert movie_name.endswith(" - TRUMP")

    tv = Meta(
        category="TV",
        type="DVDRIP",
        name="Show PAL DVD H264",
        source="PAL DVD",
        resolution="576p",
        video_codec="H264",
        audio="AAC",
    )
    tv_name = (await tracker.get_name(tv))["name"]
    assert "576p" in tv_name
    assert "AAC H264" in tv_name

    untouched = Meta(category="MOVIE", type="WEBDL", name="Release")
    assert tracker._dvdrip_name(untouched, "Release") == "Release"


def test_lst_book_media_and_scan_edges() -> None:
    tracker = _tracker()
    no_media = Meta(mediainfo={"media": []})
    assert tracker._book_media_tracks(no_media) == []
    assert (
        tracker._first_audio_track(Meta(mediainfo={"media": {"track": []}}))
        == {}
    )
    assert (
        tracker._first_audio_track(
            Meta(mediainfo={"media": {"track": ["invalid"]}})
        )
        == {}
    )
    assert tracker._ebook_scan_type(Meta(source="SCAN", ocr=False)) == "SCAN"


def test_lst_book_additional_data_helper_edges() -> None:
    tracker = _tracker()
    meta = Meta(
        openlibrary_id="OL42M",
        isbn="123",
        extra_openlibrary_ids="extra",
    )
    data = tracker._book_additional_data(meta)
    assert data["openlibrary_book_id"] == "OL42M"
    assert data["extra_openlibrary_ids"] == "extra"
