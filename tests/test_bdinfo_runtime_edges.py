from __future__ import annotations

import asyncio
import io
import tarfile
import zipfile
from pathlib import Path

import httpx
import pytest

from src.integrations.runtime_tools import bdinfo


class _Client:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


def _platform(monkeypatch: pytest.MonkeyPatch, system: str, machine: str) -> None:
    monkeypatch.setattr(bdinfo.platform, "system", lambda: system)
    monkeypatch.setattr(bdinfo.platform, "machine", lambda: machine)
    monkeypatch.setattr(bdinfo.httpx, "AsyncClient", _Client)


def _tar(path: Path, *members: tuple[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, payload in members:
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = 0o755
            archive.addfile(info, io.BytesIO(payload))


def _zip(path: Path, *members: tuple[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in members:
            archive.writestr(name, payload)


def test_bdinfo_unsupported_and_cached_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "bdinfo"
    target.mkdir()
    monkeypatch.setattr(bdinfo, "tool_install_dir", lambda *_args: target)
    _platform(monkeypatch, "FreeBSD", "x86_64")
    with pytest.raises(Exception, match="Unsupported platform"):
        asyncio.run(bdinfo.BDInfoBinaryManager.ensure_bdinfo_binary(tmp_path))

    _platform(monkeypatch, "Linux", "x86_64")
    binary = target / "bdinfo"
    binary.write_bytes(b"cached")
    binary.chmod(0o755)
    marker = target / "v0.3.1"
    marker.write_text("cached", encoding="utf-8")
    stale = target / "v0.2.0"
    stale.write_text("stale", encoding="utf-8")
    ignored = target / "directory"
    ignored.mkdir()
    plain = target / "plain.txt"
    plain.write_text("plain", encoding="utf-8")

    assert asyncio.run(bdinfo.BDInfoBinaryManager.ensure_bdinfo_binary(tmp_path)) == str(binary)
    assert not stale.exists()
    assert marker.exists() and binary.exists() and ignored.exists() and plain.exists()


def test_bdinfo_linux_tar_and_windows_zip_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    linux = tmp_path / "linux"
    linux.mkdir()
    monkeypatch.setattr(bdinfo, "tool_install_dir", lambda *_args: linux)
    _platform(monkeypatch, "Linux", "x86_64")

    async def linux_download(_client, _url: str, destination: Path, asset: str) -> None:
        assert asset.endswith("linux_amd64.tar.gz")
        _tar(destination, ("bundle/bdinfo", b"linux-binary"))

    monkeypatch.setattr(bdinfo, "download_verified_asset", linux_download)
    result = asyncio.run(bdinfo.BDInfoBinaryManager.ensure_bdinfo_binary(tmp_path))
    binary = Path(result)
    assert binary.read_bytes() == b"linux-binary"
    assert binary.stat().st_mode & 0o100
    assert (linux / "v0.3.1").is_file()
    assert not (linux / ".bdinfo-staging").exists()
    assert not any(path.name.startswith("temp_") for path in linux.iterdir())

    windows = tmp_path / "windows"
    windows.mkdir()
    monkeypatch.setattr(bdinfo, "tool_install_dir", lambda *_args: windows)
    _platform(monkeypatch, "Windows", "AMD64")

    async def windows_download(_client, _url: str, destination: Path, asset: str) -> None:
        assert asset.endswith("windows_amd64.zip")
        _zip(destination, ("bundle/bdinfo.exe", b"windows-binary"))

    monkeypatch.setattr(bdinfo, "download_verified_asset", windows_download)
    result = asyncio.run(bdinfo.BDInfoBinaryManager.ensure_bdinfo_binary(tmp_path))
    assert Path(result).name == "bdinfo.exe"
    assert Path(result).read_bytes() == b"windows-binary"


def test_bdinfo_missing_candidate_request_and_bad_archives(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "missing"
    target.mkdir()
    monkeypatch.setattr(bdinfo, "tool_install_dir", lambda *_args: target)
    _platform(monkeypatch, "Linux", "x86_64")

    async def missing(_client, _url: str, destination: Path, _asset: str) -> None:
        _tar(destination, ("bundle/other", b"other"))

    monkeypatch.setattr(bdinfo, "download_verified_asset", missing)
    with pytest.raises(RuntimeError, match="does not contain"):
        asyncio.run(bdinfo.BDInfoBinaryManager.ensure_bdinfo_binary(tmp_path))
    assert not (target / ".bdinfo-staging").exists()

    request = tmp_path / "request"
    request.mkdir()
    monkeypatch.setattr(bdinfo, "tool_install_dir", lambda *_args: request)

    async def request_error(*_args: object, **_kwargs: object) -> None:
        raise httpx.RequestError("offline")

    monkeypatch.setattr(bdinfo, "download_verified_asset", request_error)
    with pytest.raises(Exception, match="Failed to download"):
        asyncio.run(bdinfo.BDInfoBinaryManager.ensure_bdinfo_binary(tmp_path))

    invalid_zip = tmp_path / "invalid-zip"
    invalid_zip.mkdir()
    monkeypatch.setattr(bdinfo, "tool_install_dir", lambda *_args: invalid_zip)
    _platform(monkeypatch, "Windows", "x86_64")

    async def bad_zip(_client, _url: str, destination: Path, _asset: str) -> None:
        destination.write_bytes(b"not zip")

    monkeypatch.setattr(bdinfo, "download_verified_asset", bad_zip)
    with pytest.raises(Exception, match="Failed to extract"):
        asyncio.run(bdinfo.BDInfoBinaryManager.ensure_bdinfo_binary(tmp_path))

    invalid_tar = tmp_path / "invalid-tar"
    invalid_tar.mkdir()
    monkeypatch.setattr(bdinfo, "tool_install_dir", lambda *_args: invalid_tar)
    _platform(monkeypatch, "Linux", "arm64")

    async def bad_tar(_client, _url: str, destination: Path, _asset: str) -> None:
        destination.write_bytes(b"not tar")

    monkeypatch.setattr(bdinfo, "download_verified_asset", bad_tar)
    with pytest.raises(Exception, match="Failed to extract"):
        asyncio.run(bdinfo.BDInfoBinaryManager.ensure_bdinfo_binary(tmp_path))


def test_bdinfo_temp_archive_unlink_warning_is_nonfatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "unlink"
    target.mkdir()
    monkeypatch.setattr(bdinfo, "tool_install_dir", lambda *_args: target)
    _platform(monkeypatch, "Linux", "x86_64")

    async def download(_client, _url: str, destination: Path, _asset: str) -> None:
        _tar(destination, ("bdinfo", b"binary"))

    monkeypatch.setattr(bdinfo, "download_verified_asset", download)
    original_unlink = Path.unlink

    def fail_temp(path: Path, *args: object, **kwargs: object) -> None:
        if path.name.startswith("temp_bdinfo_"):
            raise OSError("cannot remove temp")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_temp)
    result = asyncio.run(bdinfo.BDInfoBinaryManager.ensure_bdinfo_binary(tmp_path))
    assert Path(result).read_bytes() == b"binary"
    assert any(path.name.startswith("temp_bdinfo_") for path in target.iterdir())
