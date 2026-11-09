from __future__ import annotations

import asyncio
from pathlib import Path

from src.domain_models.release import Meta
from src.services import tracker_status_service


def _manager() -> tracker_status_service.TrackerStatusManager:
    return tracker_status_service.TrackerStatusManager({"TRACKERS": {}})


def test_zenith_preparation_runs_before_status(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from src.services import tracker_upload_service

    prepared = Meta(category="BOOK", title="Prepared")

    async def fake_prepare(meta, tracker, config, *args):  # type: ignore[no-untyped-def]
        assert tracker == "ZENITH"
        return prepared

    monkeypatch.setattr(tracker_upload_service, "prepare_tracker_meta", fake_prepare)
    book = tmp_path / "Author" / "Title (2020)" / "book.epub"
    book.parent.mkdir(parents=True)
    book.write_bytes(b"x")
    meta = Meta(
        category="BOOK",
        trackers=["ZENITH"],
        unattended=True,
        path=str(book),
        filelist=[str(book)],
    )
    result = asyncio.run(_manager()._prepare_zenith_before_status(meta))
    assert result.title == "Prepared"


def test_other_books_are_not_prepared(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from src.services import tracker_upload_service

    async def fail(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("must not prepare")

    monkeypatch.setattr(tracker_upload_service, "prepare_tracker_meta", fail)
    meta = Meta(category="BOOK", trackers=["ZENITH"], unattended=False)
    assert asyncio.run(_manager()._prepare_zenith_before_status(meta)) is meta
