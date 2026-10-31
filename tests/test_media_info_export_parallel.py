from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from src.integrations.media import media_info_export


def _write_reports_fixture(tmp_path: Path) -> Path:
    media_file = tmp_path / "Example.Movie.2001.1080p.mkv"
    media_file.write_bytes(b"movie")
    (tmp_path / "tmp" / "release").mkdir(parents=True)
    return media_file


def _fake_parse(delay: float):
    def fake_parse(_video: str, **kwargs: object) -> str:
        time.sleep(delay)
        if kwargs.get("output") == "JSON":
            return '{"media": {"track": [{"@type": "General"}]}}'
        return "General\n"

    return fake_parse


def test_export_info_runs_text_and_json_reports_concurrently(
    tmp_path: Path,
) -> None:
    media_file = _write_reports_fixture(tmp_path)
    barrier = threading.Barrier(2, timeout=5)

    def fake_parse(video: str, **kwargs: object) -> str:
        barrier.wait()
        if kwargs.get("output") == "JSON":
            return '{"media": {"track": [{"@type": "General", "Format": "Matroska"}]}}'
        return f"General\nComplete name : {video}\n"

    with patch(
        "src.integrations.media.media_info_export.MediaInfo.parse",
        side_effect=fake_parse,
    ):
        result = asyncio.run(
            media_info_export.export_info(
                str(media_file), True, "release", str(tmp_path)
            )
        )

    assert result["media"]["track"][0]["Format"] == "Matroska"
    report = (tmp_path / "tmp" / "release" / "MEDIAINFO.txt").read_text(
        encoding="utf-8"
    )
    assert report == f"General\nComplete name : {media_file.name}\n"
    exported = json.loads(
        (tmp_path / "tmp" / "release" / "MediaInfo.json").read_text(
            encoding="utf-8"
        )
    )
    assert exported["media"]["track"][0]["Format"] == "Matroska"


def test_export_info_warns_when_mediainfo_is_slow(tmp_path: Path) -> None:
    media_file = _write_reports_fixture(tmp_path)

    with (
        patch(
            "src.integrations.media.media_info_export.MEDIAINFO_SLOW_NOTICE_SECONDS",
            0.05,
        ),
        patch(
            "src.integrations.media.media_info_export.MediaInfo.parse",
            side_effect=_fake_parse(0.3),
        ),
        patch("src.integrations.media.media_info_export.logger") as logger,
    ):
        asyncio.run(
            media_info_export.export_info(
                str(media_file), True, "release", str(tmp_path)
            )
        )

    logger.warning.assert_called_once()
    assert (
        "MediaInfo is still reading the file"
        in logger.warning.call_args.args[0]
    )
    assert (tmp_path / "tmp" / "release" / "MediaInfo.json").exists()


def test_export_info_does_not_warn_when_mediainfo_is_fast(
    tmp_path: Path,
) -> None:
    media_file = _write_reports_fixture(tmp_path)

    with (
        patch(
            "src.integrations.media.media_info_export.MediaInfo.parse",
            side_effect=_fake_parse(0),
        ),
        patch("src.integrations.media.media_info_export.logger") as logger,
    ):
        asyncio.run(
            media_info_export.export_info(
                str(media_file), True, "release", str(tmp_path)
            )
        )

    logger.warning.assert_not_called()


def test_export_info_waits_for_both_reports_before_raising(
    tmp_path: Path,
) -> None:
    media_file = _write_reports_fixture(tmp_path)
    json_finished = threading.Event()

    def fake_parse(_video: str, **kwargs: object) -> str:
        if kwargs.get("output") == "JSON":
            time.sleep(0.2)
            json_finished.set()
            return '{"media": {"track": [{"@type": "General"}]}}'
        raise RuntimeError("text report failed")

    async def run_export() -> bool:
        with pytest.raises(RuntimeError, match="text report failed"):
            await media_info_export.export_info(
                str(media_file), True, "release", str(tmp_path)
            )
        return json_finished.is_set()

    with patch(
        "src.integrations.media.media_info_export.MediaInfo.parse",
        side_effect=fake_parse,
    ):
        assert asyncio.run(run_export())
