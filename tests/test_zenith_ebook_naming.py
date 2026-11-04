from __future__ import annotations

from pathlib import Path

import pytest

from src.domain_models.release import Meta
from src.integrations.media.zentag import fallback_ebook_filename
from src.integrations.trackers.UNIT3D.znth import Zenith

TRACKER = Zenith({"DEFAULT": {}, "TRACKERS": {"ZENITH": {}}})

CASES = [
    (
        "Harvey M. Deitel, Paul J. Deitel",
        "C: How to Program",
        2010,
        "0136123562",
        "pdf",
        {"edition": "6th Edition"},
        "Harvey M. Deitel, Paul J. Deitel - C (2010) [ENG 6th 0136123562].pdf",
    ),
    (
        "Ben Frain",
        "Responsive Web Design with Html5 and Css3 (Second Edition)",
        2015,
        "9781784398934",
        "epub",
        {},
        "Ben Frain - Responsive Web Design with Html5 and Css3 "
        "(2015) [ENG Second 9781784398934].epub",
    ),
    (
        "François Chollet",
        "Deep Learning with Python, 2nd Ed",
        2021,
        "9781617296864",
        "pdf",
        {},
        "François Chollet - Deep Learning with Python "
        "(2021) [ENG 2nd 9781617296864].pdf",
    ),
    (
        "F. A. Hayek",
        "The Road to Serfdom",
        2007,
        "9780226320540",
        "epub",
        {"edition": "Definitive Edition"},
        "F. A. Hayek - The Road to Serfdom "
        "(2007) [ENG Definitive 9780226320540].epub",
    ),
    (
        "Murray N. Rothbard",
        "Man, Economy and State, A Treatise on Economic Principles "
        "(1962, 1970)(2004)",
        2004,
        "0945466307",
        "pdf",
        {"edition": "Scholar"},
        "Murray N. Rothbard - Man, Economy and State, A Treatise on "
        "Economic Principles (2004) [ENG Scholar 0945466307].pdf",
    ),
    (
        "René Descartes",
        "Meditations on First Philosophy: With Selections from the "
        "Objections and Replies",
        2008,
        "9780192806963",
        "epub",
        {},
        "René Descartes - Meditations on First Philosophy "
        "(2008) [ENG 9780192806963].epub",
    ),
]


def _meta(
    author: str,
    title: str,
    year: int | None,
    isbn: str,
    extension: str = "pdf",
    **values: object,
) -> Meta:
    defaults = {
        "category": "BOOK",
        "author": author,
        "title": title,
        "year": year,
        "isbn": isbn,
        "book_language_iso": "eng",
        "type": extension.upper(),
        "path": f"/books/book.{extension}",
        "filelist": [f"/books/book.{extension}"],
    }
    defaults.update(values)
    return Meta(**defaults)


@pytest.mark.parametrize(
    ("author", "title", "year", "isbn", "extension", "values", "expected"),
    CASES,
)
def test_zenith_ebook_filename_matches_the_staff_examples(
    author, title, year, isbn, extension, values, expected
) -> None:
    assert fallback_ebook_filename(
        _meta(author, title, year, isbn, extension, **values)
    ) == expected


@pytest.mark.parametrize(
    ("author", "title", "year", "isbn", "extension", "values", "_expected"),
    CASES,
)
def test_zenith_release_name_has_one_year_no_isbn_and_no_subtitle(
    author, title, year, isbn, extension, values, _expected
) -> None:
    name = TRACKER._book_release_name(
        _meta(author, title, year, isbn, extension, **values)
    )
    assert isbn not in name
    assert name.count(str(year)) == 1
    for subtitle in ("How to Program", "With Selections"):
        assert subtitle not in name


def test_first_edition_has_no_edition_token() -> None:
    meta = _meta("Some Author", "A Book", 2020, "9780000000002", "epub")
    assert fallback_ebook_filename(meta) == (
        "Some Author - A Book (2020) [ENG 9780000000002].epub"
    )
    assert " Edition " not in TRACKER._book_release_name(meta)


def test_series_uses_hash_and_sits_before_the_title() -> None:
    meta = _meta(
        "Some Author",
        "Second Volume",
        2020,
        "9780000000002",
        "epub",
        book_series="Great Serie",
        book_series_index="2",
    )
    assert fallback_ebook_filename(meta) == (
        "Some Author - Great Serie #2 - Second Volume "
        "(2020) [ENG 9780000000002].epub"
    )


def test_substitutions_apply_only_to_the_filename() -> None:
    meta = _meta(
        "Some Author",
        "Half: A Story/Test",
        2020,
        "9780000000002",
        "epub",
    )
    assert fallback_ebook_filename(meta) == (
        "Some Author - Half- A Story-Test (2020) [ENG 9780000000002].epub"
    )
    assert ":" in TRACKER._book_release_name(meta)


def test_duplicate_author_in_the_title_is_removed_from_the_filename() -> None:
    meta = _meta(
        "Some Author",
        "SOME AUTHOR - Real Title",
        2020,
        "9780000000002",
        "epub",
    )
    name = fallback_ebook_filename(meta)
    assert name is not None and name.count("Some Author") == 1
    assert "Real Title" in name


