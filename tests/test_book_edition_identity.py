# ruff: noqa: S101
from pathlib import Path
from typing import Any

import pytest

import src.book_prep as book_prep
from src.google_books import GoogleBooksManager
from src.meta import Meta


def test_google_books_rejects_volume_for_another_isbn() -> None:
    data = {
        "totalItems": 1,
        "items": [
            {
                "id": "print-edition",
                "volumeInfo": {
                    "title": "The Idea Factory",
                    "publishedDate": "2013",
                    "industryIdentifiers": [{"type": "ISBN_13", "identifier": "9780143122791"}],
                },
            }
        ],
    }

    assert GoogleBooksManager()._parse_volume_info(data, "9781101561089") is None


def test_google_books_accepts_equivalent_isbn10_identifier() -> None:
    data = {
        "totalItems": 1,
        "items": [
            {
                "id": "digital-edition",
                "volumeInfo": {
                    "title": "The Idea Factory",
                    "industryIdentifiers": [{"type": "ISBN_10", "identifier": "1101561084"}],
                },
            }
        ],
    }

    assert GoogleBooksManager()._parse_volume_info(data, "9781101561089") is not None


@pytest.mark.asyncio
async def test_epub_metadata_preserves_digital_edition_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "Jon Gertner - The Idea Factory - Bell Labs And The Great Age Of American Innovation - 9781101561089.epub"
    source.touch()
    meta = Meta(
        path=str(source),
        filelist=[str(source)],
        title=source.name,
        torrent_comments=[{"trackers": "https://myanonamouse.net/announce", "comment": "MID=123"}],
    )
    source_metadata = {
        "title": "The Idea Factory: Bell Labs and the Great Age of American Innovation",
        "author": "Jon Gertner",
        "publisher": "Penguin Press",
        "isbn": "9780143122791",
        "year": "2012",
        "book_language_raw": "en",
    }
    wrong_edition = {
        "title": "The Idea Factory",
        "author": "Jon Gertner",
        "publisher": "Penguin",
        "isbn": "9780143122791",
        "year": 2013,
        "book_language": "English",
        "book_language_iso": "eng",
    }

    async def export_stub(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}

    async def wrong_edition_stub(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return dict(wrong_edition)

    monkeypatch.setattr(book_prep, "_get_epubmeta_output", lambda _path: "")
    monkeypatch.setattr(book_prep, "_extract_epub_metadata", lambda _path: dict(source_metadata))
    monkeypatch.setattr(book_prep, "_epub_content_identifiers", lambda _path: ({"9780143122791", "9781101561089"}, {"B005GSZIWG"}))
    monkeypatch.setattr(book_prep, "export_info", export_stub)
    monkeypatch.setattr("src.myanonamouse.myanonamouse_manager.search_by_id", wrong_edition_stub)
    monkeypatch.setattr("src.google_books.google_books_manager.search_by_isbn", wrong_edition_stub)
    monkeypatch.setattr("src.openlibrary.openlibrary_manager.search_by_isbn", wrong_edition_stub)

    await book_prep.gather_book_prep(meta, str(source), str(tmp_path), {"DEFAULT": {}})

    assert meta.title == source_metadata["title"]
    assert meta.author == source_metadata["author"]
    assert meta.publisher == source_metadata["publisher"]
    assert meta.isbn == "9781101561089"
    assert meta.year == 2012
    assert meta.asin == "B005GSZIWG"


@pytest.mark.asyncio
async def test_epub_with_unresolved_isbn_conflict_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "The Idea Factory.epub"
    source.touch()
    monkeypatch.setattr(book_prep, "_get_epubmeta_output", lambda _path: "")
    monkeypatch.setattr(book_prep, "_extract_epub_metadata", lambda _path: {"title": "The Idea Factory", "isbn": "9780143122791"})
    monkeypatch.setattr(book_prep, "_epub_content_identifiers", lambda _path: ({"9780143122791", "9781101561089"}, set()))

    with pytest.raises(book_prep.ItemProcessingError, match="Conflicting EPUB ISBNs"):
        await book_prep.gather_book_prep(Meta(path=str(source), filelist=[str(source)]), str(source), str(tmp_path), {"DEFAULT": {}})


def test_book_identity_removes_trailing_source_isbn() -> None:
    author, title = book_prep.book_identity_from_path(
        "Jon Gertner - The Idea Factory - Bell Labs And The Great Age Of American Innovation - 9781101561089.epub"
    )

    assert author == "Jon Gertner"
    assert title == "The Idea Factory - Bell Labs And The Great Age Of American Innovation"
