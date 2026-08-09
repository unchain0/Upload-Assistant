# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
from pathlib import Path
from typing import Any

import pytest

import src.book_prep as book_prep
from src.args import Args
from src.meta import Meta


def test_cli_accepts_complete_audiobook_edition_override(tmp_path: Path) -> None:
    source = tmp_path / "The Gabriel Hounds.mp3"
    source.touch()

    meta, _, _ = Args({"DEFAULT": {"screens": 1}}).parse(
        [
            str(source),
            "--book-title",
            "The Gabriel Hounds",
            "--author",
            "Mary Stewart",
            "--narrator",
            "Davina Porter",
            "--publisher",
            "Recorded Books",
            "--year",
            "1991",
            "--isbn",
            "9781664616110",
        ],
        Meta(),
    )

    assert meta.title == "The Gabriel Hounds"
    assert meta.author == "Mary Stewart"
    assert meta.narrator == "Davina Porter"
    assert meta.publisher == "Recorded Books"
    assert meta.year == 1991
    assert meta.isbn == "9781664616110"


@pytest.mark.asyncio
async def test_complete_audiobook_edition_override_survives_conflicting_enrichment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "The Gabriel Hounds.mp3"
    source.touch()
    meta = Meta(
        path=str(source),
        filelist=[str(source)],
        audiobook=True,
        book_title="The Gabriel Hounds",
        book_author="Mary Stewart",
        narrator="Davina Porter",
        book_publisher="Recorded Books",
        book_isbn="9781664616110",
        manual_year=1991,
        title="The Gabriel Hounds",
        author="Mary Stewart",
        publisher="Recorded Books",
        isbn="9781664616110",
        year=1991,
        torrent_comments=[{"trackers": "https://myanonamouse.net/announce", "comment": "MID=123"}],
    )

    async def export_stub(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}

    async def mam_stub(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "title": "The Gabriel Hounds",
            "author": "Mary Stewart",
            "narrator": "Ellie Heydon",
            "publisher": "Hodder & Stoughton",
            "year": 2019,
            "isbn": "9781529378917",
        }

    async def no_result(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def duration_stub(_filelist: list[str]) -> tuple[float, str]:
        return 39875.0, "11h 04m 35s"

    async def bitrate_stub(_filelist: list[str]) -> int:
        return 64

    monkeypatch.setattr(book_prep, "export_info", export_stub)
    monkeypatch.setattr("src.myanonamouse.myanonamouse_manager.search_by_id", mam_stub)
    monkeypatch.setattr("src.google_books.google_books_manager.search_by_isbn", no_result)
    monkeypatch.setattr("src.openlibrary.openlibrary_manager.search_by_isbn", no_result)
    monkeypatch.setattr(book_prep, "get_audiobook_duration", duration_stub)
    monkeypatch.setattr(book_prep, "get_audiobook_bitrate", bitrate_stub)

    await book_prep.gather_book_prep(meta, str(source), str(tmp_path), {"DEFAULT": {}})

    assert meta.title == "The Gabriel Hounds"
    assert meta.author == "Mary Stewart"
    assert meta.narrator == "Davina Porter"
    assert meta.publisher == "Recorded Books"
    assert meta.year == 1991
    assert meta.isbn == "9781664616110"
