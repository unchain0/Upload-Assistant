import asyncio

import pytest

from src.domain_models.release import Meta
from src.integrations.filesystem.tags import get_tag
from src.integrations.trackers.UNIT3D.yuscene import YUSCENE
from src.services.episode_service import SeasonEpisodeManager
from src.services.release_naming_service import NameManager


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("To Philly with Love 2026 1080p WEB-DL HEVC x265 BONE.mkv", "-BONE"),
        ("To Philly with Love 2026 1080p WEB-DL HEVC x265.mkv", ""),
        ("To Philly with LOVE.mkv", ""),
        ("Movie.2026.1080p.WEB-DL.H.265-GROUP.mkv", "-GROUP"),
    ],
)
def test_get_tag_handles_known_space_separated_release_groups(filename: str, expected: str) -> None:
    meta = Meta(category="MOVIE")

    assert asyncio.run(get_tag(filename, meta)) == expected


def test_prefixed_group_flows_through_episode_parser_naming_and_yuscene() -> None:
    filename = "[Gecko]_False_Memory_-_S01E05_[BILI.WEB-DL_1080P_HEVC_AAC_D-SUB][5A86C56D].mkv"
    meta = Meta(
        path=filename,
        filename=filename,
        uuid=filename,
        filelist=[filename],
        category="TV",
        anime=False,
        title="False Memory",
        aka="AKA Jiyi Guanli Ju",
        year=2026,
        search_year=2026,
        type="WEBDL",
        resolution="1080p",
        audio="AAC 2.0",
        video_encode="H.265",
        video_codec="HEVC",
        source="Web",
        service="",
        uhd="",
        hdr="",
        webdv=False,
        repack="",
        edition="",
        part="",
        three_d="",
        trackers=[],
    )

    meta = asyncio.run(SeasonEpisodeManager({"DEFAULT": {"tmdb_api": "test-key"}}).get_season_episode(filename, meta))
    meta.tag = asyncio.run(get_tag(filename, meta))
    meta.name_notag, meta.name, meta.clean_name, meta.potential_missing = asyncio.run(NameManager({}).get_name(meta))

    assert (meta.tag, meta.season, meta.episode) == ("-Gecko", "S01", "E05")
    assert meta.name == "False Memory 2026 AKA Jiyi Guanli Ju S01E05 1080p WEB-DL AAC 2.0 H.265-Gecko"

    tracker = YUSCENE({"DEFAULT": {}, "TRACKERS": {"YUSCENE": {}}})
    assert asyncio.run(tracker.get_name(meta)) == {"name": "False Memory 2026 AKA Jiyi Guanli Ju S01E05 1080p WEB-DL AAC 2 0 H 265-Gecko"}


def test_space_separated_group_is_preserved_in_generated_release_name() -> None:
    filename = "To Philly with Love 2026 1080p WEB-DL HEVC x265 BONE.mkv"
    meta = Meta(
        category="MOVIE",
        title="To Philly with Love",
        year=2026,
        type="WEBDL",
        resolution="1080p",
        uhd="",
        service="",
        audio="AAC 2.0",
        video_encode="H.265",
    )
    meta.tag = asyncio.run(get_tag(filename, meta))

    _name_notag, name, _clean_name, _missing = asyncio.run(NameManager({}).get_name(meta))

    assert name == "To Philly with Love 2026 1080p WEB-DL AAC 2.0 H.265-BONE"
