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


def test_series_marker_variants_are_dropped() -> None:
    from src.domain_models.release import Meta
    from src.integrations.media import zenith_ebook

    for series in ("C #9", "C-9", "C\uff039", "C.9"):
        meta = Meta(
            category="BOOK",
            type="PDF",
            author="Mark J. Price",
            book_author="Mark J. Price",
            title="C# 9 and .NET 5",
            year=2020,
            book_language_iso="ENG",
            isbn="9781800568105",
            book_series=series,
            path="x.pdf",
        )
        assert zenith_ebook.series(meta) == "", series


def test_zentag_fallback_name_uses_the_series_guard() -> None:
    from src.domain_models.release import Meta
    from src.integrations.media.zentag import fallback_ebook_filename

    meta = Meta(
        category="BOOK",
        type="PDF",
        author="Mark J. Price",
        book_author="Mark J. Price",
        title="C# 9 and .NET 5",
        year=2020,
        book_language_iso="ENG",
        isbn="9781800568105",
        book_series="C #9",
        manual_edition="5th",
        path="x.pdf",
    )
    assert fallback_ebook_filename(meta) == (
        "Mark J. Price - C# 9 and .NET 5 (2020) [ENG 5th 9781800568105].pdf"
    )


def _series_meta(series: str) -> object:
    from src.domain_models.release import Meta

    return Meta(
        category="BOOK",
        type="PDF",
        author="Mark J. Price",
        book_author="Mark J. Price",
        title="C# 9 and .NET 5",
        year=2020,
        book_language_iso="ENG",
        isbn="9781800568105",
        book_series=series,
        manual_edition="5th",
        path="x.pdf",
    )


def test_naming_meta_drops_only_rejected_series() -> None:
    from src.integrations.media.zentag import _naming_meta

    rejected = _naming_meta(_series_meta("C #9"))
    kept = _naming_meta(_series_meta("Dune"))
    assert rejected.book_series == ""
    assert kept.book_series == "Dune"


def test_zentag_copy_name_has_no_rejected_series(tmp_path) -> None:
    from pathlib import Path

    from src.integrations.media.zentag import _named_ebook_copy

    source = tmp_path / "book.pdf"
    source.write_bytes(b"book")
    meta = _series_meta("C #9")
    meta.uuid = "00000000-0000-7000-8000-000000000123"
    meta.path = str(source)

    copied = _named_ebook_copy(meta, source, str(tmp_path))

    assert copied is not None
    assert Path(copied).name == (
        "Mark J. Price - C# 9 and .NET 5 (2020) [ENG 5th 9781800568105].pdf"
    )
