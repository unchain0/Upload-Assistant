from __future__ import annotations

import asyncio
import hashlib
import io
import runpy
import stat
import tarfile
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Self

import pytest

from src.integrations.runtime_tools import dvd_media_info, ffmpeg_docker, pesto, zentag_binary


class _AsyncClient:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


def test_ffmpeg_docker_platform_missing_success_and_main(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(ffmpeg_docker.platform, "system", lambda: "Darwin")
    with pytest.raises(Exception, match="Docker/Linux only"):
        ffmpeg_docker.FfmpegBinaryManager.download_ffmpeg_for_docker()

    monkeypatch.setattr(ffmpeg_docker.platform, "system", lambda: "Linux")
    monkeypatch.setattr(ffmpeg_docker.shutil, "which", lambda _name: None)
    with pytest.raises(RuntimeError, match="must be installed"):
        ffmpeg_docker.FfmpegBinaryManager.download_ffmpeg_for_docker()

    monkeypatch.setattr(ffmpeg_docker.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    assert ffmpeg_docker.FfmpegBinaryManager.download_ffmpeg_for_docker("ignored") == "/usr/bin/ffmpeg"

    runpy.run_module("src.integrations.runtime_tools.ffmpeg_docker", run_name="__main__")
    assert "/usr/bin/ffmpeg" in capsys.readouterr().out


def _pesto_paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "pesto"
    root.mkdir(parents=True, exist_ok=True)
    return root, root / "pesto", root / "pesto-v0.6.0"


def test_pesto_unsupported_cached_linux_windows_success_and_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pesto, "tool_install_dir", lambda *_args: _pesto_paths(tmp_path)[0])
    monkeypatch.setattr(pesto.platform, "system", lambda: "Plan9")
    monkeypatch.setattr(pesto.platform, "machine", lambda: "mips")
    with pytest.raises(Exception, match="Unsupported platform"):
        asyncio.run(pesto.PestoBinaryManager.ensure_pesto_binary(tmp_path))

    root, binary, marker = _pesto_paths(tmp_path)
    binary.write_bytes(b"pesto")
    binary.chmod(0o700)
    marker.write_text("ok", encoding="utf-8")
    monkeypatch.setattr(pesto.platform, "system", lambda: "Linux")
    monkeypatch.setattr(pesto.platform, "machine", lambda: "x86_64")
    assert asyncio.run(pesto.PestoBinaryManager.ensure_pesto_binary(tmp_path)) == str(binary)

    binary.unlink()
    marker.unlink()
    stale = root / "pesto-v0.5.0"
    stale.write_text("old", encoding="utf-8")
    monkeypatch.setattr(pesto.httpx, "AsyncClient", _AsyncClient)

    async def download(_client: object, _url: str, target: Path, _label: str) -> None:
        target.write_bytes(b"new pesto")

    def promote(pairs, _backup, *, remove_targets=()):
        for source, target in pairs:
            source.replace(target)
        for target in remove_targets:
            target.unlink(missing_ok=True)

    monkeypatch.setattr(pesto, "download_verified_asset", download)
    monkeypatch.setattr(pesto, "promote_files_with_rollback", promote)
    assert asyncio.run(pesto.PestoBinaryManager.ensure_pesto_binary(tmp_path)) == str(binary)
    assert binary.is_file() and bool(binary.stat().st_mode & stat.S_IEXEC)
    assert marker.is_file() and not stale.exists()

    windows_root = tmp_path / "windows-pesto"
    windows_root.mkdir()
    monkeypatch.setattr(pesto, "tool_install_dir", lambda *_args: windows_root)
    monkeypatch.setattr(pesto.platform, "system", lambda: "Windows")
    monkeypatch.setattr(pesto.platform, "machine", lambda: "AMD64")
    assert asyncio.run(pesto.PestoBinaryManager.ensure_pesto_binary(tmp_path)) == str(windows_root / "pesto.exe")

    async def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("download failed")

    failure_root = tmp_path / "failure-pesto"
    failure_root.mkdir()
    monkeypatch.setattr(pesto, "tool_install_dir", lambda *_args: failure_root)
    monkeypatch.setattr(pesto.platform, "system", lambda: "Linux")
    monkeypatch.setattr(pesto.platform, "machine", lambda: "amd64")
    monkeypatch.setattr(pesto, "download_verified_asset", fail)
    with pytest.raises(Exception, match="Failed to setup Pesto"):
        asyncio.run(pesto.PestoBinaryManager.ensure_pesto_binary(tmp_path))
    assert list(failure_root.glob(".*staged")) == []


class _ZentagClient(_AsyncClient):
    pass


def _promote(pairs, _backup, *, remove_targets=()):
    for source, target in pairs:
        source.replace(target)
    for target in remove_targets:
        target.unlink(missing_ok=True)


def _zip_payload(path: Path, name: str, payload: bytes) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"folder/{name}", payload)


