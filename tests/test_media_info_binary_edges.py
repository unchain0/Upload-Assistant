from __future__ import annotations

import asyncio
import io
import plistlib
import stat
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest

from src.integrations.runtime_tools import media_info_binary as media_info


class _Response:
    payload: ClassVar[bytes] = b""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def aiter_bytes(self, *, chunk_size: int):
        del chunk_size
        yield self.payload


class _Client:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def stream(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()


def _zip_bytes(
    name: str,
    payload: bytes = b"mediainfo",
    *,
    mode: int = stat.S_IFREG | 0o755,
) -> bytes:
    output = io.BytesIO()
    info = zipfile.ZipInfo(name)
    info.external_attr = mode << 16
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(info, payload)
    return output.getvalue()


def test_android_macos_and_platform_matrix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(media_info.sys, "platform", "android")
    assert media_info.MediaInfoBinaryManager._is_android()
    monkeypatch.setattr(media_info.sys, "platform", "linux")
    monkeypatch.setenv("PREFIX", "/data/data/com.termux/files/usr")
    assert media_info.MediaInfoBinaryManager._is_android()
    monkeypatch.delenv("PREFIX")
    assert not media_info.MediaInfoBinaryManager._is_android()

    monkeypatch.setattr(media_info.platform, "system", lambda: "Darwin")
    assert media_info.MediaInfoBinaryManager._is_macos()
    monkeypatch.setattr(media_info.platform, "system", lambda: "Linux")
    assert not media_info.MediaInfoBinaryManager._is_macos()

    cases = [
        (
            "Windows",
            "AMD64",
            (
                "windows",
                "MediaInfo_CLI_26.05_Windows_x64.zip",
                "MediaInfo.exe",
                "zip",
            ),
        ),
        (
            "Windows",
            "arm64",
            (
                "windows/arm64",
                "MediaInfo_CLI_26.05_Windows_ARM64.zip",
                "MediaInfo.exe",
                "zip",
            ),
        ),
        (
            "Linux",
            "x86_64",
            (
                "linux",
                "MediaInfo_CLI_26.05_Lambda_x86_64.zip",
                "mediainfo",
                "zip",
            ),
        ),
        (
            "Linux",
            "aarch64",
            (
                "linux/arm64",
                "MediaInfo_CLI_26.05_Lambda_arm64.zip",
                "mediainfo",
                "zip",
            ),
        ),
        (
            "Darwin",
            "arm64",
            ("macos", "MediaInfo_CLI_26.05_Mac.dmg", "mediainfo", "dmg"),
        ),
    ]
    for system, machine, expected in cases:
        monkeypatch.setattr(
            media_info.platform, "system", lambda value=system: value
        )
        monkeypatch.setattr(
            media_info.platform, "machine", lambda value=machine: value
        )
        assert media_info.MediaInfoBinaryManager._platform_info() == expected
    monkeypatch.setattr(media_info.platform, "system", lambda: "FreeBSD")
    monkeypatch.setattr(media_info.platform, "machine", lambda: "riscv64")
    with pytest.raises(RuntimeError, match="Unsupported MediaInfo"):
        media_info.MediaInfoBinaryManager._platform_info()


def test_binary_path_and_existing_android_managed_and_system(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        media_info.MediaInfoBinaryManager,
        "_is_android",
        staticmethod(lambda: True),
    )
    monkeypatch.setattr(
        media_info.shutil, "which", lambda _name: "/termux/mediainfo"
    )
    assert (
        media_info.MediaInfoBinaryManager.find_existing_binary(tmp_path)
        == "/termux/mediainfo"
    )
    assert (
        media_info.MediaInfoBinaryManager.find_managed_binary(tmp_path) is None
    )

    monkeypatch.setattr(
        media_info.MediaInfoBinaryManager,
        "_is_android",
        staticmethod(lambda: False),
    )
    monkeypatch.setattr(
        media_info.MediaInfoBinaryManager,
        "_platform_info",
        classmethod(lambda _cls: ("linux", "asset.zip", "mediainfo", "zip")),
    )
    binary = media_info.MediaInfoBinaryManager.binary_path(tmp_path)
    assert binary == tmp_path / "bin" / "MI" / "linux" / "mediainfo"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"tool")
    marker = binary.parent / "version_26.05"
    marker.write_text("version", encoding="utf-8")
    monkeypatch.setattr(media_info.os, "access", lambda *_args: True)
    assert media_info.MediaInfoBinaryManager.find_managed_binary(
        tmp_path
    ) == str(binary)

    marker.unlink()
    monkeypatch.setattr(
        media_info.shutil, "which", lambda _name: "/usr/bin/mediainfo"
    )
    assert (
        media_info.MediaInfoBinaryManager.find_existing_binary(tmp_path)
        == "/usr/bin/mediainfo"
    )

    monkeypatch.setattr(
        media_info.MediaInfoBinaryManager,
        "_platform_info",
        classmethod(
            lambda _cls: (_ for _ in ()).throw(RuntimeError("unsupported"))
        ),
    )
    assert (
        media_info.MediaInfoBinaryManager.find_managed_binary(tmp_path) is None
    )


