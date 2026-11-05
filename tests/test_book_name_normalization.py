from __future__ import annotations

from src.domain_models.book_naming import (
    canonical_book_author,
    clean_book_title,
)
from src.domain_models.release import Meta


def test_author_repeated_punctuation_collapses() -> None:
    meta = Meta(book_author="Joseph T.. Salerno")
    assert canonical_book_author(meta) == "Joseph T. Salerno"


def test_joined_title_restores_the_subtitle_separator() -> None:
    assert (
        clean_book_title("DemocracyThe God That Failed")
        == "Democracy: The God That Failed"
    )


def test_surname_only_author_uses_the_full_lookup_name() -> None:
    meta = Meta(book_author="Hazlitt", lookup_author="Henry Hazlitt")
    assert canonical_book_author(meta) == "Henry Hazlitt"


def test_surname_only_author_keeps_the_suffix_from_lookup() -> None:
    meta = Meta(book_author="Thomas", lookup_author="Thomas E. Woods Jr.")
    assert canonical_book_author(meta) == "Thomas E. Woods Jr."


def test_camel_case_product_names_are_not_split() -> None:
    title = "PHP-MySQL - JavaScript All-in-One Guide"
    assert clean_book_title(title) == title
