from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, ClassVar, cast

import pytest

from src.domain_models.release import Meta
from src.integrations.torrent import torrent_creator as creator
from src.integrations.torrent.torrent_creator import (
    CustomTorrent,
    TorrentCreator,
)


@pytest.mark.parametrize(
    ("mib", "expected"),
    [
        (60, 32 * 1024),
        (61, 64 * 1024),
        (121, 128 * 1024),
        (241, 256 * 1024),
        (481, 512 * 1024),
        (961, 1024 * 1024),
        (1921, 2 * 1024 * 1024),
        (3841, 4 * 1024 * 1024),
        (7681, 8 * 1024 * 1024),
        (15361, 16 * 1024 * 1024),
        (46081, 32 * 1024 * 1024),
        (92161, 64 * 1024 * 1024),
        (138241, 128 * 1024 * 1024),
    ],
)
def test_calculate_piece_size_thresholds(mib: int, expected: int) -> None:
    meta = Meta(trackers=[])
    assert (
        TorrentCreator.calculate_piece_size(
            mib * 1024 * 1024, 32 * 1024, 128 * 1024 * 1024, meta
        )
        == expected
    )
    assert (
        creator.calculate_piece_size(
            mib * 1024 * 1024, 32 * 1024, 128 * 1024 * 1024, meta
        )
        == expected
    )


def test_piece_size_tracker_cap_custom_limits_and_invalid_override() -> None:
    huge = 200_000 * 1024 * 1024
    assert (
        TorrentCreator.calculate_piece_size(
            huge, 32 * 1024, 128 * 1024 * 1024, Meta(trackers=["HDBITS"])
        )
        == 16 * 1024 * 1024
    )
    assert (
        TorrentCreator.calculate_piece_size(
            huge,
            32 * 1024,
            128 * 1024 * 1024,
            Meta(trackers=["PASSTHEPOPCORN"]),
        )
        == 16 * 1024 * 1024
    )
    assert (
        TorrentCreator.calculate_piece_size(
            10 * 1024 * 1024, 64 * 1024, 128 * 1024 * 1024, Meta(trackers=[])
        )
        == 64 * 1024
    )
    assert (
        TorrentCreator.calculate_piece_size(
            10_000 * 1024 * 1024,
            32 * 1024,
            128 * 1024 * 1024,
            Meta(trackers=[]),
            piece_size=1,
        )
        == 1024 * 1024
    )
    assert (
        TorrentCreator.calculate_piece_size(
            huge,
            32 * 1024,
            128 * 1024 * 1024,
            Meta(trackers=[]),
            piece_size=1000,
        )
        == 128 * 1024 * 1024
    )
    assert (
        TorrentCreator.calculate_piece_size(
            huge,
            32 * 1024,
            128 * 1024 * 1024,
            Meta(trackers=[]),
            piece_size=cast(Any, "bad"),
        )
        == 128 * 1024 * 1024
    )


def test_custom_torrent_precalculated_properties_and_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_init(self, *_args: object, **_kwargs: object) -> None:
        self._metainfo = {"info": {}}
        self._piece_size = 0

    monkeypatch.setattr(creator.torf.Torrent, "__init__", fake_init)
    torrent = CustomTorrent(Meta(), path="release", piece_size=65536)
    assert torrent.piece_size_min == creator.PIECE_SIZE_MIN
    assert torrent.piece_size_max == creator.PIECE_SIZE_MAX
    assert torrent.piece_size == 65536
    assert torrent.metainfo["info"]["piece length"] == 65536
    torrent.piece_size_min = None
    torrent.piece_size_max = None
    torrent.piece_size = 131072
    assert torrent.piece_size == 65536
    torrent.validate_piece_size()
    assert torrent.metainfo["info"]["piece length"] == 65536

    torrent._precalculated_piece_size = None
    torrent.piece_size = None
    assert torrent.piece_size == 65536
    torrent.piece_size = 262144
    assert torrent.piece_size == 262144
    torrent.validate_piece_size()