def _tar_payload(path: Path, name: str, payload: bytes) -> None:
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo(f"folder/{name}")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))


def test_zentag_unsupported_and_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(zentag_binary.platform, "system", lambda: "Plan9")
    monkeypatch.setattr(zentag_binary.platform, "machine", lambda: "mips")
    with pytest.raises(RuntimeError, match="Unsupported zentag"):
        asyncio.run(zentag_binary.ZentagBinaryManager.ensure_binary(tmp_path))

    root = tmp_path / "zentag-cache"
    root.mkdir()
    binary = root / "zentag"
    payload = b"cached zentag"
    binary.write_bytes(payload)
    marker = root / zentag_binary.ZentagBinaryManager.VERSION
    marker.write_text(zentag_binary.ZentagBinaryManager.VERSION, encoding="utf-8")
    asset = "zentag_0.3.0_linux_amd64.tar.gz"
    monkeypatch.setattr(zentag_binary, "tool_install_dir", lambda *_args: root)
    monkeypatch.setattr(zentag_binary.platform, "system", lambda: "Linux")
    monkeypatch.setattr(zentag_binary.platform, "machine", lambda: "x86_64")
    monkeypatch.setitem(zentag_binary.ZentagBinaryManager.BINARY_CHECKSUMS, asset, hashlib.sha256(payload).hexdigest())
    assert asyncio.run(zentag_binary.ZentagBinaryManager.ensure_binary(tmp_path)) == str(binary)


def test_zentag_zip_and_tar_install_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(zentag_binary.HTTPX, "AsyncClient", _ZentagClient)
    monkeypatch.setattr(zentag_binary, "promote_files_with_rollback", _promote)

    async def install(system: str, machine: str, root: Path, binary_name: str, archive_writer) -> str:
        root.mkdir()
        monkeypatch.setattr(zentag_binary, "tool_install_dir", lambda *_args: root)
        monkeypatch.setattr(zentag_binary.platform, "system", lambda: system)
        monkeypatch.setattr(zentag_binary.platform, "machine", lambda: machine)
        payload = f"zentag-{system}".encode()
        version = zentag_binary.ZentagBinaryManager.VERSION.lstrip("v")
        os_name = system.lower()
        extension = "zip" if os_name == "windows" else "tar.gz"
        asset = f"zentag_{version}_{os_name}_amd64.{extension}"
        archive_source = tmp_path / f"source-{asset}"
        archive_writer(archive_source, binary_name, payload)
        archive_hash = hashlib.sha256(archive_source.read_bytes()).hexdigest()
        monkeypatch.setitem(zentag_binary.ZentagBinaryManager.CHECKSUMS, asset, archive_hash)
        monkeypatch.setitem(zentag_binary.ZentagBinaryManager.BINARY_CHECKSUMS, asset, hashlib.sha256(payload).hexdigest())

        async def download(_client: object, _url: str, target: Path) -> None:
            target.write_bytes(archive_source.read_bytes())

        monkeypatch.setattr(zentag_binary, "download_bounded_asset", download)
        result = await zentag_binary.ZentagBinaryManager.ensure_binary(tmp_path)
        assert (root / zentag_binary.ZentagBinaryManager.VERSION).is_file()
        return result

    windows = tmp_path / "zentag-windows"
    assert asyncio.run(install("Windows", "AMD64", windows, "zentag.exe", _zip_payload)) == str(windows / "zentag.exe")

    linux = tmp_path / "zentag-linux"
    stale = linux / "v0.2.0"
    linux.mkdir()
    stale.write_text("old", encoding="utf-8")
    linux.rmdir() if False else None
    # install() creates the directory, so remove it after staging the stale name separately.
    stale.unlink()
    linux.rmdir()
    result = asyncio.run(install("Linux", "x86_64", linux, "zentag", _tar_payload))
    assert result == str(linux / "zentag")
    assert bool((linux / "zentag").stat().st_mode & stat.S_IEXEC)


