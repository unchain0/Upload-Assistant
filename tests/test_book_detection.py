"""Regression tests for automatic ebook category detection."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.book_extractors import validate_isbn_checksum
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


def test_myanonamouse_uses_explicit_publication_year():
    metadata = MyAnonamouseManager()._parse_torrent_info({"title": "80's Adventures", "publication_year": "2021"})

    assert metadata["year"] == 2021