def test_build_mkbrr_exclude_string_keeps_selected_and_patterns(
    tmp_path: Path,
) -> None:
    keep = tmp_path / "keep.bin"
    keep.write_bytes(b"keep")
    (tmp_path / "remove.bin").write_bytes(b"remove")
    (tmp_path / "sample.nfo").write_bytes(b"nfo")
    (tmp_path / "subtitle.srt").write_bytes(b"sub")

    excluded = TorrentCreator.build_mkbrr_exclude_string(
        str(tmp_path), [str(keep)]
    )
    assert "remove.bin" in excluded
    assert "keep.bin" not in excluded
    assert "sample.nfo" not in excluded
    assert "subtitle.srt" not in excluded
    assert "*.srt" in excluded

    allowed = creator.build_mkbrr_exclude_string(
        str(tmp_path), [str(keep)], allow_subs=True
    )
    assert "subtitle.srt" in allowed
    assert "*.srt" not in allowed


class _FakeTorrent:
    writes: ClassVar[list[tuple[str, bool, dict[str, Any]]]] = []
    read_result: ClassVar[_FakeTorrent | None] = None

    def __init__(self) -> None:
        self.metainfo: dict[str, Any] = {
            "announce": "old",
            "announce-list": ["old"],
            "comment": "old",
            "creation date": 1,
            "created by": "old",
            "encoding": "UTF-8",
            "extra": "remove",
            "info": {
                "name": "release",
                "piece length": 65536,
                "pieces": b"pieces",
                "private": True,
                "source": "OLD",
                "files": [{"length": 1, "path": ["video.mkv"]}],
                "junk": "remove",
            },
        }
        self.trackers: list[str] = []
        self.comment = ""
        self.created_by = ""
        self.source = ""
        self.private = False
        self.files = ["video.mkv", "subtitle.srt"]

    @classmethod
    def read(cls, _path: str):
        assert cls.read_result is not None
        return cls.read_result

    @classmethod
    def copy(cls, torrent):
        return torrent

    def write(self, path: str | Path, *, overwrite: bool) -> None:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"torrent")
        self.__class__.writes.append(
            (str(output), overwrite, dict(self.metainfo))
        )


def test_random_torrents_and_module_wrapper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeTorrent()
    _FakeTorrent.read_result = fake
    _FakeTorrent.writes = []
    monkeypatch.setattr(creator, "Torrent", _FakeTorrent)
    state = tmp_path / "tmp" / "uuid"
    state.mkdir(parents=True)
    (state / "BASE.torrent").write_bytes(b"base")
    TorrentCreator.create_random_torrents(
        str(tmp_path), "uuid", "2", "Release Name.mkv"
    )
    creator.create_random_torrents(
        str(tmp_path), "uuid", 1, "Release Name.mkv"
    )
    assert len(_FakeTorrent.writes) == 3
    assert all(
        "entropy" in written[2]["info"] for written in _FakeTorrent.writes
    )
    assert Path(_FakeTorrent.writes[0][0]).name.startswith(
        "[RAND-1]Release.Name.mkv"
    )


def test_create_base_from_existing_multi_single_subtitles_and_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    existing = tmp_path / "existing.torrent"
    existing.write_bytes(b"torrent")
    fake = _FakeTorrent()
    _FakeTorrent.read_result = fake
    _FakeTorrent.writes = []
    monkeypatch.setattr(creator, "Torrent", _FakeTorrent)

    result = asyncio.run(
        TorrentCreator.create_base_from_existing_torrent(
            str(existing), str(tmp_path), "multi"
        )
    )
    assert result and Path(result).name == "BASE_SUBS.torrent"
    assert set(fake.metainfo["info"]) == {
        "name",
        "piece length",
        "pieces",
        "private",
        "source",
        "files",
    }
    assert (
        "announce-list" not in fake.metainfo and "extra" not in fake.metainfo
    )
    assert fake.source == "L4G" and fake.private is True

    fake = _FakeTorrent()
    fake.metainfo["info"].pop("files")
    fake.metainfo["info"]["length"] = 123
    fake.files = ["video.mkv"]
    _FakeTorrent.read_result = fake
    result = asyncio.run(
        creator.create_base_from_existing_torrent(
            str(existing), str(tmp_path), "single"
        )
    )
    assert result and Path(result).name == "BASE.torrent"
    assert "length" in fake.metainfo["info"]
    assert (
        asyncio.run(
            TorrentCreator.create_base_from_existing_torrent(
                str(tmp_path / "missing"), str(tmp_path), "none"
            )
        )
        is None
    )


