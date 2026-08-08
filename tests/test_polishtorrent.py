# ruff: noqa: S101

from src.trackers.UNIT3D.polishtorrent import PolishTorrent


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


def test_polishtorrent_tracker_detection_ignores_url_paths_but_rejects_domains():
    assert PolishTorrent._contains_other_tracker_mention("https://images.example/kat/rutracker/screen.png") is False
    assert PolishTorrent._contains_other_tracker_mention("//rutracker.net./release") is True
    assert PolishTorrent._contains_other_tracker_mention("mirrored from YIFY") is True
