from __future__ import annotations

from src.domain_models.release import Meta
from src.services.tracker_upload_service import _zenith_isolated_meta


def test_ebook_torrent_is_single_file() -> None:
    meta = Meta(category="BOOK", audiobook=False, uuid="abc")
    prepared = _zenith_isolated_meta(meta, "/tmp/release/book.epub")
    assert prepared.keep_folder is False
    assert prepared.path == "/tmp/release/book.epub"


def test_audiobook_keeps_its_folder() -> None:
    meta = Meta(category="BOOK", audiobook=True, uuid="abc")
    prepared = _zenith_isolated_meta(meta, "/tmp/release/book.m4b")
    assert prepared.keep_folder is True
