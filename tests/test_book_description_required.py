from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.domain_models.errors import BookDescriptionMissingError
from src.domain_models.release import Meta
from src.integrations.trackers.description_builder import (
    require_book_description,
)
from src.services import book_preparation as book_prep


def test_book_without_any_description_source_is_rejected() -> None:
    meta = Meta(category="BOOK", title="Hell Mode - Volume 08")
    with pytest.raises(BookDescriptionMissingError, match="description or overview"):
        require_book_description(meta)


def test_book_with_overview_or_description_source_passes(tmp_path: Path) -> None:
    require_book_description(
        Meta(category="BOOK", title="Book", overview="A real description.")
    )
    require_book_description(
        Meta(
            category="BOOK",
            title="Book",
            description_file=str(tmp_path / "description.txt"),
        )
    )
    require_book_description(Meta(category="BOOK", title="Comic", comic=True))
    require_book_description(Meta(category="MOVIE", title="Movie"))


@pytest.mark.asyncio
async def test_epub_without_metadata_is_blocked_before_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "Hell Mode - Volume 08.epub"
    source.touch()
    meta = Meta(path=str(source), filelist=[str(source)])

    async def empty(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}

    async def no_result(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(book_prep, "_get_epubmeta_output", lambda _path: "")
    monkeypatch.setattr(book_prep, "_extract_epub_metadata", lambda _path: {})
    monkeypatch.setattr(
        book_prep, "_epub_content_identifiers", lambda _path: (set(), set())
    )
    monkeypatch.setattr(book_prep, "export_info", empty)
    monkeypatch.setattr(
        "src.integrations.external_apis.myanonamouse.myanonamouse_manager.search_by_id",
        no_result,
    )
    monkeypatch.setattr(
        "src.integrations.external_apis.google_books.google_books_manager.search_by_isbn",
        no_result,
    )
    monkeypatch.setattr(
        "src.integrations.external_apis.openlibrary.openlibrary_manager.search_by_isbn",
        no_result,
    )

    await book_prep.gather_book_prep(
        meta, str(source), str(tmp_path), {"DEFAULT": {}}
    )

    with pytest.raises(BookDescriptionMissingError):
        require_book_description(meta)
