# ruff: noqa: S101

import asyncio

from src.exportmi import mi_resolution
from src.video import VideoManager


def test_get_hdr_returns_empty_when_mediainfo_has_no_video_track() -> None:
    mediainfo = {"media": {"track": [{"@type": "General", "Format": "DMG"}]}}

    assert asyncio.run(VideoManager().get_hdr(mediainfo, None)) == ""


def test_resolution_honors_compatible_576p_release_label() -> None:
    resolution = asyncio.run(mi_resolution("1280x540p", {"screen_size": "576p"}, 1280, "p"))

    assert resolution == "576p"


def test_resolution_rejects_incompatible_release_label() -> None:
    resolution = asyncio.run(mi_resolution("1280x720p", {"screen_size": "576p"}, 1280, "p"))

    assert resolution == "720p"
