"""Regression tests for explicit content categories."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.domain_models.processing import ItemProcessingError
from src.domain_models.release import Meta
from src.integrations.media.video import video_manager
from src.services.preparation_helpers import detect_disc_and_category
from src.services.preparation_service import Prep


def test_manual_music_category_routes_to_music_before_media_processing(
    tmp_path,
):
    album = tmp_path / "Artist - Album"
    album.mkdir()
    (album / "01 - Track.flac").write_bytes(b"audio")
    meta = Meta(path=str(album), manual_category="music")
    prep = SimpleNamespace(
        disc_info_manager=SimpleNamespace(
            get_disc=AsyncMock(return_value=("", str(album), {}, []))
        )
    )

    asyncio.run(detect_disc_and_category(prep, meta))

    assert meta.category == "MUSIC"


def test_manual_podcast_category_routes_before_media_processing(tmp_path):
    episode = tmp_path / "episode.mp3"
    episode.write_bytes(b"audio")
    meta = Meta(path=str(episode), manual_category="podcast")
    prep = SimpleNamespace(
        disc_info_manager=SimpleNamespace(
            get_disc=AsyncMock(return_value=("", str(episode), {}, []))
        )
    )

    asyncio.run(detect_disc_and_category(prep, meta))

    assert meta.category == "PODCAST"


def test_podcast_symlinks_are_rejected_before_disc_detection(tmp_path):
    podcast = tmp_path / "podcast"
    outside = tmp_path / "outside"
    podcast.mkdir()
    outside.mkdir()
    (podcast / "BDMV").symlink_to(outside, target_is_directory=True)
    prep = Prep.__new__(Prep)
    prep.config = {"DEFAULT": {}}
    prep.publish_preview = None
    meta = Meta(
        manual_category="podcast",
        path=str(podcast),
        base_dir=str(tmp_path),
        uuid="podcast-disc-symlink",
    )
    disc_detection = AsyncMock()

    with (
        patch(
            "src.services.preparation_service.prep_helpers.init_meta",
            return_value=(False, False, object(), False, [], []),
        ),
        patch(
            "src.services.preparation_service.prep_helpers.detect_disc_and_category",
            new=disc_detection,
        ),
        pytest.raises(ValueError, match="symbolic links"),
    ):
        asyncio.run(prep.gather_prep(meta, "cli"))

    disc_detection.assert_not_awaited()


def test_missing_cli_video_reports_item_level_failure_in_batch(tmp_path):
    with pytest.raises(ItemProcessingError, match="No Video files found"):
        asyncio.run(video_manager.get_video(str(tmp_path), "cli"))


def test_archive_only_video_reports_safe_item_level_failure(tmp_path):
    (tmp_path / "Release.rar").write_bytes(b"archive")
    (tmp_path / "Release.r00").write_bytes(b"part")
    sample = tmp_path / "Sample"
    sample.mkdir()
    (sample / "sample.mkv").write_bytes(b"sample")

    with pytest.raises(
        ItemProcessingError, match="Video exists only inside an archive"
    ):
        asyncio.run(video_manager.get_video(str(tmp_path), "cli"))


def test_folder_scan_includes_avi_files(tmp_path: Path) -> None:
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    expected = media_dir / "movie.avi"
    expected.write_bytes(b"avi data")

    path, filelist = asyncio.run(
        video_manager.get_video(str(media_dir), "cli")
    )

    assert path == str(expected)
    assert filelist == [str(expected)]
