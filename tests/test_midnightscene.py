"""Regression tests for MidnightScene naming support."""

import asyncio

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.midnightscene import MidnightScene


def _tracker() -> MidnightScene:
    return MidnightScene({"DEFAULT": {}, "TRACKERS": {"MIDNIGHTSCENE": {}}})


@pytest.mark.parametrize("screens", [None, "invalid", float("inf")])
def test_midnightscene_rejects_malformed_screenshot_counts(screens):
    meta = Meta(category="MOVIE", screens=screens, unattended=True, unattended_confirm=False)

    assert asyncio.run(_tracker().get_additional_checks(meta)) is False


def test_midnightscene_music_category_and_format_type_ids():
    tracker = _tracker()
    meta = Meta(category="MUSIC", format="FLAC")

    assert asyncio.run(tracker.get_category_id(meta)) == {"category_id": "3"}
    assert asyncio.run(tracker.get_type_id(meta)) == {"type_id": "8"}
    assert asyncio.run(tracker.get_type_id(Meta(category="MUSIC", format="MP3"))) == {"type_id": "7"}


def test_midnightscene_scene_music_name_replaces_only_underscores():
    meta = Meta(
        category="MUSIC",
        scene=True,
        scene_name="The_Longing_Ghost-Estuary-(TLG03)-WEB-2025-BABAS",
    )

    assert asyncio.run(_tracker().get_name(meta)) == {"name": "The Longing Ghost-Estuary-(TLG03)-WEB-2025-BABAS"}


def test_midnightscene_non_scene_music_name_uses_directory_style():
    meta = Meta(
        category="MUSIC",
        music_release={
            "fields": {
                "artist": {"value": "Björk"},
                "album": {"value": "Vespertine"},
                "release_year": {"value": "2001"},
                "release_catalogue_number": {"value": "TPLP101CD"},
                "media": {"value": "CD"},
                "format": {"value": "FLAC"},
            }
        },
    )

    assert asyncio.run(_tracker().get_name(meta)) == {"name": "Björk - Vespertine (2001) [TPLP101CD] [CD - FLAC]"}


def test_midnightscene_removes_dual_audio_without_english_audio():
    meta = Meta(
        category="TV",
        name="Example Show S01 1080p BluRay Dual-Audio FLAC 2.0 x265-ExampleGroup",
        resolution="1080p",
        type="ENCODE",
        audio_languages=["japanese", "portuguese"],
        language_checked=True,
    )

    assert asyncio.run(_tracker().get_name(meta)) == {"name": "Example Show S01 JAPANESE 1080p BluRay FLAC 2.0 x265-ExampleGroup"}


def test_midnightscene_keeps_dual_audio_with_english_audio():
    meta = Meta(
        category="TV",
        name="Example Show S01 1080p BluRay Dual-Audio FLAC 2.0 x265-ExampleGroup",
        resolution="1080p",
        type="ENCODE",
        audio_languages=["japanese", "english"],
        language_checked=True,
    )

    assert asyncio.run(_tracker().get_name(meta)) == {"name": "Example Show S01 1080p BluRay Dual-Audio FLAC 2.0 x265-ExampleGroup"}


def test_midnightscene_reorders_year_before_aka_for_tv_names():
    meta = Meta(
        category="TV",
        name="Shrouding the Heavens 2023 AKA Zhe Tian S01E175 2160p WEB-DL DD+ 2.0 H.265-QHstudIo",
        title="Shrouding the Heavens",
        aka="AKA Zhe Tian",
        year=2023,
        resolution="2160p",
        type="WEBDL",
        audio_languages=["chinese"],
        language_checked=True,
    )

    assert asyncio.run(_tracker().get_name(meta)) == {"name": "Shrouding the Heavens AKA Zhe Tian 2023 S01E175 CHINESE 2160p WEB-DL DD+ 2.0 H.265-QHstudIo"}


def test_midnightscene_does_not_treat_dvdrip_as_an_unofficial_source():
    meta = Meta(
        category="MOVIE",
        name="The Green Slime 1968 NTSC x264 DVDRip DD 2.0",
        uuid="The.Green.Slime.1968.DVDRip.x264-angrybunny-[CG].mkv",
    )

    assert MidnightScene._contains_unofficial_release_tag(meta) is False


@pytest.mark.parametrize("resolution", ["360p", "360i"])
def test_midnightscene_requires_confirmation_for_every_resolution_below_720p(resolution: str):
    meta = Meta(
        category="MOVIE",
        name="Example Movie",
        resolution=resolution,
        screens=3,
        unattended=True,
        unattended_confirm=False,
    )

    assert asyncio.run(_tracker().get_additional_checks(meta)) is False
