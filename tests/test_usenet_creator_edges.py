from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar, Self
from unittest.mock import AsyncMock

import pytest

from src.domain_models.release import Meta
from src.integrations.usenet import creator

RANDOM_PASSWORD_MODE = "random"
STATIC_ARCHIVE_PASSWORD = "configured"
ARCHIVE_PASSWORD = "secret"


class _Progress:
    instances: ClassVar[list[_Progress]] = []

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.tasks: list[SimpleNamespace] = []
        self.updates: list[tuple[int, dict[str, object]]] = []
        type(self).instances.append(self)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def add_task(self, description: str, *, total: object = None) -> int:
        task_id = len(self.tasks)
        self.tasks.append(SimpleNamespace(description=description, total=total, completed=0))
        return task_id

    def update(self, task_id: int, **kwargs: object) -> None:
        self.updates.append((task_id, dict(kwargs)))
        task = self.tasks[task_id]
        for key, value in kwargs.items():
            setattr(task, key, value)

    def advance(self, task_id: int, advance: float = 1) -> None:
        self.tasks[task_id].completed += advance


class _Stream:
    def __init__(self, chunks: list[bytes] | None = None, lines: list[bytes] | None = None) -> None:
        self.chunks = list(chunks or [])
        self.lines = list(lines or [])

    async def read(self, _size: int = -1) -> bytes:
        return self.chunks.pop(0) if self.chunks else b""

    async def readline(self) -> bytes:
        return self.lines.pop(0) if self.lines else b""


class _Process:
    def __init__(
        self,
        *,
        stdout: _Stream | None = None,
        stderr: _Stream | None = None,
        returncode: int = 0,
        communicate: tuple[bytes, bytes] = (b"", b""),
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.pid = os.getpid()
        self.communicate_result = communicate

    async def communicate(self) -> tuple[bytes, bytes]:
        return self.communicate_result

    async def wait(self) -> int:
        return self.returncode


def _meta(
    tmp_path: Path,
    path: Path | str | None = None,
    *,
    create_source: bool = True,
    **values: object,
) -> Meta:
    source = Path(path) if path not in (None, "") else (tmp_path / "release.mkv" if path is None else None)
    if source is not None and create_source and not source.exists():
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"video")
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "path": str(source) if source is not None else "",
        "uuid": "release-uuid",
        "basename_no_ext": "Release Name",
        "category": "MOVIE",
        "debug": False,
        "archive_password": None,
        "usenet_archive_password_is_random": None,
        "usenet_archive_name": "",
        "usenet_prepared_files": [],
        "usenet_subject": "",
        "tmdb_id": 123,
        "imdb_tt": "tt1234567",
        "tvdb_id": 456,
        "mal_id": 789,
    }
    state.update(values)
    return Meta(state)


def _cfg(**values: object) -> dict[str, Any]:
    return {
        "USENET": {
            "host": "news.invalid",
            "port": 563,
            "username": "user",
            "password": "pass",
            "connections": 8,
            "newsgroups": "alt.binaries.test",
            "usenet_uploader": "nyuu",
            **values,
        }
    }


@pytest.fixture(autouse=True)
def _progress(monkeypatch: pytest.MonkeyPatch) -> None:
    _Progress.instances = []
    monkeypatch.setattr(creator, "progress_display", _Progress)


def test_random_poster_sizes_paths_dynamic_and_connections(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lengths = iter((5, 6, 7, 8))
    monkeypatch.setattr(creator.random, "randint", lambda *_args: next(lengths))
    monkeypatch.setattr(creator.random, "choice", lambda values: values[0])
    assert creator.generate_random_poster() == "Aaaaa Aaaaaa <aaaaaaa@aaaaaaaa.com>"

    file = tmp_path / "file.bin"
    file.write_bytes(b"12345")
    assert creator.get_path_size(str(file)) == 5
    original_stat = Path.stat

    def fail_stat(path: Path, *args: object, **kwargs: object):
        if path == file:
            raise OSError("stat failed")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fail_stat)
    assert creator.get_path_size(str(file)) == 0
    monkeypatch.setattr(Path, "stat", original_stat)

    root = tmp_path / "root"
    root.mkdir()
    (root / "one").write_bytes(b"123")
    link = root / "link"
    link.symlink_to(root / "one")
    assert creator.get_path_size(str(root)) == 3

    gib = 1024**3
    assert creator.get_dynamic_volume_size(1) == "100m"
    assert creator.get_dynamic_volume_size(2 * gib) == "200m"
    assert creator.get_dynamic_volume_size(10 * gib) == "500m"
    assert creator.get_dynamic_volume_size(50 * gib) == "1g"

    assert creator.compute_nyuu_connections(8, False, None) == (8, None)
    assert creator.compute_nyuu_connections(8, True, 3) == (8, 3)
    assert creator.compute_nyuu_connections(1, True, "") == (1, 1)
    assert creator.compute_nyuu_connections(9, True, None) == (4, 5)

    assert creator.format_byte_size(1) == "1 B"
    assert creator.format_byte_size(1024) == "1.0 KiB"
    assert creator.format_byte_size(1024**5) == "1024.0 TiB"


def test_check_binary_path_managers_download_failures_and_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(creator.shutil, "which", lambda path: f"/resolved/{path}" if path == "configured" else None)
    assert asyncio.run(creator.check_binary("7z", "configured")) == "/resolved/configured"

    from src.integrations.runtime_tools.nyuu import NyuuBinaryManager
    from src.integrations.runtime_tools.par2 import Par2BinaryManager
    from src.integrations.runtime_tools.pesto import PestoBinaryManager
    from src.integrations.runtime_tools.seven_zip import SevenZipBinaryManager

    monkeypatch.setattr(SevenZipBinaryManager, "ensure_7z_binary", AsyncMock(return_value="/auto/7z"))
    monkeypatch.setattr(NyuuBinaryManager, "ensure_nyuu_binary", AsyncMock(return_value="/auto/nyuu"))
    monkeypatch.setattr(Par2BinaryManager, "ensure_par2_binary", AsyncMock(return_value="/auto/par2"))
    monkeypatch.setattr(PestoBinaryManager, "ensure_pesto_binary", AsyncMock(return_value="/auto/pesto"))
    meta = _meta(tmp_path)
    assert asyncio.run(creator.check_binary("7z", meta=meta)) == "/auto/7z"
    assert asyncio.run(creator.check_binary("nyuu", meta=meta, path_7z="/auto/7z")) == "/auto/nyuu"
    assert asyncio.run(creator.check_binary("par2", meta=meta)) == "/auto/par2"
    assert asyncio.run(creator.check_binary("pesto", meta=meta)) == "/auto/pesto"

    monkeypatch.setattr(SevenZipBinaryManager, "ensure_7z_binary", AsyncMock(side_effect=RuntimeError("download failed")))
    with pytest.raises(FileNotFoundError, match="not found"):
        asyncio.run(creator.check_binary("7z", meta=meta))
    with pytest.raises(FileNotFoundError, match="missing"):
        asyncio.run(creator.check_binary("missing"))