def test_ensure_android_existing_missing_cache_and_zip_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        media_info.MediaInfoBinaryManager,
        "_is_android",
        staticmethod(lambda: True),
    )
    monkeypatch.setattr(
        media_info.MediaInfoBinaryManager,
        "find_existing_binary",
        classmethod(lambda _cls, _base: "/termux/mediainfo"),
    )
    assert (
        asyncio.run(
            media_info.MediaInfoBinaryManager.ensure_mediainfo_binary(tmp_path)
        )
        == "/termux/mediainfo"
    )
    monkeypatch.setattr(
        media_info.MediaInfoBinaryManager,
        "find_existing_binary",
        classmethod(lambda _cls, _base: None),
    )
    with pytest.raises(RuntimeError, match="pkg install mediainfo"):
        asyncio.run(
            media_info.MediaInfoBinaryManager.ensure_mediainfo_binary(tmp_path)
        )

    monkeypatch.setattr(
        media_info.MediaInfoBinaryManager,
        "_is_android",
        staticmethod(lambda: False),
    )
    monkeypatch.setattr(
        media_info.MediaInfoBinaryManager,
        "_platform_info",
        classmethod(lambda _cls: ("linux", "asset.zip", "mediainfo", "zip")),
    )
    binary = tmp_path / "bin" / "MI" / "linux" / "mediainfo"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"cached")
    marker = binary.parent / "version_26.05"
    marker.write_text("cached", encoding="utf-8")
    assert asyncio.run(
        media_info.MediaInfoBinaryManager.ensure_mediainfo_binary(tmp_path)
    ) == str(binary)

    binary.unlink()
    marker.unlink()
    monkeypatch.setattr(media_info.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(
        media_info, "verify_downloaded_asset", lambda *_args: None
    )
    _Response.payload = _zip_bytes("bundle/mediainfo")
    result = asyncio.run(
        media_info.MediaInfoBinaryManager.ensure_mediainfo_binary(tmp_path)
    )
    assert result == str(binary) and binary.read_bytes() == b"mediainfo"
    assert binary.stat().st_mode & stat.S_IXUSR and marker.is_file()
    assert not (binary.parent / "temp_asset.zip").exists()


def test_ensure_zip_missing_unsafe_unsupported_and_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        media_info.MediaInfoBinaryManager,
        "_is_android",
        staticmethod(lambda: False),
    )
    monkeypatch.setattr(
        media_info.MediaInfoBinaryManager,
        "_platform_info",
        classmethod(
            lambda _cls: ("windows", "asset.zip", "MediaInfo.exe", "zip")
        ),
    )
    monkeypatch.setattr(media_info.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(
        media_info, "verify_downloaded_asset", lambda *_args: None
    )

    _Response.payload = _zip_bytes("README")
    with pytest.raises(RuntimeError, match="was not found"):
        asyncio.run(
            media_info.MediaInfoBinaryManager.ensure_mediainfo_binary(
                tmp_path / "missing"
            )
        )

    _Response.payload = _zip_bytes("../MediaInfo.exe")
    with pytest.raises(RuntimeError, match="Unsafe MediaInfo"):
        asyncio.run(
            media_info.MediaInfoBinaryManager.ensure_mediainfo_binary(
                tmp_path / "unsafe"
            )
        )

    monkeypatch.setattr(
        media_info.MediaInfoBinaryManager,
        "_platform_info",
        classmethod(lambda _cls: ("other", "asset.rar", "mediainfo", "rar")),
    )
    _Response.payload = b"archive"
    with pytest.raises(
        RuntimeError, match="Unsupported MediaInfo archive type"
    ):
        asyncio.run(
            media_info.MediaInfoBinaryManager.ensure_mediainfo_binary(
                tmp_path / "unsupported"
            )
        )

    monkeypatch.setattr(
        media_info.MediaInfoBinaryManager,
        "_platform_info",
        classmethod(
            lambda _cls: ("windows", "asset.zip", "MediaInfo.exe", "zip")
        ),
    )
    monkeypatch.setattr(
        media_info,
        "verify_downloaded_asset",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("checksum")),
    )
    root = tmp_path / "checksum"
    with pytest.raises(RuntimeError, match="checksum"):
        asyncio.run(
            media_info.MediaInfoBinaryManager.ensure_mediainfo_binary(root)
        )
    assert not (root / "bin" / "MI" / "windows" / "temp_asset.zip").exists()


