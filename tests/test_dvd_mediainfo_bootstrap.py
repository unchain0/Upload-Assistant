import asyncio
import stat
import zipfile
from pathlib import Path

import pytest

from bin.MI.get_linux_mi import extract_linux
from src.discparse import DiscParse


def _write_member(archive: zipfile.ZipFile, name: str, payload: bytes, mode: int = stat.S_IFREG | 0o644) -> None:
    member = zipfile.ZipInfo(name)
    member.external_attr = mode << 16
    archive.writestr(member, payload)


def test_extract_linux_promotes_verified_regular_members_together(tmp_path: Path) -> None:
    cli_archive = tmp_path / "cli.zip"
    lib_archive = tmp_path / "lib.zip"
    output = tmp_path / "output"
    output.mkdir()
    with zipfile.ZipFile(cli_archive, "w") as archive:
        _write_member(archive, "bin/mediainfo", b"cli")
    with zipfile.ZipFile(lib_archive, "w") as archive:
        _write_member(archive, "lib/libmediainfo.so.0.0.0", b"lib")

    extract_linux(cli_archive, lib_archive, output)

    assert (output / "mediainfo").read_bytes() == b"cli"  # noqa: S101
    assert (output / "libmediainfo.so.0").read_bytes() == b"lib"  # noqa: S101


def test_extract_linux_does_not_bless_stale_cli_when_library_is_missing(tmp_path: Path) -> None:
    cli_archive = tmp_path / "cli.zip"
    lib_archive = tmp_path / "lib.zip"
    output = tmp_path / "output"
    output.mkdir()
    stale = output / "mediainfo"
    stale.write_bytes(b"stale")
    with zipfile.ZipFile(cli_archive, "w") as archive:
        _write_member(archive, "bin/mediainfo", b"new")
    with zipfile.ZipFile(lib_archive, "w") as archive:
        _write_member(archive, "unrelated", b"x")

    with pytest.raises(RuntimeError, match="required library"):
        extract_linux(cli_archive, lib_archive, output)

    assert stale.read_bytes() == b"stale"  # noqa: S101
    assert not (output / "libmediainfo.so.0").exists()  # noqa: S101


def test_specialized_mediainfo_timeout_kills_and_reaps_process(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class Process:
        returncode = None

        def __init__(self) -> None:
            self.killed = False
            self.calls = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            self.calls += 1
            if self.calls == 1:
                await asyncio.sleep(1)
            return b"", b""

        def kill(self) -> None:
            self.killed = True

    process = Process()

    async def create_process(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return process

    async def immediate_timeout(awaitable, **kwargs):  # type: ignore[no-untyped-def]
        awaitable.close()
        assert kwargs["timeout"] == 30  # noqa: S101
        raise TimeoutError

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    monkeypatch.setattr(asyncio, "wait_for", immediate_timeout)

    with pytest.raises(RuntimeError, match="timed out after 30 seconds"):
        asyncio.run(DiscParse({})._run_specialized_mediainfo("mediainfo", "input.ifo"))

    assert process.killed is True  # noqa: S101
    assert process.calls == 1  # noqa: S101
