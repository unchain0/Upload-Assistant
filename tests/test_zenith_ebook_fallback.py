from __future__ import annotations

from pathlib import Path

from src.domain_models.release import Meta
from src.integrations.media.zentag import fallback_ebook_filename


def _meta(**values: object) -> Meta:
    defaults: dict[str, object] = {
        "category": "BOOK",
        "author": "J.K. Rowling",
        "title": "Harry Potter and the Goblet of Fire",
        "year": 2000,
        "book_language_iso": "eng",
        "isbn": "9781781100103",
        "book_series": "Harry Potter",
        "book_series_index": "4",
        "path": "/books/Harry Potter.epub",
    }
    defaults.update(values)
    return Meta(**defaults)


def test_fallback_filename_matches_the_wiki_pattern() -> None:
    assert fallback_ebook_filename(_meta()) == (
        "J.K. Rowling - Harry Potter #4 - Harry Potter and the Goblet of Fire "
        "(2000) [ENG 9781781100103].epub"
    )


def test_fallback_filename_applies_the_character_substitutions() -> None:
    name = fallback_ebook_filename(
        _meta(title="PHP: MySQL | JavaScript? <All-in-One> *Guide*")
    )
    assert name is not None
    assert name.startswith(
        "J.K. Rowling - Harry Potter #4 - PHP- MySQL - JavaScript All-in-One Guide"
    )


def test_fallback_filename_needs_the_identifier() -> None:
    assert fallback_ebook_filename(_meta(isbn="")) is None
    assert fallback_ebook_filename(_meta(year=None)) is None
    assert fallback_ebook_filename(_meta(book_language_iso="")) is None


def test_fallback_copies_the_ebook_without_touching_the_original(
    tmp_path: Path,
) -> None:
    from src.integrations.media.zentag import _fallback_ebook_copy

    source = tmp_path / "original.epub"
    source.write_bytes(b"book")
    meta = _meta(base_dir=str(tmp_path), uuid="abc")
    target = _fallback_ebook_copy(meta, source, str(tmp_path))
    assert target is not None
    assert Path(target).name == (
        "J.K. Rowling - Harry Potter #4 - Harry Potter and the Goblet of Fire "
        "(2000) [ENG 9781781100103].epub"
    )
    assert Path(target).read_bytes() == b"book"
    assert source.read_bytes() == b"book"