def _mac_run_factory(
    mount: Path,
    *,
    create_package: bool = True,
    create_payload: bool = True,
    create_binary: bool = True,
):
    calls: list[list[str]] = []

    def run(command: list[str], **_kwargs: object):
        calls.append(command)
        if command[0] == "hdiutil" and command[1] == "attach":
            return SimpleNamespace(
                stdout=plistlib.dumps(
                    {"system-entities": [{"mount-point": str(mount)}]}
                )
            )
        if command[0] == "pkgutil":
            destination = Path(command[-1])
            if create_payload:
                (destination / "Payload").write_bytes(b"payload")
            return SimpleNamespace(stdout=b"")
        if command[0] == "bsdtar":
            payload_root = Path(command[-1])
            if create_binary:
                source = payload_root / "usr" / "local" / "bin" / "mediainfo"
                source.parent.mkdir(parents=True)
                source.write_bytes(b"mac-binary")
            return SimpleNamespace(stdout=b"")
        return SimpleNamespace(stdout=b"")

    if create_package:
        package = mount / "MediaInfo" / "mediainfo.pkg"
        package.parent.mkdir(parents=True, exist_ok=True)
        package.write_bytes(b"pkg")
    return run, calls


def test_extract_macos_success_and_all_missing_components(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "asset.dmg"
    archive.write_bytes(b"dmg")
    binary = tmp_path / "output" / "mediainfo"
    binary.parent.mkdir()
    mount = tmp_path / "mount"
    mount.mkdir()
    run, calls = _mac_run_factory(mount)
    monkeypatch.setattr(media_info.subprocess, "run", run)
    asyncio.run(
        media_info.MediaInfoBinaryManager._extract_macos_binary(
            archive, binary
        )
    )
    assert binary.read_bytes() == b"mac-binary"
    assert any(command[:2] == ["hdiutil", "detach"] for command in calls)
    assert not any(
        path.name.startswith("mediainfo-pkg-")
        for path in binary.parent.iterdir()
    )

    def no_mount(_command: list[str], **_kwargs: object):
        return SimpleNamespace(stdout=plistlib.dumps({"system-entities": []}))

    monkeypatch.setattr(media_info.subprocess, "run", no_mount)
    with pytest.raises(RuntimeError, match="did not provide a mount point"):
        asyncio.run(
            media_info.MediaInfoBinaryManager._extract_macos_binary(
                archive, binary
            )
        )

    no_package_mount = tmp_path / "no-package"
    no_package_mount.mkdir()
    run, calls = _mac_run_factory(no_package_mount, create_package=False)
    monkeypatch.setattr(media_info.subprocess, "run", run)
    with pytest.raises(RuntimeError, match=r"mediainfo\.pkg was not found"):
        asyncio.run(
            media_info.MediaInfoBinaryManager._extract_macos_binary(
                archive, binary
            )
        )
    assert any(command[:2] == ["hdiutil", "detach"] for command in calls)

    no_payload_mount = tmp_path / "no-payload"
    no_payload_mount.mkdir()
    run, _calls = _mac_run_factory(no_payload_mount, create_payload=False)
    monkeypatch.setattr(media_info.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="did not contain a payload"):
        asyncio.run(
            media_info.MediaInfoBinaryManager._extract_macos_binary(
                archive, binary
            )
        )

    no_binary_mount = tmp_path / "no-binary"
    no_binary_mount.mkdir()
    run, _calls = _mac_run_factory(no_binary_mount, create_binary=False)
    monkeypatch.setattr(media_info.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="mediainfo was not found"):
        asyncio.run(
            media_info.MediaInfoBinaryManager._extract_macos_binary(
                archive, binary
            )
        )


def test_ensure_macos_delegates_to_extractor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        media_info.MediaInfoBinaryManager,
        "_is_android",
        staticmethod(lambda: False),
    )
    monkeypatch.setattr(
        media_info.MediaInfoBinaryManager,
        "_platform_info",
        classmethod(lambda _cls: ("macos", "asset.dmg", "mediainfo", "dmg")),
    )
    monkeypatch.setattr(media_info.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(
        media_info, "verify_downloaded_asset", lambda *_args: None
    )
    _Response.payload = b"dmg"

    async def extract(_archive: Path, binary: Path) -> None:
        binary.write_bytes(b"mac")

    monkeypatch.setattr(
        media_info.MediaInfoBinaryManager,
        "_extract_macos_binary",
        staticmethod(extract),
    )
    result = asyncio.run(
        media_info.MediaInfoBinaryManager.ensure_mediainfo_binary(tmp_path)
    )
    assert Path(result).read_bytes() == b"mac"
