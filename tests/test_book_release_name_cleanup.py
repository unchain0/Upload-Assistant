from __future__ import annotations

import re

import pytest

from src.domain_models.book_naming import (
    canonical_book_author,
    clean_book_name,
    clean_book_title,
)
from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.capybarabr import CapybaraBR
from src.integrations.trackers.UNIT3D.znth import Zenith

CASES = [
    (
        "Murray N. Rothbard",
        "Rothbard, Murray N. - Man, Economy and State, "
        "A Treatise on Economic Principles",
        2004,
        "Man, Economy and State, A Treatise on Economic Principles",
    ),
    (
        "Richard Szeliski",
        "Richard Szeliski - Computer Vision_ Algorithms and Applications "
        "(Second Edition)",
        2022,
        "Computer Vision Algorithms and Applications (Second Edition)",
    ),
    (
        "François Chollet",
        "François Chollet Francois Chollet - Deep Learning with Python, "
        "2nd Ed - François Chollet",
        2021,
        "Deep Learning with Python, 2nd Edition",
    ),
    (
        "Mark J. Price",
        "MARK J. PRICE - C #9 - Mark J. Price - C#9 and .NET 5 - "
        "Modern Cross-Platform Development_5e",
        2020,
        "C#9 and .NET 5 - Modern Cross-Platform Development 5th Edition",
    ),
    (
        "Dusty Phillips",
        "Python_3_Object_Oriented_Programming",
        2015,
        "Python 3 Object Oriented Programming",
    ),
    (
        "MARK J. PRICE",
        "C#9 And .NET 5 - Modern Cross-Platform Development_5e",
        2020,
        "C#9 And .NET 5 - Modern Cross-Platform Development 5th Edition",
    ),
    (
        "Olavo de Carvalho",
        "A Filosofia e Seu Inverso: E Outros Estudos - de Carvalho",
        2020,
        "A Filosofia e Seu Inverso: E Outros Estudos",
    ),
    (
        "Fahad Batla",
        "Deep Learning (Fahad Batla's Library)",
        2021,
        "Deep Learning",
    ),
]


def _meta(author: str, title: str, year: int) -> Meta:
    return Meta(
        category="BOOK",
        type="PDF",
        title=title,
        author=author,
        year=year,
        book_language_iso="eng",
        isbn="9780000000000",
        filelist=["book.pdf"],
    )


def _assert_clean(name: str, author: str) -> None:
    canonical = canonical_book_author(Meta(book_author=author))
    assert "undefined" not in name.lower()
    assert "_" not in name
    assert not re.search(r"\b5e\b", name)
    assert not re.search(r"\((\d{4})\)\s+\1\b", name)
    assert "Library" not in name and "Biblioteca" not in name
    folded = name.casefold()
    assert folded.count(canonical.casefold()) <= 1


@pytest.mark.parametrize(("author", "title", "year", "expected"), CASES)
def test_clean_book_title_removes_artifacts(
    author: str, title: str, year: int, expected: str
) -> None:
    canonical = canonical_book_author(_meta(author, title, year))
    assert clean_book_title(title, canonical) == expected


@pytest.mark.parametrize(("author", "title", "year", "_expected"), CASES)
def test_zenith_release_name_has_no_artifacts(
    author: str, title: str, year: int, _expected: str
) -> None:
    meta = _meta(author, title, year)
    name = Zenith(
        {"DEFAULT": {}, "TRACKERS": {"ZENITH": {}}}
    )._book_release_name(meta)
    _assert_clean(name, author)


@pytest.mark.parametrize(("author", "title", "year", "_expected"), CASES)
def test_capybara_release_name_has_no_artifacts(
    author: str, title: str, year: int, _expected: str
) -> None:
    meta = _meta(author, title, year)
    builder = CapybaraBR(
        {"DEFAULT": {}, "TRACKERS": {"CAPYBARABR": {}}}
    )
    _assert_clean(builder._book_name(meta), author)


def test_canonical_author_keeps_the_full_list_and_fixes_all_caps() -> None:
    assert (
        canonical_book_author(
            Meta(author="Sebastian Raschka, Vahid Mirjalili")
        )
        == "Sebastian Raschka, Vahid Mirjalili"
    )
    assert canonical_book_author(Meta(author="MARK J. PRICE")) == "Mark J. Price"


def test_duplicate_year_and_library_note_are_removed_from_the_name() -> None:
    name = "Author - Title (2022) 2022 (Someone's Library)"
    assert clean_book_name(name) == "Author - Title (2022)"
