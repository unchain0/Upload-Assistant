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

    binary = asyncio.run(NyuuBinaryManager.ensure_nyuu_binary(tmp_path, path_7z="7zr.exe"))

    assert Path(binary).read_bytes() == b"executable"  # noqa: S101
    assert Path(binary).name == "nyuu.exe"  # noqa: S101
