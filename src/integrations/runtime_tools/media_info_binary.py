#!/usr/bin/env python3
# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
"""Download the official MediaInfo CLI used by Upload Assistant."""

import asyncio
import os
import platform
import plistlib
import shutil
import stat
import subprocess  # nosec B404 -- fixed argv only; shell execution is never enabled
import sys
import tempfile
import zipfile
from pathlib import Path

import aiofiles
import httpx

from src.integrations.observability.console import logger
from src.integrations.runtime_tools.download_integrity import (
    verify_downloaded_asset,
)

_PLATFORM_CONFIGS: dict[tuple[str, str], tuple[str, str, str, str]] = {
    ("windows", "amd64"): (
        "windows",
        "Windows_x64.zip",
        "MediaInfo.exe",
        "zip",
    ),
    ("windows", "x86_64"): (
        "windows",
        "Windows_x64.zip",
        "MediaInfo.exe",
        "zip",
    ),
    ("windows", "arm64"): (
        "windows/arm64",
        "Windows_ARM64.zip",
        "MediaInfo.exe",
        "zip",
    ),
    ("windows", "aarch64"): (
        "windows/arm64",
        "Windows_ARM64.zip",
        "MediaInfo.exe",
        "zip",
    ),
    ("linux", "amd64"): ("linux", "Lambda_x86_64.zip", "mediainfo", "zip"),
    ("linux", "x86_64"): ("linux", "Lambda_x86_64.zip", "mediainfo", "zip"),
    ("linux", "arm64"): (
        "linux/arm64",
        "Lambda_arm64.zip",
        "mediainfo",
        "zip",
    ),
    ("linux", "aarch64"): (
        "linux/arm64",
        "Lambda_arm64.zip",
        "mediainfo",
        "zip",
    ),
    ("darwin", "amd64"): ("macos", "Mac.dmg", "mediainfo", "dmg"),
    ("darwin", "x86_64"): ("macos", "Mac.dmg", "mediainfo", "dmg"),
    ("darwin", "arm64"): ("macos", "Mac.dmg", "mediainfo", "dmg"),
    ("darwin", "aarch64"): ("macos", "Mac.dmg", "mediainfo", "dmg"),
}


