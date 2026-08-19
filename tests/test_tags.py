import asyncio

import pytest

from src.domain_models.release import Meta
from src.integrations.filesystem.tags import get_tag
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
