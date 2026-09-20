from __future__ import annotations

import asyncio
import errno
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import AsyncMock

import pytest

from src.domain_models.release import Meta
from src.integrations.torrent_clients import rtorrent as rtorrent_module
from src.integrations.torrent_clients.rtorrent import RtorrentClientMixin


class _RPC:
    instances: ClassVar[list[_RPC]] = []

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.load = self
        self.d = SimpleNamespace(custom1=self)
        self.loads: list[tuple[object, ...]] = []
        self.labels: list[tuple[object, ...]] = []
        self.__class__.instances.append(self)

    def start_verbose(self, *args: object) -> None:
        self.loads.append(args)

    def set(self, *args: object) -> None:
        self.labels.append(args)


class _Torrent:
    def __init__(
        self,
        *,
        infohash: str = "hash",
        comment: str = "",
        name: str = "Release",
    ) -> None:
        self.infohash = infohash
        self.infohash_v1 = infohash
        self.comment = comment
        self.name = name


class _Rtorrent(RtorrentClientMixin):
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {
            "DEFAULT": {},
            "TORRENT_CLIENTS": {},
            "TRACKERS": {},
        }
        self.valid_result: tuple[bool, str] = (True, "")
        self.valid_calls: list[tuple[object, ...]] = []
        self.ids: dict[str, Any] = {"PTP": 123}

    async def is_valid_torrent(
        self,
        meta: Meta,
        torrent_path: str,
        torrenthash: str,
        torrent_client: str,
        client: dict[str, Any],
    ) -> tuple[bool, str]:
        self.valid_calls.append(
            (meta, torrent_path, torrenthash, torrent_client, client)
        )
        return self.valid_result

    def _extract_tracker_ids_from_comment(
        self, comment: str
    ) -> dict[str, Any]:
        assert isinstance(comment, str)
        return self.ids


def _meta(tmp_path: Path, path: Path | str, **values: object) -> Meta:
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "uuid": "release",
        "path": str(path),
        "filelist": [str(path)],
        "keep_folder": False,
        "category": "MOVIE",
        "debug": True,
        "rtorrent_label": None,
        "torrent_comments": [],
        "infohash": "abc",
    }
    state.update(values)
    return Meta(state)


def _client(**values: object) -> dict[str, Any]:
    result: dict[str, Any] = {
        "rtorrent_url": "https://rtorrent.invalid/RPC2",
        "linking": None,
        "linked_folder": [],
    }
    result.update(values)
    return result


def _patch_base(
    monkeypatch: pytest.MonkeyPatch, metainfo: dict[str, Any] | None = None
) -> None:
    _RPC.instances = []
    monkeypatch.setattr(rtorrent_module.xmlrpc.client, "Server", _RPC)
    monkeypatch.setattr(
        rtorrent_module.ssl, "create_default_context", lambda: object()
    )
    monkeypatch.setattr(rtorrent_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        rtorrent_module,
        "_bencode_bread",
        lambda _path: (
            metainfo
            or {
                "info": {
                    "name": "release.mkv",
                    "length": 4,
                    "pieces": b"x" * 20,
                    "piece length": 32768,
                }
            }
        ),
    )
    encoded_calls = 0

    def encode(value: object) -> bytes:
        nonlocal encoded_calls
        encoded_calls += 1
        return f"encoded-{encoded_calls}-{bool(value)}".encode()

    monkeypatch.setattr(rtorrent_module, "_bencode_bencode", encode)
    monkeypatch.setattr(
        rtorrent_module,
        "_bencode_bwrite",
        lambda value, path: Path(path).write_bytes(repr(value).encode()),
    )


def test_rtorrent_rejects_missing_source_and_link_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_base(monkeypatch)
    mixin = _Rtorrent()
    with pytest.raises(ValueError, match="No source path"):
        mixin.rtorrent(
            "",
            "release.torrent",
            _Torrent(),
            _meta(tmp_path, "", filelist=[]),
            "/local",
            "/remote",
            _client(),
            "TEST",
        )

    source = tmp_path / "source.mkv"
    source.write_bytes(b"data")
    meta = _meta(tmp_path, source)
    with pytest.raises(ValueError, match="No suitable linked folder"):
        mixin.rtorrent(
            str(source),
            str(tmp_path / "release.torrent"),
            _Torrent(),
            meta,
            str(tmp_path),
            "/remote",
            _client(linking="hardlink"),
            "TEST",
        )