class MediaInfoBinaryManager:
    """Install the pinned official MediaInfo CLI into ``bin/MI``."""

    VERSION = "26.05"
    BASE_URL = "https://old.mediaarea.net/download/binary/mediainfo"

    @staticmethod
    def _is_android() -> bool:
        return sys.platform == "android" or os.environ.get(
            "PREFIX", ""
        ).startswith("/data/data/com.termux/")

    @staticmethod
    def _is_macos() -> bool:
        return platform.system().lower() == "darwin"

    @classmethod
    def _platform_info(cls) -> tuple[str, str, str, str]:
        system = platform.system().lower()
        machine = platform.machine().lower()
        config = _PLATFORM_CONFIGS.get((system, machine))
        if config is None:
            raise RuntimeError(
                f"Unsupported MediaInfo platform: {system} {machine}"
            )
        folder, archive_suffix, binary_name, archive_type = config
        archive_name = f"MediaInfo_CLI_{cls.VERSION}_{archive_suffix}"
        return folder, archive_name, binary_name, archive_type

    @classmethod
    def binary_path(cls, base_dir: str | Path) -> Path:
        folder, _archive, binary_name, _archive_type = cls._platform_info()
        return Path(base_dir) / "bin" / "MI" / folder / binary_name

    @classmethod
    def find_existing_binary(cls, base_dir: str | Path) -> str | None:
        if cls._is_android():
            return shutil.which("mediainfo")
        return cls.find_managed_binary(base_dir) or shutil.which("mediainfo")

    @classmethod
    def _managed_binary_path(cls, base_dir: str | Path) -> Path | None:
        if cls._is_android():
            return None
        try:
            return cls.binary_path(base_dir)
        except RuntimeError:
            return None

    @classmethod
    def _managed_binary_ready(cls, binary: Path) -> bool:
        version_marker = binary.parent / f"version_{cls.VERSION}"
        if not binary.is_file():
            return False
        if not version_marker.is_file():
            return False
        if binary.suffix.lower() == ".exe":
            return True
        return os.access(binary, os.X_OK)

    @classmethod
    def find_managed_binary(cls, base_dir: str | Path) -> str | None:
        binary = cls._managed_binary_path(base_dir)
        if binary is None:
            return None
        return str(binary) if cls._managed_binary_ready(binary) else None

    @classmethod
    def _android_binary(cls, base_dir: str | Path) -> str:
        binary = cls.find_existing_binary(base_dir)
        if binary:
            logger.debug(
                f"[blue]Using MediaInfo from Android PATH: {binary}[/blue]"
            )
            return binary
        raise RuntimeError(
            "MediaInfo is required on Android/Termux. Install it with: pkg install mediainfo"
        )

    @classmethod
    def _installation_paths(
        cls, base_dir: str | Path
    ) -> tuple[Path, Path, Path, str, str]:
        folder, archive_name, binary_name, archive_type = cls._platform_info()
        binary = Path(base_dir) / "bin" / "MI" / folder / binary_name
        version_marker = binary.parent / f"version_{cls.VERSION}"
        archive = binary.parent / f"temp_{archive_name}"
        return binary, version_marker, archive, archive_name, archive_type

    @staticmethod
    def _cached_binary(binary: Path, version_marker: Path) -> str | None:
        if version_marker.is_file() and binary.is_file():
            return str(binary)
        return None

    @classmethod
    async def _download_archive(cls, archive: Path, archive_name: str) -> None:
        url = f"{cls.BASE_URL}/{cls.VERSION}/{archive_name}"
        async with (
            httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client,
            client.stream("GET", url) as response,
        ):
            response.raise_for_status()
            async with aiofiles.open(archive, "wb") as output:
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    await output.write(chunk)

    @staticmethod
    def _zip_member(
        zip_file: zipfile.ZipFile, binary_name: str, archive_name: str
    ) -> str:
        for name in zip_file.namelist():
            if Path(name).name == binary_name:
                return name
        raise RuntimeError(f"{binary_name} was not found in {archive_name}")

    @staticmethod
    def _unsafe_zip_member(info: zipfile.ZipInfo, member: str) -> bool:
        if stat.S_ISLNK(info.external_attr >> 16):
            return True
        if Path(member).is_absolute():
            return True
        return ".." in Path(member).parts

    @classmethod
    def _extract_zip_binary(
        cls, archive: Path, binary: Path, binary_name: str, archive_name: str
    ) -> None:
        with zipfile.ZipFile(archive) as zip_file:
            member = cls._zip_member(zip_file, binary_name, archive_name)
            info = zip_file.getinfo(member)
            if cls._unsafe_zip_member(info, member):
                raise RuntimeError(
                    f"Unsafe MediaInfo archive member: {member}"
                )
            with (
                zip_file.open(info) as source,
                binary.open("wb") as destination,
            ):
                shutil.copyfileobj(source, destination)

    @classmethod
    async def _extract_archive(
        cls,
        archive: Path,
        binary: Path,
        binary_name: str,
        archive_name: str,
        archive_type: str,
    ) -> None:
        if archive_type == "zip":
            cls._extract_zip_binary(archive, binary, binary_name, archive_name)
            return
        if archive_type == "dmg":
            await cls._extract_macos_binary(archive, binary)
            return
        raise RuntimeError(
            f"Unsupported MediaInfo archive type: {archive_type}"
        )

    @classmethod
    def _finalize_binary(cls, binary: Path, version_marker: Path) -> None:
        if binary.suffix.lower() != ".exe":
            binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
        version_marker.write_text(
            f"MediaInfo CLI {cls.VERSION}\n", encoding="utf-8"
        )

    @classmethod
    async def ensure_mediainfo_binary(cls, base_dir: str | Path) -> str:
        if cls._is_android():
            return cls._android_binary(base_dir)

        binary, version_marker, archive, archive_name, archive_type = (
            cls._installation_paths(base_dir)
        )
        cached = cls._cached_binary(binary, version_marker)
        if cached is not None:
            return cached

        binary.parent.mkdir(parents=True, exist_ok=True)
        logger.info(
            f"[yellow]Downloading MediaInfo CLI {cls.VERSION}...[/yellow]"
        )
        try:
            await cls._download_archive(archive, archive_name)
            verify_downloaded_asset(archive, archive_name)
            await cls._extract_archive(
                archive,
                binary,
                binary.name,
                archive_name,
                archive_type,
            )
            cls._finalize_binary(binary, version_marker)
            return str(binary)
        finally:
            if archive.exists():
                archive.unlink()

    @staticmethod
    async def _attach_macos_dmg(archive: Path) -> Path:
        attached = await asyncio.to_thread(
            subprocess.run,
            [
                "hdiutil",
                "attach",
                "-nobrowse",
                "-readonly",
                "-plist",
                str(archive),
            ],
            check=True,
            capture_output=True,
        )
        devices = plistlib.loads(attached.stdout).get("system-entities", [])
        mount = next(
            (
                item.get("mount-point")
                for item in devices
                if item.get("mount-point")
            ),
            None,
        )
        if not mount:
            raise RuntimeError("MediaInfo DMG did not provide a mount point")
        return Path(mount)

    @staticmethod
    def _required_macos_file(root: Path, name: str, error: str) -> Path:
        path = next(
            (
                candidate
                for candidate in root.rglob(name)
                if candidate.is_file()
            ),
            None,
        )
        if path is None:
            raise RuntimeError(error)
        return path

    @staticmethod
    async def _expand_macos_package(package: Path, binary: Path) -> Path:
        extracted_package = Path(
            tempfile.mkdtemp(prefix="mediainfo-pkg-", dir=binary.parent)
        )
        await asyncio.to_thread(
            subprocess.run,
            [
                "pkgutil",
                "--expand-full",
                str(package),
                str(extracted_package),
            ],
            check=True,
            capture_output=True,
        )
        return extracted_package

    @staticmethod
    def _required_macos_payload(
        extracted_package: Path, package: Path
    ) -> Path:
        payload = next(extracted_package.rglob("Payload"), None)
        if payload is None:
            raise RuntimeError(
                f"MediaInfo package did not contain a payload: {package.name}"
            )
        return payload

    @staticmethod
    async def _extract_macos_payload(
        payload: Path, extracted_package: Path
    ) -> Path:
        payload_root = extracted_package / "payload"
        payload_root.mkdir()
        await asyncio.to_thread(
            subprocess.run,
            ["bsdtar", "-xf", str(payload), "-C", str(payload_root)],
            check=True,
            capture_output=True,
        )
        return payload_root

    @staticmethod
    async def _cleanup_macos_extraction(
        extracted_package: Path | None, mount_point: Path
    ) -> None:
        if extracted_package is not None:
            await asyncio.to_thread(
                shutil.rmtree, extracted_package, ignore_errors=True
            )
        await asyncio.to_thread(
            subprocess.run,
            ["hdiutil", "detach", str(mount_point)],
            check=True,
            capture_output=True,
        )

    @classmethod
    async def _extract_macos_binary(cls, archive: Path, binary: Path) -> None:
        """Mount the official DMG, expand its package, and copy the CLI executable."""
        mount_point = await cls._attach_macos_dmg(archive)
        extracted_package: Path | None = None
        try:
            package = cls._required_macos_file(
                mount_point,
                "mediainfo.pkg",
                f"mediainfo.pkg was not found in {archive.name}",
            )
            extracted_package = await cls._expand_macos_package(
                package, binary
            )
            payload = cls._required_macos_payload(extracted_package, package)
            payload_root = await cls._extract_macos_payload(
                payload, extracted_package
            )
            source = cls._required_macos_file(
                payload_root,
                "mediainfo",
                f"mediainfo was not found in {archive.name}",
            )
            await asyncio.to_thread(shutil.copy2, source, binary)
        finally:
            await cls._cleanup_macos_extraction(extracted_package, mount_point)
