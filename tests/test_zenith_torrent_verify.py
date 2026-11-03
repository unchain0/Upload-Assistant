from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

from src.domain_models.release import Meta
from src.integrations.trackers.common import Common
from src.services import tracker_upload_service


def _common(tmp_path: Path) -> Common:
    return Common({"DEFAULT": {}, "TRACKERS": {}})


def test_isolated_zenith_meta_skips_the_early_torrent() -> None:
    meta = Meta(category="BOOK", audiobook=True, uuid="abc")
    prepared = tracker_upload_service._zenith_isolated_meta(
        meta, "/tmp/release/book.m4b"
    )
    assert prepared.nohash is True


def test_missing_base_is_created_from_the_prepared_path(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    from src.integrations.torrent import torrent_creator

    meta = Meta(
        category="BOOK",
        audiobook=True,
        uuid="abc",
        base_dir=str(tmp_path),
        path=str(tmp_path / "book.m4b"),
    )
    target = tmp_path / "tmp" / "abc" / "BASE.torrent"
    target.parent.mkdir(parents=True)

    async def fake_create(_meta, path, _name, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        target.write_bytes(b"x")
        return target

    monkeypatch.setattr(
        torrent_creator.TorrentCreator, "create_torrent", staticmethod(fake_create)
    )
    common = _common(tmp_path)
    common._load_upload_torrent = AsyncMock(  # type: ignore[method-assign]
        return_value=type("T", (), {"metainfo": {"info": {}}, "verify": lambda self, _p: None})()
    )
    common._write_upload_torrent = AsyncMock()  # type: ignore[method-assign]
    asyncio.run(
        common.create_torrent_for_upload(meta, "ZENITH", "[ZENITH]")
    )
    common._write_upload_torrent.assert_awaited()


def test_torrent_mismatch_blocks_the_upload(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    meta = Meta(
        category="BOOK",
        audiobook=True,
        zentag_prepared=True,
        uuid="abc",
        base_dir=str(tmp_path),
        path=str(tmp_path / "book.m4b"),
    )
    target = tmp_path / "tmp" / "abc" / "BASE.torrent"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x")
    common = _common(tmp_path)

    class FakeTorrent:
        metainfo = {"info": {}}

        def verify(self, _path: str) -> None:
            raise RuntimeError("Corruption in piece 1913")

    common._load_upload_torrent = AsyncMock(  # type: ignore[method-assign]
        return_value=FakeTorrent()
    )
    common._write_upload_torrent = AsyncMock()  # type: ignore[method-assign]
    asyncio.run(
        common.create_torrent_for_upload(meta, "ZENITH", "[ZENITH]")
    )
    common._write_upload_torrent.assert_not_awaited()


class _Shape:
    def __init__(self, info: dict) -> None:
        self.metainfo = {"info": info}


def test_folder_torrent_is_verified_from_its_parent(tmp_path: Path) -> None:
    release = tmp_path / "release"
    release.mkdir()
    meta = Meta(category="BOOK", path=str(release))
    root = Common._verify_root(_Shape({"files": [{"path": ["x.m4b"]}]}), meta)
    assert root == tmp_path


def test_single_file_torrent_is_verified_from_the_file(tmp_path: Path) -> None:
    book = tmp_path / "book.epub"
    book.write_bytes(b"x")
    meta = Meta(category="BOOK", path=str(book))
    root = Common._verify_root(_Shape({"length": 1}), meta)
    assert root == book
