from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from src.integrations.runtime_tools import dvd_media_info as dvd


def _zip(path: Path, *members: tuple[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in members:
            archive.writestr(name, payload)


def test_filename_url_and_download_helpers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert dvd.get_filename("windows", "x86_64") == "MediaInfo_CLI_23.04_Windows_x64.zip"
    assert dvd.get_filename("linux", "arm64", "cli") == "MediaInfo_CLI_23.04_Lambda_arm64.zip"
    assert dvd.get_filename("linux", "x86_64", "lib") == "MediaInfo_DLL_23.04_Lambda_x86_64.zip"
    assert dvd.get_filename("darwin", "x86_64") == ""
    with pytest.raises(ValueError, match="Unknown library_type"):
        dvd.get_filename("linux", "x86_64", "bad")
    assert "/mediainfo/23.04/" in dvd.get_url("linux", "x86_64", "cli")
    assert "/libmediainfo0/23.04/" in dvd.get_url("linux", "x86_64", "lib")
    with pytest.raises(ValueError, match="Unknown library_type"):
        dvd.get_url("windows", "x86_64", "bad")

    calls: list[tuple[str, Path, str]] = []
    monkeypatch.setattr(dvd, "download_verified_asset_sync", lambda url, path, asset: calls.append((url, path, asset)))
    target = tmp_path / "asset.zip"
    dvd.download_file("https://example.invalid", target)
    assert calls == [("https://example.invalid", target, "asset.zip")]


def test_extract_windows_success_missing_and_duplicate(tmp_path: Path) -> None:
    archive = tmp_path / "cli.zip"
    _zip(archive, ("bundle/MediaInfo.exe", b"exe"))
    output = tmp_path / "output"
    output.mkdir()
    dvd.extract_windows(archive, output)
    assert (output / "MediaInfo.exe").read_bytes() == b"exe"

    missing = tmp_path / "missing.zip"
    _zip(missing, ("README", b"readme"))
    with pytest.raises(RuntimeError, match="exactly one"):
        dvd.extract_windows(missing, tmp_path / "missing-output")

    duplicate = tmp_path / "duplicate.zip"
    _zip(duplicate, ("one/MediaInfo.exe", b"one"), ("two/MediaInfo.exe", b"two"))
    with pytest.raises(RuntimeError, match="exactly one"):
        dvd.extract_windows(duplicate, tmp_path / "duplicate-output")


def test_extract_linux_duplicate_cli_and_missing_library(tmp_path: Path) -> None:
    cli = tmp_path / "cli.zip"
    lib = tmp_path / "lib.zip"
    _zip(cli, ("one/mediainfo", b"one"), ("two/mediainfo", b"two"))
    _zip(lib, ("lib/libmediainfo.so.0.0.0", b"lib"))
    output = tmp_path / "output"
    output.mkdir()
    with pytest.raises(RuntimeError, match="exactly one CLI"):
        dvd.extract_linux(cli, lib, output)

    _zip(cli, ("mediainfo", b"cli"))
    _zip(lib, ("wrong", b"lib"))
    with pytest.raises(RuntimeError, match="required library"):
        dvd.extract_linux(cli, lib, output)


def test_download_windows_cache_arm_error_success_and_extract_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dvd.platform, "system", lambda: "Windows")
    monkeypatch.setattr(dvd.platform, "machine", lambda: "ARM64")
    with pytest.raises(RuntimeError, match="unavailable for Windows ARM64"):
        dvd.download_dvd_mediainfo(str(tmp_path))

    monkeypatch.setattr(dvd.platform, "machine", lambda: "AMD64")
    output = tmp_path / "bin" / "MI" / "windows" / "dvd"
    output.mkdir(parents=True)
    cli = output / "MediaInfo.exe"
    cli.write_bytes(b"cached")
    marker = output / "version_23.04"
    marker.write_text("cached", encoding="utf-8")
    assert dvd.download_dvd_mediainfo(str(tmp_path)) == str(cli)

    cli.unlink()
    marker.unlink()

    def download(_url: str, archive: Path) -> None:
        _zip(archive, ("bundle/MediaInfo.exe", b"windows"))

    monkeypatch.setattr(dvd, "download_file", download)
    assert dvd.download_dvd_mediainfo(str(tmp_path)) == str(cli)
    assert cli.read_bytes() == b"windows" and marker.is_file()

    cli.unlink()
    marker.unlink()
    monkeypatch.setattr(dvd, "extract_windows", lambda *_args: None)
    with pytest.raises(RuntimeError, match="Failed to extract MediaInfo CLI"):
        dvd.download_dvd_mediainfo(str(tmp_path))


def test_download_unsupported_linux_cache_success_and_missing_outputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dvd.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(dvd.platform, "machine", lambda: "x86_64")
    assert dvd.download_dvd_mediainfo(str(tmp_path)) is None

    monkeypatch.setattr(dvd.platform, "system", lambda: "Linux")
    monkeypatch.setattr(dvd.platform, "machine", lambda: "riscv64")
    assert dvd.download_dvd_mediainfo(str(tmp_path)) is None

    monkeypatch.setattr(dvd.platform, "machine", lambda: "arm64")
    output = tmp_path / "bin" / "MI" / "linux" / "dvd"
    output.mkdir(parents=True)
    cli = output / "mediainfo"
    lib = output / "libmediainfo.so.0"
    marker = output / "version_23.04"
    for path in (cli, lib, marker):
        path.write_bytes(b"cached")
    assert dvd.download_dvd_mediainfo(str(tmp_path)) == str(cli)

    for path in (cli, lib, marker):
        path.unlink()

    def download(_url: str, archive: Path) -> None:
        if "DLL" in archive.name:
            _zip(archive, ("lib/libmediainfo.so.0.0.0", b"lib"))
        else:
            _zip(archive, ("bin/mediainfo", b"cli"))

    monkeypatch.setattr(dvd, "download_file", download)
    assert dvd.download_dvd_mediainfo(str(tmp_path)) == str(cli)
    assert cli.exists() and lib.exists()

    cli.unlink()
    lib.unlink()
    marker.unlink()
    monkeypatch.setattr(dvd, "extract_linux", lambda *_args: None)
    with pytest.raises(Exception, match="Failed to extract CLI"):
        dvd.download_dvd_mediainfo(str(tmp_path))

    cli.write_bytes(b"cli")
    with pytest.raises(Exception, match="Failed to extract library"):
        dvd.download_dvd_mediainfo(str(tmp_path))
