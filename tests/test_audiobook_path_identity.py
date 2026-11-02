from __future__ import annotations

from pathlib import Path

from src.domain_models.release import Meta
from src.services import book_preparation


class _Ctx:
    def __init__(self, meta: Meta, videopath: str) -> None:
        self.meta = meta
        self.videopath = videopath


def _ctx(tmp_path: Path) -> _Ctx:
    book = tmp_path / "Brené Brown" / "Daring Greatly (2012)" / "Daring Greatly.m4b"
    book.parent.mkdir(parents=True)
    book.write_bytes(b"audio")
    meta = Meta(audiobook=True, category="BOOK", path=str(book), filelist=[str(book)])
    return _Ctx(meta, str(book))


def test_identity_is_derived_from_the_audiobook_path(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    book_preparation._apply_audiobook_path_identity(ctx)  # type: ignore[arg-type]
    assert ctx.meta.author == "Brené Brown"
    assert ctx.meta.title == "Daring Greatly"
    assert ctx.meta.year == 2012


def test_existing_metadata_is_not_overwritten(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    ctx.meta.author = "Someone Else"
    ctx.meta.title = "Another Title"
    book_preparation._apply_audiobook_path_identity(ctx)  # type: ignore[arg-type]
    assert ctx.meta.author == "Someone Else"
    assert ctx.meta.title == "Another Title"


def test_non_audiobook_is_untouched(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    ctx.meta.audiobook = False
    book_preparation._apply_audiobook_path_identity(ctx)  # type: ignore[arg-type]
    assert ctx.meta.author == ""
    assert ctx.meta.title == ""
