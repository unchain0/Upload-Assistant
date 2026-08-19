import asyncio
from pathlib import Path
from typing import Any

from PIL import Image

from src.domain_models.release import Meta
from src.integrations.trackers.hdspace import HDSpace


def _tracker() -> HDSpace:
    return HDSpace({"TRACKERS": {"HDSPACE": {}}})


def _create_png(path: Path, width: int = 1280, height: int = 720) -> Path:
    image = Image.new("RGB", (width, height), color="black")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG")
    return path


def _screenshot_entry(path: Path, filename: str) -> dict[str, str]:
    return {
        "raw_url": f"https://example.com/{filename}",
        "local_file_path": str(path),
    }


def _make_meta(**overrides: Any) -> Meta:
    values: dict[str, Any] = {
        "category": "MOVIE",
        "resolution": "1080p",
        "video_codec": "H.264",
        "video_encode": "x264",
        "filelist": ["Movie.2024.mkv"],
        "image_list": [],
        "name": "Example Movie 2024 1080p WEB-DL H.264-GROUP",
    }
    values.update(overrides)
    return Meta(**values)


def test_hdspace_rejects_resolution_below_720p() -> None:
    assert not asyncio.run(_tracker().get_additional_checks(_make_meta(resolution="480p")))


def test_hdspace_rejects_forbidden_xvid_codecs() -> None:
    assert not asyncio.run(_tracker().get_additional_checks(_make_meta(video_codec="xvid", video_encode="xvid")))
    assert not asyncio.run(_tracker().get_additional_checks(_make_meta(video_codec="x264", video_encode="divx")))


def test_hdspace_rejects_rar_payload_files() -> None:
    assert not asyncio.run(_tracker().get_additional_checks(_make_meta(filelist=["Movie.2024.rar"])))


def test_hdspace_rejects_when_not_enough_screenshots() -> None:
    assert not asyncio.run(_tracker().get_additional_checks(_make_meta(image_list=[{"raw_url": "https://example.com/frame-1.png", "local_file_path": ""}])))


def test_hdspace_rejects_non_png_screenshots(tmp_path: Path) -> None:
    image_paths = {
        "0": _create_png(tmp_path / "a.png", 1280, 720),
        "1": _create_png(tmp_path / "b.png", 1280, 720),
        "2": _create_png(tmp_path / "c.png", 1920, 1080),
    }
    image_list = [
        _screenshot_entry(image_paths["0"], "frame-01.jpg"),
        _screenshot_entry(image_paths["1"], "frame-02.png"),
        _screenshot_entry(image_paths["2"], "frame-03.png"),
    ]
    assert not asyncio.run(_tracker().get_additional_checks(_make_meta(image_list=image_list)))


def test_hdspace_rejects_invalid_screenshot_width(tmp_path: Path) -> None:
    image_list = [
        _screenshot_entry(_create_png(tmp_path / "frame-1.png", 1280, 720), "frame-1.png"),
        _screenshot_entry(_create_png(tmp_path / "frame-2.png", 1366, 768), "frame-2.png"),
        _screenshot_entry(_create_png(tmp_path / "frame-3.png", 1920, 1080), "frame-3.png"),
    ]
    assert not asyncio.run(_tracker().get_additional_checks(_make_meta(image_list=image_list)))


def test_hdspace_allows_valid_screenshots_with_allowed_dimensions_and_format(tmp_path: Path) -> None:
    image_list = [
        _screenshot_entry(_create_png(tmp_path / "frame-1.png", 1280, 720), "frame-1.png"),
        _screenshot_entry(_create_png(tmp_path / "frame-2.png", 1920, 1080), "frame-2.png"),
        _screenshot_entry(_create_png(tmp_path / "frame-3.png", 3840, 2160), "frame-3.png"),
    ]
    assert asyncio.run(_tracker().get_additional_checks(_make_meta(image_list=image_list)))
