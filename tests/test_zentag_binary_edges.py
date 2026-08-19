from __future__ import annotations

import asyncio
import hashlib
import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from src.integrations.runtime_tools import zentag_binary as zentag


class _Client:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


def _zip_bytes(name: str, payload: bytes) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(name, payload)
    return output.getvalue()


def _tar_bytes(name: str, payload: bytes) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        info = tarfile.TarInfo(name)
        info.size = len(payload)
        info.mode = 0o755
        archive.addfile(info, io.BytesIO(payload))
    return output.getvalue()


def _platform(monkeypatch: pytest.MonkeyPatch, system: str, machine: str, target: Path) -> None:
    monkeypatch.setattr(zentag.platform, "system", lambda: system)
    monkeypatch.setattr(zentag.platform, "machine", lambda: machine)
    monkeypatch.setattr(zentag, "tool_install_dir", lambda *_args: target)
    monkeypatch.setattr(zentag.HTTPX, "AsyncClient", _Client)


def _configure_asset(monkeypatch: pytest.MonkeyPatch, asset: str, archive: bytes, binary: bytes) -> None:
    monkeypatch.setitem(zentag.ZentagBinaryManager.CHECKSUMS, asset, hashlib.sha256(archive).hexdigest())
    monkeypatch.setitem(zentag.ZentagBinaryManager.BINARY_CHECKSUMS, asset, hashlib.sha256(binary).hexdigest())


def test_unsupported_and_cached_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "target"
    target.mkdir()
    _platform(monkeypatch, "FreeBSD", "riscv64", target)
    with pytest.raises(RuntimeError, match="Unsupported zentag"):
        asyncio.run(zentag.ZentagBinaryManager.ensure_binary(tmp_path))

    _platform(monkeypatch, "Linux", "x86_64", target)
    asset = "zentag_0.3.0_linux_amd64.tar.gz"
    binary = target / "zentag"
    binary.write_bytes(b"cached")
    marker = target / "v0.3.0"
    marker.write_text("v0.3.0", encoding="utf-8")
    monkeypatch.setitem(zentag.ZentagBinaryManager.BINARY_CHECKSUMS, asset, hashlib.sha256(b"cached").hexdigest())
    assert asyncio.run(zentag.ZentagBinaryManager.ensure_binary(tmp_path)) == str(binary)


def test_windows_zip_and_linux_tar_install_with_stale_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    windows = tmp_path / "windows"
    windows.mkdir()
    _platform(monkeypatch, "Windows", "AMD64", windows)
    payload = b"windows-binary"
    archive = _zip_bytes("bundle/zentag.exe", payload)
    asset = "zentag_0.3.0_windows_amd64.zip"
    _configure_asset(monkeypatch, asset, archive, payload)

    async def download(_client, _url: str, destination: Path) -> None:
        destination.write_bytes(archive)

    monkeypatch.setattr(zentag, "download_bounded_asset", download)
    result = asyncio.run(zentag.ZentagBinaryManager.ensure_binary(tmp_path))
    assert Path(result).name == "zentag.exe" and Path(result).read_bytes() == payload
    assert (windows / "v0.3.0").read_text(encoding="utf-8") == "v0.3.0"
    assert not any(path.name.endswith(".download") for path in windows.iterdir())

    linux = tmp_path / "linux"
    linux.mkdir()
    stale = linux / "v0.2.0"
    stale.write_text("stale", encoding="utf-8")
    _platform(monkeypatch, "Linux", "arm64", linux)
    payload = b"linux-binary"
    archive = _tar_bytes("bundle/zentag", payload)
    asset = "zentag_0.3.0_linux_arm64.tar.gz"
    _configure_asset(monkeypatch, asset, archive, payload)

    async def linux_download(_client, _url: str, destination: Path) -> None:
        destination.write_bytes(archive)

    monkeypatch.setattr(zentag, "download_bounded_asset", linux_download)
    result = asyncio.run(zentag.ZentagBinaryManager.ensure_binary(tmp_path))
    assert Path(result).read_bytes() == payload and Path(result).stat().st_mode & 0o100
    assert not stale.exists()


