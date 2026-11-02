from __future__ import annotations

from src.services.book_preparation import book_identity_from_path


def test_folder_author_disambiguates_title_author(tmp_path) -> None:  # type: ignore[no-untyped-def]
    book = tmp_path / "Brené Brown" / "Daring Greatly (2012)" / "Daring Greatly - Brene Brown PhD, LMSW.m4b"
    book.parent.mkdir(parents=True)
    book.write_bytes(b"x")
    author, title = book_identity_from_path(str(book))
    assert author == "Brene Brown PhD, LMSW"
    assert title == "Daring Greatly"


def test_author_title_order_still_works(tmp_path) -> None:  # type: ignore[no-untyped-def]
    book = tmp_path / "Author Name" / "Title (2020)" / "Author Name - Title.epub"
    book.parent.mkdir(parents=True)
    book.write_bytes(b"x")
    author, title = book_identity_from_path(str(book))
    assert author == "Author Name"
    assert title == "Title"
