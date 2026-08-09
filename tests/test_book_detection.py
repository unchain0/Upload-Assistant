"""Regression tests for automatic ebook category detection."""

# ruff: noqa: S101

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.book_extractors import extract_isbn_from_pdf, extract_pdf_page_count, validate_isbn_checksum
from src.book_prep import book_identity_conflict, book_identity_from_path, missing_book_fields, resolve_book_filelist
from src.exceptions import ItemProcessingError
from src.meta import Meta
from src.myanonamouse import MyAnonamouseManager
from src.prep_helpers import detect_disc_and_category


def test_validate_isbn_checksum_rejects_mam_numeric_id() -> None:
    assert validate_isbn_checksum("465097588") is None


@pytest.mark.parametrize("extension", [".azw", ".azw3", ".fb2", ".html", ".chm", ".djvu", ".doc", ".docx", ".kfx", ".lit", ".pdb", ".txt", ".rtf"])
def test_azw_files_are_detected_as_books(extension, tmp_path):
    book = tmp_path / f"example{extension}"
    book.write_bytes(b"Kindle ebook")
    meta = Meta(path=str(book))
    prep = SimpleNamespace(disc_info_manager=SimpleNamespace(get_disc=AsyncMock(return_value=("", str(book), {}, []))))

    asyncio.run(detect_disc_and_category(prep, meta))

    assert meta.category == "BOOK"


@pytest.mark.parametrize("extension", [".azw", ".azw3", ".fb2", ".html", ".chm", ".djvu", ".doc", ".docx", ".kfx", ".lit", ".pdb", ".rtf"])
def test_azw_files_are_included_when_resolving_book_directories(extension, tmp_path):
    book = tmp_path / f"example{extension}"
    book.write_bytes(b"Kindle ebook")
    meta = Meta()

    videopath, filelist, _, _ = resolve_book_filelist(meta, str(tmp_path))

    assert videopath == str(book.resolve())
    assert filelist == [str(book.resolve())]
    assert meta.audiobook is False


@pytest.mark.parametrize("extension", [".opus", ".alac", ".aax", ".aaxc"])
def test_new_audiobook_formats_are_detected(extension, tmp_path):
    audiobook = tmp_path / f"chapter01{extension}"
    audiobook.write_bytes(b"audiobook")
    meta = Meta()

    _, filelist, _, _ = resolve_book_filelist(meta, str(tmp_path))

    assert filelist == [str(audiobook.resolve())]
    assert meta.audiobook is True


def test_text_sidecars_are_excluded_when_a_richer_book_format_exists(tmp_path):
    book = tmp_path / "book.epub"
    readme = tmp_path / "README.txt"
    cover = tmp_path / "cover.html"
    book.write_bytes(b"ebook")
    readme.write_text("release notes")
    cover.write_text("cover page")
    meta = Meta()

    videopath, filelist, _, _ = resolve_book_filelist(meta, str(tmp_path))

    assert videopath == str(book.resolve())
    assert filelist == [str(book.resolve())]


@pytest.mark.parametrize(
    "filename",
    [
        "Pocket PC Serials.txt",
        "10 Security Enhancements.txt",
        "A very small tut for RealMedia.txt",
    ],
)
def test_standalone_plain_text_book_is_rejected(filename: str, tmp_path: Path) -> None:
    document = tmp_path / filename
    document.write_text("plain text", encoding="utf-8")

    with pytest.raises(ItemProcessingError, match="Plain-text TXT files are not supported"):
        resolve_book_filelist(Meta(), str(document))


def test_plain_text_only_book_directory_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "First.txt").write_text("first", encoding="utf-8")
    (tmp_path / "Second.txt").write_text("second", encoding="utf-8")

    with pytest.raises(ItemProcessingError, match="Plain-text TXT files are not supported"):
        resolve_book_filelist(Meta(), str(tmp_path))


def test_multiple_ebooks_in_one_path_are_rejected(tmp_path) -> None:
    (tmp_path / "Steve Jobs.pdf").write_bytes(b"pdf")
    (tmp_path / "Einstein.pdf").write_bytes(b"pdf")

    with pytest.raises(ItemProcessingError, match="Upload each book separately"):
        resolve_book_filelist(Meta(), str(tmp_path))


