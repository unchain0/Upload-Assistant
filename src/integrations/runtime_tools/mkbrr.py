# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import os
import platform
import shutil
import stat
import tarfile
import zipfile
from pathlib import Path
from typing import ClassVar

import aiofiles
import httpx

from src.integrations.observability.console import logger
from src.integrations.runtime_tools.download_integrity import (
    MAX_EXTRACTED_BYTES,
    download_verified_asset,
    download_verified_asset_sync,
    promote_files_with_rollback,
    safe_extract_tar,
    safe_extract_zip,
)
from src.integrations.runtime_tools.runtime_tool_paths import tool_install_dir, trusted_executable


class MkbrrBinaryManager:
    platform_map: ClassVar[dict[str, dict[str, dict[str, str]]]] = {
        "windows": {
            "x86_64": {"file": "windows_x86_64.zip", "folder": "windows/x86_64"},
            "amd64": {"file": "windows_x86_64.zip", "folder": "windows/x86_64"},
            "arm64": {"file": "windows_x86_64.zip", "folder": "windows/x86_64"},
            "aarch64": {"file": "windows_x86_64.zip", "folder": "windows/x86_64"},
        },
        "darwin": {
            "arm64": {"file": "darwin_arm64.tar.gz", "folder": "macos/arm64"},
            "x86_64": {"file": "darwin_x86_64.tar.gz", "folder": "macos/x86_64"},
            "amd64": {"file": "darwin_x86_64.tar.gz", "folder": "macos/x86_64"},
        },
        "linux": {
            "x86_64": {"file": "linux_x86_64.tar.gz", "folder": "linux/amd64"},
            "amd64": {"file": "linux_x86_64.tar.gz", "folder": "linux/amd64"},
            "arm64": {"file": "linux_arm64.tar.gz", "folder": "linux/arm64"},
            "aarch64": {"file": "linux_arm64.tar.gz", "folder": "linux/arm64"},
            "armv7l": {"file": "linux_arm.tar.gz", "folder": "linux/arm"},
            "armv6l": {"file": "linux_arm.tar.gz", "folder": "linux/armv6"},
            "arm": {"file": "linux_arm.tar.gz", "folder": "linux/arm"},
        },
        "freebsd": {
            "x86_64": {"file": "freebsd_x86_64.tar.gz", "folder": "freebsd/x86_64"},
            "amd64": {"file": "freebsd_x86_64.tar.gz", "folder": "freebsd/x86_64"},
        },
    }

    @staticmethod
    def find_existing_binary(base_dir: str | Path, version: str | None = None) -> str | None:
        """Return an existing mkbrr binary, version-checking the managed cache when requested."""
        system = platform.system().lower()
        machine = platform.machine().lower()
        binary_name = "mkbrr.exe" if system == "windows" else "mkbrr"
        bin_root = Path(base_dir) / "bin"
        platform_info = MkbrrBinaryManager.platform_map.get(system, {}).get(machine)
        candidates = [bin_root / binary_name, bin_root / "mkbrr" / binary_name]
        if platform_info and (version is None or (bin_root / "mkbrr" / platform_info["folder"] / version).is_file()):
            candidates.append(bin_root / "mkbrr" / platform_info["folder"] / binary_name)

        for binary_path in candidates:
            if trusted_executable(binary_path):
                logger.debug(f"[blue]Using existing mkbrr binary: {binary_path}[/blue]")
                return str(binary_path)

        return shutil.which("mkbrr")

    @staticmethod
    async def ensure_mkbrr_binary(base_dir: str | Path, version: str) -> str:
        existing_binary = MkbrrBinaryManager.find_existing_binary(base_dir, version)
        if existing_binary:
            return existing_binary

        system = platform.system().lower()
        machine = platform.machine().lower()
        logger.debug(f"[blue]Detected system: {system}, architecture: {machine}[/blue]")

        platform_info = MkbrrBinaryManager.platform_map.get(system, {}).get(machine)
        if not platform_info:
            raise Exception(f"Unsupported platform: {system} {machine}")

        file_pattern = platform_info["file"]
        folder_path = platform_info["folder"]
        logger.debug(f"[blue]Using file pattern: {file_pattern}[/blue]")
        logger.debug(f"[blue]Target folder: {folder_path}[/blue]")

        bin_dir = tool_install_dir(base_dir, "mkbrr", folder_path)
        logger.debug(f"[blue]Binary directory: {bin_dir}[/blue]")

        binary_name = "mkbrr.exe" if system == "windows" else "mkbrr"
        binary_path = bin_dir / binary_name
        logger.debug(f"[blue]Binary path: {binary_path}[/blue]")

        version_path = bin_dir / version
        binary_exists = binary_path.exists() and binary_path.is_file()
        binary_executable = system == "windows" or os.access(binary_path, os.X_OK)
        binary_valid = binary_exists and binary_executable
        version_markers = [candidate for candidate in bin_dir.glob("v*") if candidate.is_file()]
        if version_path.exists() and version_path.is_file() and binary_valid and version_markers == [version_path]:
            logger.debug("[blue]mkbrr version is up to date[/blue]")
            return str(binary_path)

        download_url = f"https://github.com/autobrr/mkbrr/releases/download/{version}/mkbrr_{version[1:]}_{file_pattern}"
        logger.debug(f"[blue]Download URL: {download_url}[/blue]")

        temp_archive = bin_dir / f"temp_{file_pattern}"
        staging = bin_dir / ".mkbrr-staging"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir()
        try:
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                await download_verified_asset(client, download_url, temp_archive, f"mkbrr_{version[1:]}_{file_pattern}")
            logger.debug(f"[green]Downloaded {file_pattern}[/green]")
            if file_pattern.endswith(".zip"):
                with zipfile.ZipFile(temp_archive, "r") as zip_ref:
                    safe_extract_zip(zip_ref, staging, max_bytes=MAX_EXTRACTED_BYTES)

            elif file_pattern.endswith(".tar.gz"):
                with tarfile.open(temp_archive, "r:gz") as tar_ref:
                    safe_extract_tar(tar_ref, staging, max_bytes=MAX_EXTRACTED_BYTES)

            candidates = [candidate for candidate in staging.rglob(binary_name) if candidate.is_file()]
            if len(candidates) != 1:
                raise Exception(f"Failed to extract mkbrr binary to {binary_path}")
            staged_binary = candidates[0]
            if system != "windows":
                staged_binary.chmod(staged_binary.stat().st_mode | stat.S_IEXEC)

            staged_version = staging / version
            async with aiofiles.open(staged_version, "w", encoding="utf-8") as version_file:
                await version_file.write(f"mkbrr version {version} installed successfully.")
            promote_files_with_rollback(
                [(staged_binary, binary_path), (staged_version, version_path)],
                bin_dir / ".mkbrr-backup",
                remove_targets=[candidate for candidate in version_markers if candidate != version_path],
            )
            return str(binary_path)

        except httpx.RequestError as e:
            raise Exception(f"Failed to download mkbrr binary: {e}") from e
        except (zipfile.BadZipFile, tarfile.TarError) as e:
            raise Exception(f"Failed to extract mkbrr binary: {e}") from e
        finally:
            temp_archive.unlink(missing_ok=True)
            shutil.rmtree(staging, ignore_errors=True)

    @staticmethod
    def download_mkbrr_for_docker(base_dir: str | Path = ".", version: str = "v1.18.0") -> str:
        """Download mkbrr binary for Docker/Linux - synchronous version."""
        system = platform.system().lower()
        machine = platform.machine().lower()
        logger.info(f"Detected system: {system}, architecture: {machine}", extra={"markup": False})

        if system != "linux":
            raise Exception(f"This script is for Docker/Linux only, detected: {system}")

        platform_info = MkbrrBinaryManager.platform_map["linux"].get(machine)
        if not platform_info:
            raise Exception(f"Unsupported architecture: {machine}")

        file_pattern = platform_info["file"]
        folder_path = platform_info["folder"]

        logger.info(f"Using file pattern: {file_pattern}", extra={"markup": False})
        logger.info(f"Target folder: {folder_path}", extra={"markup": False})

        bin_dir = Path(base_dir) / "bin" / "mkbrr" / folder_path
        bin_dir.mkdir(parents=True, exist_ok=True)
        binary_path = bin_dir / "mkbrr"
        version_path = bin_dir / version

        binary_exists = binary_path.exists() and binary_path.is_file()
        binary_executable = os.access(binary_path, os.X_OK)
        binary_valid = binary_exists and binary_executable
        version_markers = [candidate for candidate in bin_dir.glob("v*") if candidate.is_file()]
        if version_path.exists() and version_path.is_file() and binary_valid and version_markers == [version_path]:
            logger.info(f"mkbrr {version} already exists, skipping download", extra={"markup": False})
            return str(binary_path)

        download_url = f"https://github.com/autobrr/mkbrr/releases/download/{version}/mkbrr_{version[1:]}_{file_pattern}"
        logger.info(f"Downloading from: {download_url}", extra={"markup": False})

        temp_archive = bin_dir / f"temp_{file_pattern}"
        staging = bin_dir / ".mkbrr-staging"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir()
        try:
            download_verified_asset_sync(download_url, temp_archive, f"mkbrr_{version[1:]}_{file_pattern}")

            logger.info(f"Downloaded {file_pattern}", extra={"markup": False})
            with tarfile.open(temp_archive, "r:gz") as tar_ref:
                safe_extract_tar(tar_ref, staging, max_bytes=MAX_EXTRACTED_BYTES)
            candidates = [candidate for candidate in staging.rglob("mkbrr") if candidate.is_file()]
            if len(candidates) != 1:
                raise Exception(f"Failed to extract exactly one mkbrr binary for {binary_path}")
            staged_binary = candidates[0]
            staged_binary.chmod(0o700)
            staged_version = staging / version
            staged_version.write_text(f"mkbrr version {version} installed successfully.", encoding="utf-8")
            promote_files_with_rollback(
                [(staged_binary, binary_path), (staged_version, version_path)],
                bin_dir / ".mkbrr-backup",
                remove_targets=[candidate for candidate in version_markers if candidate != version_path],
            )
            logger.info(f"mkbrr binary ready at: {binary_path}", extra={"markup": False})
            return str(binary_path)

        except Exception as e:
            raise Exception(f"Error downloading mkbrr: {e}") from e
        finally:
            temp_archive.unlink(missing_ok=True)
            shutil.rmtree(staging, ignore_errors=True)