def test_zentag_archive_checksum_binary_checksum_missing_and_size_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "zentag-errors"
    root.mkdir()
    monkeypatch.setattr(zentag_binary, "tool_install_dir", lambda *_args: root)
    monkeypatch.setattr(zentag_binary.platform, "system", lambda: "Windows")
    monkeypatch.setattr(zentag_binary.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(zentag_binary.HTTPX, "AsyncClient", _ZentagClient)
    asset = "zentag_0.3.0_windows_amd64.zip"

    async def write_bytes(payload: bytes, _name: str = "zentag.exe"):
        source = tmp_path / "zentag-error-source.zip"
        _zip_payload(source, _name, payload)

        async def download(_client: object, _url: str, target: Path) -> None:
            target.write_bytes(source.read_bytes())

        monkeypatch.setattr(zentag_binary, "download_bounded_asset", download)
        monkeypatch.setitem(zentag_binary.ZentagBinaryManager.CHECKSUMS, asset, hashlib.sha256(source.read_bytes()).hexdigest())
        return payload

    awaitable_payload = asyncio.run(write_bytes(b"payload"))
    monkeypatch.setitem(zentag_binary.ZentagBinaryManager.CHECKSUMS, asset, "bad")
    with pytest.raises(RuntimeError, match="checksum verification failed for"):
        asyncio.run(zentag_binary.ZentagBinaryManager.ensure_binary(tmp_path))

    asyncio.run(write_bytes(awaitable_payload))
    monkeypatch.setitem(zentag_binary.ZentagBinaryManager.BINARY_CHECKSUMS, asset, "bad")
    with pytest.raises(RuntimeError, match="binary checksum verification failed"):
        asyncio.run(zentag_binary.ZentagBinaryManager.ensure_binary(tmp_path))

    asyncio.run(write_bytes(b"payload", "other.exe"))
    with pytest.raises(RuntimeError, match="binary not found"):
        asyncio.run(zentag_binary.ZentagBinaryManager.ensure_binary(tmp_path))

    asyncio.run(write_bytes(b"0123456789"))
    monkeypatch.setattr(zentag_binary, "MAX_ASSET_BYTES", 5)
    with pytest.raises(RuntimeError, match="exceeds"):
        asyncio.run(zentag_binary.ZentagBinaryManager.ensure_binary(tmp_path))


def test_zentag_tar_missing_member_size_and_stream_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "zentag-tar-errors"
    root.mkdir()
    monkeypatch.setattr(zentag_binary, "tool_install_dir", lambda *_args: root)
    monkeypatch.setattr(zentag_binary.platform, "system", lambda: "Linux")
    monkeypatch.setattr(zentag_binary.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(zentag_binary.HTTPX, "AsyncClient", _ZentagClient)
    asset = "zentag_0.3.0_linux_amd64.tar.gz"

    async def set_archive(name: str, payload: bytes) -> None:
        source = tmp_path / "zentag-error-source.tar.gz"
        _tar_payload(source, name, payload)

        async def download(_client: object, _url: str, target: Path) -> None:
            target.write_bytes(source.read_bytes())

        monkeypatch.setattr(zentag_binary, "download_bounded_asset", download)
        monkeypatch.setitem(zentag_binary.ZentagBinaryManager.CHECKSUMS, asset, hashlib.sha256(source.read_bytes()).hexdigest())

    asyncio.run(set_archive("other", b"payload"))
    with pytest.raises(RuntimeError, match="binary not found"):
        asyncio.run(zentag_binary.ZentagBinaryManager.ensure_binary(tmp_path))

    asyncio.run(set_archive("zentag", b"0123456789"))
    monkeypatch.setattr(zentag_binary, "MAX_ASSET_BYTES", 5)
    with pytest.raises(RuntimeError, match="exceeds"):
        asyncio.run(zentag_binary.ZentagBinaryManager.ensure_binary(tmp_path))


def test_dvd_filename_url_download_extract_errors_and_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert dvd_media_info.get_filename("windows", "x86_64") == "MediaInfo_CLI_23.04_Windows_x64.zip"
    assert dvd_media_info.get_filename("windows", "arm64") == ""
    assert "Lambda_x86_64" in dvd_media_info.get_filename("linux", "x86_64", "cli")
    assert "MediaInfo_DLL" in dvd_media_info.get_filename("linux", "arm64", "lib")
    assert dvd_media_info.get_filename("darwin", "x86_64") == ""
    with pytest.raises(ValueError, match="Unknown library_type"):
        dvd_media_info.get_filename("linux", "x86_64", "bad")
    assert "mediainfo" in dvd_media_info.get_url("linux", "x86_64", "cli")
    assert "libmediainfo0" in dvd_media_info.get_url("linux", "x86_64", "lib")
    with pytest.raises(ValueError):
        dvd_media_info.get_url("linux", "x86_64", "bad")

    called: list[tuple[str, Path, str]] = []
    monkeypatch.setattr(dvd_media_info, "download_verified_asset_sync", lambda url, output, label: called.append((url, output, label)))
    target = tmp_path / "asset.zip"
    dvd_media_info.download_file("https://example.invalid/asset", target)
    assert called == [("https://example.invalid/asset", target, "asset.zip")]

    cli = tmp_path / "cli.zip"
    lib = tmp_path / "lib.zip"
    with zipfile.ZipFile(cli, "w") as archive:
        archive.writestr("folder/mediainfo", b"cli")
    with zipfile.ZipFile(lib, "w") as archive:
        archive.writestr("lib/libmediainfo.so.0.0.0", b"lib")
    output = tmp_path / "linux-out"
    output.mkdir()
    dvd_media_info.extract_linux(cli, lib, output)
    assert (output / "mediainfo").read_bytes() == b"cli"
    assert (output / "libmediainfo.so.0").read_bytes() == b"lib"

    bad_cli = tmp_path / "bad-cli.zip"
    with zipfile.ZipFile(bad_cli, "w") as archive:
        archive.writestr("one", b"x")
    with pytest.raises(RuntimeError, match="exactly one CLI"):
        dvd_media_info.extract_linux(bad_cli, lib, output)

    duplicate_cli = tmp_path / "duplicate-cli.zip"
    with zipfile.ZipFile(duplicate_cli, "w") as archive:
        archive.writestr("one/mediainfo", b"one")
        archive.writestr("two/mediainfo", b"two")
    with pytest.raises(RuntimeError, match="exactly one CLI"):
        dvd_media_info.extract_linux(duplicate_cli, lib, output)

    bad_lib = tmp_path / "bad-lib.zip"
    with zipfile.ZipFile(bad_lib, "w") as archive:
        archive.writestr("other", b"x")
    with pytest.raises(RuntimeError, match="required library"):
        dvd_media_info.extract_linux(cli, bad_lib, output)

    windows = tmp_path / "windows.zip"
    with zipfile.ZipFile(windows, "w") as archive:
        archive.writestr("folder/MediaInfo.exe", b"exe")
    windows_out = tmp_path / "windows-out"
    windows_out.mkdir()
    dvd_media_info.extract_windows(windows, windows_out)
    assert (windows_out / "MediaInfo.exe").read_bytes() == b"exe"
    with pytest.raises(RuntimeError, match="exactly one MediaInfo"):
        dvd_media_info.extract_windows(bad_cli, windows_out)


def test_download_dvd_mediainfo_platform_cache_success_and_missing_outputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dvd_media_info.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(dvd_media_info.platform, "machine", lambda: "x86_64")
    assert dvd_media_info.download_dvd_mediainfo(str(tmp_path)) is None

    monkeypatch.setattr(dvd_media_info.platform, "system", lambda: "Linux")
    monkeypatch.setattr(dvd_media_info.platform, "machine", lambda: "mips")
    assert dvd_media_info.download_dvd_mediainfo(str(tmp_path)) is None

    monkeypatch.setattr(dvd_media_info.platform, "system", lambda: "Windows")
    monkeypatch.setattr(dvd_media_info.platform, "machine", lambda: "arm64")
    with pytest.raises(RuntimeError, match="Windows ARM64"):
        dvd_media_info.download_dvd_mediainfo(str(tmp_path))

    windows_root = tmp_path / "windows-cache"
    win_dir = windows_root / "bin" / "MI" / "windows" / "dvd"
    win_dir.mkdir(parents=True)
    win_cli = win_dir / "MediaInfo.exe"
    win_cli.write_bytes(b"exe")
    (win_dir / f"version_{dvd_media_info.MEDIAINFO_VERSION}").write_text("version", encoding="utf-8")
    monkeypatch.setattr(dvd_media_info.platform, "machine", lambda: "AMD64")
    assert dvd_media_info.download_dvd_mediainfo(str(windows_root)) == str(win_cli)

    windows_install = tmp_path / "windows-install"

    def windows_download(_url: str, target: Path) -> None:
        with zipfile.ZipFile(target, "w") as archive:
            archive.writestr("MediaInfo.exe", b"exe")

    monkeypatch.setattr(dvd_media_info, "download_file", windows_download)
    assert dvd_media_info.download_dvd_mediainfo(str(windows_install)).endswith("MediaInfo.exe")

    monkeypatch.setattr(dvd_media_info, "extract_windows", lambda *_args: None)
    with pytest.raises(RuntimeError, match="Failed to extract"):
        dvd_media_info.download_dvd_mediainfo(str(tmp_path / "windows-missing"))

    linux_cache = tmp_path / "linux-cache"
    linux_dir = linux_cache / "bin" / "MI" / "linux" / "dvd"
    linux_dir.mkdir(parents=True)
    linux_cli = linux_dir / "mediainfo"
    linux_cli.write_bytes(b"cli")
    (linux_dir / "libmediainfo.so.0").write_bytes(b"lib")
    (linux_dir / f"version_{dvd_media_info.MEDIAINFO_VERSION}").write_text("version", encoding="utf-8")
    monkeypatch.setattr(dvd_media_info.platform, "system", lambda: "Linux")
    monkeypatch.setattr(dvd_media_info.platform, "machine", lambda: "x86_64")
    assert dvd_media_info.download_dvd_mediainfo(str(linux_cache)) == str(linux_cli)

    linux_install = tmp_path / "linux-install"

    def linux_download(_url: str, target: Path) -> None:
        target.write_bytes(b"archive")

    def linux_extract(_cli: Path, _lib: Path, output: Path) -> None:
        (output / "mediainfo").write_bytes(b"cli")
        (output / "libmediainfo.so.0").write_bytes(b"lib")

    monkeypatch.setattr(dvd_media_info, "download_file", linux_download)
    monkeypatch.setattr(dvd_media_info, "extract_linux", linux_extract)
    assert dvd_media_info.download_dvd_mediainfo(str(linux_install)).endswith("mediainfo")

    monkeypatch.setattr(dvd_media_info, "extract_linux", lambda *_args: None)
    with pytest.raises(Exception, match="Failed to extract CLI"):
        dvd_media_info.download_dvd_mediainfo(str(tmp_path / "linux-no-cli"))

    def only_cli(_cli: Path, _lib: Path, output: Path) -> None:
        (output / "mediainfo").write_bytes(b"cli")

    monkeypatch.setattr(dvd_media_info, "extract_linux", only_cli)
    with pytest.raises(Exception, match="Failed to extract library"):
        dvd_media_info.download_dvd_mediainfo(str(tmp_path / "linux-no-lib"))


def test_zentag_tar_stream_can_exceed_declared_member_size(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "zentag-stream-limit"
    root.mkdir()
    monkeypatch.setattr(zentag_binary, "tool_install_dir", lambda *_args: root)
    monkeypatch.setattr(zentag_binary.platform, "system", lambda: "Linux")
    monkeypatch.setattr(zentag_binary.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(zentag_binary.HTTPX, "AsyncClient", _ZentagClient)
    asset = "zentag_0.3.0_linux_amd64.tar.gz"

    async def download(_client: object, _url: str, target: Path) -> None:
        target.write_bytes(b"archive")

    monkeypatch.setattr(zentag_binary, "download_bounded_asset", download)
    monkeypatch.setitem(zentag_binary.ZentagBinaryManager.CHECKSUMS, asset, hashlib.sha256(b"archive").hexdigest())
    monkeypatch.setattr(zentag_binary, "MAX_ASSET_BYTES", 5)

    member = SimpleNamespace(name="zentag", size=5, isfile=lambda: True)

    class Archive:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def getmembers(self):
            return [member]

        def extractfile(self, _member: object):
            return io.BytesIO(b"123456")

    monkeypatch.setattr(zentag_binary.tarfile, "open", lambda *_args, **_kwargs: Archive())
    with pytest.raises(RuntimeError, match="exceeds"):
        asyncio.run(zentag_binary.ZentagBinaryManager.ensure_binary(tmp_path))


def test_dvd_get_url_unknown_type_on_non_linux_path() -> None:
    with pytest.raises(ValueError, match="Unknown library_type"):
        dvd_media_info.get_url("windows", "x86_64", "bad")