def test_same_ebook_in_multiple_formats_is_accepted(tmp_path) -> None:
    stem = "Jordan B. Peterson - 12 Rules for Life_ An Antidote to Chaos"
    for extension in (".azw3", ".epub", ".mobi"):
        (tmp_path / f"{stem}{extension}").write_bytes(extension.encode())
    (tmp_path / "Jordan B. Peterson - 12 Rules for Life.txt").write_text("sidecar")

    _, filelist, _, _ = resolve_book_filelist(Meta(), str(tmp_path))

    assert len(filelist) == 3
    assert {Path(file).suffix for file in filelist} == {".azw3", ".epub", ".mobi"}


def test_mixed_ebook_and_audiobook_release_is_rejected(tmp_path) -> None:
    (tmp_path / "Everything Is F_cked.epub").write_bytes(b"ebook")
    (tmp_path / "Everything Is F_cked.mp3").write_bytes(b"audio")

    with pytest.raises(ItemProcessingError, match="Upload each media type separately"):
        resolve_book_filelist(Meta(), str(tmp_path))


def test_book_identity_falls_back_to_directory_name(tmp_path) -> None:
    release = tmp_path / "Ian Stewart - How to Cut a Cake_ And Other Mathematical Conundrums"
    release.mkdir()

    assert book_identity_from_path(str(release)) == ("Ian Stewart", "How to Cut a Cake: And Other Mathematical Conundrums")


def test_book_identity_rejects_unrelated_enriched_title_for_same_author(tmp_path) -> None:
    release = tmp_path / "Gary John Bishop - Wise as Fu_k; Simple Truths to Guide You Through the Sh_tstorms of Life"
    meta = Meta(author="Gary John Bishop", title="Stop Doing That Sh*t: End Self-Sabotage and Demand Your Life Back")

    assert book_identity_conflict(meta, str(release)) is not None
    assert book_identity_from_path(str(release))[1].startswith("Wise as Fu_k;")


def test_book_identity_accepts_matching_enriched_title(tmp_path) -> None:
    release = tmp_path / "Jordan B. Peterson - 12 Rules for Life_ An Antidote to Chaos"
    meta = Meta(author="Jordan B. Peterson", title="12 Rules for Life: An Antidote to Chaos")

    assert book_identity_conflict(meta, str(release)) is None


def test_book_identity_rejects_conflicting_author(tmp_path) -> None:
    release = tmp_path / "Brian Kernighan - The C Programming Language"
    meta = Meta(author="Dennis Ritchie", title="The C Programming Language")

    assert book_identity_conflict(meta, str(release)) == "Book metadata author 'Dennis Ritchie' conflicts with source author 'Brian Kernighan'"


def test_unattended_audiobook_requires_complete_edition_metadata() -> None:
    meta = Meta(
        audiobook=True,
        title="Meditations",
        author="Marcus Aurelius",
        year=2011,
        book_language="English",
        book_language_iso="eng",
        asin="invalid",
    )

    assert missing_book_fields(meta) == ["narrator", "publisher", "isbn_or_asin"]


@pytest.mark.asyncio
async def test_m4b_cover_fallback_ignores_malformed_chapter_title(tmp_path, monkeypatch) -> None:
    import mutagen
    import mutagen.mp4

    from src.takescreens import extract_embedded_cover_from_audiobook

    audiobook = tmp_path / "book.m4b"
    artwork = tmp_path / "cover.jpg"
    audiobook.write_bytes(b"m4b")
    monkeypatch.setattr(mutagen, "File", lambda _path: (_ for _ in ()).throw(ValueError("chapter 0 title: invalid UTF-8")))
    monkeypatch.setattr(mutagen.mp4, "Atoms", lambda _fileobj: object())
    monkeypatch.setattr(mutagen.mp4, "MP4Tags", lambda _atoms, _fileobj: {"covr": [b"cover-bytes"]})

    result = await extract_embedded_cover_from_audiobook(Meta(filelist=[str(audiobook)]), str(artwork))

    assert result is True
    assert artwork.read_bytes() == b"cover-bytes"


