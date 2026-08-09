# ruff: noqa: S101

import asyncio

import pytest

from src.meta import Meta
from src.trackers.UNIT3D.polishtorrent import PolishTorrent


def _movie_meta(**kwargs):
    values = {
        "category": "MOVIE",
        "filelist": ["Example.Movie.2024.mkv"],
        "image_list": [],
        "mediainfo": {"media": {"track": []}},
        "name": "Example Movie 2024 1080p WEB-DL H264",
        "screens": 3,
        "unattended": True,
        "unattended_confirm": False,
    }
    values.update(kwargs)
    return Meta(**values)


def test_polishtorrent_accepts_png_and_tiff_screenshot_links():
    image = {
        "raw_url": "https://images.example/full.png",
        "web_url": "https://images.example/view",
        "img_url": "https://images.example/thumb.tiff",
    }

    assert PolishTorrent._is_allowed_screenshot_image(image) is True
    assert PolishTorrent._has_valid_screenshot_thumb_and_full(image) is True


def test_polishtorrent_rejects_mixed_or_malformed_screenshot_links():
    mixed = {"raw_url": "https://images.example/full.png", "img_url": "https://images.example/thumb.jpg"}
    malformed = {"raw_url": "http://[", "img_url": "https://images.example/thumb.png"}

    assert PolishTorrent._is_allowed_screenshot_image(mixed) is False
    assert PolishTorrent._is_allowed_screenshot_image(malformed) is False


def test_polishtorrent_rejects_extensionless_screenshot_links_through_public_validation():
    image = {"raw_url": "https://images.example/full", "img_url": "https://images.example/thumb"}

    assert PolishTorrent._is_allowed_screenshot_image(image) is False
    assert asyncio.run(PolishTorrent({"TRACKERS": {}}).get_additional_checks(_movie_meta(image_list=[image]))) is False


def test_polishtorrent_rejects_malformed_filelist_and_screenshot_counts():
    tracker = PolishTorrent({"TRACKERS": {}})

    assert asyncio.run(tracker.get_additional_checks(_movie_meta(filelist=1))) is False
    assert asyncio.run(tracker.get_additional_checks(_movie_meta(screens=None))) is False
    assert asyncio.run(tracker.get_additional_checks(_movie_meta(screens="2"))) is False


@pytest.mark.parametrize(
    "url",
    ["not-a-url.png", "/relative/full.png", "ftp://images.example/full.png", "https:///full.png", "not a URL"],
)
def test_polishtorrent_rejects_non_http_absolute_screenshot_links(url):
    assert PolishTorrent._is_allowed_screenshot_image({"raw_url": url}) is False


def test_polishtorrent_tracker_detection_ignores_url_paths_but_rejects_domains():
    assert PolishTorrent._contains_other_tracker_mention("https://images.example/kat/rutracker/screen.png") is False
    assert PolishTorrent._contains_other_tracker_mention("//rutracker.net./release") is True
    assert PolishTorrent._contains_other_tracker_mention("https://limetorrents.cc/release") is True
    assert PolishTorrent._contains_other_tracker_mention("https://[") is False
    assert PolishTorrent._contains_other_tracker_mention("mirrored from YIFY") is True
