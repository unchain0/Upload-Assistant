from __future__ import annotations

import pytest

from src.meta import Meta
from src.trackers.UNIT3D.zenith import Zenith

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
    return Zenith({"TRACKERS": {}})


@pytest.mark.parametrize("author", _BANNED_AUTHORS)
def test_zenith_rejects_every_banned_author(author: str) -> None:
    assert _tracker()._is_banned_author(author) is True


@pytest.mark.parametrize(
    "author",
    ("Ursula K. Le Guin", "J. R. R. Martin", "Ana Maria Machado"),
)
def test_zenith_allows_authors_outside_the_banned_list(author: str) -> None:
    assert _tracker()._is_banned_author(author) is False
