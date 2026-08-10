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
async def test_exact_isbn_metadata_replaces_divergent_embedded_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "Another World Survival.epub"
    source.touch()
    meta = Meta(
        path=str(source),
        filelist=[str(source)],
        torrent_comments=[{"trackers": "https://myanonamouse.net/announce", "comment": "MID=456"}],
    )
    embedded = {
        "title": "Another World Survival",
        "author": "Harris Hayes",
        "year": "2015",
        "book_language_raw": "en",
    }
    edition = {
        "title": "Another World Survival, Volume 5",
        "author": "Yokotsuka Tsukasa",
        "publisher": "Hanashi Media",
        "isbn": "9781961788022",
        "asin": "B0C7M77Q5M",
        "year": 2023,
        "overview": "Kazuhisa forms a new party with Rushia.",
        "comic": True,
    }

    async def export_stub(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}

    async def mam_stub(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return dict(edition)

    async def google_stub(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return dict(edition)

    async def no_result(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(book_prep, "_get_epubmeta_output", lambda _path: "")
    monkeypatch.setattr(book_prep, "_extract_epub_metadata", lambda _path: dict(embedded))
    monkeypatch.setattr(book_prep, "_epub_content_identifiers", lambda _path: ({"9781961788022"}, {"B0C7M77Q5M"}))
    monkeypatch.setattr(book_prep, "export_info", export_stub)
    monkeypatch.setattr("src.myanonamouse.myanonamouse_manager.search_by_id", mam_stub)
    monkeypatch.setattr("src.google_books.google_books_manager.search_by_isbn", google_stub)
    monkeypatch.setattr("src.openlibrary.openlibrary_manager.search_by_isbn", no_result)

    await book_prep.gather_book_prep(meta, str(source), str(tmp_path), {"DEFAULT": {}})

    assert meta.title == "Another World Survival, Volume 5"
    assert meta.author == "Yokotsuka Tsukasa"
    assert meta.year == 2023
    assert meta.isbn == "9781961788022"
    assert meta.asin == "B0C7M77Q5M"
    assert meta.comic is False


@pytest.mark.asyncio
async def test_explicit_epub_comic_flag_is_preserved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "Another World Survival.epub"
    source.touch()
    meta = Meta(path=str(source), filelist=[str(source)], comic=True, skip_auto_torrent=True)

    async def export_stub(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(book_prep, "_get_epubmeta_output", lambda _path: "")
    monkeypatch.setattr(book_prep, "_extract_epub_metadata", lambda _path: {"title": "Another World Survival", "author": "Yokotsuka Tsukasa"})
    monkeypatch.setattr(book_prep, "_epub_content_identifiers", lambda _path: (set(), set()))
    monkeypatch.setattr(book_prep, "export_info", export_stub)

    await book_prep.gather_book_prep(meta, str(source), str(tmp_path), {"DEFAULT": {}})

    assert meta.comic is True


@pytest.mark.asyncio
async def test_epub_with_unresolved_isbn_conflict_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "The Idea Factory.epub"
    source.touch()
    monkeypatch.setattr(book_prep, "_get_epubmeta_output", lambda _path: "")
    monkeypatch.setattr(book_prep, "_extract_epub_metadata", lambda _path: {"title": "The Idea Factory"})
    monkeypatch.setattr(book_prep, "_epub_content_identifiers", lambda _path: ({"9780143122791", "9781101561089"}, set()))

    with pytest.raises(book_prep.ItemProcessingError, match="Conflicting EPUB ISBNs"):
        await book_prep.gather_book_prep(Meta(path=str(source), filelist=[str(source)]), str(source), str(tmp_path), {"DEFAULT": {}})


def test_epub_primary_isbn_wins_over_incidental_body_numbers(monkeypatch: pytest.MonkeyPatch) -> None:
    epub_meta = {"isbn": "9780134076454"}
    monkeypatch.setattr(book_prep, "_epub_content_identifiers", lambda _path: ({"9780134076423", "9780134076454", "0000000000"}, set()))

    book_prep._reconcile_epub_identifiers(Meta(), epub_meta, "Computer Science.epub")

    assert epub_meta["isbn"] == "9780134076454"


@pytest.mark.asyncio
@pytest.mark.parametrize(("source_publisher", "expected"), [("Seven Seas", "Seven Seas"), ("", "Seven Seas Entertainment")])
async def test_exact_edition_publisher_prefers_source_then_openlibrary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source_publisher: str, expected: str
) -> None:
    source = tmp_path / "No Game No Life Vol 2.cbz"
    source.touch()
    meta = Meta(path=str(source), filelist=[str(source)], skip_auto_torrent=True)
    source_metadata = {
        "title": "No Game, No Life Vol. 2",
        "author": "Yuu Kamiya",
        "publisher": source_publisher,
        "isbn": "9781642750379",
        "year": "2019",
        "book_language_raw": "en",
    }
    google = {**source_metadata, "publisher": "National Geographic Books"}
    openlibrary = {**source_metadata, "publisher": "Seven Seas Entertainment"}

    async def export_stub(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}

    async def google_stub(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return google

    async def openlibrary_stub(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return openlibrary

    monkeypatch.setattr(book_prep, "_extract_cbr_cbz_metadata", lambda _path: source_metadata)
    monkeypatch.setattr(book_prep, "export_info", export_stub)
    monkeypatch.setattr("src.google_books.google_books_manager.search_by_isbn", google_stub)
    monkeypatch.setattr("src.openlibrary.openlibrary_manager.search_by_isbn", openlibrary_stub)

    await book_prep.gather_book_prep(meta, str(source), str(tmp_path), {"DEFAULT": {}})

    assert meta.publisher == expected


def test_book_identity_removes_trailing_source_isbn() -> None:
    author, title = book_prep.book_identity_from_path(
        "Jon Gertner - The Idea Factory - Bell Labs And The Great Age Of American Innovation - 9781101561089.epub"
    )

    assert author == "Jon Gertner"
    assert title == "The Idea Factory - Bell Labs And The Great Age Of American Innovation"
