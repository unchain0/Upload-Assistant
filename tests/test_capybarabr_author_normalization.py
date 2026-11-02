from __future__ import annotations

import asyncio

from src.meta import Meta
from src.trackers.UNIT3D.capybarabr import CapybaraBR


def _name(title: str, author: str) -> str:
    tracker = CapybaraBR({"TRACKERS": {"CAPYBARABR": {}}})
    meta = Meta(
        category="BOOK",
        title=title,
        author=author,
        year=1996,
        book_series="",
        book_series_index="",
        book_language_iso="eng",
    )
    return asyncio.run(tracker.get_name(meta))["name"]


def test_author_prefix_is_removed_from_the_title() -> None:
    assert _name("Henry Hazlitt - Economics in One Lesson", "Henry Hazlitt").casefold().startswith(
        "economics in one lesson - henry hazlitt"
    )


def test_author_suffix_is_removed_from_the_title() -> None:
    assert _name("Economics in One Lesson - Henry Hazlitt", "Henry Hazlitt").casefold().startswith(
        "economics in one lesson - henry hazlitt"
    )


def test_title_without_author_stays_untouched() -> None:
    assert _name("Economics in One Lesson", "Henry Hazlitt").casefold().startswith(
        "economics in one lesson - henry hazlitt"
    )
