from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.domain_models.release import Meta
from src.integrations.media import zentag


class Reader:
    def __init__(self, data: bytes) -> None:
        self.data = bytearray(data)

    async def read(self, size: int = -1) -> bytes:
        if not self.data:
            return b""
        if size < 0:
            value = bytes(self.data)
            self.data.clear()
            return value
        value = bytes(self.data[:size])
        del self.data[:size]
        return value


class Writer:
    def __init__(self) -> None:
        self.values: list[bytes] = []

    def write(self, value: bytes) -> None:
        self.values.append(value)

    async def drain(self) -> None:
        return None


class Process:
    def __init__(
        self,
        stdout: bytes = b"",
        stderr: bytes = b"",
        code: int | None = 0,
        *,
        pipes: bool = True,
    ) -> None:
        self.stdin = Writer() if pipes else None
        self.stdout = Reader(stdout) if pipes else None
        self.stderr = Reader(stderr) if pipes else None
        self.returncode: int | None = code
        self.killed = False

    async def wait(self) -> int:
        return int(self.returncode or 0)

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def _meta(path: Path, **values: object) -> Meta:
    state: dict[str, object] = {
        "path": str(path),
        "filelist": [str(path)],
        "trackers": ["ZENITH"],
        "unattended": True,
        "site_check": False,
        "category": "BOOK",
        "audiobook": path.suffix.lower() == ".m4b",
        "comic": False,
        "manga": False,
        "magazine": False,
        "newspaper": False,
    }
    state.update(values)
    return Meta(state)


def test_source_detection_and_prepare_flags(tmp_path: Path) -> None:
    directory = tmp_path / "audio"
    directory.mkdir()
    m4b = directory / "book.m4b"
    m4b.write_bytes(b"audio")
    assert zentag._contains_m4b(directory)
    assert not zentag._contains_m4b(tmp_path / "missing")

    config = {"DEFAULT": {"auto_zentag": False}}
    assert not zentag.should_prepare_zenith_audiobook(_meta(m4b), config)
    assert not zentag.should_prepare_zenith_ebook(
        _meta(tmp_path / "book.epub", audiobook=False), config
    )

    epub = tmp_path / "book.epub"
    epub.write_bytes(b"book")
    meta = _meta(tmp_path / "folder", audiobook=False, filelist=[str(epub)])
    assert zentag._ebook_source(meta) == epub.resolve()
    meta.filelist = [str(epub), str(tmp_path / "other.pdf")]
    (tmp_path / "other.pdf").write_bytes(b"pdf")
    assert zentag._ebook_source(meta) is None


def test_run_transform_prompts_and_missing_pipes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = Process(b"Choice [2]: Proceed? [y/N]: Done", b"warning", 0)
    monkeypatch.setattr(
        zentag.asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=process),
    )
    code, stdout, stderr = asyncio.run(
        zentag._run_transform(["zentag", "transform"])
    )
    assert code == 0 and "Done" in stdout and stderr == "warning"
    assert process.stdin is not None and process.stdin.values == [
        b"\n",
        b"y\n",
    ]

    no_pipes = Process(pipes=False, code=None)
    monkeypatch.setattr(
        zentag.asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=no_pipes),
    )
    with pytest.raises(RuntimeError, match="pipes are unavailable"):
        asyncio.run(zentag._run_transform(["zentag"]))
    assert no_pipes.killed


def test_written_output_all_shapes(tmp_path: Path) -> None:
    root = tmp_path / "output"
    root.mkdir()
    directory = root / "release"
    directory.mkdir()
    file = directory / "book.epub"
    file.write_bytes(b"book")
    assert zentag._written_output("nothing", root) is None
    assert zentag._written_output(f"Wrote {directory}", root) == directory
    assert zentag._written_output(f"Wrote {file}", root) == directory
    assert (
        zentag._written_output(f"Wrote {tmp_path / 'outside'}", root) is None
    )
    assert zentag._written_output('Wrote "bad json', root) is None
    assert zentag._written_output(f"Wrote {root / 'missing'}", root) is None


def test_paths_nonfile_and_windows_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "book.epub"
    source.write_bytes(b"book")
    directory = tmp_path / "ebook-meta-dir"
    directory.mkdir()
    with pytest.raises(RuntimeError, match="is not a file"):
        zentag._zentag_paths(
            source, str(tmp_path), {"ebook_meta_path": str(directory)}
        )

    executable = tmp_path / "ebook-meta.exe"
    executable.write_bytes(b"binary")
    monkeypatch.setattr(
        zentag,
        "os",
        SimpleNamespace(name="nt", access=os.access, X_OK=os.X_OK),
    )
    output, config = zentag._zentag_paths(
        source, str(tmp_path), {"ebook_meta_path": str(executable)}
    )
    assert output.is_dir() and str(executable) in config.read_text(
        encoding="utf-8"
    )


