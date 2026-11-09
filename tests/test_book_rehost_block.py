from __future__ import annotations

from pathlib import Path

import pytest

from src.domain_models.release import Meta
from src.integrations.image_hosts.rehosting import (
    BookScreenshotsMissingError,
    _require_hosted_book_images,
)


def _hosted(count: int) -> list[dict[str, str]]:
    return [
        {"img_url": f"https://img.example/{index}.png"}
        for index in range(count)
    ]


def test_book_rehost_with_fewer_than_three_urls_blocks() -> None:
    with pytest.raises(BookScreenshotsMissingError, match="hosted"):
        _require_hosted_book_images(Meta(category="BOOK"), _hosted(0))
    with pytest.raises(BookScreenshotsMissingError):
        _require_hosted_book_images(Meta(category="BOOK"), _hosted(2))


def test_three_hosted_urls_and_other_categories_pass() -> None:
    _require_hosted_book_images(Meta(category="BOOK"), _hosted(3))
    _require_hosted_book_images(Meta(category="MOVIE"), _hosted(0))
    _require_hosted_book_images(
        Meta(category="BOOK", audiobook=True), _hosted(0)
    )


def test_identical_screenshot_bytes_upload_once(tmp_path: Path) -> None:
    from src.integrations.image_hosts.rehosting import _unique_screenshot_files

    first = tmp_path / "old-0.png"
    second = tmp_path / "new-0.png"
    third = tmp_path / "new-1.png"
    first.write_bytes(b"same")
    second.write_bytes(b"same")
    third.write_bytes(b"other")
    assert _unique_screenshot_files([str(first), str(second), str(third)]) == [
        str(first),
        str(third),
    ]
