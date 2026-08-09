"""Regression tests for automatic ebook category detection."""

# ruff: noqa: S101

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.book_extractors import extract_isbn_from_pdf, extract_pdf_page_count, validate_isbn_checksum
from src.book_prep import resolve_book_filelist
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


@pytest.mark.parametrize("extension", [".azw", ".azw3", ".fb2", ".html", ".chm", ".djvu", ".doc", ".docx", ".kfx", ".lit", ".pdb", ".txt", ".rtf"])
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
