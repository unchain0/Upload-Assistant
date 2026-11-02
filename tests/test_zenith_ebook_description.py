from __future__ import annotations

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.znth import Zenith


def _tracker() -> Zenith:
    return Zenith({"DEFAULT": {}, "TRACKERS": {"ZENITH": {}}})


def _meta(**values: object) -> Meta:
    defaults: dict[str, object] = {
        "category": "BOOK",
        "type": "EPUB",
        "isbn": "9780000000000",
        "author": "Henry Hazlitt",
        "title": "Economics in One Lesson",
        "filelist": ["Henry Hazlitt - Economics in One Lesson (1946) [ENG 9780000000000].epub"],
    }
    defaults.update(values)
    return Meta(**defaults)


def test_zenith_ebook_with_publisher_passes() -> None:
    meta = _meta(publisher="Harper")
    assert _tracker()._ebook_description_policy(meta) is True
    assert meta.modq is False


def test_zenith_ebook_without_publisher_goes_to_moderation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    meta = _meta()
    assert _tracker()._ebook_description_policy(meta) is True
    assert meta.modq is True
    assert "publisher" in caplog.text


def test_zenith_language_does_not_hold_the_upload() -> None:
    meta = _meta(publisher="Harper", book_language_iso="eng")
    assert _tracker()._ebook_description_policy(meta) is True
    assert meta.modq is False
