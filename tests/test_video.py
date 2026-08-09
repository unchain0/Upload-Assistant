# ruff: noqa: S101

import asyncio

from src.video import VideoManager


def test_get_hdr_returns_empty_when_mediainfo_has_no_video_track() -> None:
    mediainfo = {"media": {"track": [{"@type": "General", "Format": "DMG"}]}}

    assert asyncio.run(VideoManager().get_hdr(mediainfo, None)) == ""
