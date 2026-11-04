from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from src.domain_models.release import Meta
from src.integrations.external_apis.google_books import GoogleBooksManager
from src.integrations.media import zentag
from src.integrations.media.zenith_ebook import edition, filename
from src.services.tracker_upload_service import _zenith_isolated_meta


def _hayek(path: Path, base: Path) -> Meta:
    return Meta(
        category="BOOK",
        author="F. A. Hayek",
        title="The Road to Serfdom",
        year=2007,
        isbn="9780226320540",
        book_language_iso="eng",
        edition="Definitive",
        source="Retail",
        path=str(path),
        filelist=[str(path)],
        base_dir=str(base),
        uuid="hayek",
        trackers=["ZENITH"],
        unattended=True,
    )


@pytest.mark.asyncio
async def test_prepare_returns_the_file_that_becomes_the_torrent_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "The Road to Serfdom (2007).epub"
    source.write_bytes(b"book")
    meta = _hayek(source, tmp_path)
    monkeypatch.setattr(zentag, "should_prepare_zenith_ebook", lambda *_: True)

    async def missing_ebook_meta(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("zentag failed: ebook-meta not usable")

    monkeypatch.setattr(zentag, "_prepare_ebook_copy", missing_ebook_meta)
    target = await zentag.prepare_zenith_ebook(meta, str(tmp_path), {})

    assert target is not None
    prepared_file = Path(target)
    assert prepared_file.is_file()
    assert prepared_file.name == (
        "F. A. Hayek - The Road to Serfdom (2007) "
        "[ENG Definitive 9780226320540 Retail].epub"
    )

    isolated = _zenith_isolated_meta(meta, target)
    assert isolated.path == str(prepared_file)
    assert isolated.filelist == [str(prepared_file)]
    assert isolated.isdir is False
    assert isolated.keep_folder is False
    assert len(isolated.filelist) == 1
    assert "/" not in Path(isolated.filelist[0]).name


def test_manual_edition_wins_over_a_lookup_edition() -> None:
    meta = Meta(
        category="BOOK",
        author="F. A. Hayek",
        title="The Road to Serfdom",
        year=2007,
        isbn="9780226320540",
        book_language_iso="eng",
        manual_edition="Definitive",
        edition="2nd",
    )
    assert edition(meta) == "Definitive"
    name = filename(meta)
    assert name is not None
    assert "[ENG Definitive 9780226320540]" in name
    assert "2nd" not in name


@pytest.mark.asyncio
async def test_google_quota_429_does_not_raise() -> None:
    response = httpx.Response(
        429, request=httpx.Request("GET", "https://google.test/books")
    )
    cache = SimpleNamespace(set=_unused_set)
    assert (
        await GoogleBooksManager()._metadata_from_response(
            response, "9780226320540", "9780226320540", cache
        )
        is None
    )


async def _unused_set(*_args: object, **_kwargs: object) -> None:
    return None
