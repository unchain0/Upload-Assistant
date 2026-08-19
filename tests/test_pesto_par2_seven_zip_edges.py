from __future__ import annotations

import asyncio
import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from src.integrations.runtime_tools import par2, pesto, seven_zip


class _Client:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


def _zip(path: Path, *members: tuple[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in members:
            archive.writestr(name, payload)


def _tar(path: Path, mode: str, *members: tuple[str, bytes]) -> None:
    with tarfile.open(path, mode) as archive:
        for name, payload in members:
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = 0o755
            archive.addfile(info, io.BytesIO(payload))


def _platform(monkeypatch: pytest.MonkeyPatch, module, system: str, machine: str) -> None:
    monkeypatch.setattr(module.platform, "system", lambda: system)
    monkeypatch.setattr(module.platform, "machine", lambda: machine)
    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)


def test_pesto_unsupported_cache_linux_windows_success_and_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "pesto"
    target.mkdir()
    monkeypatch.setattr(pesto, "tool_install_dir", lambda *_args: target)
    _platform(monkeypatch, pesto, "FreeBSD", "x86_64")
    with pytest.raises(Exception, match="Unsupported platform"):
        asyncio.run(pesto.PestoBinaryManager.ensure_pesto_binary(tmp_path))

    _platform(monkeypatch, pesto, "Linux", "x86_64")
    binary = target / "pesto"
    binary.write_bytes(b"cached")
    binary.chmod(0o755)
    marker = target / "pesto-v0.6.0"
    marker.write_text("cached", encoding="utf-8")
    assert asyncio.run(pesto.PestoBinaryManager.ensure_pesto_binary(tmp_path)) == str(binary)

    marker.unlink()
    binary.unlink()
    stale = target / "pesto-v0.5.0"
    stale.write_text("stale", encoding="utf-8")

    async def download(_client, _url: str, destination: Path, _asset: str) -> None:
        destination.write_bytes(b"new-pesto")

    monkeypatch.setattr(pesto, "download_verified_asset", download)
    result = asyncio.run(pesto.PestoBinaryManager.ensure_pesto_binary(tmp_path))
    assert result == str(binary)
    assert binary.read_bytes() == b"new-pesto" and binary.stat().st_mode & 0o100
    assert marker.is_file() and not stale.exists()
    assert not any(path.name.startswith("temp_") or path.name.startswith(".pesto") for path in target.iterdir())

    windows = tmp_path / "pesto-win"
    windows.mkdir()
    monkeypatch.setattr(pesto, "tool_install_dir", lambda *_args: windows)
    _platform(monkeypatch, pesto, "Windows", "AMD64")
    result = asyncio.run(pesto.PestoBinaryManager.ensure_pesto_binary(tmp_path))
    assert Path(result).name == "pesto.exe"

    async def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("download failed")

    failed = tmp_path / "pesto-failed"
    failed.mkdir()
    monkeypatch.setattr(pesto, "tool_install_dir", lambda *_args: failed)
    monkeypatch.setattr(pesto, "download_verified_asset", fail)
    with pytest.raises(Exception, match="Failed to setup Pesto"):
        asyncio.run(pesto.PestoBinaryManager.ensure_pesto_binary(tmp_path))
    assert list(failed.iterdir()) == []


def test_par2_unsupported_cache_zip_success_duplicate_and_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "par2"
    target.mkdir()
    monkeypatch.setattr(par2, "tool_install_dir", lambda *_args: target)
    _platform(monkeypatch, par2, "FreeBSD", "x86_64")
    with pytest.raises(Exception, match="Unsupported platform"):
        asyncio.run(par2.Par2BinaryManager.ensure_par2_binary(tmp_path))

    _platform(monkeypatch, par2, "Linux", "x86_64")
    binary = target / "par2"
    binary.write_bytes(b"cached")
    binary.chmod(0o755)
    marker = target / "v1.4.0"
    marker.write_text("cached", encoding="utf-8")
    assert asyncio.run(par2.Par2BinaryManager.ensure_par2_binary(tmp_path)) == str(binary)

    binary.unlink()
    marker.unlink()
    stale = target / "v1.3.0"
    stale.write_text("stale", encoding="utf-8")

    async def download(_client, _url: str, destination: Path, _asset: str) -> None:
        _zip(destination, ("bundle/par2", b"new-par2"))

    monkeypatch.setattr(par2, "download_verified_asset", download)
    result = asyncio.run(par2.Par2BinaryManager.ensure_par2_binary(tmp_path))
    assert result == str(binary) and binary.read_bytes() == b"new-par2"
    assert binary.stat().st_mode & 0o100 and marker.is_file() and not stale.exists()
    assert not (target / ".par2-staging").exists()

    duplicate = tmp_path / "par2-duplicate"
    duplicate.mkdir()
    monkeypatch.setattr(par2, "tool_install_dir", lambda *_args: duplicate)

    async def duplicate_download(_client, _url: str, destination: Path, _asset: str) -> None:
        _zip(destination, ("one/par2", b"one"), ("two/par2", b"two"))

    monkeypatch.setattr(par2, "download_verified_asset", duplicate_download)
    with pytest.raises(Exception, match="exactly one"):
        asyncio.run(par2.Par2BinaryManager.ensure_par2_binary(tmp_path))
    assert not (duplicate / ".par2-staging").exists()

    windows = tmp_path / "par2-win"
    windows.mkdir()
    monkeypatch.setattr(par2, "tool_install_dir", lambda *_args: windows)
    _platform(monkeypatch, par2, "Windows", "ARM64")

    async def windows_download(_client, _url: str, destination: Path, _asset: str) -> None:
        _zip(destination, ("par2.exe", b"windows"))

    monkeypatch.setattr(par2, "download_verified_asset", windows_download)
    assert Path(asyncio.run(par2.Par2BinaryManager.ensure_par2_binary(tmp_path))).name == "par2.exe"

    failed = tmp_path / "par2-failed"
    failed.mkdir()
    monkeypatch.setattr(par2, "tool_install_dir", lambda *_args: failed)

    async def fail_download(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("bad")

    monkeypatch.setattr(par2, "download_verified_asset", fail_download)
    with pytest.raises(Exception, match="Failed to setup PAR2"):
        asyncio.run(par2.Par2BinaryManager.ensure_par2_binary(tmp_path))


def test_seven_zip_unsupported_cache_windows_linux_duplicate_and_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "seven"
    target.mkdir()
    monkeypatch.setattr(seven_zip, "tool_install_dir", lambda *_args: target)
    _platform(monkeypatch, seven_zip, "FreeBSD", "x86_64")
    with pytest.raises(Exception, match="Unsupported platform"):
        asyncio.run(seven_zip.SevenZipBinaryManager.ensure_7z_binary(tmp_path))

    _platform(monkeypatch, seven_zip, "Linux", "x86_64")
    binary = target / "7zz"
    binary.write_bytes(b"cached")
    binary.chmod(0o755)
    marker = target / "26.01"
    marker.write_text("cached", encoding="utf-8")
    assert asyncio.run(seven_zip.SevenZipBinaryManager.ensure_7z_binary(tmp_path)) == str(binary)

    binary.unlink()
    marker.unlink()
    stale = target / "25.01"
    stale.write_text("stale", encoding="utf-8")

    async def linux_download(_client, _url: str, destination: Path, _asset: str) -> None:
        _tar(destination, "w:xz", ("bundle/7zz", b"new-7z"))

    monkeypatch.setattr(seven_zip, "download_verified_asset", linux_download)
    result = asyncio.run(seven_zip.SevenZipBinaryManager.ensure_7z_binary(tmp_path))
    assert result == str(binary) and binary.read_bytes() == b"new-7z"
    assert not stale.exists() and not (target / ".7z-staging").exists()

    windows = tmp_path / "seven-win"
    windows.mkdir()
    monkeypatch.setattr(seven_zip, "tool_install_dir", lambda *_args: windows)
    _platform(monkeypatch, seven_zip, "Windows", "x86")

    async def windows_download(_client, _url: str, destination: Path, asset: str) -> None:
        assert asset == "26.01/7zr.exe"
        destination.write_bytes(b"windows")

    monkeypatch.setattr(seven_zip, "download_verified_asset", windows_download)
    result = asyncio.run(seven_zip.SevenZipBinaryManager.ensure_7z_binary(tmp_path))
    assert Path(result).name == "7zr.exe" and Path(result).read_bytes() == b"windows"

    duplicate = tmp_path / "seven-duplicate"
    duplicate.mkdir()
    monkeypatch.setattr(seven_zip, "tool_install_dir", lambda *_args: duplicate)
    _platform(monkeypatch, seven_zip, "Linux", "arm64")

    async def duplicate_download(_client, _url: str, destination: Path, _asset: str) -> None:
        _tar(destination, "w:xz", ("one/7zz", b"one"), ("two/7zz", b"two"))

    monkeypatch.setattr(seven_zip, "download_verified_asset", duplicate_download)
    with pytest.raises(Exception, match="exactly one"):
        asyncio.run(seven_zip.SevenZipBinaryManager.ensure_7z_binary(tmp_path))

    failed = tmp_path / "seven-failed"
    failed.mkdir()
    monkeypatch.setattr(seven_zip, "tool_install_dir", lambda *_args: failed)

    async def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("download failed")

    monkeypatch.setattr(seven_zip, "download_verified_asset", fail)
    with pytest.raises(Exception, match="Failed to setup 7z"):
        asyncio.run(seven_zip.SevenZipBinaryManager.ensure_7z_binary(tmp_path))