def test_validator_accepts_the_template_and_rejects_the_old_names() -> None:
    meta = _meta("Some Author", "A Book", 2020, "9780000000002", "epub")
    good = "Some Author - A Book (2020) [ENG 9780000000002]"
    meta.filelist = [f"/books/{good}.epub"]
    meta.basename_no_ext = good
    assert TRACKER._ebook_filename_policy(meta) is True

    for bad in (
        "Some Author - A Book 2020 ENG EPUB",
        "Some Author - A Book (2020) [ENG 9780000000002].epub",
        "Some Author - A_Book (2020) [ENG 9780000000002].epub",
        "Some Author - Some Author - A Book (2020) [ENG 9780000000002].epub",
        "Some Author - A Book (2020) [ENG 1st 9780000000002].epub",
    ):
        meta.filelist = [f"/books/{bad}"]
        meta.basename_no_ext = bad
        assert TRACKER._ebook_filename_policy(meta) is False


def test_missing_isbn_fails_before_the_upload() -> None:
    meta = _meta("Some Author", "A Book", 2020, "")
    assert fallback_ebook_filename(meta) is None
    assert TRACKER._ebook_format_policy(meta) is False


def test_missing_year_fails_before_the_upload() -> None:
    meta = _meta("Some Author", "A Book", None, "9780000000002")
    assert fallback_ebook_filename(meta) is None
    assert TRACKER._ebook_format_policy(meta) is False


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({"edition": "Second Edition"}, "Second"),
        ({"edition": "2nd Ed"}, "2nd"),
        ({"title": "Book, 5th Ed"}, "5th"),
        ({"title": "Book_5e"}, "5th"),
        ({"title": "Book (First Edition)"}, ""),
        ({"edition": "Definitive Edition"}, "Definitive"),
        ({"edition": "Scholar"}, "Scholar"),
        ({"edition": "Revised and Updated"}, "Revised"),
    ],
)
def test_edition_detection(values: dict[str, object], expected: str) -> None:
    from src.integrations.media.zenith_ebook import edition

    defaults = {
        "category": "BOOK",
        "author": "A",
        "title": "Book",
        "year": 2020,
        "isbn": "1",
    }
    defaults.update(values)
    meta = Meta(**defaults)
    assert edition(meta) == expected


def test_google_books_and_openlibrary_supply_the_edition() -> None:
    from src.integrations.external_apis.google_books import GoogleBooksManager
    from src.integrations.external_apis.openlibrary import OpenLibraryManager

    google: dict[str, object] = {}
    GoogleBooksManager._add_edition(google, {"edition": "Definitive Edition"})
    assert google["edition"] == "Definitive Edition"

    details = {
        "title": "The Road to Serfdom",
        "edition_name": "Definitive",
        "publishers": ["Routledge"],
    }
    metadata = OpenLibraryManager()._metadata_from_book_details({}, details, "9780226320540")
    assert metadata["edition"] == "Definitive"


@pytest.mark.asyncio
async def test_prepare_names_the_single_ebook_even_when_zentag_fails(
    tmp_path, monkeypatch
) -> None:
    from src.integrations.media import zentag

    source = tmp_path / "The Road to Serfdom (2007).epub"
    source.write_bytes(b"book")
    meta = _meta(
        "F. A. Hayek",
        "The Road to Serfdom",
        2007,
        "9780226320540",
        "epub",
        edition="Definitive",
        source="Retail",
        path=str(source),
        base_dir=str(tmp_path),
        uuid="hayek",
    )
    monkeypatch.setattr(zentag, "should_prepare_zenith_ebook", lambda *_: True)

    async def failing(*_args, **_kwargs):
        raise RuntimeError("zentag naming mismatch")

    monkeypatch.setattr(zentag, "_prepare_ebook_copy", failing)
    target = await zentag.prepare_zenith_ebook(meta, str(tmp_path), {})
    assert target is not None
    release_dir = Path(target)
    assert release_dir.name == (
        "F. A. Hayek - The Road to Serfdom (2007) "
        "[ENG Definitive 9780226320540 Retail]"
    )
    assert (release_dir / f"{release_dir.name}.epub").is_file()
    assert TRACKER._book_release_name(meta) == (
        "F. A. Hayek - The Road to Serfdom 2007 ENG Definitive EPUB Retail"
    )


@pytest.mark.asyncio
async def test_prepare_renames_a_zentag_output_with_the_original_name(
    tmp_path, monkeypatch
) -> None:
    from src.integrations.media import zentag

    source = tmp_path / "The Road to Serfdom (2007).epub"
    source.write_bytes(b"book")
    prepared = tmp_path / "zentag"
    prepared.mkdir()
    (prepared / source.name).write_bytes(b"book")
    meta = _meta(
        "F. A. Hayek",
        "The Road to Serfdom",
        2007,
        "9780226320540",
        "epub",
        edition="Definitive",
        path=str(source),
        base_dir=str(tmp_path),
        uuid="hayek",
    )
    monkeypatch.setattr(zentag, "should_prepare_zenith_ebook", lambda *_: True)

    async def original_name(*_args, **_kwargs):
        return str(prepared)

    monkeypatch.setattr(zentag, "_prepare_ebook_copy", original_name)
    target = await zentag.prepare_zenith_ebook(meta, str(tmp_path), {})
    assert target is not None
    assert Path(target).name == (
        "F. A. Hayek - The Road to Serfdom (2007) "
        "[ENG Definitive 9780226320540]"
    )
