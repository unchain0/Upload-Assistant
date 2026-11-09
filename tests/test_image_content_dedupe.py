from __future__ import annotations

from pathlib import Path

from src.domain_models.release import Meta
from src.integrations.filesystem.content_digest import unique_by_content
from src.services.tracker_metadata_parser import _unique_images


def _write(path: Path, payload: bytes) -> str:
    path.write_bytes(payload)
    return str(path)


def test_identical_bytes_are_uploaded_once(tmp_path: Path) -> None:
    first = _write(tmp_path / "shot-1.png", b"same-bytes")
    second = _write(tmp_path / "shot-2.png", b"same-bytes")
    third = _write(tmp_path / "shot-3.png", b"other-bytes")
    fourth = _write(tmp_path / "shot-4.png", b"other-bytes")

    assert unique_by_content([first, second, third, fourth]) == [first, third]


def test_description_keeps_one_image_per_content_hash(tmp_path: Path) -> None:
    first = _write(tmp_path / "shot-1.png", b"same-bytes")
    second = _write(tmp_path / "shot-2.png", b"same-bytes")
    third = _write(tmp_path / "shot-3.png", b"other-bytes")
    fourth = _write(tmp_path / "shot-4.png", b"other-bytes")
    images = [
        {"raw_url": f"https://host/{index}.png", "local_file_path": path}
        for index, path in enumerate((first, second, third, fourth))
    ]

    unique = _unique_images(images, Meta(category="MOVIE"))

    assert [image["raw_url"] for image in unique] == [
        "https://host/0.png",
        "https://host/2.png",
    ]


def test_distinct_images_are_all_kept(tmp_path: Path) -> None:
    paths = [
        _write(tmp_path / f"shot-{index}.png", f"bytes-{index}".encode())
        for index in range(4)
    ]
    images = [
        {"raw_url": f"https://host/{index}.png", "local_file_path": path}
        for index, path in enumerate(paths)
    ]

    assert len(_unique_images(images, Meta(category="MOVIE"))) == 4