def test_rtorrent_without_linking_remote_mapping_labels_and_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.mkv"
    source.write_bytes(b"data")
    torrent_path = tmp_path / "release.torrent"
    torrent_path.write_bytes(b"torrent")
    _patch_base(monkeypatch)
    mixin = _Rtorrent()
    mixin.add_fast_resume = lambda meta, _path, _torrent: {
        **meta,
        "resume": True,
    }  # type: ignore[method-assign]
    meta = _meta(tmp_path, source, rtorrent_label="meta-label")
    client = _client(rtorrent_label="client-label")

    mixin.rtorrent(
        str(source),
        str(torrent_path),
        _Torrent(),
        meta,
        str(tmp_path),
        "/remote",
        client,
        "TEST",
    )

    rpc = _RPC.instances[-1]
    assert rpc.loads
    assert rpc.labels == [("hash", "client-label"), ("hash", "meta-label")]
    assert not (tmp_path / "fr.torrent").exists()

    # Failure to remove the temporary mapped fast-resume file is intentionally non-fatal.
    monkeypatch.setattr(
        Path,
        "unlink",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("denied")),
    )
    mixin.rtorrent(
        str(source),
        str(torrent_path),
        _Torrent(),
        meta,
        str(tmp_path),
        "/other",
        client,
        "TEST",
    )


def test_rtorrent_fast_resume_oserror_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.mkv"
    source.write_bytes(b"data")
    torrent_path = tmp_path / "release.torrent"
    torrent_path.write_bytes(b"torrent")
    _patch_base(monkeypatch)
    mixin = _Rtorrent()

    def fail(*_args: object, **_kwargs: object):
        raise OSError(errno.EINVAL, "bad resume")

    mixin.add_fast_resume = fail  # type: ignore[method-assign]
    with pytest.raises(OSError, match="bad resume"):
        mixin.rtorrent(
            str(source),
            str(torrent_path),
            _Torrent(),
            _meta(tmp_path, source),
            str(tmp_path),
            "/remote",
            _client(),
            "TEST",
        )


def test_rtorrent_hardlink_file_success_copy_and_existing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.mkv"
    source.write_bytes(b"data")
    torrent_path = tmp_path / "release.torrent"
    torrent_path.write_bytes(b"torrent")
    links = tmp_path / "links"
    links.mkdir()
    _patch_base(monkeypatch)
    mixin = _Rtorrent({"TRACKERS": {"TEST": {"link_dir_name": "custom"}}})
    mixin.add_fast_resume = lambda meta, _path, _torrent: meta  # type: ignore[method-assign]
    meta = _meta(tmp_path, source)

    mixin.rtorrent(
        str(source),
        str(torrent_path),
        _Torrent(),
        meta,
        str(tmp_path),
        str(tmp_path),
        _client(linking="hardlink", linked_folder=[str(links)]),
        "TEST",
    )
    linked = links / "custom" / source.name
    assert linked.exists() and source.samefile(linked)

    # Existing destination skips relinking.
    mixin.rtorrent(
        str(source),
        str(torrent_path),
        _Torrent(),
        meta,
        str(tmp_path),
        str(tmp_path),
        _client(linking="hardlink", linked_folder=[str(links)]),
        "TEST",
    )

    copy_links = tmp_path / "copy-links"
    copy_links.mkdir()
    original_link = os.link
    monkeypatch.setattr(
        rtorrent_module.os,
        "link",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("cross-device")
        ),
    )
    mixin.rtorrent(
        str(source),
        str(torrent_path),
        _Torrent(),
        meta,
        str(tmp_path),
        str(tmp_path),
        _client(linking="hardlink", linked_folder=[str(copy_links)]),
        "COPY",
    )
    copied = copy_links / "COPY" / source.name
    assert copied.read_bytes() == b"data"
    monkeypatch.setattr(rtorrent_module.os, "link", original_link)


