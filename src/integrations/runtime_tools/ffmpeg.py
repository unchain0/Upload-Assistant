"""Provision the pinned Windows FFmpeg build used by Upload Assistant."""

from __future__ import annotations

import platform
import shutil
import stat
import zipfile
from pathlib import Path

import aiofiles
import httpx

from src.integrations.observability.console import logger
from src.integrations.runtime_tools.download_integrity import (
    verify_downloaded_asset,
)


class FfmpegBinaryManager:
    """Download the verified Windows FFmpeg build into the runtime ``bin`` cache."""

    VERSION = "9.0.1"
    ASSET_NAME = "ffmpeg-9.0.1-essentials_build.zip"
    DOWNLOAD_URL = f"https://github.com/GyanD/codexffmpeg/releases/download/{VERSION}/{ASSET_NAME}"

    @classmethod
    def binary_path(cls, base_dir: str | Path) -> Path:
        return (
            Path(base_dir)
            / "bin"
            / "ffmpeg"
            / "windows"
            / "x64"
            / "ffmpeg.exe"
        )

    @classmethod
    def find_existing_binary(cls, base_dir: str | Path) -> str | None:
        binary = cls.binary_path(base_dir)
        version_marker = binary.parent / f"version_{cls.VERSION}"
        if binary.is_file() and version_marker.is_file():
            return str(binary)
        binary_name = (
            "ffmpeg.exe"
            if platform.system().lower() == "windows"
            else "ffmpeg"
        )
        return shutil.which(binary_name)

    @staticmethod
    def _require_windows_install() -> None:
        if platform.system().lower() == "windows":
            return
        raise RuntimeError(
            "FFmpeg was not found on PATH; install it with your system "
            "package manager or configure ffmpeg_path."
        )

    @classmethod
    async def _download_archive(cls, archive: Path) -> None:
        logger.info(f"[yellow]Downloading FFmpeg {cls.VERSION}...[/yellow]")
        async with (
            httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client,
            client.stream("GET", cls.DOWNLOAD_URL) as response,
        ):
            response.raise_for_status()
            async with aiofiles.open(archive, "wb") as output:
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    await output.write(chunk)

    @staticmethod
    def _archive_member_is_safe(info: zipfile.ZipInfo) -> bool:
        path = Path(info.filename)
        return bool(
            not stat.S_ISLNK(info.external_attr >> 16)
            and not path.is_absolute()
            and ".." not in path.parts
        )

    @classmethod
    def _ffmpeg_archive_member(
        cls, zip_file: zipfile.ZipFile
    ) -> zipfile.ZipInfo:
        member = next(
            (
                name
                for name in zip_file.namelist()
                if Path(name).name.lower() == "ffmpeg.exe"
            ),
            None,
        )
        if member is None:
            raise RuntimeError(f"ffmpeg.exe was not found in {cls.ASSET_NAME}")
        info = zip_file.getinfo(member)
        if not cls._archive_member_is_safe(info):
            raise RuntimeError(f"Unsafe FFmpeg archive member: {member}")
        return info

    @classmethod
    def _extract_binary(cls, archive: Path, binary: Path) -> None:
        with zipfile.ZipFile(archive) as zip_file:
            info = cls._ffmpeg_archive_member(zip_file)
            with (
                zip_file.open(info) as source,
                binary.open("wb") as destination,
            ):
                shutil.copyfileobj(source, destination)

    @classmethod
    def _write_version_marker(cls, binary: Path) -> None:
        (binary.parent / f"version_{cls.VERSION}").write_text(
            f"FFmpeg {cls.VERSION}\n", encoding="utf-8"
        )

    @staticmethod
    def _cleanup_archive(archive: Path) -> None:
        if archive.exists():
            archive.unlink()

    @classmethod
    async def ensure_ffmpeg_binary(cls, base_dir: str | Path) -> str:
        existing_binary = cls.find_existing_binary(base_dir)
        if existing_binary:
            return existing_binary
        cls._require_windows_install()
        binary = cls.binary_path(base_dir)
        binary.parent.mkdir(parents=True, exist_ok=True)
        archive = binary.parent / f"temp_{cls.ASSET_NAME}"
        try:
            await cls._download_archive(archive)
            verify_downloaded_asset(archive, cls.ASSET_NAME)
            cls._extract_binary(archive, binary)
            cls._write_version_marker(binary)
            return str(binary)
        finally:
            cls._cleanup_archive(archive)
