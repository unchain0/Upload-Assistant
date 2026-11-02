from __future__ import annotations

import asyncio

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