def test_rtorrent_hardlink_directory_nested_copy_and_outer_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "release"
    nested = source / "Season 01"
    nested.mkdir(parents=True)
    root_file = source / "root.mkv"
    nested_file = nested / "episode.mkv"
    root_file.write_bytes(b"root")
    nested_file.write_bytes(b"episode")
    torrent_path = tmp_path / "release.torrent"
    torrent_path.write_bytes(b"torrent")
    links = tmp_path / "links"
    links.mkdir()
    _patch_base(monkeypatch)
    mixin = _Rtorrent()
    mixin.add_fast_resume = lambda meta, _path, _torrent: meta  # type: ignore[method-assign]
    meta = _meta(
        tmp_path,
        source,
        filelist=[str(root_file), str(nested_file)],
        keep_folder=True,
    )

    calls = 0
    original_link = os.link

    def selective_link(src: str | Path, dst: str | Path) -> None:
        nonlocal calls
        calls += 1
        if Path(src).name == "episode.mkv":
            raise OSError("fallback copy")
        original_link(src, dst)

    monkeypatch.setattr(rtorrent_module.os, "link", selective_link)
    mixin.rtorrent(
        str(source),
        str(torrent_path),
        _Torrent(),
        meta,
        str(tmp_path),
        str(tmp_path),
        _client(linking="hardlink", linked_folder=[str(links)]),
        "TEST",
    )
    target = links / "TEST" / source.name
    assert (target / "root.mkv").exists()
    assert (target / "Season 01" / "episode.mkv").read_bytes() == b"episode"
    assert calls == 2

    outer = tmp_path / "outer"
    outer.mkdir()
    monkeypatch.setattr(
        rtorrent_module.os,
        "walk",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("walk failed")
        ),
    )
    # Linking failure is logged and rTorrent addition continues to the mocked boundary.
    mixin.rtorrent(
        str(source),
        str(torrent_path),
        _Torrent(),
        meta,
        str(tmp_path),
        str(tmp_path),
        _client(linking="hardlink", linked_folder=[str(outer)]),
        "OUTER",
    )


def test_rtorrent_symlink_unix_windows_fallback_and_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.mkv"
    source.write_bytes(b"data")
    torrent_path = tmp_path / "release.torrent"
    torrent_path.write_bytes(b"torrent")
    links = tmp_path / "links"
    links.mkdir()
    _patch_base(monkeypatch)
    mixin = _Rtorrent()
    mixin.add_fast_resume = lambda meta, _path, _torrent: meta  # type: ignore[method-assign]
    meta = _meta(tmp_path, source)

    monkeypatch.setattr(rtorrent_module.platform, "system", lambda: "Linux")
    mixin.rtorrent(
        str(source),
        str(torrent_path),
        _Torrent(),
        meta,
        str(tmp_path),
        str(tmp_path),
        _client(linking="symlink", linked_folder=[str(links)]),
        "TEST",
    )
    assert (links / "TEST" / source.name).is_symlink()

    windows_links = tmp_path / "windows-links"
    windows_links.mkdir()
    monkeypatch.setattr(rtorrent_module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        rtorrent_module.os.path, "splitdrive", lambda value: ("C:", value)
    )
    mixin.rtorrent(
        str(source),
        str(torrent_path),
        _Torrent(),
        meta,
        str(tmp_path),
        str(tmp_path),
        _client(linking="symlink", linked_folder=[str(windows_links)]),
        "WIN",
    )
    assert (windows_links / "WIN" / source.name).is_symlink()

    failed_links = tmp_path / "failed-links"
    failed_links.mkdir()
    monkeypatch.setattr(
        Path,
        "symlink_to",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("symlink denied")
        ),
    )
    mixin.rtorrent(
        str(source),
        str(torrent_path),
        _Torrent(),
        meta,
        str(tmp_path),
        str(tmp_path),
        _client(linking="symlink", linked_folder=[str(failed_links)]),
        "FAIL",
    )


