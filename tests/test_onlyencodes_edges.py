from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D import onlyencodes as onlyencodes_module
from src.integrations.trackers.UNIT3D.onlyencodes import OnlyEncodes


def _tracker() -> OnlyEncodes:
    return OnlyEncodes({"DEFAULT": {}, "TRACKERS": {"ONLYENCODES": {}}})


def test_onlyencodes_rejects_adult_media_when_confirmation_fails() -> None:
    tracker = _tracker()
    tracker.common.check_and_confirm_adult_media_upload = AsyncMock(
        return_value=False
    )  # type: ignore[method-assign]

    assert not asyncio.run(tracker.get_additional_checks(Meta()))


def test_onlyencodes_uses_imdb_title_aka_and_year() -> None:
    tracker = _tracker()
    meta = Meta(
        name="Local AKA 2020 1080p WEB-DL H264-GROUP",
        title="Local",
        aka="AKA",
        year=2020,
        category="MOVIE",
        resolution="1080p",
        type="WEBDL",
        source="WEB",
        video_encode="",
        video_codec="H264",
        audio="AAC 2.0",
        imdb_info={"title": "Canonical", "aka": "Alternate", "year": "2021"},
        audio_languages=["English"],
        basename_no_ext="Local",
        tag="-GROUP",
    )

    name = asyncio.run(tracker.get_name(meta))["name"]

    assert name == "Canonical AKA Alternate 2021 1080p WEB-DL H264-GROUP"


def test_onlyencodes_tv_dvdrip_rewrites_source_and_audio() -> None:
    tracker = _tracker()
    meta = Meta(
        name="Show DVD H264-GROUP",
        title="Show",
        category="TV",
        resolution="480p",
        type="DVDRIP",
        source="DVD",
        video_encode="x264",
        video_codec="H264",
        audio="AAC 2.0",
        imdb_info={},
        audio_languages=["English"],
        basename_no_ext="Show",
        tag="-GROUP",
    )

    name = asyncio.run(tracker.get_name(meta))["name"]

    assert "480p" in name
    assert "AAC 2.0 H264" in name


def test_onlyencodes_marks_foreign_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        onlyencodes_module.languages_manager,
        "has_english_language",
        AsyncMock(return_value=False),
    )
    meta = Meta(
        name="Movie 1080p WEB-DL H264-GROUP",
        title="Movie",
        category="MOVIE",
        resolution="1080p",
        type="WEBDL",
        source="WEB",
        video_encode="",
        video_codec="H264",
        audio="AAC 2.0",
        imdb_info={},
        audio_languages=["French"],
        basename_no_ext="Movie",
        tag="-GROUP",
    )

    assert "FRENCH 1080p" in asyncio.run(tracker.get_name(meta))["name"]


def test_onlyencodes_type_mapping_for_dvdrip_and_modern_codecs() -> None:
    tracker = _tracker()
    cases = (
        ("DVDRIP", "HEVC", "10"),
        ("ENCODE", "AV1", "14"),
        ("WEBRIP", "AVC", "15"),
    )
    for release_type, codec, expected in cases:
        assert asyncio.run(
            tracker.get_type_id(Meta(type=release_type, video_codec=codec))
        ) == {"type_id": expected}