def test_run_command_logging_success_failure_and_spawn_error(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[tuple[object, ...]] = []

    async def spawn(*args: object, **_kwargs: object) -> _Process:
        commands.append(args)
        return _Process(returncode=0)

    monkeypatch.setattr(creator.asyncio, "create_subprocess_exec", spawn)
    asyncio.run(creator.run_command_with_logging(["tool", "-p", "secret", "--username", "user"], "tool"))
    assert commands[0][:3] == ("tool", "-p", "secret")

    monkeypatch.setattr(
        creator.asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=_Process(returncode=2, communicate=(b"out", b"err"))),
    )
    with pytest.raises(RuntimeError, match="failed with exit code 2"):
        asyncio.run(creator.run_command_with_logging(["tool", "--password", "secret"], "tool"))

    monkeypatch.setattr(creator.asyncio, "create_subprocess_exec", AsyncMock(side_effect=OSError("spawn failed")))
    with pytest.raises(RuntimeError, match="spawn failed"):
        asyncio.run(creator.run_command_with_logging(["tool"], "tool"))


def test_nzb_validation_injection_and_verification(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = tmp_path / "missing.nzb"
    assert not asyncio.run(creator.is_valid_nzb(missing))
    tiny = tmp_path / "tiny.nzb"
    tiny.write_text("<nzb></nzb>", encoding="utf-8")
    assert not asyncio.run(creator.is_valid_nzb(tiny))
    medium = tmp_path / "medium.nzb"
    medium.write_text("<nzb>" + "x" * 120 + "</nzb>", encoding="utf-8")
    assert asyncio.run(creator.is_valid_nzb(medium))

    valid = tmp_path / "valid.nzb"
    valid.write_text("<nzb><head></head>" + "x" * 1200 + "</nzb>", encoding="utf-8")
    assert asyncio.run(creator.is_valid_nzb(valid))
    asyncio.run(creator.inject_nzb_password(missing, "ignored"))

    original_getsize = creator.aiofiles.ospath.getsize

    async def fail_size(_path: object) -> int:
        raise OSError("size failed")

    monkeypatch.setattr(creator.aiofiles.ospath, "getsize", fail_size)
    assert not asyncio.run(creator.is_valid_nzb(valid))
    monkeypatch.setattr(creator.aiofiles.ospath, "getsize", original_getsize)

    no_head = tmp_path / "no-head.nzb"
    no_head.write_text("<nzb>body</nzb>", encoding="utf-8")
    asyncio.run(creator.inject_nzb_password(no_head, "secret"))
    assert '<meta type="password">secret</meta>' in no_head.read_text()
    assert asyncio.run(creator.verify_nzb_has_password(str(no_head)))

    with_head = tmp_path / "head.nzb"
    with_head.write_text("<nzb><head><meta type='title'>x</meta></head></nzb>", encoding="utf-8")
    asyncio.run(creator.inject_nzb_password(with_head, "secret2"))
    assert "secret2" in with_head.read_text()
    assert asyncio.run(creator.verify_nzb_has_password(str(with_head)))

    malformed = tmp_path / "malformed.nzb"
    malformed.write_text("plain text", encoding="utf-8")
    asyncio.run(creator.inject_nzb_password(malformed, "ignored"))
    assert malformed.read_text() == "plain text"
    assert not asyncio.run(creator.verify_nzb_has_password(str(malformed)))
    assert not asyncio.run(creator.verify_nzb_has_password(str(missing)))

    monkeypatch.setattr(creator.aiofiles, "open", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("open failed")))
    asyncio.run(creator.inject_nzb_password(valid, "secret"))
    assert not asyncio.run(creator.verify_nzb_has_password(str(valid)))


