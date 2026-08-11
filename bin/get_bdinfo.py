#!/usr/bin/env python3
# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import os
import platform
import shutil
import stat
import tarfile
import zipfile
from pathlib import Path

import aiofiles
import httpx

from bin.download_integrity import MAX_EXTRACTED_BYTES, download_verified_asset, safe_extract_tar, safe_extract_zip

try:
    from src.console import console, logger
except ImportError:
    import logging

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    class SimpleConsole:
        def print(self, message: str, markup: bool = False) -> None:  # noqa: ARG002
            print(message)

    console = SimpleConsole()
    logger = logging.getLogger(__name__)


class BDInfoBinaryManager:
    """Download autobrr/go-bdinfo binaries for the host architecture.

    Default version pinned (see https://github.com/autobrr/go-bdinfo/releases).
    """

    @staticmethod
    async def ensure_bdinfo_binary(base_dir: str | Path, version: str = "v0.3.1") -> str:
        system = platform.system().lower()
        machine = platform.machine().lower()
        logger.debug(f"[blue]Detected system: {system}, architecture: {machine}[/blue]")

        platform_map: dict[str, dict[str, dict[str, str]]] = {
            "windows": {
                "x86_64": {"asset": "windows_amd64.zip", "folder": "windows/x86_64"},
                "amd64": {"asset": "windows_amd64.zip", "folder": "windows/x86_64"},
            },
            "darwin": {
                "arm64": {"asset": "darwin_arm64.tar.gz", "folder": "macos/arm64"},
                "x86_64": {"asset": "darwin_amd64.tar.gz", "folder": "macos/x86_64"},
                "amd64": {"asset": "darwin_amd64.tar.gz", "folder": "macos/x86_64"},
            },
            "linux": {
                "x86_64": {"asset": "linux_amd64.tar.gz", "folder": "linux/amd64"},
                "amd64": {"asset": "linux_amd64.tar.gz", "folder": "linux/amd64"},
                "arm64": {"asset": "linux_arm64.tar.gz", "folder": "linux/arm64"},
                "aarch64": {"asset": "linux_arm64.tar.gz", "folder": "linux/arm64"},
                "armv7l": {"asset": "linux_arm.tar.gz", "folder": "linux/arm"},
                "armv6l": {"asset": "linux_arm.tar.gz", "folder": "linux/arm"},
                "arm": {"asset": "linux_arm.tar.gz", "folder": "linux/arm"},
            },
        }

        if system not in platform_map or machine not in platform_map[system]:
            raise Exception(f"Unsupported platform: {system} {machine}")

        platform_info = platform_map[system][machine]
        release_version = version.removeprefix("v")
        file_pattern = f"bdinfo_{release_version}_{platform_info['asset']}"
        folder_path = platform_info["folder"]
        logger.debug(f"[blue]Using file pattern: {file_pattern}[/blue]")
        logger.debug(f"[blue]Target folder: {folder_path}[/blue]")

        bin_dir = Path(base_dir) / "bin" / "bdinfo" / folder_path
        bin_dir.mkdir(parents=True, exist_ok=True)
        logger.debug(f"[blue]Binary directory: {bin_dir}[/blue]")

        binary_name = "bdinfo.exe" if system == "windows" else "bdinfo"
        binary_path = bin_dir / binary_name
        logger.debug(f"[blue]Binary path: {binary_path}[/blue]")

        version_path = bin_dir / version
        binary_exists = binary_path.exists() and binary_path.is_file()
        binary_executable = system == "windows" or os.access(binary_path, os.X_OK)
        binary_valid = binary_exists and binary_executable

        def cleanup_old_version_files() -> None:
            for candidate in bin_dir.iterdir():
                if not candidate.is_file():
                    continue
                if candidate.name == version or candidate.name == binary_name:
                    continue
                if candidate.name.startswith("v"):
                    if system != "windows":
                        Path(candidate).chmod(0o644)
                    candidate.unlink()
                    logger.debug(f"[blue]Removed old version file at: {candidate}[/blue]")

        if version_path.exists() and version_path.is_file() and binary_valid:
            cleanup_old_version_files()
            logger.debug("[blue]bdinfo version is up to date[/blue]")
            return str(binary_path)

        # Remove any old binary/version markers
        if binary_path.exists() and binary_path.is_file():
            if system != "windows":
                Path(binary_path).chmod(0o600)
            binary_path.unlink()
            logger.debug(f"[blue]Removed existing binary at: {binary_path}[/blue]")

        if version_path.exists():
            if system != "windows":
                Path(version_path).chmod(0o644)
            version_path.unlink()
            logger.debug(f"[blue]Removed existing version file at: {version_path}[/blue]")

        cleanup_old_version_files()

        # Construct download URL using autobrr/go-bdinfo release asset filename.
        download_url = f"https://github.com/autobrr/go-bdinfo/releases/download/{version}/{file_pattern}"
        logger.debug(f"[blue]Download URL: {download_url}[/blue]")

        try:
            temp_archive = bin_dir / f"temp_{file_pattern}"
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                await download_verified_asset(client, download_url, temp_archive, file_pattern)
            logger.debug(f"[green]Downloaded {file_pattern}[/green]")

            # Extract archive safely and ensure temporary archive is always removed.
            try:
                if file_pattern.endswith(".zip"):
                    with zipfile.ZipFile(temp_archive, "r") as zip_ref:
                        safe_extract_zip(zip_ref, bin_dir, max_bytes=MAX_EXTRACTED_BYTES)

                elif file_pattern.endswith(".tar.gz"):
                    with tarfile.open(temp_archive, "r:gz") as tar_ref:
                        safe_extract_tar(tar_ref, bin_dir, max_bytes=MAX_EXTRACTED_BYTES)

                # If extraction created a nested directory (common for GitHub release zips),
                # search for the bdinfo executable and move it to the expected binary path.
                if not binary_path.exists():
                    binary_basename = binary_name
                    found = None
                    for p in bin_dir.rglob(binary_basename):
                        if p.is_file():
                            found = p
                            break

                    if found:
                        # Move to target location
                        shutil.move(str(found), str(binary_path))

                if system != "windows" and binary_path.exists():
                    binary_path.chmod(binary_path.stat().st_mode | stat.S_IEXEC)

                async with aiofiles.open(version_path, "w", encoding="utf-8") as version_file:
                    await version_file.write(f"autobrr/go-bdinfo version {version} installed successfully.")
                return str(binary_path)
            finally:
                try:
                    if temp_archive.exists():
                        temp_archive.unlink()
                        logger.debug(f"[blue]Removed temporary archive: {temp_archive}[/blue]")
                except Exception as unlink_exc:
                    logger.debug(f"[yellow]Warning: Failed to remove temporary archive {temp_archive}: {unlink_exc}[/yellow]")
        except httpx.RequestError as e:
            raise Exception(f"Failed to download bdinfo binary: {e}") from e
        except (zipfile.BadZipFile, tarfile.TarError) as e:
            raise Exception(f"Failed to extract bdinfo binary: {e}") from e
