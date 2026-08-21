from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar, cast

import pytest

from src.domain_models.release import Meta
from src.integrations.torrent import torrent_creator as creator
from src.integrations.torrent.torrent_creator import TorrentCreator


class _FakeCustomTorrent:
    instances: ClassVar[list[_FakeCustomTorrent]] = []
    fail_generate: ClassVar[bool] = False

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.piece_size = int(kwargs.get("piece_size") or 0)
        self.generated = False
        self.written = ""
        self.verified = ""
        self.__class__.instances.append(self)

    def generate(self, *, callback, interval: int) -> None:
        assert interval == 5
        if self.fail_generate:
            raise RuntimeError("generation failed")
        self.generated = True
        callback(self, "file", 0, 2)
        callback(self, "file", 2, 2)

    def write(self, path: str, *, overwrite: bool) -> None:
        assert overwrite
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"torrent")
        self.written = str(output)

    def verify_filesize(self, path: str | os.PathLike[str]) -> None:
        self.verified = os.fspath(path)


class _Progress:
    updates: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def add_task(self, *_args: object, **_kwargs: object) -> int:
        return 1

    def update(self, _task: int, **kwargs: Any) -> None:
        self.__class__.updates.append(kwargs)


def _meta(tmp_path: Path, **values: object) -> Meta:
    state_dir = tmp_path / "tmp" / "release"
    state_dir.mkdir(parents=True, exist_ok=True)
    video = tmp_path / "video.mkv"
    video.write_bytes(b"video")
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "uuid": "release",
        "path": str(video),
        "filelist": [str(video)],
        "subtitle_files": [],
        "category": "MOVIE",
        "isdir": False,
        "keep_folder": False,
        "keep_nfo": False,
        "tv_pack": False,
        "is_disc": "",
        "mkbrr": False,
        "mkbrr_threads": "0",
        "max_piece_size": 0,
        "randomized": 0,
        "trackers": [],
        "debug": True,
        "ua_name": "Upload Assistant",
    }
    state.update(values)
    return Meta(state)


def _patch_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeCustomTorrent.instances = []
    _FakeCustomTorrent.fail_generate = False
    monkeypatch.setattr(creator, "CustomTorrent", _FakeCustomTorrent)
    monkeypatch.setattr(creator, "is_cli_progress_suppressed", lambda: True)


def test_fallback_single_subtitles_outside_and_nonmedia_path_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_fallback(monkeypatch)
    video = tmp_path / "video.mkv"
    subtitle = tmp_path / "video.en.srt"
    subtitle.write_text("subtitle", encoding="utf-8")
    outside = tmp_path.parent / "outside.srt"
    outside.write_text("outside", encoding="utf-8")
    meta = _meta(tmp_path, subtitle_files=[str(subtitle), str(outside)])
    result = asyncio.run(
        TorrentCreator.create_torrent(meta, video, "BASE_SUBS")
    )
    assert result is _FakeCustomTorrent.instances[-1]
    kwargs = result.kwargs
    assert kwargs["path"] == video.parent
    assert "video.en.srt" in kwargs["include_globs"]
    assert all("outside.srt" not in item for item in kwargs["include_globs"])
    assert result.generated and Path(result.written).exists()

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    payload = data_dir / "book.epub"
    payload.write_bytes(b"book")
    meta = _meta(
        tmp_path,
        category="BOOK",
        isdir=True,
        filelist=[str(payload)],
        keep_folder=False,
    )
    result = asyncio.run(creator.create_torrent(meta, data_dir, "BASE"))
    assert result.kwargs["path"] == str(payload)
    assert (
        result.kwargs["include_globs"] == []
        and result.kwargs["exclude_globs"] == []
    )


