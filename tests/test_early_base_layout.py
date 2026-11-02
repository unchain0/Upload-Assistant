from __future__ import annotations

from pathlib import Path

from torf import Torrent

from src.domain_models.release import Meta
from src.services.early_artifact_service import _base_layout_matches


def _write(path: Path, root: Path) -> Path:
    torrent = Torrent(path=str(root), trackers=["https://example/announce"])
    torrent.generate()
    torrent.write(str(path))
    return path


def test_folder_base_does_not_match_a_single_file_meta(tmp_path: Path) -> None:
    folder = tmp_path / "folder"
    folder.mkdir()
    (folder / "Book.epub").write_bytes(b"x" * 100)
    base = _write(tmp_path / "BASE.torrent", folder)
    single = Meta(category="BOOK", isdir=False, audiobook=False)
    assert _base_layout_matches(single, base) is False


def test_single_file_base_matches(tmp_path: Path) -> None:
    book = tmp_path / "Book.epub"
    book.write_bytes(b"x" * 100)
    base = _write(tmp_path / "BASE.torrent", book)
    meta = Meta(category="BOOK", isdir=False, audiobook=False)
    assert _base_layout_matches(meta, base) is True


def test_folder_base_matches_a_folder_meta(tmp_path: Path) -> None:
    folder = tmp_path / "folder"
    folder.mkdir()
    (folder / "Book.epub").write_bytes(b"x" * 100)
    base = _write(tmp_path / "BASE.torrent", folder)
    meta = Meta(category="BOOK", isdir=True, audiobook=True)
    assert _base_layout_matches(meta, base) is True