def test_scene_tv_rar_is_not_auto_detected_as_game(tmp_path):
    release = tmp_path / "Il.Etait.Une.Fois.Dans.Le.Trouble.S07E12.FRENCH.1080p.WEB.H264-BAWLS"
    release.mkdir()
    (release / "release.rar").write_bytes(b"archive")
    (release / "release.nfo").write_text("TV release", encoding="utf-8")
    meta = Meta(path=str(release))
    prep = SimpleNamespace(disc_info_manager=SimpleNamespace(get_disc=AsyncMock(return_value=("", str(release), {}, []))))

    asyncio.run(detect_disc_and_category(prep, meta))

    assert meta.category != "GAME"


def test_scene_game_iso_is_still_auto_detected_as_game(tmp_path):
    release = tmp_path / "Cellar.Keeper-TENOKE"
    release.mkdir()
    (release / "tenoke-cellar.keeper.iso").write_bytes(b"disc")
    meta = Meta(path=str(release))
    prep = SimpleNamespace(disc_info_manager=SimpleNamespace(get_disc=AsyncMock(return_value=("", str(release), {}, []))))

    asyncio.run(detect_disc_and_category(prep, meta))

    assert meta.category == "GAME"


def test_dmg_is_auto_detected_as_game_software(tmp_path):
    installer = tmp_path / "Native_Instruments_SuperStarSaw_1.0.0_[HCiSO].dmg"
    installer.write_bytes(b"installer")
    meta = Meta(path=str(installer))
    prep = SimpleNamespace(disc_info_manager=SimpleNamespace(get_disc=AsyncMock(return_value=("", str(installer), {}, []))))

    asyncio.run(detect_disc_and_category(prep, meta))

    assert meta.category == "GAME"


def test_pkg_takes_precedence_over_text_sidecar(tmp_path):
    release = tmp_path / "Guitar_Pro_8.1.5-31_[atb]"
    release.mkdir()
    (release / "Guitar Pro 8.1.5-31 [atb].pkg").write_bytes(b"installer")
    (release / "Read.txt").write_text("install PKG\nUse Serial", encoding="utf-8")
    meta = Meta(path=str(release))
    prep = SimpleNamespace(disc_info_manager=SimpleNamespace(get_disc=AsyncMock(return_value=("", str(release), {}, []))))

    asyncio.run(detect_disc_and_category(prep, meta))

    assert meta.category == "GAME"


def test_myanonamouse_uses_explicit_publication_year():
    metadata = MyAnonamouseManager()._parse_torrent_info({"title": "80's Adventures", "publication_year": "2021"})

    assert metadata["year"] == 2021


def test_myanonamouse_restores_valid_leading_zero_isbn() -> None:
    metadata = MyAnonamouseManager()._parse_torrent_info({"title": "The Quantum Labyrinth", "isbn": 465097588})

    assert metadata["isbn"] == "0465097588"


def test_myanonamouse_does_not_replace_metadata_with_invalid_isbn() -> None:
    metadata = MyAnonamouseManager()._parse_torrent_info({"title": "Example", "isbn": 123})

    assert "isbn" not in metadata


def test_myanonamouse_cleans_filename_title() -> None:
    metadata = MyAnonamouseManager()._parse_torrent_info(
        {
            "title": "Jon Gertner - The Idea Factory - Bell Labs And The Great Age Of American Innovation - 9781101561089.epub",
            "author_info": {"1": "Jon Gertner"},
        }
    )

    assert metadata["title"] == "The Idea Factory - Bell Labs And The Great Age Of American Innovation"


def test_pdf_extraction_counts_pages_and_ignores_unlabelled_isbn10(tmp_path) -> None:
    fitz = pytest.importorskip("fitz")
    pdf = tmp_path / "book.pdf"
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), "Reference number 4959787066")
        document.new_page()
        document.save(pdf)

    assert extract_pdf_page_count(str(pdf)) == 2
    assert extract_isbn_from_pdf(str(pdf)) is None
