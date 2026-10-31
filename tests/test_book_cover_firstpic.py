from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from src.domain_models.release import Meta
from src.integrations.image_hosts import rehosting
from src.integrations.trackers.digitalcore import DigitalCore


def _write_cover(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (4, 4), (200, 30, 30)).save(path, "PNG")
    return path


def _meta(tmp_path: Path, **overrides: object) -> Meta:
    values: dict[str, object] = {
        "base_dir": str(tmp_path),
        "uuid": "release",
        "category": "BOOK",
    }
    values.update(overrides)
    return Meta(**values)


def _tracker() -> DigitalCore:
    tracker = DigitalCore.__new__(DigitalCore)
    tracker.tracker = "DigitalCore"
    tracker.config = {"TRACKERS": {"DigitalCore": {}}}
    tracker.rehost_images_manager = AsyncMock()
    tracker.debug = False
    tracker.image_host_policy = DigitalCore.image_host_policy
    return tracker


def test_book_cover_screens_prefer_the_prepared_artwork(
    tmp_path: Path,
) -> None:
    prepared = _write_cover(
        tmp_path / "tmp" / "release" / "artwork" / "POSTER.png"
    )
    meta = _meta(tmp_path, artwork_path=str(prepared))

    screens = asyncio.run(rehosting._cover_screens(meta))

    assert screens == [str(prepared)]


def test_book_cover_screens_use_the_artwork_poster_without_metadata(
    tmp_path: Path,
) -> None:
    poster = _write_cover(
        tmp_path / "tmp" / "release" / "artwork" / "POSTER.png"
    )
    meta = _meta(tmp_path)

    assert asyncio.run(rehosting._cover_screens(meta)) == [str(poster)]


def test_cover_screens_ignore_book_artwork_for_video(tmp_path: Path) -> None:
    _write_cover(tmp_path / "tmp" / "release" / "artwork" / "POSTER.png")
    meta = _meta(tmp_path, category="MOVIE")

    assert asyncio.run(rehosting._cover_screens(meta)) == []


def test_book_cover_screens_skip_an_invalid_prepared_cover(
    tmp_path: Path,
) -> None:
    broken = tmp_path / "tmp" / "release" / "artwork" / "POSTER.png"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_bytes(b"not an image")
    meta = _meta(tmp_path, artwork_path=str(broken))

    assert asyncio.run(rehosting._cover_screens(meta)) == []


@pytest.mark.asyncio
async def test_fetch_data_refuses_a_book_without_a_cover() -> None:
    tracker = _tracker()
    tracker.generate_description = AsyncMock(return_value="description")
    tracker.mediainfo = AsyncMock(return_value="mediainfo")
    tracker.get_firstpic = AsyncMock(return_value="")
    meta = Meta(category="BOOK", resolution="1080p", imdb_tt="")

    with pytest.raises(ValueError, match="firstpic is empty"):
        await tracker.fetch_data(meta)


@pytest.mark.asyncio
async def test_upload_reports_the_missing_book_cover() -> None:
    tracker = _tracker()
    tracker.fetch_data = AsyncMock(
        side_effect=ValueError("firstpic is empty for a book")
    )
    meta = Meta(category="BOOK", resolution="1080p")

    assert await tracker.upload(meta) is False
    assert (
        "firstpic is empty"
        in meta.tracker_status["DigitalCore"]["status_message"]
    )


def test_music_without_a_cover_still_uploads() -> None:
    tracker = _tracker()
    tracker._require_book_cover(Meta(category="MUSIC"), "")


def test_cover_cleanup_keeps_the_prepared_book_artwork(
    tmp_path: Path,
) -> None:
    prepared = _write_cover(
        tmp_path / "tmp" / "release" / "artwork" / "POSTER.png"
    )
    disposable = tmp_path / "tmp" / "release" / "artwork" / "cover_1.jpg"
    _write_cover(disposable)
    meta = _meta(tmp_path, artwork_path=str(prepared))

    rehosting._cleanup_cover_files(meta, [str(prepared), str(disposable)])

    assert prepared.exists()
    assert not disposable.exists()
