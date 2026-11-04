from __future__ import annotations

import pytest

from src.domain_models.release import Meta
from src.integrations.image_hosts.rehosting import (
    BookScreenshotsMissingError,
    _require_hosted_book_images,
)


def _hosted(count: int) -> list[dict[str, str]]:
    return [{"img_url": f"https://img.example/{index}.png"} for index in range(count)]


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
