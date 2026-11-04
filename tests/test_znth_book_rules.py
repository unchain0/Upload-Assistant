from __future__ import annotations

import asyncio

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.znth import Zenith


def _tracker() -> Zenith:
    return Zenith({"DEFAULT": {}, "TRACKERS": {"ZENITH": {}}})


def test_zenith_book_category_ids_cover_ebook_audiobook_and_misc() -> None:
    tracker = _tracker()
    run = asyncio.run
    assert run(tracker.get_category_id(Meta(category="BOOK"))) == {
        "category_id": "6"
    }
    assert run(
        tracker.get_category_id(Meta(category="BOOK", audiobook=True))
    ) == {"category_id": "7"}
    assert run(tracker.get_category_id(Meta(category="BOOK", manga=True))) == {
        "category_id": "9"
    }
    assert run(tracker.get_category_id(Meta(category="BOOK", magazine=True))) == {
        "category_id": "9"
    }


def test_zenith_book_type_ids_follow_the_ebook_format() -> None:
    tracker = _tracker()
    run = asyncio.run
    assert run(tracker.get_type_id(Meta(category="BOOK", type="EPUB"))) == {
        "type_id": "9"
    }
    assert run(tracker.get_type_id(Meta(category="BOOK", type="PDF"))) == {
        "type_id": "19"
    }
    assert run(tracker.get_type_id(Meta(category="BOOK", type="M4B"))) == {
        "type_id": "10"
    }


def test_zenith_book_type_id_reads_the_container_when_type_is_empty() -> None:
    tracker = _tracker()
    assert asyncio.run(
        tracker.get_type_id(Meta(category="BOOK", type="", container=".epub"))
    ) == {"type_id": "9"}


def test_zenith_book_identifier_data_sends_isbn_and_asin() -> None:
    tracker = _tracker()
    run = asyncio.run
    assert run(
        tracker.get_additional_data(
            Meta(category="BOOK", isbn="9780000000000", asin="B000000000")
        )
    ) == {"isbn": "9780000000000", "asin": "B000000000"}
    assert run(
        tracker.get_additional_data(Meta(category="BOOK", isbn="9780000000000"))
    ) == {"isbn": "9780000000000"}
    assert run(tracker.get_additional_data(Meta(category="BOOK"))) == {}
    assert run(
        tracker.get_additional_data(
            Meta(category="BOOK", manga=True, isbn="9780000000000")
        )
    ) == {}


def test_zenith_accepts_the_official_ebook_filename_examples() -> None:
    tracker = _tracker()
    for name in (
        "J.K. Rowling - Harry Potter #4 - Harry Potter and the Goblet of Fire (2000) [ENG UK 9781781100103 Retail].epub",
        "Lisa Jewell - None of This is True (2023) [ENG 9781804940228 Retail].epub",
        "Brian Herbert - codelude to Dune #1 - House Atreides (1999) [ENG 0553897829].epub",
        "Janet Valade - PHP, MySQL, JavaScript & Html5 All-In-One for Dummies (2013) [ENG 3rd 9781118213704].epub",
    ):
        meta = Meta(
            category="BOOK",
            type="EPUB",
            isbn="9781781100103",
            filelist=[name],
        )
        assert tracker._ebook_format_policy(meta) is True


def test_zenith_rejects_an_ebook_filename_outside_the_pattern(
    caplog: pytest.LogCaptureFixture,
) -> None:
    tracker = _tracker()
    meta = Meta(
        category="BOOK",
        type="EPUB",
        isbn="9781541706194",
        filelist=["Author - Title (2025).epub"],
    )
    assert tracker._ebook_format_policy(meta) is False
    assert "does not follow" in caplog.text


def test_zenith_rejects_forbidden_characters_in_the_ebook_filename(
    caplog: pytest.LogCaptureFixture,
) -> None:
    tracker = _tracker()
    meta = Meta(
        category="BOOK",
        type="EPUB",
        isbn="9781541706194",
        filelist=[
            "Author - Title: Subtitle (2025) [ENG 9781541706194].epub"
        ],
    )
    assert tracker._ebook_format_policy(meta) is False
    assert "must be replaced" in caplog.text


def test_zenith_ebook_without_isbn_stops_on_the_identifier_rule() -> None:
    tracker = _tracker()
    meta = Meta(category="BOOK", type="EPUB", isbn="", asin="")
    assert asyncio.run(tracker._book_format_policy(meta, [], False)) is False


