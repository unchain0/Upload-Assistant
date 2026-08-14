import asyncio

from src.get_name import NameManager
from src.meta import Meta


def _book_name(meta: Meta) -> str:
    name_notag, *_rest = asyncio.run(NameManager({}).get_name(meta))
    return name_notag


def test_book_name_does_not_repeat_author_when_prefixed_in_title():
    meta = Meta(
        category="BOOK",
        author="Taylor Driggers",
        title="Taylor Driggers - Queering Faith in Fantasy Literature",
        year=2022,
        type="PDF",
        source="SCAN",
    )

    assert _book_name(meta) == "Taylor Driggers - Queering Faith in Fantasy Literature 2022 SCAN eBOOK"


def test_book_name_keeps_title_when_author_prefix_does_not_match():
    meta = Meta(
        category="BOOK",
        author="Taylor Driggers",
        title="Not the Author - Queering Faith in Fantasy Literature",
        year=2022,
        type="PDF",
        source="SCAN",
    )

    assert _book_name(meta) == "Taylor Driggers - Not the Author - Queering Faith in Fantasy Literature 2022 SCAN eBOOK"


def test_book_name_deduplicates_publisher_when_author_is_missing():
    meta = Meta(
        category="BOOK",
        publisher="Bloomsbury Publishing Plc",
        title="Bloomsbury Publishing Plc - The C Programming Language",
        year=2022,
        type="EPUB",
        source="RETAIL",
    )

    assert _book_name(meta) == "Bloomsbury Publishing Plc - The C Programming Language 2022 RETAiL ePUB eBOOK"


def test_book_name_preserves_title_with_colon_after_author_name():
    meta = Meta(
        category="BOOK",
        author="Cher",
        title="Cher: The Memoir, Part One",
        year=2022,
        type="EPUB",
        source="RETAIL",
    )

    assert _book_name(meta) == "Cher - Cher: The Memoir, Part One 2022 RETAiL ePUB eBOOK"
