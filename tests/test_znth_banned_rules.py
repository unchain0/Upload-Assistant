from __future__ import annotations

import asyncio

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.znth import Zenith

_BANNED_AUTHORS = (
    "J.R.R. Tolkien",
    "Anne Perry",
    "Simon Scarrow",
    "Sara Gruen",
    "Joan Elliott",
    "Alan Dart",
    "Chris Mead",
    "Paul Moore & Gavin Jones",
    "Noah K Sturdevant",
    "Benedict Brown",
    "Erika T Wurth",
    "Randolph Lalonde",
    "Andrea Sfiligoi",
    "Ana-Maria Babanica",
)


def _tracker() -> Zenith:
    return Zenith({"DEFAULT": {}, "TRACKERS": {"ZENITH": {}}})


@pytest.mark.parametrize("author", _BANNED_AUTHORS)
def test_zenith_rejects_every_banned_author(author: str) -> None:
    assert _tracker()._is_banned_author(author) is True


@pytest.mark.parametrize(
    "author",
    ("Ursula K. Le Guin", "J. R. R. Martin", "Ana Maria Machado"),
)
def test_zenith_allows_authors_outside_the_banned_list(author: str) -> None:
    assert _tracker()._is_banned_author(author) is False


def test_zenith_rejects_the_banned_book_work_case_insensitively() -> None:
    tracker = _tracker()
    meta = Meta(
        category="BOOK",
        title="Four Against Darkness Expanded Edition",
        author="Andrea Sfiligoi",
        type="PDF",
    )
    assert tracker._is_banned_book_work(meta) is True
    assert (
        tracker._book_work_policy(
            Meta(
                category="BOOK",
                title="four against darkness expanded edition",
                type="PDF",
            )
        )
        is False
    )


def test_zenith_allows_a_book_outside_the_banned_work_list() -> None:
    meta = Meta(
        category="BOOK",
        title="The Left Hand of Darkness",
        author="Ursula K. Le Guin",
        type="EPUB",
    )
    assert _tracker()._is_banned_book_work(meta) is False


def test_zenith_blocks_a_banned_author_in_the_full_book_policy() -> None:
    meta = Meta(
        category="BOOK",
        title="Any Title",
        author="Anne Perry",
        type="EPUB",
        isbn="9780000000000",
    )
    assert asyncio.run(_tracker()._book_upload_policy(meta, ["book.epub"])) is False
