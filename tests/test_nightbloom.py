from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.domain_models.release import Meta
from src.domain_models.tracker_catalog import TRACKER_DEFINITIONS
from src.integrations.trackers.registry import tracker_class_map
from src.integrations.trackers.UNIT3D import UNIT3D
from src.integrations.trackers.UNIT3D.nightbloom import Nightbloom


def _tracker(**options: object) -> Nightbloom:
    return Nightbloom({"DEFAULT": {}, "TRACKERS": {"NIGHTBLOOM": options}})


def test_nightbloom_is_registered_and_has_a_canonical_alias() -> None:
    assert tracker_class_map["NIGHTBLOOM"] is Nightbloom
    assert TRACKER_DEFINITIONS["NIGHTBLOOM"].auth_type == "unit3d_api"
    assert Meta.canonical_tracker_name("nig") == "NIGHTBLOOM"


@pytest.mark.parametrize(
    ("category", "anime", "expected"),
    [("MOVIE", False, "1"), ("TV", False, "2"), ("TV", True, "6")],
)
def test_category_ids(category: str, anime: bool, expected: str) -> None:
    meta = Meta(category=category, anime=anime)
    assert asyncio.run(_tracker().get_category_id(meta)) == {
        "category_id": expected
    }


def test_name_preserves_group_release_with_scene_punctuation() -> None:
    meta = Meta(
        category="TV",
        scene_name="Fiction.Show.S01E02.1080p.WEB-DL.H.264-GROUP",
        basename_no_ext="Fiction.Show.S01E02",
        tag="-GROUP",
    )

    assert asyncio.run(_tracker().get_name(meta)) == {
        "name": "Fiction Show S01E02 1080p WEB-DL H.264-GROUP"
    }


def test_single_episode_in_folder_uses_episode_filename() -> None:
    meta = Meta(
        category="TV",
        name="Fiction Show S01 720p WEB-DL-GROUP",
        basename_no_ext="Fiction.Show.S01.720p.WEB-DL-GROUP",
        isdir=True,
        season="S01",
        episode="E02",
        filelist=[
            "/library/Fiction.Show.S01-GROUP/Fiction.Show.S01E02.Episode.Title.720p.WEB-DL-GROUP.mkv"
        ],
        tag="-GROUP",
    )

    assert asyncio.run(_tracker().get_name(meta)) == {
        "name": "Fiction Show S01E02 Episode Title 720p WEB-DL-GROUP"
    }


def test_season_pack_uses_folder_name() -> None:
    meta = Meta(
        category="TV",
        name="Fiction Show S01 720p WEB-DL-GROUP",
        basename_no_ext="Fiction.Show.S01.720p.WEB-DL-GROUP",
        isdir=True,
        tv_pack=True,
        season="S01",
        filelist=[
            "/library/Fiction.Show.S01-GROUP/Fiction.Show.S01E01.720p.WEB-DL-GROUP.mkv"
        ],
        tag="-GROUP",
    )

    assert asyncio.run(_tracker().get_name(meta)) == {
        "name": "Fiction Show S01 720p WEB-DL-GROUP"
    }


@pytest.mark.parametrize(
    ("media_type", "expected"),
    [
        ("DISC", "1"),
        ("REMUX", "2"),
        ("ENCODE", "3"),
        ("WEBDL", "4"),
        ("WEBRIP", "5"),
        ("HDTV", "6"),
    ],
)
def test_type_ids(media_type: str, expected: str) -> None:
    assert asyncio.run(_tracker().get_type_id(Meta(type=media_type))) == {
        "type_id": expected
    }


@pytest.mark.parametrize(
    ("resolution", "expected"),
    [
        ("4320p", "1"),
        ("2160p", "2"),
        ("1080p", "3"),
        ("1080i", "4"),
        ("720p", "5"),
        ("576p", "6"),
        ("576i", "7"),
        ("480p", "8"),
        ("480i", "9"),
        ("OTHER", "10"),
    ],
)
def test_resolution_ids(resolution: str, expected: str) -> None:
    assert asyncio.run(
        _tracker().get_resolution_id(Meta(resolution=resolution))
    ) == {"resolution_id": expected}


@pytest.mark.parametrize(
    ("basename", "expected"),
    [
        (
            "The.Act.S01.BluRay.1080p.DTS-HD.MA.5.1.AVC.REMUX-FraMeSToR",
            "The Act S01 BluRay 1080p DTS-HD MA 5.1 AVC REMUX-FraMeSToR",
        ),
        (
            "Blade.Runner.2049.2017.2160p.AMZN.WEB-DL.DDP5.1.H.265-GROUP",
            "Blade Runner 2049 2017 2160p AMZN WEB-DL DDP5.1 H.265-GROUP",
        ),
    ],
)
def test_name_preserves_audio_and_codec_punctuation(
    basename: str, expected: str
) -> None:
    meta = Meta(
        category="TV",
        basename_no_ext=basename,
        tag=basename[basename.rfind("-") :],
    )
    assert asyncio.run(_tracker().get_name(meta)) == {"name": expected}


def test_name_falls_back_to_standard_name_when_group_is_missing() -> None:
    meta = Meta(
        category="TV",
        name="My Hero Academia S02 1080p BluRay REMUX AVC TrueHD 5.1-ZR",
        basename_no_ext="My hero Academia S02 BDREMUX",
        tag="-ZR",
    )
    assert asyncio.run(_tracker().get_name(meta)) == {
        "name": "My Hero Academia S02 1080p BluRay REMUX AVC TrueHD 5.1-ZR"
    }


def test_upload_payload_contains_only_fields_supported_by_nightbloom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def base_data(_self: UNIT3D, _meta: Meta) -> dict[str, Any]:
        return {
            "name": "Fiction 2025 1080p WEB-DL-GROUP",
            "category_id": "1",
            "type_id": "4",
            "resolution_id": "3",
            "anonymous": "0",
            "featured": "1",
            "free": "100",
        }

    monkeypatch.setattr(UNIT3D, "get_data", base_data)
    payload = asyncio.run(_tracker().get_data(Meta(category="MOVIE")))

    assert payload == {
        "name": "Fiction 2025 1080p WEB-DL-GROUP",
        "category_id": "1",
        "type_id": "4",
        "resolution_id": "3",
        "anonymous": "0",
    }