def _expected_mkbrr(base: Path, system: str, arch: str) -> Path:
    if system == "windows":
        return base / "bin" / "mkbrr" / "windows" / "x86_64" / "mkbrr.exe"
    if system == "darwin":
        folder = "arm64" if "arm" in arch else "x86_64"
        return base / "bin" / "mkbrr" / "macos" / folder / "mkbrr"
    if arch in {"aarch64", "arm64"}:
        folder = "arm64"
    elif "armv6" in arch:
        folder = "armv6"
    elif "arm" in arch:
        folder = "arm"
    else:
        folder = "amd64"
    return base / "bin" / "mkbrr" / "linux" / folder / "mkbrr"


def test_get_mkbrr_path_config_existing_platform_matrix_and_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    meta = Meta(base_dir=str(tmp_path))
    monkeypatch.setattr(
        creator, "configured_binary", lambda _name: "/configured/mkbrr"
    )
    assert TorrentCreator.get_mkbrr_path(meta) == "/configured/mkbrr"

    monkeypatch.setattr(creator, "configured_binary", lambda _name: None)
    monkeypatch.setattr(
        creator.MkbrrBinaryManager,
        "find_existing_binary",
        staticmethod(
            lambda root: (
                "/existing/mkbrr" if Path(root) == creator.CODE_DIR else None
            )
        ),
    )
    assert TorrentCreator.get_mkbrr_path(meta) == "/existing/mkbrr"
    monkeypatch.setattr(
        creator.MkbrrBinaryManager,
        "find_existing_binary",
        staticmethod(lambda _root: None),
    )

    for system, arch in (
        ("Windows", "AMD64"),
        ("Darwin", "arm64"),
        ("Darwin", "x86_64"),
        ("Linux", "x86_64"),
        ("Linux", "armv6l"),
        ("Linux", "armv7l"),
        ("Linux", "aarch64"),
    ):
        expected = _expected_mkbrr(tmp_path, system.lower(), arch.lower())
        expected.parent.mkdir(parents=True, exist_ok=True)
        expected.write_bytes(b"tool")
        monkeypatch.setattr(
            creator.platform, "system", lambda value=system: value
        )
        monkeypatch.setattr(
            creator.platform, "machine", lambda value=arch: value
        )
        assert TorrentCreator.get_mkbrr_path(meta) == str(expected)
        expected.unlink()

    monkeypatch.setattr(creator.platform, "system", lambda: "Windows")
    monkeypatch.setattr(creator.platform, "machine", lambda: "i686")
    with pytest.raises(Exception, match="Unsupported Windows"):
        TorrentCreator.get_mkbrr_path(meta)
    monkeypatch.setattr(creator.platform, "system", lambda: "Linux")
    monkeypatch.setattr(creator.platform, "machine", lambda: "riscv64")
    with pytest.raises(Exception, match="Unsupported Linux"):
        TorrentCreator.get_mkbrr_path(meta)
    monkeypatch.setattr(creator.platform, "system", lambda: "FreeBSD")
    with pytest.raises(Exception, match="Unsupported OS"):
        TorrentCreator.get_mkbrr_path(meta)
    monkeypatch.setattr(creator.platform, "system", lambda: "Linux")
    monkeypatch.setattr(creator.platform, "machine", lambda: "x86_64")
    with pytest.raises(FileNotFoundError, match="binary not found"):
        TorrentCreator.get_mkbrr_path(meta)
    with pytest.raises(FileNotFoundError):
        creator.get_mkbrr_path(meta)
