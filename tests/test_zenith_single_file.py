from __future__ import annotations

from pathlib import Path

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.znth import Zenith
from src.services.tracker_upload_service import _zenith_isolated_meta


def _tracker() -> Zenith:
    return Zenith({"DEFAULT": {}, "TRACKERS": {"ZENITH": {}}})


def test_ebook_is_a_single_file_torrent(tmp_path: Path) -> None:
    release = tmp_path / "release"
    release.mkdir()
    book = release / "Author - Title (2020) [ENG 9780000000000].epub"
    book.write_bytes(b"x")
    meta = Meta(category="BOOK", audiobook=False, uuid="abc")
    prepared = _zenith_isolated_meta(meta, str(release))
    assert prepared.path == str(book)
    assert prepared.filelist == [str(book)]
    assert prepared.isdir is False
    assert prepared.keep_folder is False
    assert prepared.nohash is True
    assert _tracker()._book_folder_policy(
        prepared, prepared.filelist, False
    ) is True


def test_audiobook_keeps_its_folder(tmp_path: Path) -> None:
    release = tmp_path / "release"
    release.mkdir()
    (release / "book.m4b").write_bytes(b"x")
    meta = Meta(category="BOOK", audiobook=True, uuid="abc")
    prepared = _zenith_isolated_meta(meta, str(release))
    assert prepared.path == str(release)
    assert prepared.keep_folder is True


def test_unprepared_single_file_is_still_rejected(tmp_path: Path) -> None:
    book = tmp_path / "release" / "book.epub"
    book.parent.mkdir()
    book.write_bytes(b"x")
    meta = Meta(
        category="BOOK",
        audiobook=False,
        isdir=True,
        keep_folder=False,
        path=str(book.parent),
        filelist=[str(book)],
    )
    assert _tracker()._book_folder_policy(meta, meta.filelist, False) is False


def test_generated_ebook_torrent_matches_the_site_single_file_shape(
    tmp_path: Path,
) -> None:
    import asyncio

    from src.integrations.torrent.torrent_creator import TorrentCreator

    release = tmp_path / "release"
    release.mkdir()
    book = release / "Author - Title (2020) [ENG 9780000000000].epub"
    book.write_bytes(b"x" * 1000)
    meta = Meta(
        category="BOOK",
        audiobook=False,
        uuid="abc",
        base_dir=str(tmp_path),
        trackers=["ZENITH"],
    )
    prepared = _zenith_isolated_meta(meta, str(release))
    (tmp_path / "tmp" / "abc-zenith").mkdir(parents=True)
    torrent = asyncio.run(
        TorrentCreator.create_torrent(prepared, prepared.path, "BASE")
    )
    info = torrent.metainfo["info"]
    assert info["name"] == book.name
    assert "files" not in info
    assert info["length"] == 1000
