from __future__ import annotations

from src.services.book_preparation import (
    _author_identity_tokens,
    _normalized_book_identity,
)


def test_accented_and_plain_author_match() -> None:
    assert _author_identity_tokens("Brené Brown") == _author_identity_tokens(
        "Brene Brown"
    )
    assert _normalized_book_identity("Brené") == _normalized_book_identity("Brene")
    assert _author_identity_tokens("García Márquez") == _author_identity_tokens(
        "Garcia Marquez"
    )


def test_different_authors_still_differ() -> None:
    assert _author_identity_tokens("Brené Brown").isdisjoint(
        _author_identity_tokens("Malcolm Gladwell")
    )
