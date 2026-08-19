import asyncio
import io
import stat
import tarfile
import zipfile
from pathlib import Path

import pytest

from src.integrations.media.disc_parser import DiscParse
from src.integrations.runtime_tools import bdinfo as get_bdinfo
from src.integrations.runtime_tools.bdinfo import BDInfoBinaryManager
from src.integrations.runtime_tools.dvd_media_info import extract_linux


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

    assert (output / "mediainfo").read_bytes() == b"cli"
    assert (output / "libmediainfo.so.0").read_bytes() == b"lib"


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

    assert stale.read_bytes() == b"stale"
    assert not (output / "libmediainfo.so.0").exists()


def test_extract_linux_restores_existing_pair_when_second_promotion_fails(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    cli_archive = tmp_path / "cli.zip"
    lib_archive = tmp_path / "lib.zip"
    output = tmp_path / "output"
    output.mkdir()
    (output / "mediainfo").write_bytes(b"old-cli")
    (output / "libmediainfo.so.0").write_bytes(b"old-lib")
    marker = output / "version_23.04"
    marker.write_text("old-version")
    with zipfile.ZipFile(cli_archive, "w") as archive:
        _write_member(archive, "bin/mediainfo", b"new-cli")
    with zipfile.ZipFile(lib_archive, "w") as archive:
        _write_member(archive, "lib/libmediainfo.so.0.0.0", b"new-lib")

    original_replace = Path.replace

    def fail_library_promotion(source: Path, target: Path) -> Path:
        if source.name == "libmediainfo.so.0" and source.parent.name == ".mediainfo-staging":
            raise OSError("simulated promotion failure")
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_library_promotion)

    with pytest.raises(OSError, match="simulated promotion failure"):
        extract_linux(cli_archive, lib_archive, output)

    assert (output / "mediainfo").read_bytes() == b"old-cli"
    assert (output / "libmediainfo.so.0").read_bytes() == b"old-lib"
    assert marker.read_text() == "old-version"


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

    timeouts: list[int] = []

    async def immediate_timeout(awaitable, **kwargs):  # type: ignore[no-untyped-def]
        awaitable.close()
        timeouts.append(kwargs["timeout"])
        raise TimeoutError

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    monkeypatch.setattr(asyncio, "wait_for", immediate_timeout)

    with pytest.raises(RuntimeError, match="timed out after 30 seconds"):
        asyncio.run(DiscParse({})._run_specialized_mediainfo("mediainfo", "input.ifo"))

    assert process.killed is True
    assert process.calls == 0
    assert timeouts == [30, 5]


def test_specialized_mediainfo_cancellation_kills_and_reaps_process(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class Process:
        returncode = None

        def __init__(self) -> None:
            self.killed = False
            self.calls = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            self.calls += 1
            if self.calls == 1:
                await asyncio.sleep(60)
            return b"", b""

        def kill(self) -> None:
            self.killed = True

    process = Process()

    async def create_process(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return process

    async def exercise() -> None:
        task = asyncio.create_task(DiscParse({})._run_specialized_mediainfo("mediainfo", "input.ifo"))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    asyncio.run(exercise())

    assert process.killed is True
    assert process.calls == 2


def test_bdinfo_progress_cancellation_kills_and_reaps_process(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class Stderr:
        async def read(self, _size: int) -> bytes:
            await asyncio.sleep(60)
            return b""

    class Process:
        returncode = None
        stderr = Stderr()

        def __init__(self) -> None:
            self.killed = False
            self.waited = False

        def kill(self) -> None:
            self.killed = True

        async def wait(self) -> int:
            self.waited = True
            self.returncode = -9
            return self.returncode

    class Progress:
        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *_args):  # type: ignore[no-untyped-def]
            return None

        def add_task(self, *_args, **_kwargs) -> int:  # type: ignore[no-untyped-def]
            return 1

        def update(self, *_args, **_kwargs) -> None:  # type: ignore[no-untyped-def]
            return None

    process = Process()

    async def create_process(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return process

    async def exercise() -> None:
        task = asyncio.create_task(DiscParse({})._run_bdinfo_with_progress(["bdinfo"], "qa"))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    monkeypatch.setattr("src.integrations.media.disc_parser.progress_display", lambda *_args, **_kwargs: Progress())
    asyncio.run(exercise())

    assert process.killed is True
    assert process.waited is True


def test_bdinfo_archive_without_binary_does_not_write_version_marker(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async def write_invalid_archive(_client, _url, destination, _asset_name):  # type: ignore[no-untyped-def]
        payload = b"not bdinfo"
        with tarfile.open(destination, "w:gz") as archive:
            member = tarfile.TarInfo("README.txt")
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))

    monkeypatch.setattr(get_bdinfo, "download_verified_asset", write_invalid_archive)
    monkeypatch.setattr(get_bdinfo.platform, "system", lambda: "Linux")
    monkeypatch.setattr(get_bdinfo.platform, "machine", lambda: "x86_64")

    with pytest.raises(RuntimeError, match="does not contain the expected bdinfo executable"):
        asyncio.run(BDInfoBinaryManager.ensure_bdinfo_binary(tmp_path))

    output = tmp_path / "bin" / "bdinfo" / "linux" / "amd64"
    assert not (output / "v0.3.1").exists()
    assert not (output / "bdinfo").exists()