def test_prepare_audiobook_and_ebook_failure_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    m4b = tmp_path / "book.m4b"
    m4b.write_bytes(b"audio")
    epub = tmp_path / "book.epub"
    epub.write_bytes(b"book")
    config = {"DEFAULT": {"auto_zentag": True}}

    monkeypatch.setattr(
        zentag, "should_prepare_zenith_audiobook", lambda *_args: False
    )
    assert (
        asyncio.run(
            zentag.prepare_zenith_audiobook(_meta(m4b), str(tmp_path), config)
        )
        is None
    )
    monkeypatch.setattr(
        zentag, "should_prepare_zenith_audiobook", lambda *_args: True
    )
    assert (
        asyncio.run(
            zentag.prepare_zenith_audiobook(
                _meta(tmp_path / "missing"), str(tmp_path), config
            )
        )
        is None
    )

    monkeypatch.setattr(
        zentag.ZentagBinaryManager,
        "ensure_binary",
        AsyncMock(return_value="zentag"),
    )
    monkeypatch.setattr(
        zentag,
        "_run_transform",
        AsyncMock(return_value=(1, "", "transform failed")),
    )
    assert (
        asyncio.run(
            zentag.prepare_zenith_audiobook(_meta(m4b), str(tmp_path), config)
        )
        is None
    )
    monkeypatch.setattr(
        zentag, "_run_transform", AsyncMock(return_value=(0, "no output", ""))
    )
    assert (
        asyncio.run(
            zentag.prepare_zenith_audiobook(_meta(m4b), str(tmp_path), config)
        )
        is None
    )

    output = tmp_path / "zentag-output" / "book"
    output.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        zentag,
        "_run_transform",
        AsyncMock(return_value=(0, f"Wrote {output}", "")),
    )
    monkeypatch.setattr(
        zentag, "_run_process", AsyncMock(return_value=(1, "", "check failed"))
    )
    assert (
        asyncio.run(
            zentag.prepare_zenith_audiobook(_meta(m4b), str(tmp_path), config)
        )
        is None
    )

    monkeypatch.setattr(
        zentag, "should_prepare_zenith_ebook", lambda *_args: False
    )
    assert (
        asyncio.run(
            zentag.prepare_zenith_ebook(
                _meta(epub, audiobook=False), str(tmp_path), config
            )
        )
        is None
    )
    monkeypatch.setattr(
        zentag, "should_prepare_zenith_ebook", lambda *_args: True
    )
    assert (
        asyncio.run(
            zentag.prepare_zenith_ebook(
                _meta(tmp_path / "missing", audiobook=False),
                str(tmp_path),
                config,
            )
        )
        is None
    )
    monkeypatch.setattr(zentag, "_ebook_source", lambda _meta: epub)
    monkeypatch.setattr(
        zentag, "_run_process", AsyncMock(return_value=(1, "", "ebook failed"))
    )
    assert (
        asyncio.run(
            zentag.prepare_zenith_ebook(
                _meta(epub, audiobook=False), str(tmp_path), config
            )
        )
        is None
    )
    monkeypatch.setattr(
        zentag, "_run_process", AsyncMock(return_value=(0, "no output", ""))
    )
    assert (
        asyncio.run(
            zentag.prepare_zenith_ebook(
                _meta(epub, audiobook=False), str(tmp_path), config
            )
        )
        is None
    )


def test_final_zentag_branch_matrix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    m4b = tmp_path / "book.m4b"
    m4b.write_bytes(b"audio")
    epub = tmp_path / "book.epub"
    epub.write_bytes(b"book")
    config = {"DEFAULT": {"auto_zentag": True}}
    assert not zentag.should_prepare_zenith_audiobook(
        _meta(m4b, site_check=True), config
    )
    assert not zentag.should_prepare_zenith_ebook(
        _meta(epub, audiobook=False, unattended=False), config
    )

    output = tmp_path / "zentag-output" / "book"
    output.mkdir(parents=True)
    monkeypatch.setattr(
        zentag.ZentagBinaryManager,
        "ensure_binary",
        AsyncMock(return_value="zentag"),
    )
    monkeypatch.setattr(
        zentag, "should_prepare_zenith_audiobook", lambda *_args: True
    )
    monkeypatch.setattr(
        zentag,
        "_run_transform",
        AsyncMock(return_value=(0, f"Wrote {output}", "")),
    )
    monkeypatch.setattr(
        zentag,
        "_run_process",
        AsyncMock(return_value=(0, '[{"violation": true}]', "")),
    )
    assert (
        asyncio.run(
            zentag.prepare_zenith_audiobook(_meta(m4b), str(tmp_path), config)
        )
        is None
    )

    monkeypatch.setattr(
        zentag, "should_prepare_zenith_ebook", lambda *_args: True
    )
    monkeypatch.setattr(zentag, "_ebook_source", lambda _meta: epub)
    monkeypatch.setattr(
        zentag,
        "_run_process",
        AsyncMock(
            side_effect=[
                (0, f"Wrote {output}", ""),
                (1, "", "ebook check failed"),
            ]
        ),
    )
    assert (
        asyncio.run(
            zentag.prepare_zenith_ebook(
                _meta(epub, audiobook=False), str(tmp_path), config
            )
        )
        is None
    )
