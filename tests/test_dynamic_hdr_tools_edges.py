from __future__ import annotations

import asyncio
import hashlib
import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from src.integrations.runtime_tools import dynamic_hdr_tools as tools


def test_asset_name_all_platforms_and_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases = [
        (
            "Windows",
            "AMD64",
            "dovi_tool-2.3.3-x86_64-pc-windows-msvc.zip",
            ".exe",
        ),
        (
            "Windows",
            "arm64",
            "dovi_tool-2.3.3-aarch64-pc-windows-msvc.zip",
            ".exe",
        ),
        ("Darwin", "x86_64", "dovi_tool-2.3.3-universal-macOS.zip", ""),
        (
            "Linux",
            "aarch64",
            "dovi_tool-2.3.3-aarch64-unknown-linux-musl.tar.gz",
            "",
        ),
        (
            "Linux",
            "amd64",
            "dovi_tool-2.3.3-x86_64-unknown-linux-musl.tar.gz",
            "",
        ),
    ]
    for system, machine, expected, extension in cases:
        monkeypatch.setattr(
            tools.platform, "system", lambda value=system: value
        )
        monkeypatch.setattr(
            tools.platform, "machine", lambda value=machine: value
        )
        assert tools._asset_name("dovi") == (expected, extension)

    monkeypatch.setattr(tools.platform, "system", lambda: "Linux")
    monkeypatch.setattr(tools.platform, "machine", lambda: "riscv64")
    with pytest.raises(RuntimeError, match="not supported"):
        tools._asset_name("dovi")
    monkeypatch.setattr(tools.platform, "system", lambda: "FreeBSD")
    monkeypatch.setattr(tools.platform, "machine", lambda: "x86_64")
    with pytest.raises(RuntimeError, match="not supported"):
        tools._asset_name("dovi")


def test_checksum_missing_mismatch_and_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asset = tmp_path / "asset"
    asset.write_bytes(b"tool")
    with pytest.raises(RuntimeError, match="Missing checksum"):
        tools._verify_checksum_file("missing", asset)
    monkeypatch.setitem(tools.ASSET_SHA256, "asset", "0" * 64)
    with pytest.raises(RuntimeError, match="Checksum mismatch"):
        tools._verify_checksum_file("asset", asset)
    monkeypatch.setitem(
        tools.ASSET_SHA256, "asset", hashlib.sha256(b"tool").hexdigest()
    )
    tools._verify_checksum_file("asset", asset)


def test_safe_extract_zip_and_tar(tmp_path: Path) -> None:
    zip_path = tmp_path / "tool.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("bundle/dovi_tool", b"tool")
    zip_out = tmp_path / "zip-out"
    tools._safe_extract(zip_path, zip_out)
    assert (zip_out / "bundle" / "dovi_tool").read_bytes() == b"tool"

    tar_path = tmp_path / "tool.tar.gz"
    with tarfile.open(tar_path, "w:gz") as archive:
        info = tarfile.TarInfo("bundle/dovi_tool")
        info.size = 4
        archive.addfile(info, io.BytesIO(b"tool"))
    tar_out = tmp_path / "tar-out"
    tools._safe_extract(tar_path, tar_out)
    assert (tar_out / "bundle" / "dovi_tool").read_bytes() == b"tool"


def test_get_tool_installed_cached_download_success_and_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        tools.shutil, "which", lambda _command: "/usr/bin/dovi_tool"
    )
    assert (
        asyncio.run(tools.get_tool(str(tmp_path), "dovi"))
        == "/usr/bin/dovi_tool"
    )

    monkeypatch.setattr(tools.shutil, "which", lambda _command: None)
    target = tmp_path / "target"
    target.mkdir()
    binary = target / "dovi_tool"
    binary.write_bytes(b"cached")
    marker = target / tools.TOOLS["dovi"]["version"]
    marker.write_text("cached", encoding="utf-8")
    monkeypatch.setattr(tools, "tool_install_dir", lambda *_args: target)
    monkeypatch.setattr(tools.platform, "system", lambda: "Linux")
    monkeypatch.setattr(tools.platform, "machine", lambda: "x86_64")
    assert asyncio.run(tools.get_tool(str(tmp_path), "dovi")) == str(binary)

    binary.unlink()
    marker.unlink()
    stale = target / "stale-version"
    stale.write_text("stale", encoding="utf-8")

    async def download(_client, _url: str, archive: Path) -> None:
        archive.write_bytes(b"archive")

    def extract(_archive: Path, destination: Path) -> None:
        candidate = destination / "bundle" / "dovi_tool"
        candidate.parent.mkdir(parents=True)
        candidate.write_bytes(b"new-binary")

    monkeypatch.setattr(tools, "download_bounded_asset", download)
    monkeypatch.setattr(tools, "_verify_checksum_file", lambda *_args: None)
    monkeypatch.setattr(tools, "_safe_extract", extract)
    result = asyncio.run(tools.get_tool(str(tmp_path), "dovi"))
    assert result == str(binary)
    assert binary.read_bytes() == b"new-binary"
    assert binary.stat().st_mode & 0o111
    assert marker.is_file()
    assert not stale.exists()
    assert not (target / ".download").exists()


def test_get_tool_windows_missing_candidate_and_download_error_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    monkeypatch.setattr(tools.shutil, "which", lambda _command: None)
    monkeypatch.setattr(tools, "tool_install_dir", lambda *_args: target)
    monkeypatch.setattr(tools.platform, "system", lambda: "Windows")
    monkeypatch.setattr(tools.platform, "machine", lambda: "AMD64")

    async def download(_client, _url: str, archive: Path) -> None:
        archive.write_bytes(b"archive")

    monkeypatch.setattr(tools, "download_bounded_asset", download)
    monkeypatch.setattr(tools, "_verify_checksum_file", lambda *_args: None)
    monkeypatch.setattr(tools, "_safe_extract", lambda *_args: None)
    with pytest.raises(RuntimeError, match="did not contain"):
        asyncio.run(tools.get_tool(str(tmp_path), "dovi"))
    assert not (target / ".download").exists()

    async def fail_download(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("download failed")

    monkeypatch.setattr(tools, "download_bounded_asset", fail_download)
    with pytest.raises(RuntimeError, match="download failed"):
        asyncio.run(tools.get_tool(str(tmp_path), "dovi"))
    assert not (target / ".download").exists()