def _book(**values: object) -> Meta:
    defaults: dict[str, object] = {
        "category": "BOOK",
        "type": "EPUB",
        "isbn": "9780000000000",
        "author": "Henry Hazlitt",
        "title": "Economics in One Lesson",
        "filelist": ["Henry Hazlitt - Economics in One Lesson.epub"],
    }
    defaults.update(values)
    return Meta(**defaults)


def test_zenith_blocks_site_names_in_the_payload() -> None:
    tracker = _tracker()
    meta = _book(filelist=["Henry Hazlitt - Economics (z-lib.org).epub"])
    assert tracker._book_hygiene_policy(meta, list(meta.filelist)) is False
    meta = _book(description="Baixado de lelivros.love")
    assert tracker._book_hygiene_policy(meta, list(meta.filelist)) is False
    assert tracker._book_hygiene_policy(_book(), list(_book().filelist)) is True


def test_zenith_blocks_a_duplicated_series_in_the_title() -> None:
    tracker = _tracker()
    meta = _book(title="Untamed (Untamed #1)", author="Untamed #1")
    assert tracker._book_hygiene_policy(meta, list(meta.filelist)) is False


def test_zenith_blocks_an_incomplete_author() -> None:
    tracker = _tracker()
    meta = _book(filelist=["Hazlitt - Economics in One Lesson.epub"])
    assert tracker._book_hygiene_policy(meta, list(meta.filelist)) is False
    meta = _book(filelist=["Henry Hazlitt - Economics in One Lesson.epub"])
    assert tracker._book_hygiene_policy(meta, list(meta.filelist)) is True


def test_zenith_blocks_a_pack_with_different_authors() -> None:
    tracker = _tracker()
    meta = _book(
        author="",
        filelist=[
            "Henry Hazlitt - Economics in One Lesson.epub",
            "Ludwig von Mises - Human Action.epub",
        ],
    )
    assert tracker._book_hygiene_policy(meta, list(meta.filelist)) is False


def test_zenith_blocks_a_language_conflict() -> None:
    tracker = _tracker()
    meta = _book(
        name="The Power of Now [RUS].epub",
        title="The Power of Now",
        book_language_iso="ENG",
        filelist=["The Power of Now [RUS].epub"],
    )
    assert tracker._book_hygiene_policy(meta, list(meta.filelist)) is False
    meta = _book(
        name="The Power of Now [ENG].epub",
        title="The Power of Now",
        book_language_iso="ENG",
        filelist=["The Power of Now [ENG].epub"],
    )
    assert tracker._book_hygiene_policy(meta, list(meta.filelist)) is True


def test_trailing_article_moves_to_the_front() -> None:
    meta = Meta(
        category="BOOK",
        type="EPUB",
        title="Origins of the Federal Reserve, The",
        author="Murray N. Rothbard",
        year=2009,
        book_language_iso="eng",
        isbn="9781933550473",
        filelist=["book.epub"],
    )
    name = asyncio.run(_tracker().get_name(meta))["name"]
    assert name.startswith("Murray N. Rothbard - The Origins of the Federal Reserve")


def test_retail_only_appears_when_the_filename_has_it() -> None:
    tracker = _tracker()
    base = {
        "category": "BOOK",
        "type": "EPUB",
        "title": "Title",
        "author": "Author",
        "year": 2009,
        "book_language_iso": "eng",
        "isbn": "9781933550473",
    }
    without = Meta(**base, filelist=["Author - Title (2009) [ENG 9781933550473].epub"])
    with_retail = Meta(
        **base,
        filelist=["Author - Title (2009) [ENG 9781933550473 Retail].epub"],
    )
    assert "Retail" not in asyncio.run(tracker.get_name(without))["name"]
    assert "Retail" in asyncio.run(tracker.get_name(with_retail))["name"]


def test_release_name_keeps_every_author_in_metadata_order() -> None:
    meta = Meta(
        category="BOOK",
        type="PDF",
        title="Python Machine Learning",
        author="Vahid Mirjalili Sebastian Raschka",
        book_author="Sebastian Raschka, Vahid Mirjalili",
        year=2019,
        book_language_iso="eng",
        filelist=["book.pdf"],
    )
    name = asyncio.run(_tracker().get_name(meta))["name"]
    assert name.startswith("Sebastian Raschka, Vahid Mirjalili - Python Machine Learning")