def test_add_fast_resume_single_directory_multifile_and_errors(
    tmp_path: Path,
) -> None:
    mixin = _Rtorrent()
    single = tmp_path / "single.bin"
    single.write_bytes(b"12345")
    meta = {
        "info": {
            "name": single.name,
            "length": 5,
            "pieces": b"x" * 40,
            "piece length": 4,
        }
    }
    result = mixin.add_fast_resume(meta, str(single), _Torrent())
    assert result["libtorrent_resume"]["bitfield"] == 2
    assert result["libtorrent_resume"]["files"][0]["completed"] == 2

    directory = tmp_path / "directory"
    directory.mkdir()
    inside = directory / "named.bin"
    inside.write_bytes(b"1234")
    meta = {
        "info": {
            "name": inside.name,
            "length": "4",
            "pieces": b"x" * 20,
            "piece length": "4",
        }
    }
    result = mixin.add_fast_resume(meta, str(directory), _Torrent())
    assert result["libtorrent_resume"]["files"][0]["completed"] == 1

    multi = tmp_path / "multi"
    multi.mkdir()
    (multi / "one.bin").write_bytes(b"123")
    (multi / "sub").mkdir()
    (multi / "sub" / "two.bin").write_bytes(b"12345")
    meta = {
        "info": {
            "files": [
                {"path": ["one.bin"], "length": 3},
                {"path": ["sub", "two.bin"], "length": 5},
            ],
            "pieces": b"x" * 40,
            "piece length": 4,
        }
    }
    result = mixin.add_fast_resume(meta, str(multi), _Torrent())
    assert [
        entry["completed"] for entry in result["libtorrent_resume"]["files"]
    ] == [1, 2]

    invalid_piece = {
        "info": {
            "name": single.name,
            "length": 5,
            "pieces": b"x" * 20,
            "piece length": object(),
        }
    }
    with pytest.raises(ValueError, match="Invalid piece length"):
        mixin.add_fast_resume(invalid_piece, str(single), _Torrent())

    zero_piece = {
        "info": {
            "name": single.name,
            "length": 5,
            "pieces": b"x" * 20,
            "piece length": 0,
        }
    }
    with pytest.raises(ValueError, match="Invalid piece length"):
        mixin.add_fast_resume(zero_piece, str(single), _Torrent())

    mismatch = {
        "info": {
            "name": single.name,
            "length": 9,
            "pieces": b"x" * 20,
            "piece length": 4,
        }
    }
    with pytest.raises(OSError) as error:
        mixin.add_fast_resume(mismatch, str(single), _Torrent())
    assert error.value.errno == errno.EINVAL

    invalid_length = {
        "info": {
            "name": single.name,
            "length": object(),
            "pieces": b"x" * 20,
            "piece length": 4,
        }
    }
    with pytest.raises(OSError):
        mixin.add_fast_resume(invalid_length, str(single), _Torrent())


def _ptp_meta(tmp_path: Path, **values: object) -> Meta:
    path = tmp_path / "release"
    path.mkdir(exist_ok=True)
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "path": str(path),
        "uuid": "",
        "infohash": " abc ",
        "torrent_comments": [],
        "debug": True,
        "tracker_ids": {},
    }
    state.update(values)
    return Meta(state)


def test_get_ptp_missing_config_fields_and_not_found(tmp_path: Path) -> None:
    mixin = _Rtorrent({"DEFAULT": {}, "TORRENT_CLIENTS": {}})
    meta = _ptp_meta(tmp_path)
    assert asyncio.run(mixin.get_ptp_from_hash_rtorrent(meta)) is meta

    explicit = {"torrent_storage_dir": "", "other": True}
    assert (
        asyncio.run(mixin.get_ptp_from_hash_rtorrent(meta, client=explicit))
        is meta
    )
    assert (
        asyncio.run(
            mixin.get_ptp_from_hash_rtorrent(
                _ptp_meta(tmp_path, infohash=None),
                client={"torrent_storage_dir": str(tmp_path)},
            )
        ).infohash
        is None
    )
    assert (
        asyncio.run(
            mixin.get_ptp_from_hash_rtorrent(
                _ptp_meta(tmp_path, path=""),
                client={"torrent_storage_dir": str(tmp_path)},
            )
        ).path
        == ""
    )

    storage = tmp_path / "storage"
    storage.mkdir()
    assert (
        asyncio.run(
            mixin.get_ptp_from_hash_rtorrent(
                meta, client={"torrent_storage_dir": str(storage)}
            )
        )
        is meta
    )


def test_get_ptp_direct_case_insensitive_pathed_and_comments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = tmp_path / "storage"
    storage.mkdir()
    direct = storage / "ABC.torrent"
    direct.write_bytes(b"torrent")
    fake = SimpleNamespace(
        comment="https://ptp/123", infohash_v1="hash-v1", name="Release"
    )
    monkeypatch.setattr(rtorrent_module.Torrent, "read", lambda _path: fake)
    mixin = _Rtorrent(
        {
            "DEFAULT": {"default_torrent_client": "rt"},
            "TORRENT_CLIENTS": {"rt": {"torrent_storage_dir": str(storage)}},
        }
    )
    meta = _ptp_meta(tmp_path, torrent_comments=["bad", {"comment": "old"}])
    result = asyncio.run(mixin.get_ptp_from_hash_rtorrent(meta, pathed=True))
    assert result.get_tracker_id("PTP") == "123"
    assert result.torrent_comments[-1] == {
        "hash": "hash-v1",
        "name": "Release",
        "comment": "https://ptp/123",
    }
    assert mixin.valid_calls == []
    assert result.uuid == Path(result.path).name

    direct.unlink()
    mixed_case = storage / "aBc.ToRrEnT"
    mixed_case.write_bytes(b"torrent")
    result = asyncio.run(
        mixin.get_ptp_from_hash_rtorrent(
            _ptp_meta(tmp_path),
            pathed=True,
            client={"torrent_storage_dir": str(storage)},
        )
    )
    assert result.get_tracker_id("PTP") == "123"