def test_run_7z_progress_success_failure_psutil_and_spawn_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    directory = tmp_path / "usenet"
    directory.mkdir()
    (directory / "release.7z.001").write_bytes(b"x" * 20)
    process = _Process(
        stdout=_Stream(chunks=[b" 10%\r 50%\n", b" 100%"]),
        stderr=_Stream(chunks=[b"warning 25%\r"]),
        returncode=0,
    )
    monkeypatch.setattr(creator.psutil, "Process", lambda _pid: (_ for _ in ()).throw(creator.psutil.AccessDenied()))
    monkeypatch.setattr(creator.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    asyncio.run(
        creator.run_7z_with_progress(
            ["7z", "a", "-p", "secret", str(directory / "release.7z"), "source"],
            directory,
            "release",
            "100m",
            100,
        )
    )
    assert _Progress.instances[-1].tasks[0].completed == 100

    error = _Process(
        stdout=_Stream(chunks=[b"5%\rout"]),
        stderr=_Stream(chunks=[b"err"]),
        returncode=2,
    )
    monkeypatch.setattr(creator.asyncio, "create_subprocess_exec", AsyncMock(return_value=error))
    with pytest.raises(RuntimeError, match="exit code 2"):
        asyncio.run(creator.run_7z_with_progress(["7z", "a", "archive", "source"], directory, "release", None, 0))

    monkeypatch.setattr(creator.asyncio, "create_subprocess_exec", AsyncMock(side_effect=OSError("spawn failed")))
    with pytest.raises(RuntimeError, match="spawn failed"):
        asyncio.run(creator.run_7z_with_progress(["7z", "a", "archive", "source"], directory, "release", None, 10))


def test_run_par2_progress_actions_failures_and_missing_stdout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = b"Constructing: 10%\rProcessing data: 20%\nComputing matrix: 30%\rWriting recovery: 40%\nLoading packets: 50%\rGenerating: 60%"
    process = _Process(stdout=_Stream(chunks=[output]), returncode=0)
    monkeypatch.setattr(creator.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    asyncio.run(creator.run_par2_with_progress(["par2", "c", "-p", "secret", "file"], cwd=str(tmp_path)))
    descriptions = [str(update.get("description", "")) for _task, update in _Progress.instances[-1].updates]
    assert any("matrix" in value for value in descriptions)
    assert _Progress.instances[-1].tasks[0].completed == 100

    failed = _Process(stdout=_Stream(chunks=[b"Writing 25%\nout"]), returncode=3)
    monkeypatch.setattr(creator.asyncio, "create_subprocess_exec", AsyncMock(return_value=failed))
    with pytest.raises(RuntimeError, match="exit code 3"):
        asyncio.run(creator.run_par2_with_progress(["par2", "c", "file"]))

    missing = _Process(stdout=None, returncode=0)
    monkeypatch.setattr(creator.asyncio, "create_subprocess_exec", AsyncMock(return_value=missing))
    with pytest.raises(RuntimeError, match="stdout is None"):
        asyncio.run(creator.run_par2_with_progress(["par2", "c", "file"]))

    monkeypatch.setattr(creator.asyncio, "create_subprocess_exec", AsyncMock(side_effect=OSError("spawn failed")))
    with pytest.raises(RuntimeError, match="spawn failed"):
        asyncio.run(creator.run_par2_with_progress(["par2", "c", "file"]))


def test_run_nyuu_progress_post_check_failure_and_missing_stdout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lines = [
        b"Uploading 10 article(s)\n",
        b"Article posting progress: 2 read, 2 posted\n",
        b"Article posting progress: 10 read, 10 posted, 4 checked\n",
        b"Article posting progress: 10 read, 10 posted, 10 checked\n",
    ]
    process = _Process(stdout=_Stream(lines=lines), returncode=0)
    monkeypatch.setattr(creator.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    asyncio.run(creator.run_nyuu_with_progress(["nyuu", "-u", "user", "-p", "pass"], cwd=str(tmp_path)))
    assert _Progress.instances[-1].tasks[0].completed == 100

    no_total = _Process(stdout=_Stream(lines=[b"Article posting progress: 1 read, 1 posted\n"]), returncode=0)
    monkeypatch.setattr(creator.asyncio, "create_subprocess_exec", AsyncMock(return_value=no_total))
    asyncio.run(creator.run_nyuu_with_progress(["nyuu"]))

    failed = _Process(stdout=_Stream(lines=[b"Uploading 2 article(s)\n", b"Article posting progress: 1 read, 1 posted\n", b"failure\n"]), returncode=4)
    monkeypatch.setattr(creator.asyncio, "create_subprocess_exec", AsyncMock(return_value=failed))
    with pytest.raises(RuntimeError, match="exit code 4"):
        asyncio.run(creator.run_nyuu_with_progress(["nyuu"]))

    missing = _Process(stdout=None, returncode=0)
    monkeypatch.setattr(creator.asyncio, "create_subprocess_exec", AsyncMock(return_value=missing))
    with pytest.raises(RuntimeError, match="stdout is None"):
        asyncio.run(creator.run_nyuu_with_progress(["nyuu"]))


def _pesto_lines(*events: dict[str, object] | str) -> list[bytes]:
    return [(json.dumps(event) if isinstance(event, dict) else event).encode() + b"\n" for event in events]


def test_run_pesto_all_events_success(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[dict[str, object] | str] = [
        {"type": "status", "text": " starting "},
        {"type": "status", "text": ""},
        {"type": "par2_encode_progress", "done": 1, "total": 2},
        {"type": "par2_slice_written"},
        {"type": "failed", "description": "segment transient"},
        {"type": "par2_encode_started", "input_slices": 3},
        {"type": "par2_encode_progress", "done": 1, "total": 3},
        {"type": "par2_write_started", "total": 2},
        {"type": "par2_slice_written"},
        {"type": "segment_done", "progress_pct": 50, "total_segments": 2, "segment_done": 1},
        {"type": "par2_encode_started", "input_slices": 4},
        {"type": "par2_write_started", "total": 3},
        {"type": "par2_slice_written"},
        {"type": "check_progress", "checked": 1, "ok": False},
        {"type": "check_retrying", "attempt": 1, "max_attempts": 3, "delay_secs": 1, "reason": "missing"},
        {"type": "check_reposted", "reposted": 1},
        {"type": "check_progress", "checked": 2, "ok": True},
        {"type": "check_done", "failed": 0},
        "not-json",
    ]
    process = _Process(
        stdout=_Stream(lines=_pesto_lines(*events)),
        stderr=_Stream(lines=[b"warning\n"]),
        returncode=0,
    )
    monkeypatch.setattr(creator.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    asyncio.run(creator.run_pesto_with_progress(["pesto", "-u", "user", "--auth-password", "secret"]))
    assert len(_Progress.instances[-1].tasks) >= 4


def test_run_pesto_failures_missing_articles_generic_and_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _pesto_lines(
        {"type": "segment_done", "progress_pct": 100, "total_segments": 3, "segment_done": 3},
        {"type": "check_progress", "checked": 1, "ok": False},
        {"type": "check_reposted", "reposted": 2},
        {"type": "check_done", "failed": 1},
    )
    process = _Process(stdout=_Stream(lines=events), stderr=_Stream(lines=[b"provider rejected\n"]), returncode=5)
    monkeypatch.setattr(creator.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    with pytest.raises(RuntimeError, match="could not be confirmed"):
        asyncio.run(creator.run_pesto_with_progress(["pesto"]))

    generic = _Process(
        stdout=_Stream(lines=_pesto_lines({"type": "segment_done", "progress_pct": 20, "total_segments": 5})),
        stderr=_Stream(lines=[b"generic stderr\n"]),
        returncode=6,
    )
    monkeypatch.setattr(creator.asyncio, "create_subprocess_exec", AsyncMock(return_value=generic))
    with pytest.raises(RuntimeError, match="exit code 6"):
        asyncio.run(creator.run_pesto_with_progress(["pesto"]))

    missing = _Process(stdout=None, stderr=None, returncode=0)
    monkeypatch.setattr(creator.asyncio, "create_subprocess_exec", AsyncMock(return_value=missing))
    with pytest.raises(RuntimeError, match="stdout or stderr is None"):
        asyncio.run(creator.run_pesto_with_progress(["pesto"]))

    monkeypatch.setattr(creator.asyncio, "create_subprocess_exec", AsyncMock(side_effect=OSError("spawn failed")))
    with pytest.raises(RuntimeError, match="spawn failed"):
        asyncio.run(creator.run_pesto_with_progress(["pesto"]))


def test_remaining_progress_and_nzb_helper_branches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Exercise the psutil I/O-counter path and the stream=None path in 7z.
    directory = tmp_path / "progress"
    directory.mkdir()
    (directory / "release.7z").write_bytes(b"x" * 10)

    class Counter:
        def __init__(self) -> None:
            self.calls = 0

        def io_counters(self) -> object:
            self.calls += 1
            if self.calls > 1:
                raise creator.psutil.NoSuchProcess(os.getpid())
            return SimpleNamespace(read_bytes=5)

    class OneShotEvent:
        def __init__(self) -> None:
            self.stopped = False

        def is_set(self) -> bool:
            return self.stopped

        def wait(self, _timeout: float) -> None:
            self.stopped = True

        def set(self) -> None:
            self.stopped = True

    class ImmediateThread:
        def __init__(self, *, target: Any, **_kwargs: object) -> None:
            self.target = target

        def start(self) -> None:
            self.target()

        def join(self, _timeout: float) -> None:
            return None

    monkeypatch.setattr(creator.psutil, "Process", lambda _pid: Counter())
    monkeypatch.setattr(creator, "threading", SimpleNamespace(Event=OneShotEvent, Thread=ImmediateThread))
    process = _Process(stdout=None, stderr=_Stream(chunks=[b"25%", b""]), returncode=0)
    monkeypatch.setattr(creator.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    asyncio.run(creator.run_7z_with_progress(["7z", "a"], directory, "release", None, 10))
    assert any("processed" in str(update.get("description", "")) for _task, update in _Progress.instances[-1].updates)

    # Password redaction and blank progress records in PAR2.
    process = _Process(stdout=_Stream(chunks=[b"\rWriting 10%\r"]), returncode=0)
    monkeypatch.setattr(creator.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    asyncio.run(creator.run_par2_with_progress(["par2", "c", "-p", "secret"], cwd=str(tmp_path)))

    # Pesto can report PAR2 progress without a preceding *_started event.
    process = _Process(
        stdout=_Stream(
            lines=_pesto_lines(
                "",
                {"type": "par2_encode_progress", "done": 1, "total": 2},
                {"type": "par2_slice_written"},
            )
        ),
        stderr=_Stream(lines=[]),
        returncode=0,
    )
    monkeypatch.setattr(creator.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    asyncio.run(creator.run_pesto_with_progress(["pesto", "-u", "user", "--auth-password", "secret"]))

    medium = tmp_path / "medium.nzb"
    medium.write_text("<nzb>" + "x" * 150 + "</nzb>", encoding="utf-8")
    assert asyncio.run(creator.is_valid_nzb(medium))
    asyncio.run(creator.inject_nzb_password(tmp_path / "missing.nzb", "secret"))


def _patch_usenet_tools(monkeypatch: pytest.MonkeyPatch, captured: dict[str, list[list[str]]] | None = None) -> dict[str, list[list[str]]]:
    captured = captured or {"7z": [], "par2": [], "nyuu": [], "pesto": []}

    async def check(name: str, *_args: object, **_kwargs: object) -> str:
        return f"/tools/{name}"

    async def seven(cmd: list[str], *_args: object, **_kwargs: object) -> None:
        captured["7z"].append(list(cmd))
        archive = Path(cmd[-2])
        target = Path(f"{archive}.001") if any(str(arg).startswith("-v") for arg in cmd) else archive
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"archive")

    async def par2(cmd: list[str], cwd: str | None = None) -> None:
        del cwd
        captured["par2"].append(list(cmd))
        target = Path(cmd[4])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"par2")

    async def nyuu(cmd: list[str], cwd: str | None = None) -> None:
        del cwd
        captured["nyuu"].append(list(cmd))
        target = Path(cmd[cmd.index("-o") + 1])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("<nzb><head></head>" + "x" * 120 + "</nzb>", encoding="utf-8")

    async def pesto(cmd: list[str], cwd: str | None = None) -> None:
        del cwd
        captured["pesto"].append(list(cmd))
        target = Path(cmd[cmd.index("--out") + 1])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("<nzb><head></head>" + "x" * 120 + "</nzb>", encoding="utf-8")

    monkeypatch.setattr(creator, "check_binary", check)
    monkeypatch.setattr(creator, "run_7z_with_progress", seven)
    monkeypatch.setattr(creator, "run_par2_with_progress", par2)
    monkeypatch.setattr(creator, "run_nyuu_with_progress", nyuu)
    monkeypatch.setattr(creator, "run_pesto_with_progress", pesto)
    return captured


def test_prepare_missing_config_input_existing_nzb_and_path_fallbacks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert asyncio.run(creator.prepare_and_upload_usenet(_meta(tmp_path), {})) is None
    assert asyncio.run(creator.prepare_and_upload_usenet(_meta(tmp_path, path=""), _cfg())) is None

    source = tmp_path / "folder"
    source.mkdir()
    (source / "file.bin").write_bytes(b"data")
    output = tmp_path / "output"
    output.mkdir()
    existing = output / "folder.nzb"
    existing.write_text("<nzb>" + "x" * 120 + "</nzb>", encoding="utf-8")
    meta = _meta(tmp_path, source, basename_no_ext="", uuid="folder")
    assert asyncio.run(creator.prepare_and_upload_usenet(meta, _cfg(nzb_output_dir=str(output)))) == str(existing)

    invalid_output = tmp_path / "output-file"
    invalid_output.write_bytes(b"file")
    invalid_tmp = tmp_path / "tmp-file"
    invalid_tmp.write_bytes(b"file")
    _patch_usenet_tools(monkeypatch)
    meta = _meta(tmp_path, source, basename_no_ext="Folder", debug=True)
    result = asyncio.run(
        creator.prepare_and_upload_usenet(
            meta,
            _cfg(nzb_output_dir=str(invalid_output), usenet_tmp_dir=str(invalid_tmp), skip_archive=True),
            prepare_only=True,
        )
    )
    assert result == str(source)


def test_prepare_random_static_password_obfuscation_and_reuse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_usenet_tools(monkeypatch)
    tokens = iter(("-invalid", "random-secret"))
    monkeypatch.setattr(creator.secrets, "token_urlsafe", lambda _length: next(tokens))
    monkeypatch.setattr(creator.secrets, "token_hex", lambda _length: "obfuscated")
    meta = _meta(tmp_path, debug=True)
    result = asyncio.run(creator.prepare_and_upload_usenet(meta, _cfg(archive_password=RANDOM_PASSWORD_MODE), prepare_only=True))
    assert result
    assert meta.archive_password == "random-secret"
    assert meta.usenet_archive_password_is_random is True
    assert meta.usenet_archive_name == "obfuscated"

    prepared = list(meta.usenet_prepared_files)
    assert prepared
    meta.debug = True
    result2 = asyncio.run(creator.prepare_and_upload_usenet(meta, _cfg(archive_password=RANDOM_PASSWORD_MODE), prepare_only=True))
    assert result2 and meta.archive_password == "random-secret"

    static = _meta(tmp_path, uuid="static", debug=True)
    asyncio.run(creator.prepare_and_upload_usenet(static, _cfg(archive_password=STATIC_ARCHIVE_PASSWORD), prepare_only=True))
    assert static.archive_password == "configured" and static.usenet_archive_password_is_random is False


def test_prepare_binary_missing_debug_and_non_debug_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def missing(*_args: object, **_kwargs: object) -> str:
        raise FileNotFoundError("missing binary")

    monkeypatch.setattr(creator, "check_binary", missing)
    normal = _meta(tmp_path, debug=False)
    assert asyncio.run(creator.prepare_and_upload_usenet(normal, _cfg(), prepare_only=True)) is None

    debug = _meta(tmp_path, uuid="debug", debug=True)
    result = asyncio.run(creator.prepare_and_upload_usenet(debug, _cfg(), prepare_only=True))
    assert result and Path(result).is_dir()

    pesto = _meta(tmp_path, uuid="pesto", debug=False)
    assert asyncio.run(creator.prepare_and_upload_usenet(pesto, _cfg(usenet_uploader="pesto"))) is None

    calls: list[str] = []

    async def selective(name: str, *_args: object, **_kwargs: object) -> str:
        calls.append(name)
        if name == "nyuu":
            raise FileNotFoundError("missing nyuu")
        return f"/{name}"

    monkeypatch.setattr(creator, "check_binary", selective)
    assert asyncio.run(creator.prepare_and_upload_usenet(_meta(tmp_path, uuid="nyuu"), _cfg())) is None
    assert calls == ["7z", "par2", "nyuu"]


def test_prepare_skip_archive_directory_file_missing_and_password(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_usenet_tools(monkeypatch)
    source_dir = tmp_path / "source-dir"
    source_dir.mkdir()
    (source_dir / "one.bin").write_bytes(b"one")
    (source_dir / "nested").mkdir()
    (source_dir / "nested" / "two.bin").write_bytes(b"two")
    meta = _meta(tmp_path, source_dir, uuid="skip-dir", archive_password=ARCHIVE_PASSWORD)
    result = asyncio.run(creator.prepare_and_upload_usenet(meta, _cfg(skip_archive=True), prepare_only=True))
    assert result == str(source_dir)
    assert captured["par2"] and f"-B{source_dir}" in captured["par2"][0]

    source_file = tmp_path / "single.bin"
    source_file.write_bytes(b"single")
    single = _meta(tmp_path, source_file, uuid="skip-file")
    result = asyncio.run(creator.prepare_and_upload_usenet(single, _cfg(skip_archive=True), prepare_only=True))
    assert result == str(tmp_path)

    missing = _meta(tmp_path, tmp_path / "missing.bin", uuid="skip-missing", debug=True, create_source=False)
    result = asyncio.run(creator.prepare_and_upload_usenet(missing, _cfg(skip_archive=True), prepare_only=True))
    assert result and Path(result, "missing.bin").is_file()


def test_prepare_archive_auto_single_copy_and_prepared_reuse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_usenet_tools(monkeypatch)
    source = tmp_path / "source-dir"
    source.mkdir()
    (source / "one.bin").write_bytes(b"data")
    monkeypatch.setattr(creator, "get_path_size", lambda _path: 12 * 1024**3)
    meta = _meta(tmp_path, source, uuid="auto", debug=True)
    result = asyncio.run(creator.prepare_and_upload_usenet(meta, _cfg(rar_volume_size="auto"), prepare_only=True))
    assert result
    assert "-v500m" in captured["7z"][0]
    assert meta.usenet_prepared_files

    file = tmp_path / "copy.bin"
    file.write_bytes(b"copy")
    copied = _meta(tmp_path, file, uuid="copy")
    result = asyncio.run(creator.prepare_and_upload_usenet(copied, _cfg(), prepare_only=True))
    assert result
    assert any(Path(path).name == "copy.bin" for path in copied.usenet_prepared_files)

    prepared_archive = tmp_path / "prepared.7z"
    prepared_par2 = tmp_path / "prepared.par2"
    prepared_archive.write_bytes(b"archive")
    prepared_par2.write_bytes(b"par2")
    captured["7z"].clear()
    captured["par2"].clear()
    reused = _meta(
        tmp_path,
        file,
        uuid="reuse",
        usenet_prepared_files=[str(prepared_archive), str(prepared_par2)],
    )
    result = asyncio.run(creator.prepare_and_upload_usenet(reused, _cfg(), prepare_only=True))
    assert result and captured["7z"] == [] and captured["par2"] == []


def test_prepare_debug_nyuu_simulation_password_and_final_move(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def missing(*_args: object, **_kwargs: object) -> str:
        raise FileNotFoundError("missing")

    monkeypatch.setattr(creator, "check_binary", missing)
    monkeypatch.setattr(creator.secrets, "token_hex", lambda _length: "hex-subject")
    meta = _meta(tmp_path, uuid="debug-post", debug=True, archive_password=ARCHIVE_PASSWORD)
    result = asyncio.run(creator.prepare_and_upload_usenet(meta, _cfg(archive_password=ARCHIVE_PASSWORD)))
    assert result and Path(result).is_file()
    content = Path(result).read_text(encoding="utf-8")
    assert '<meta type="password">secret</meta>' in content


def test_prepare_nyuu_command_ids_checks_subject_and_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_usenet_tools(monkeypatch)
    meta = _meta(tmp_path, uuid="nyuu-command", usenet_subject="Custom Subject")
    config = _cfg(
        random_poster=False,
        poster="Poster <poster@example.com>",
        obscure_subject=True,
        nyuu_check=True,
        nyuu_check_connections=2,
        nyuu_check_delay="3",
        nyuu_check_retries=4,
    )
    result = asyncio.run(creator.prepare_and_upload_usenet(meta, config))
    assert result and Path(result).is_file()
    cmd = captured["nyuu"][0]
    assert "Custom Subject" in cmd and "-S" in cmd
    assert "--filename" not in cmd
    assert cmd[cmd.index("--check-connections") : cmd.index("--check-connections") + 2] == ["--check-connections", "2"]
    assert "tmdbid: movie/123" in cmd and "imdbid: tt1234567" in cmd
    assert "tvdbid: 456" in cmd and "malid: 789" in cmd

    no_check = _meta(tmp_path, uuid="nyuu-no-check", basename_no_ext="Nyuu No Check", tmdb_id=0, imdb_tt="", tvdb_id=0, mal_id=0)
    asyncio.run(
        creator.prepare_and_upload_usenet(
            no_check,
            _cfg(ssl=False, obscure_subject=False, nyuu_check=False, random_poster=False),
        )
    )
    cmd = captured["nyuu"][-1]
    assert "-S" not in cmd and "--check-connections" not in cmd and "--filename" not in cmd


def test_prepare_pesto_command_options_no_check_and_password(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_usenet_tools(monkeypatch)
    meta = _meta(tmp_path, uuid="pesto-command", archive_password=ARCHIVE_PASSWORD)
    config = _cfg(
        usenet_uploader="pesto",
        ssl=False,
        random_poster=False,
        obscure_subject=True,
        archive_password=ARCHIVE_PASSWORD,
        pesto_check=True,
        pesto_check_delay=2,
        pesto_check_retries=4,
        pesto_check_connections=3,
        pesto_check_post_retries=5,
    )
    result = asyncio.run(creator.prepare_and_upload_usenet(meta, config))
    assert result and Path(result).is_file()
    cmd = captured["pesto"][0]
    for flag in ("--no-ssl", "-f", "--obfuscate=full", "--nzb-password", "--check", "--check-delay", "--check-retries", "--check-connections", "--check-post-retries"):
        assert flag in cmd
    assert "movie/123" in cmd and "tt1234567" in cmd

    no_check = _meta(tmp_path, uuid="pesto-no-check", basename_no_ext="Pesto No Check", tmdb_id="bad", imdb_tt="", tvdb_id="bad", mal_id="bad")
    asyncio.run(
        creator.prepare_and_upload_usenet(
            no_check,
            _cfg(usenet_uploader="pesto", pesto_check=False, obscure_subject=False),
        )
    )
    assert "--no-check" in captured["pesto"][-1]


def test_prepare_upload_failures_remove_nzb_and_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_usenet_tools(monkeypatch)

    async def fail_nyuu(cmd: list[str], cwd: str | None = None) -> None:
        del cwd
        target = Path(cmd[cmd.index("-o") + 1])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("<nzb>" + "x" * 120 + "</nzb>", encoding="utf-8")
        raise RuntimeError("nyuu failed")

    monkeypatch.setattr(creator, "run_nyuu_with_progress", fail_nyuu)
    meta = _meta(tmp_path, uuid="nyuu-failure")
    with pytest.raises(RuntimeError, match="nyuu failed"):
        asyncio.run(creator.prepare_and_upload_usenet(meta, _cfg()))

    async def fail_pesto(cmd: list[str], cwd: str | None = None) -> None:
        del cwd
        target = Path(cmd[cmd.index("--out") + 1])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("<nzb>" + "x" * 120 + "</nzb>", encoding="utf-8")
        raise RuntimeError("pesto failed")

    monkeypatch.setattr(creator, "run_pesto_with_progress", fail_pesto)
    meta = _meta(tmp_path, uuid="pesto-failure")
    with pytest.raises(RuntimeError, match="pesto failed"):
        asyncio.run(creator.prepare_and_upload_usenet(meta, _cfg(usenet_uploader="pesto", rar_volume_size="100m")))
    assert captured["7z"]


def test_prepare_move_and_cleanup_failures_are_nonfatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_usenet_tools(monkeypatch)
    original_move = creator.shutil.move
    original_rmtree = creator.shutil.rmtree
    monkeypatch.setattr(creator.shutil, "rmtree", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("cleanup failed")))
    monkeypatch.setattr(creator.shutil, "move", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("move failed")))
    meta = _meta(tmp_path, uuid="move-failure")
    result = asyncio.run(creator.prepare_and_upload_usenet(meta, _cfg()))
    assert result and Path(result).is_file()
    monkeypatch.setattr(creator.shutil, "move", original_move)
    monkeypatch.setattr(creator.shutil, "rmtree", original_rmtree)


def test_remaining_pesto_progress_and_archive_monitor_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    process = _Process(
        stdout=_Stream(
            lines=_pesto_lines(
                {"type": "par2_encode_started", "input_slices": 2},
                {"type": "par2_write_started", "total": 2},
            )
        ),
        stderr=_Stream(lines=[]),
        returncode=0,
    )
    monkeypatch.setattr(creator.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    asyncio.run(creator.run_pesto_with_progress(["pesto"]))
    assert len(_Progress.instances[-1].tasks) == 2

    directory = tmp_path / "archive-monitor"
    directory.mkdir()

    class Counter:
        def io_counters(self) -> object:
            return SimpleNamespace(read_bytes=1)

    class OneShotEvent:
        def __init__(self) -> None:
            self.stopped = False

        def is_set(self) -> bool:
            return self.stopped

        def wait(self, _timeout: float) -> None:
            self.stopped = True

        def set(self) -> None:
            self.stopped = True

    class ImmediateThread:
        def __init__(self, *, target: Any, **_kwargs: object) -> None:
            self.target = target

        def start(self) -> None:
            self.target()

        def join(self, _timeout: float) -> None:
            return None

    monkeypatch.setattr(creator.psutil, "Process", lambda _pid: Counter())
    monkeypatch.setattr(creator, "threading", SimpleNamespace(Event=OneShotEvent, Thread=ImmediateThread))
    original_iterdir = Path.iterdir

    def fail_iterdir(path: Path):
        if path == directory:
            raise OSError("volume renamed")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", fail_iterdir)
    process = _Process(stdout=_Stream(chunks=[]), stderr=_Stream(chunks=[]), returncode=0)
    monkeypatch.setattr(creator.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    asyncio.run(creator.run_7z_with_progress(["7z", "a"], directory, "release", None, 10))


def test_prepare_empty_basename_file_and_binary_failure_stages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_usenet_tools(monkeypatch)
    source = tmp_path / "unnamed.bin"
    source.write_bytes(b"data")
    meta = _meta(tmp_path, source, uuid="empty-name", basename_no_ext="")
    result = asyncio.run(creator.prepare_and_upload_usenet(meta, _cfg()))
    assert result and Path(result).name == ".nzb"
    assert captured["nyuu"]

    async def fail_pesto(name: str, *_args: object, **_kwargs: object) -> str:
        if name == "pesto":
            raise FileNotFoundError("pesto missing")
        return f"/{name}"

    monkeypatch.setattr(creator, "check_binary", fail_pesto)
    normal = _meta(tmp_path, uuid="pesto-missing", basename_no_ext="Pesto Missing")
    assert asyncio.run(creator.prepare_and_upload_usenet(normal, _cfg(usenet_uploader="pesto"))) is None
    debug = _meta(tmp_path, uuid="pesto-missing-debug", basename_no_ext="Pesto Missing Debug", debug=True)
    result = asyncio.run(creator.prepare_and_upload_usenet(debug, _cfg(usenet_uploader="pesto")))
    assert result and Path(result).is_file()

    async def fail_par2(name: str, *_args: object, **_kwargs: object) -> str:
        if name == "par2":
            raise FileNotFoundError("par2 missing")
        return f"/{name}"

    monkeypatch.setattr(creator, "check_binary", fail_par2)
    normal = _meta(tmp_path, uuid="par2-missing", basename_no_ext="Par2 Missing")
    assert asyncio.run(creator.prepare_and_upload_usenet(normal, _cfg(), prepare_only=True)) is None


def test_prepare_debug_missing_single_file_pesto_simulation_and_absolute_prepared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_usenet_tools(monkeypatch)
    missing = _meta(
        tmp_path,
        tmp_path / "missing-source.bin",
        create_source=False,
        uuid="debug-copy-missing",
        basename_no_ext="Debug Missing",
        debug=True,
    )
    result = asyncio.run(creator.prepare_and_upload_usenet(missing, _cfg(), prepare_only=True))
    assert result and Path(result, "missing-source.bin").is_file()

    async def pesto_missing(name: str, *_args: object, **_kwargs: object) -> str:
        if name == "pesto":
            raise FileNotFoundError("pesto missing")
        return f"/{name}"

    monkeypatch.setattr(creator, "check_binary", pesto_missing)
    pesto = _meta(tmp_path, uuid="pesto-debug-sim", basename_no_ext="Pesto Debug", debug=True)
    result = asyncio.run(creator.prepare_and_upload_usenet(pesto, _cfg(usenet_uploader="pesto")))
    assert result and Path(result).is_file()

    external = tmp_path / "external-prepared.bin"
    external.write_bytes(b"prepared")
    captured = _patch_usenet_tools(monkeypatch)
    prepared = _meta(
        tmp_path,
        uuid="absolute-prepared",
        basename_no_ext="Absolute Prepared",
        usenet_prepared_files=[str(external)],
    )
    result = asyncio.run(creator.prepare_and_upload_usenet(prepared, _cfg()))
    assert result and str(external) in captured["nyuu"][0]


def test_prepare_skips_deleted_upload_file_and_real_password_injection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_usenet_tools(monkeypatch)
    source = tmp_path / "source-dir"
    source.mkdir()
    (source / "one.bin").write_bytes(b"data")

    async def par2_and_delete(cmd: list[str], cwd: str | None = None) -> None:
        del cwd
        captured["par2"].append(list(cmd))
        Path(cmd[4]).write_bytes(b"par2")
        for target in Path(cmd[4]).parent.glob("*.7z*"):
            target.unlink()

    monkeypatch.setattr(creator, "run_par2_with_progress", par2_and_delete)
    meta = _meta(tmp_path, source, uuid="deleted-upload", basename_no_ext="Deleted Upload")
    result = asyncio.run(creator.prepare_and_upload_usenet(meta, _cfg(rar_volume_size="100m")))
    assert result and Path(result).is_file()

    captured = _patch_usenet_tools(monkeypatch)
    password = ARCHIVE_PASSWORD
    secured = _meta(tmp_path, uuid="secured-real", basename_no_ext="Secured Real", archive_password=password)
    result = asyncio.run(creator.prepare_and_upload_usenet(secured, _cfg(archive_password=password)))
    assert result and asyncio.run(creator.verify_nzb_has_password(result))


def test_remaining_monitor_and_initial_pesto_tasks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    directory = tmp_path / "monitor-errors"
    directory.mkdir()

    class OneShotEvent:
        def __init__(self) -> None:
            self.stopped = False

        def is_set(self) -> bool:
            return self.stopped

        def wait(self, _timeout: float) -> None:
            self.stopped = True

        def set(self) -> None:
            self.stopped = True

    class ImmediateThread:
        def __init__(self, *, target: Any, **_kwargs: object) -> None:
            self.target = target

        def start(self) -> None:
            self.target()

        def join(self, _timeout: float) -> None:
            return None

    original_iterdir = Path.iterdir

    def fail_iterdir(path: Path):
        if path == directory:
            raise OSError("volume renamed")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", fail_iterdir)
    monkeypatch.setattr(creator, "threading", SimpleNamespace(Event=OneShotEvent, Thread=ImmediateThread))
    monkeypatch.setattr(
        creator.asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=_Process(stdout=_Stream(chunks=[b""]), stderr=_Stream(chunks=[b""]), returncode=0)),
    )
    asyncio.run(creator.run_7z_with_progress(["7z", "a"], directory, "archive", None, 10))

    started = _Process(
        stdout=_Stream(
            lines=_pesto_lines(
                {"type": "par2_encode_started", "input_slices": 3},
                {"type": "par2_write_started", "total": 2},
            )
        ),
        stderr=_Stream(lines=[]),
        returncode=0,
    )
    monkeypatch.setattr(creator.asyncio, "create_subprocess_exec", AsyncMock(return_value=started))
    asyncio.run(creator.run_pesto_with_progress(["pesto"]))
    descriptions = [task.description for task in _Progress.instances[-1].tasks]
    assert "Calculating PAR2 parity" in descriptions and "Writing PAR2 recovery files" in descriptions


def test_prepare_remaining_binary_debug_name_and_missing_source_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Empty media basename for a file reaches the final safe-name fallback.
    captured = _patch_usenet_tools(monkeypatch)
    empty_name_source = tmp_path / "empty-name.bin"
    empty_name_source.write_bytes(b"source")
    empty_name = _meta(tmp_path, empty_name_source, uuid="empty-name", basename_no_ext="", debug=True)
    assert asyncio.run(creator.prepare_and_upload_usenet(empty_name, _cfg(), prepare_only=True))

    # Pesto missing in debug mode simulates the post; non-debug treats it as a
    # configuration error. PAR2 has the same non-debug contract.
    async def debug_missing(_name: str, *_args: object, **_kwargs: object) -> str:
        raise FileNotFoundError("missing tool")

    monkeypatch.setattr(creator, "check_binary", debug_missing)
    debug_source = tmp_path / "debug-pesto.bin"
    debug_source.write_bytes(b"source")
    debug_pesto = _meta(tmp_path, debug_source, uuid="debug-pesto", basename_no_ext="Debug Pesto", debug=True)
    result = asyncio.run(creator.prepare_and_upload_usenet(debug_pesto, _cfg(usenet_uploader="pesto")))
    assert result and Path(result).is_file()

    async def pesto_missing(name: str, *_args: object, **_kwargs: object) -> str:
        if name == "7z":
            return "/tools/7z"
        raise FileNotFoundError(f"missing {name}")

    monkeypatch.setattr(creator, "check_binary", pesto_missing)
    pesto_source = tmp_path / "pesto-missing.bin"
    pesto_source.write_bytes(b"source")
    assert (
        asyncio.run(
            creator.prepare_and_upload_usenet(
                _meta(tmp_path, pesto_source, uuid="pesto-missing", basename_no_ext="Pesto Missing", debug=False),
                _cfg(usenet_uploader="pesto"),
            )
        )
        is None
    )

    par2_source = tmp_path / "par2-missing.bin"
    par2_source.write_bytes(b"source")
    assert (
        asyncio.run(
            creator.prepare_and_upload_usenet(
                _meta(tmp_path, par2_source, uuid="par2-missing", basename_no_ext="Par2 Missing", debug=False),
                _cfg(),
            )
        )
        is None
    )

    # A debug-only absent single file gets represented by a deterministic dummy
    # staging file instead of touching the source filesystem.
    captured = _patch_usenet_tools(monkeypatch)
    absent = tmp_path / "absent-source.bin"
    absent_meta = _meta(tmp_path, tmp_path / "placeholder.bin", uuid="absent", basename_no_ext="Absent", debug=True)
    absent_meta.path = str(absent)
    staged = asyncio.run(creator.prepare_and_upload_usenet(absent_meta, _cfg(), prepare_only=True))
    assert staged and any(Path(path).name == "absent-source.bin" for path in absent_meta.usenet_prepared_files)
    assert captured["par2"]


def test_prepare_relative_fallback_disappearing_files_pesto_debug_and_real_password_injection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_usenet_tools(monkeypatch)
    prepared = tmp_path / "outside-prepared.7z"
    prepared.write_bytes(b"archive")
    relative = _meta(
        tmp_path,
        tmp_path / "relative-source.bin",
        uuid="relative",
        basename_no_ext="Relative",
        usenet_prepared_files=[str(prepared)],
    )
    Path(relative.path).write_bytes(b"source")
    result = asyncio.run(creator.prepare_and_upload_usenet(relative, _cfg()))
    assert result and str(prepared) in captured["nyuu"][-1]

    # The prepared-file validity check can race with cleanup by another process;
    # an entry that disappears before collection is skipped safely.
    disappearing = tmp_path / "disappearing.7z"
    disappearing.write_bytes(b"archive")
    race = _meta(
        tmp_path,
        tmp_path / "race-source.bin",
        uuid="race",
        basename_no_ext="Race",
        usenet_prepared_files=[str(disappearing)],
    )
    Path(race.path).write_bytes(b"source")
    original_is_file = Path.is_file
    calls = 0

    def race_is_file(path: Path) -> bool:
        nonlocal calls
        if path == disappearing:
            calls += 1
            return calls == 1
        return original_is_file(path)

    monkeypatch.setattr(Path, "is_file", race_is_file)
    assert asyncio.run(creator.prepare_and_upload_usenet(race, _cfg()))
    monkeypatch.setattr(Path, "is_file", original_is_file)

    # Debug Pesto writes its mock NZB without invoking a binary.
    async def missing(*_args: object, **_kwargs: object) -> str:
        raise FileNotFoundError("missing")

    monkeypatch.setattr(creator, "check_binary", missing)
    pesto_source = tmp_path / "pesto-debug.bin"
    pesto_source.write_bytes(b"source")
    pesto = _meta(tmp_path, pesto_source, uuid="pesto-debug", basename_no_ext="Pesto Debug", debug=True)
    pesto_result = asyncio.run(creator.prepare_and_upload_usenet(pesto, _cfg(usenet_uploader="pesto")))
    assert pesto_result and Path(pesto_result).is_file()

    # The real/non-debug Nyuu path injects archive credentials after upload.
    captured = _patch_usenet_tools(monkeypatch)
    password_source = tmp_path / "password-source.bin"
    password_source.write_bytes(b"source")
    password_meta = _meta(
        tmp_path,
        password_source,
        uuid="password-real",
        basename_no_ext="Password Real",
        archive_password=ARCHIVE_PASSWORD,
        debug=False,
    )
    password_result = asyncio.run(creator.prepare_and_upload_usenet(password_meta, _cfg(archive_password=ARCHIVE_PASSWORD)))
    assert password_result and '<meta type="password">secret</meta>' in Path(password_result).read_text(encoding="utf-8")
