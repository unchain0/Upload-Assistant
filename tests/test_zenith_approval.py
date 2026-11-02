from __future__ import annotations

import asyncio

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.znth import Zenith


def _tracker() -> Zenith:
    return Zenith({"DEFAULT": {}, "TRACKERS": {"ZENITH": {}}})


def _audiobook(**values: object) -> Meta:
    defaults: dict[str, object] = {
        "category": "BOOK",
        "type": "M4B",
        "audiobook": True,
        "narrator": "Narrator",
        "isbn": "",
        "asin": "",
        "book_language_iso": "ENG",
        "filelist": ["Author - Title (2024) [ENG].m4b"],
    }
    defaults.update(values)
    return Meta(**defaults)


def test_zenith_audiobook_without_identifier_goes_to_moderation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    meta = _audiobook()
    assert asyncio.run(_tracker()._book_format_policy(meta, [], True)) is True
    assert meta.modq is True
    assert "staff approval" in caplog.text


def test_zenith_ebook_without_identifier_still_blocks() -> None:
    meta = Meta(category="BOOK", type="EPUB", isbn="", asin="")
    assert asyncio.run(_tracker()._book_format_policy(meta, [], False)) is False


def test_zenith_language_is_derived_from_the_metadata() -> None:
    meta = Meta(category="BOOK", type="EPUB", isbn="9780000000000", book_language="Danish")
    assert _tracker()._book_language_policy(meta) is True
    assert meta.book_language_iso == "dan"
    meta = Meta(category="BOOK", type="EPUB", isbn="9780000000000")
    assert _tracker()._book_language_policy(meta) is False


@pytest.mark.asyncio
async def test_zenith_sends_the_moderation_flag() -> None:
    tracker = _tracker()
    data = await tracker.get_additional_data(_audiobook(modq=True))
    assert data["mod_queue_opt_in"] == "1"
