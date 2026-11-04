from __future__ import annotations

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
    return Meta(
        category="BOOK",
        author=author,
        title=title,
        year=year,
        isbn=isbn,
        book_language_iso="eng",
        type=extension.upper(),
        path=f"/books/book.{extension}",
        filelist=[f"/books/book.{extension}"],
        **values,
    )


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