def test_archive_checksum_missing_member_and_size_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "target"
    target.mkdir()
    _platform(monkeypatch, "Windows", "x86_64", target)
    asset = "zentag_0.3.0_windows_amd64.zip"

    archive = _zip_bytes("zentag.exe", b"payload")

    async def download(_client, _url: str, destination: Path) -> None:
        destination.write_bytes(archive)

    monkeypatch.setattr(zentag, "download_bounded_asset", download)
    monkeypatch.setitem(zentag.ZentagBinaryManager.CHECKSUMS, asset, "")
    with pytest.raises(RuntimeError, match="checksum verification failed"):
        asyncio.run(zentag.ZentagBinaryManager.ensure_binary(tmp_path))
    assert not any(path.name.endswith(".download") for path in target.iterdir())

    missing_archive = _zip_bytes("README", b"readme")
    _configure_asset(monkeypatch, asset, missing_archive, b"payload")

    async def missing(_client, _url: str, destination: Path) -> None:
        destination.write_bytes(missing_archive)

    monkeypatch.setattr(zentag, "download_bounded_asset", missing)
    with pytest.raises(RuntimeError, match="binary not found"):
        asyncio.run(zentag.ZentagBinaryManager.ensure_binary(tmp_path))

    large_archive = _zip_bytes("zentag.exe", b"large")
    _configure_asset(monkeypatch, asset, large_archive, b"large")
    monkeypatch.setattr(zentag, "MAX_ASSET_BYTES", 4)

    async def large(_client, _url: str, destination: Path) -> None:
        destination.write_bytes(large_archive)

    monkeypatch.setattr(zentag, "download_bounded_asset", large)
    with pytest.raises(RuntimeError, match="exceeds the 4-byte"):
        asyncio.run(zentag.ZentagBinaryManager.ensure_binary(tmp_path))


def test_tar_missing_member_declared_and_stream_size_and_binary_checksum(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "target"
    target.mkdir()
    _platform(monkeypatch, "Linux", "x86_64", target)
    asset = "zentag_0.3.0_linux_amd64.tar.gz"

    missing_archive = _tar_bytes("README", b"readme")
    _configure_asset(monkeypatch, asset, missing_archive, b"payload")

    async def missing(_client, _url: str, destination: Path) -> None:
        destination.write_bytes(missing_archive)

    monkeypatch.setattr(zentag, "download_bounded_asset", missing)
    with pytest.raises(RuntimeError, match="binary not found"):
        asyncio.run(zentag.ZentagBinaryManager.ensure_binary(tmp_path))

    payload = b"large"
    large_archive = _tar_bytes("zentag", payload)
    _configure_asset(monkeypatch, asset, large_archive, payload)
    monkeypatch.setattr(zentag, "MAX_ASSET_BYTES", 4)

    async def large(_client, _url: str, destination: Path) -> None:
        destination.write_bytes(large_archive)

    monkeypatch.setattr(zentag, "download_bounded_asset", large)
    with pytest.raises(RuntimeError, match="exceeds the 4-byte"):
        asyncio.run(zentag.ZentagBinaryManager.ensure_binary(tmp_path))

    monkeypatch.setattr(zentag, "MAX_ASSET_BYTES", 1024)
    _configure_asset(monkeypatch, asset, large_archive, b"different")
    with pytest.raises(RuntimeError, match="binary checksum verification failed"):
        asyncio.run(zentag.ZentagBinaryManager.ensure_binary(tmp_path))


def test_tar_stream_can_exceed_declared_member_size(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    target = tmp_path / "target"
    target.mkdir()
    _platform(monkeypatch, "Linux", "x86_64", target)
    asset = "zentag_0.3.0_linux_amd64.tar.gz"
    archive_payload = b"archive"
    monkeypatch.setitem(zentag.ZentagBinaryManager.CHECKSUMS, asset, hashlib.sha256(archive_payload).hexdigest())
    monkeypatch.setitem(zentag.ZentagBinaryManager.BINARY_CHECKSUMS, asset, hashlib.sha256(b"large").hexdigest())
    monkeypatch.setattr(zentag, "MAX_ASSET_BYTES", 4)

    async def download(_client, _url: str, destination: Path) -> None:
        destination.write_bytes(archive_payload)

    class Archive:
        member = SimpleNamespace(name="zentag", size=4, isfile=lambda: True)

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def getmembers(self):
            return [self.member]

        def extractfile(self, _member):
            return io.BytesIO(b"large")

    monkeypatch.setattr(zentag, "download_bounded_asset", download)
    monkeypatch.setattr(zentag.tarfile, "open", lambda *_args, **_kwargs: Archive())
    with pytest.raises(RuntimeError, match="exceeds the 4-byte"):
        asyncio.run(zentag.ZentagBinaryManager.ensure_binary(tmp_path))
