import asyncio
import io
import tarfile
from pathlib import Path

import pytest

from bin import get_nyuu
from bin.get_nyuu import NyuuBinaryManager


def test_nyuu_archive_without_binary_does_not_write_version_marker(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async def write_invalid_archive(_client, _url, destination, _asset_name):  # type: ignore[no-untyped-def]
        payload = b"not nyuu"
        with tarfile.open(destination, "w:xz") as archive:
            member = tarfile.TarInfo("README.txt")
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))

    monkeypatch.setattr(get_nyuu, "download_verified_asset", write_invalid_archive)
    monkeypatch.setattr(get_nyuu.platform, "system", lambda: "Linux")
    monkeypatch.setattr(get_nyuu.platform, "machine", lambda: "x86_64")

    with pytest.raises(Exception, match="does not contain the expected nyuu executable"):
        asyncio.run(NyuuBinaryManager.ensure_nyuu_binary(tmp_path))

    output = tmp_path / "bin" / "nyuu" / "linux" / "amd64"
    assert not (output / "v0.4.2").exists()  # noqa: S101
    assert not (output / "nyuu").exists()  # noqa: S101


def test_nyuu_failed_update_preserves_existing_installation(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async def fail_download(*_args, **_kwargs) -> None:  # type: ignore[no-untyped-def]
        raise RuntimeError("download failed")

    output = tmp_path / "bin" / "nyuu" / "linux" / "amd64"
    output.mkdir(parents=True)
    binary = output / "nyuu"
    binary.write_bytes(b"working binary")
    binary.chmod(0o700)
    marker = output / "v0.4.2"
    marker.write_text("stale marker")
    monkeypatch.setattr(get_nyuu, "download_verified_asset", fail_download)
    monkeypatch.setattr(get_nyuu.platform, "system", lambda: "Linux")
    monkeypatch.setattr(get_nyuu.platform, "machine", lambda: "x86_64")

    with pytest.raises(Exception, match="download failed"):
        asyncio.run(NyuuBinaryManager.ensure_nyuu_binary(tmp_path, version="v0.4.3"))

    assert binary.read_bytes() == b"working binary"  # noqa: S101
    assert marker.read_text() == "stale marker"  # noqa: S101


def test_windows_nyuu_extracts_only_expected_executable(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async def write_archive(_client, _url, destination, _asset_name):  # type: ignore[no-untyped-def]
        destination.write_bytes(b"verified archive")

    class Process:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b""

    async def create_process(*command, **_kwargs):  # type: ignore[no-untyped-def]
        output_arg = next(arg for arg in command if str(arg).startswith("-o"))
        staging = Path(str(output_arg)[2:])
        (staging / "nyuu.exe").write_bytes(b"executable")
        return Process()

    monkeypatch.setattr(get_nyuu, "download_verified_asset", write_archive)
    monkeypatch.setattr(get_nyuu.platform, "system", lambda: "Windows")
    monkeypatch.setattr(get_nyuu.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    stale_marker = tmp_path / "bin" / "nyuu" / "windows" / "x86_64" / "v0.4.1"
    stale_marker.parent.mkdir(parents=True)
    stale_marker.write_text("stale")

    binary = asyncio.run(NyuuBinaryManager.ensure_nyuu_binary(tmp_path, path_7z="7zr.exe"))

    assert Path(binary).read_bytes() == b"executable"  # noqa: S101
    assert Path(binary).name == "nyuu.exe"  # noqa: S101
    assert not stale_marker.exists()  # noqa: S101


def test_windows_nyuu_cancellation_kills_and_reaps_7z(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async def write_archive(_client, _url, destination, _asset_name):  # type: ignore[no-untyped-def]
        destination.write_bytes(b"verified archive")

    class Process:
        returncode = None
        pid = 123

        def __init__(self) -> None:
            self.calls = 0
            self.killed = False

        async def communicate(self) -> tuple[bytes, bytes]:
            self.calls += 1
            if self.calls == 1:
                await asyncio.sleep(60)
            return b"", b""

        def kill(self) -> None:
            self.killed = True

    process = Process()
    taskkill_command: tuple[object, ...] | None = None

    class TreeKiller:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b""

    async def create_process(*args, **_kwargs):  # type: ignore[no-untyped-def]
        nonlocal taskkill_command
        if args[0] == "taskkill":
            taskkill_command = args
            return TreeKiller()
        return process

    async def exercise() -> None:
        task = asyncio.create_task(NyuuBinaryManager.ensure_nyuu_binary(tmp_path, path_7z="7zr.exe"))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    monkeypatch.setattr(get_nyuu, "download_verified_asset", write_archive)
    monkeypatch.setattr(get_nyuu.platform, "system", lambda: "Windows")
    monkeypatch.setattr(get_nyuu.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    asyncio.run(exercise())

    assert process.killed is True  # noqa: S101
    assert process.calls == 2  # noqa: S101
    assert taskkill_command == ("taskkill", "/F", "/T", "/PID", "123")  # noqa: S101
