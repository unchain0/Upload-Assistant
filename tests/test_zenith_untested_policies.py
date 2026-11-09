from __future__ import annotations

import asyncio

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.znth import Zenith


def _tracker() -> Zenith:
    return Zenith({"DEFAULT": {}, "TRACKERS": {"ZENITH": {}}})


def test_zenith_screenshot_policy_requires_three() -> None:
    tracker = _tracker()
    assert tracker._screenshot_policy(Meta(screens=3)) is True
    assert tracker._screenshot_policy(Meta(screens=2)) is False
    assert tracker._screenshot_policy(Meta(screens=None)) is False
    assert tracker._screenshot_policy(Meta(screens="4")) is True


def test_zenith_archive_policy_blocks_archives_and_multipart(
    caplog: pytest.LogCaptureFixture,
) -> None:
    tracker = _tracker()
    assert tracker._archive_policy(["Movie.2024.mkv"]) is True
    assert tracker._archive_policy(["Movie.2024.rar"]) is False
    assert tracker._archive_policy(["Movie.2024.r00"]) is False
    assert "Archive or multipart files are not allowed" in caplog.text


def test_zenith_contains_archive_file_reports_the_name() -> None:
    assert Zenith._contains_archive_file(["a.mkv", "b.zip"]) == "b.zip"
    assert Zenith._contains_archive_file(["a.mkv"]) == ""


def test_zenith_book_search_adds_an_isbn_and_asin_query() -> None:
    tracker = _tracker()
    urls = asyncio.run(
        tracker.get_search_urls(
            Meta(category="BOOK", isbn="9780000000000", asin="B000000000"),
            [],
        )
    )
    assert (
        "https://znth.cx/api/torrents/filter",
        [("bookId", "9780000000000"), ("perPage", "100")],
        False,
    ) in urls
    assert (
        "https://znth.cx/api/torrents/filter",
        [("bookId", "B000000000"), ("perPage", "100")],
        False,
    ) in urls


def test_zenith_search_without_identifiers_keeps_the_base_urls() -> None:
    tracker = _tracker()
    urls = asyncio.run(
        tracker.get_search_urls(Meta(category="BOOK"), [])
    )
    assert all("bookId" not in dict(params) for _, params, _ in urls)