def test_get_ptp_validation_base_success_fallback_copy_and_read_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = tmp_path / "storage"
    storage.mkdir()
    torrent_path = storage / "ABC.torrent"
    torrent_path.write_bytes(b"torrent")
    resolved = tmp_path / "resolved.torrent"
    resolved.write_bytes(b"resolved")
    fake = SimpleNamespace(comment="comment", infohash_v1="", name="")
    monkeypatch.setattr(rtorrent_module.Torrent, "read", lambda _path: fake)
    mixin = _Rtorrent()
    mixin.valid_result = (True, str(resolved))
    create = AsyncMock(return_value=str(tmp_path / "BASE.torrent"))
    monkeypatch.setattr(
        rtorrent_module.TorrentCreator,
        "create_base_from_existing_torrent",
        create,
    )
    meta = _ptp_meta(tmp_path, torrent_comments="invalid")
    result = asyncio.run(
        mixin.get_ptp_from_hash_rtorrent(
            meta, client={"torrent_storage_dir": str(storage)}
        )
    )
    create.assert_awaited_once()
    assert len(result.torrent_comments) == 1

    mixin.valid_result = (False, str(resolved))
    create.reset_mock()
    asyncio.run(
        mixin.get_ptp_from_hash_rtorrent(
            _ptp_meta(tmp_path), client={"torrent_storage_dir": str(storage)}
        )
    )
    create.assert_not_awaited()

    mixin.valid_result = (True, str(resolved))
    monkeypatch.setattr(
        rtorrent_module.TorrentCreator,
        "create_base_from_existing_torrent",
        AsyncMock(side_effect=RuntimeError("create failed")),
    )
    result = asyncio.run(
        mixin.get_ptp_from_hash_rtorrent(
            _ptp_meta(tmp_path), client={"torrent_storage_dir": str(storage)}
        )
    )
    assert (
        Path(result.base_dir) / "tmp" / result.uuid / "BASE.torrent"
    ).read_bytes() == b"resolved"

    monkeypatch.setattr(
        rtorrent_module.shutil,
        "copy2",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("copy failed")
        ),
    )
    asyncio.run(
        mixin.get_ptp_from_hash_rtorrent(
            _ptp_meta(tmp_path), client={"torrent_storage_dir": str(storage)}
        )
    )

    monkeypatch.setattr(
        rtorrent_module.Torrent,
        "read",
        lambda _path: (_ for _ in ()).throw(ValueError("bad torrent")),
    )
    asyncio.run(
        mixin.get_ptp_from_hash_rtorrent(
            _ptp_meta(tmp_path), client={"torrent_storage_dir": str(storage)}
        )
    )


def test_base_mixin_abstract_boundaries_and_symlink_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = RtorrentClientMixin()
    with pytest.raises(NotImplementedError):
        asyncio.run(
            base.is_valid_torrent(Meta(), "candidate", "hash", "rtorrent", {})
        )
    with pytest.raises(NotImplementedError):
        base._extract_tracker_ids_from_comment("comment")

    source = tmp_path / "source.mkv"
    source.write_bytes(b"data")
    torrent_path = tmp_path / "release.torrent"
    torrent_path.write_bytes(b"torrent")
    fallback = tmp_path / "unrelated-fallback"
    fallback.mkdir()
    _patch_base(monkeypatch)
    mixin = _Rtorrent()
    mixin.add_fast_resume = lambda meta, _path, _torrent: meta  # type: ignore[method-assign]
    monkeypatch.setattr(rtorrent_module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        rtorrent_module.os.path,
        "splitdrive",
        lambda value: ("D:" if str(value).startswith("D:") else "C:", value),
    )
    monkeypatch.setattr(
        rtorrent_module,
        "tracker_directory",
        lambda _target, _name, _tracker: fallback / "FALLBACK",
    )
    mixin.rtorrent(
        str(source),
        str(torrent_path),
        _Torrent(),
        _meta(tmp_path, source),
        str(tmp_path),
        str(tmp_path),
        _client(linking="symlink", linked_folder=["D:/unmatched"]),
        "FALLBACK",
    )
    assert (fallback / "FALLBACK" / source.name).is_symlink()