def test_fallback_keep_folder_directory_disc_pack_and_media_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_fallback(monkeypatch)
    folder = tmp_path / "Release"
    folder.mkdir()
    video = folder / "episode.mkv"
    video.write_bytes(b"video")
    nfo = folder / "release.nfo"
    nfo.write_text("nfo", encoding="utf-8")

    keep_nfo = _meta(
        tmp_path,
        path=str(folder),
        filelist=[str(video)],
        isdir=True,
        keep_folder=True,
        keep_nfo=True,
    )
    result = asyncio.run(
        TorrentCreator.create_torrent(keep_nfo, folder, "TRACKER")
    )
    assert (
        "*.nfo" in result.kwargs["include_globs"] and keep_nfo.mkbrr is False
    )

    keep_folder = _meta(
        tmp_path,
        path=str(folder),
        filelist=[str(video)],
        isdir=True,
        keep_folder=True,
        keep_nfo=False,
    )
    result = asyncio.run(
        TorrentCreator.create_torrent(keep_folder, folder, "BASE")
    )
    assert result.kwargs["include_globs"] == [f"Release/{video.name}"]
    assert result.kwargs["exclude_globs"] == ["*", "*/**"]

    directory_nfo = _meta(
        tmp_path,
        path=str(folder),
        filelist=[str(video)],
        isdir=True,
        keep_nfo=True,
    )
    result = asyncio.run(
        TorrentCreator.create_torrent(directory_nfo, folder, "TRACKER")
    )
    assert "*.nfo" in result.kwargs["include_globs"]

    disc = _meta(
        tmp_path,
        path=str(folder),
        filelist=[str(video)],
        isdir=True,
        is_disc="BDMV",
    )
    result = asyncio.run(TorrentCreator.create_torrent(disc, folder, "BASE"))
    assert (
        result.kwargs["include_globs"] == []
        and result.kwargs["exclude_globs"] == []
    )

    one_video = _meta(
        tmp_path, path=str(folder), filelist=[str(video)], isdir=True
    )
    result = asyncio.run(
        TorrentCreator.create_torrent(one_video, folder, "BASE")
    )
    assert result.kwargs["path"] == str(video)
    assert result.kwargs["include_globs"] == ["*.mkv", "*.mp4", "*.ts"]

    pack = _meta(
        tmp_path,
        path=str(folder),
        filelist=[str(video)],
        isdir=True,
        tv_pack=True,
    )
    result = asyncio.run(TorrentCreator.create_torrent(pack, folder, "BASE"))
    assert result.kwargs["include_globs"] == [f"Release/{video.name}"]

    standard = _meta(
        tmp_path, path=str(video), filelist=[str(video)], isdir=False
    )
    result = asyncio.run(
        TorrentCreator.create_torrent(standard, video, "BASE")
    )
    assert result.kwargs["include_globs"] == ["*.mkv", "*.mp4", "*.ts"]
    assert "*sample.mkv" in result.kwargs["exclude_globs"]


def test_fallback_directory_size_and_generation_error_decrements_inflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_fallback(monkeypatch)
    folder = tmp_path / "Directory"
    folder.mkdir()
    (folder / "one.bin").write_bytes(b"123")
    (folder / "two.bin").write_bytes(b"12345")
    meta = _meta(
        tmp_path,
        category="BOOK",
        path=str(folder),
        filelist=[str(folder / "one.bin"), str(folder / "two.bin")],
        isdir=True,
        keep_folder=True,
    )
    asyncio.run(TorrentCreator.create_torrent(meta, folder, "BASE"))
    assert (
        _FakeCustomTorrent.instances[-1].piece_size >= creator.PIECE_SIZE_MIN
    )

    _FakeCustomTorrent.fail_generate = True
    before = TorrentCreator._create_torrent_inflight
    with pytest.raises(RuntimeError, match="generation failed"):
        asyncio.run(TorrentCreator.create_torrent(meta, folder, "FAILED"))
    assert TorrentCreator._create_torrent_inflight == before


