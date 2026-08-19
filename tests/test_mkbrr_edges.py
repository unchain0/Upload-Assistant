from __future__ import annotations

import asyncio
import io
import tarfile
import zipfile
from pathlib import Path

import httpx
import pytest

from src.integrations.runtime_tools import mkbrr


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


def _tar(path: Path, *members: tuple[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, payload in members:
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = 0o755
            archive.addfile(info, io.BytesIO(payload))


def _platform(monkeypatch: pytest.MonkeyPatch, system: str, machine: str) -> None:
    monkeypatch.setattr(mkbrr.platform, "system", lambda: system)
    monkeypatch.setattr(mkbrr.platform, "machine", lambda: machine)
    monkeypatch.setattr(mkbrr.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(mkbrr.shutil, "which", lambda _name: None)


def test_find_existing_candidates_and_path_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _platform(monkeypatch, "Linux", "x86_64")
    monkeypatch.setattr(mkbrr, "trusted_executable", lambda path: path.name == "mkbrr" and path.parent == tmp_path / "bin")
    binary = tmp_path / "bin" / "mkbrr"
    binary.parent.mkdir()
    binary.write_bytes(b"tool")
    assert mkbrr.MkbrrBinaryManager.find_existing_binary(tmp_path) == str(binary)

    monkeypatch.setattr(mkbrr, "trusted_executable", lambda _path: False)
    monkeypatch.setattr(mkbrr.shutil, "which", lambda _name: "/usr/bin/mkbrr")
    assert mkbrr.MkbrrBinaryManager.find_existing_binary(tmp_path) == "/usr/bin/mkbrr"


def test_async_cache_tar_zip_success_stale_and_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "target"
    target.mkdir()
    monkeypatch.setattr(mkbrr, "tool_install_dir", lambda *_args: target)
    monkeypatch.setattr(mkbrr.MkbrrBinaryManager, "find_existing_binary", staticmethod(lambda *_args: None))
    _platform(monkeypatch, "Linux", "x86_64")

    binary = target / "mkbrr"
    binary.write_bytes(b"cached")
    binary.chmod(0o755)
    marker = target / "v1.24.0"
    marker.write_text("cached", encoding="utf-8")
    assert asyncio.run(mkbrr.MkbrrBinaryManager.ensure_mkbrr_binary(tmp_path, "v1.24.0")) == str(binary)

    binary.unlink()
    marker.unlink()
    stale = target / "v1.23.0"
    stale.write_text("stale", encoding="utf-8")

    async def tar_download(_client, _url: str, destination: Path, asset: str) -> None:
        assert asset == "mkbrr_1.24.0_linux_x86_64.tar.gz"
        _tar(destination, ("bundle/mkbrr", b"linux"))

    monkeypatch.setattr(mkbrr, "download_verified_asset", tar_download)
    result = asyncio.run(mkbrr.MkbrrBinaryManager.ensure_mkbrr_binary(tmp_path, "v1.24.0"))
    assert result == str(binary) and binary.read_bytes() == b"linux"
    assert marker.is_file() and not stale.exists() and not (target / ".mkbrr-staging").exists()

    windows = tmp_path / "windows"
    windows.mkdir()
    monkeypatch.setattr(mkbrr, "tool_install_dir", lambda *_args: windows)
    _platform(monkeypatch, "Windows", "AMD64")

    async def zip_download(_client, _url: str, destination: Path, _asset: str) -> None:
        _zip(destination, ("bundle/mkbrr.exe", b"windows"))

    monkeypatch.setattr(mkbrr, "download_verified_asset", zip_download)
    result = asyncio.run(mkbrr.MkbrrBinaryManager.ensure_mkbrr_binary(tmp_path, "v1.24.0"))
    assert Path(result).name == "mkbrr.exe" and Path(result).read_bytes() == b"windows"

    missing = tmp_path / "missing"
    missing.mkdir()
    monkeypatch.setattr(mkbrr, "tool_install_dir", lambda *_args: missing)
    _platform(monkeypatch, "Linux", "arm64")

    async def missing_download(_client, _url: str, destination: Path, _asset: str) -> None:
        _tar(destination, ("README", b"readme"))

    monkeypatch.setattr(mkbrr, "download_verified_asset", missing_download)
    with pytest.raises(Exception, match="Failed to extract mkbrr"):
        asyncio.run(mkbrr.MkbrrBinaryManager.ensure_mkbrr_binary(tmp_path, "v1.24.0"))

    request = tmp_path / "request"
    request.mkdir()
    monkeypatch.setattr(mkbrr, "tool_install_dir", lambda *_args: request)

    async def request_error(*_args: object, **_kwargs: object) -> None:
        raise httpx.RequestError("offline")

    monkeypatch.setattr(mkbrr, "download_verified_asset", request_error)
    with pytest.raises(Exception, match="Failed to download"):
        asyncio.run(mkbrr.MkbrrBinaryManager.ensure_mkbrr_binary(tmp_path, "v1.24.0"))

    bad = tmp_path / "bad"
    bad.mkdir()
    monkeypatch.setattr(mkbrr, "tool_install_dir", lambda *_args: bad)
    _platform(monkeypatch, "Windows", "x86_64")

    async def bad_zip(_client, _url: str, destination: Path, _asset: str) -> None:
        destination.write_bytes(b"bad zip")

    monkeypatch.setattr(mkbrr, "download_verified_asset", bad_zip)
    with pytest.raises(Exception, match="Failed to extract"):
        asyncio.run(mkbrr.MkbrrBinaryManager.ensure_mkbrr_binary(tmp_path, "v1.24.0"))


def test_async_existing_short_circuit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mkbrr.MkbrrBinaryManager, "find_existing_binary", staticmethod(lambda *_args: "/existing/mkbrr"))
    assert asyncio.run(mkbrr.MkbrrBinaryManager.ensure_mkbrr_binary(tmp_path, "v1.24.0")) == "/existing/mkbrr"


def test_docker_platform_cache_success_duplicate_and_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _platform(monkeypatch, "Darwin", "x86_64")
    with pytest.raises(Exception, match="Docker/Linux only"):
        mkbrr.MkbrrBinaryManager.download_mkbrr_for_docker(tmp_path)

    _platform(monkeypatch, "Linux", "riscv64")
    with pytest.raises(Exception, match="Unsupported architecture"):
        mkbrr.MkbrrBinaryManager.download_mkbrr_for_docker(tmp_path)

    _platform(monkeypatch, "Linux", "x86_64")
    binary = tmp_path / "bin" / "mkbrr" / "linux" / "amd64" / "mkbrr"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"cached")
    binary.chmod(0o700)
    marker = binary.parent / "v1.18.0"
    marker.write_text("cached", encoding="utf-8")
    assert mkbrr.MkbrrBinaryManager.download_mkbrr_for_docker(tmp_path) == str(binary)

    binary.unlink()
    marker.unlink()
    stale = binary.parent / "v1.17.0"
    stale.write_text("stale", encoding="utf-8")

    def download(_url: str, destination: Path, asset: str) -> None:
        assert asset == "mkbrr_1.18.0_linux_x86_64.tar.gz"
        _tar(destination, ("bundle/mkbrr", b"docker"))

    monkeypatch.setattr(mkbrr, "download_verified_asset_sync", download)
    result = mkbrr.MkbrrBinaryManager.download_mkbrr_for_docker(tmp_path)
    assert Path(result).read_bytes() == b"docker"
    assert Path(result).stat().st_mode & 0o777 == 0o700
    assert not stale.exists()

    duplicate = tmp_path / "duplicate"

    def duplicate_download(_url: str, destination: Path, _asset: str) -> None:
        _tar(destination, ("one/mkbrr", b"one"), ("two/mkbrr", b"two"))

    monkeypatch.setattr(mkbrr, "download_verified_asset_sync", duplicate_download)
    with pytest.raises(Exception, match="exactly one"):
        mkbrr.MkbrrBinaryManager.download_mkbrr_for_docker(duplicate)

    failed = tmp_path / "failed"
    monkeypatch.setattr(mkbrr, "download_verified_asset_sync", lambda *_args: (_ for _ in ()).throw(RuntimeError("bad")))
    with pytest.raises(Exception, match="Error downloading mkbrr"):
        mkbrr.MkbrrBinaryManager.download_mkbrr_for_docker(failed)
