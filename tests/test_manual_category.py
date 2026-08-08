"""Regression tests for explicit content categories."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.meta import Meta
from src.exceptions import ItemProcessingError
from src.prep_helpers import detect_disc_and_category
from src.video import video_manager


def test_manual_music_category_routes_to_music_before_media_processing(tmp_path):
    album = tmp_path / "Artist - Album"
    album.mkdir()
    (album / "01 - Track.flac").write_bytes(b"audio")
    meta = Meta(path=str(album), manual_category="music")
    prep = SimpleNamespace(disc_info_manager=SimpleNamespace(get_disc=AsyncMock(return_value=("", str(album), {}, []))))

    asyncio.run(detect_disc_and_category(prep, meta))

    assert meta.category == "MUSIC"


def test_missing_cli_video_reports_item_level_failure_in_batch(tmp_path):
    with pytest.raises(ItemProcessingError, match="No Video files found"):
        asyncio.run(video_manager.get_video(str(tmp_path), "cli"))


def test_folder_scan_includes_avi_files(tmp_path: Path) -> None:
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    expected = media_dir / "movie.avi"
    expected.write_bytes(b"avi data")

    path, filelist = asyncio.run(video_manager.get_video(str(media_dir), "cli"))

    assert path == str(expected)
    assert filelist == [str(expected)]