class _MkbrrProcess:
    commands: ClassVar[list[list[str]]] = []
    return_code: ClassVar[int] = 0
    create_output: ClassVar[bool] = True
    stdout_lines: ClassVar[list[str] | None] = []

    def __init__(self, command: list[str], **_kwargs: object) -> None:
        self.command = command
        self.__class__.commands.append(command)
        self.stdout = (
            None if self.stdout_lines is None else iter(self.stdout_lines)
        )
        if self.create_output and "-o" in command:
            output = Path(command[command.index("-o") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"torrent")

    def wait(self) -> int:
        return self.return_code


def _patch_mkbrr(monkeypatch: pytest.MonkeyPatch, binary: Path) -> None:
    _MkbrrProcess.commands = []
    _MkbrrProcess.return_code = 0
    _MkbrrProcess.create_output = True
    _MkbrrProcess.stdout_lines = [
        "Hashing pieces [1.7 GiB/s] 14% [1s:5s]",
        "Hashing pieces [900 MiB/s] 25%",
        "Hashing pieces [100 MiB/s] 0%",
        "Wrote output.torrent",
    ]
    monkeypatch.setattr(
        TorrentCreator,
        "get_mkbrr_path",
        staticmethod(lambda _meta: str(binary)),
    )
    monkeypatch.setattr(creator.subprocess, "Popen", _MkbrrProcess)
    monkeypatch.setattr(creator, "progress_display", _Progress)


def test_mkbrr_success_command_piece_workers_random_tracker_and_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = tmp_path / "mkbrr"
    binary.write_bytes(b"tool")
    binary.chmod(0o755)
    _patch_mkbrr(monkeypatch, binary)
    _Progress.updates = []
    folder = tmp_path / "Release"
    folder.mkdir()
    video = folder / "video.mkv"
    video.write_bytes(b"video")
    extra = folder / "remove.bin"
    extra.write_bytes(b"remove")
    meta = _meta(
        tmp_path,
        path=str(folder),
        filelist=[str(video)],
        isdir=True,
        mkbrr=True,
        randomized=1,
        mkbrr_threads="3",
    )
    result = asyncio.run(
        TorrentCreator.create_torrent(
            meta, folder, "MKB", tracker_url="https://tracker", piece_size=1
        )
    )
    assert isinstance(result, str) and Path(result).exists()
    command = _MkbrrProcess.commands[-1]
    assert command[:3] == [str(binary), "create", str(video)]
    assert command[3:5] == ["-t", "https://tracker"]
    assert (
        "-e" in command and "--workers" in command and "--exclude" in command
    )
    assert "-l" not in command and "-m" not in command
    assert _Progress.updates

    meta = _meta(tmp_path, mkbrr=True, mkbrr_threads="0", randomized=0)
    result = asyncio.run(
        TorrentCreator.create_torrent(
            meta, Path(meta.path), "PIECE", piece_size=1
        )
    )
    command = _MkbrrProcess.commands[-1]
    assert "-l" in command and command[command.index("-l") + 1] == "20"
    assert isinstance(result, str)

    meta = _meta(tmp_path, mkbrr=True, trackers=[])
    asyncio.run(
        TorrentCreator.create_torrent(meta, Path(meta.path), "DEFAULT")
    )
    assert "-m" in _MkbrrProcess.commands[-1]

    meta = _meta(tmp_path, mkbrr=True)
    asyncio.run(
        TorrentCreator.create_torrent(
            meta, Path(meta.path), "BADPIECE", piece_size=cast(Any, "bad")
        )
    )
    assert "-l" not in _MkbrrProcess.commands[-1]


def test_mkbrr_stdout_none_nonzero_missing_output_and_fallback_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = tmp_path / "mkbrr"
    binary.write_bytes(b"tool")
    _patch_mkbrr(monkeypatch, binary)
    _patch_fallback(monkeypatch)

    _MkbrrProcess.stdout_lines = None
    meta = _meta(tmp_path, mkbrr=True)
    result = asyncio.run(
        TorrentCreator.create_torrent(meta, Path(meta.path), "NO_STDOUT")
    )
    assert isinstance(result, str)

    _MkbrrProcess.stdout_lines = []
    _MkbrrProcess.return_code = 2
    meta = _meta(tmp_path, mkbrr=True)
    result = asyncio.run(
        TorrentCreator.create_torrent(meta, Path(meta.path), "NONZERO")
    )
    assert isinstance(result, _FakeCustomTorrent) and meta.mkbrr is False

    _MkbrrProcess.return_code = 0
    _MkbrrProcess.create_output = False
    meta = _meta(tmp_path, mkbrr=True)
    result = asyncio.run(
        TorrentCreator.create_torrent(meta, Path(meta.path), "MISSING_OUTPUT")
    )
    assert isinstance(result, _FakeCustomTorrent) and meta.mkbrr is False

    missing_path = tmp_path / "missing"
    meta = _meta(tmp_path, mkbrr=True)
    result = asyncio.run(
        TorrentCreator.create_torrent(meta, missing_path, "MISSING_PATH")
    )
    assert isinstance(result, _FakeCustomTorrent) and meta.mkbrr is False

    binary.unlink()
    meta = _meta(tmp_path, mkbrr=True)
    result = asyncio.run(
        TorrentCreator.create_torrent(meta, Path(meta.path), "MISSING_BINARY")
    )
    assert isinstance(result, _FakeCustomTorrent) and meta.mkbrr is False


def test_mkbrr_called_process_error_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = tmp_path / "mkbrr"
    binary.write_bytes(b"tool")
    _patch_fallback(monkeypatch)
    monkeypatch.setattr(
        TorrentCreator,
        "get_mkbrr_path",
        staticmethod(lambda _meta: str(binary)),
    )

    def fail(*_args: object, **_kwargs: object):
        raise subprocess.CalledProcessError(1, ["mkbrr"])

    monkeypatch.setattr(creator.subprocess, "Popen", fail)
    meta = _meta(tmp_path, mkbrr=True)
    result = asyncio.run(
        TorrentCreator.create_torrent(meta, Path(meta.path), "CALLED_ERROR")
    )
    assert isinstance(result, _FakeCustomTorrent) and meta.mkbrr is False


def test_semaphore_wait_debug_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_fallback(monkeypatch)

    class Locked:
        def locked(self) -> bool:
            return True

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(TorrentCreator, "_create_torrent_semaphore", Locked())
    times = iter((10.0, 12.0, 13.0, 14.0))
    monkeypatch.setattr(creator.time, "time", lambda: next(times, 14.0))
    meta = _meta(tmp_path, debug=True)
    asyncio.run(TorrentCreator.create_torrent(meta, Path(meta.path), "WAIT"))


def test_torf_callback_progress_suppressed_and_module_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, int, int]] = []
    fake = SimpleNamespace(piece_size=1024 * 1024)
    times = iter((100.0, 101.0, 105.0, 110.0, 115.0))
    monkeypatch.setattr(creator.time, "time", lambda: next(times, 115.0))
    monkeypatch.setattr(
        creator.cli_ui,
        "info_progress",
        lambda label, done, total: events.append((label, done, total)),
    )
    monkeypatch.setattr(creator, "is_cli_progress_suppressed", lambda: False)
    TorrentCreator.torf_cb(fake, "file", 0, 0)
    creator.torf_cb(fake, "file", 1, 2)
    assert events[0][1:] == (0, 100)
    assert events[1][1:] == (50, 100)
    assert "MB/s" in events[1][0]

    monkeypatch.setattr(creator, "is_cli_progress_suppressed", lambda: True)
    count = len(events)
    TorrentCreator.torf_cb(fake, "file", 2, 2)
    assert len(events) == count
