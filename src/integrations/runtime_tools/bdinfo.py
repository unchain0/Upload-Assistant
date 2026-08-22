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

from src.integrations.observability.console import logger
from src.integrations.runtime_tools.download_integrity import (
    MAX_EXTRACTED_BYTES,
    download_verified_asset,
    promote_files_with_rollback,
    safe_extract_tar,
    safe_extract_zip,
)
from src.integrations.runtime_tools.runtime_tool_paths import tool_install_dir

_PLATFORM_MAP: dict[str, dict[str, dict[str, str]]] = {
    "windows": {
        "x86_64": {
            "asset": "windows_amd64.zip",
            "folder": "windows/x86_64",
        },
        "amd64": {
            "asset": "windows_amd64.zip",
            "folder": "windows/x86_64",
        },
    },
    "darwin": {
        "arm64": {
            "asset": "darwin_arm64.tar.gz",
            "folder": "macos/arm64",
        },
        "x86_64": {
            "asset": "darwin_amd64.tar.gz",
            "folder": "macos/x86_64",
        },
        "amd64": {
            "asset": "darwin_amd64.tar.gz",
            "folder": "macos/x86_64",
        },
    },
    "linux": {
        "x86_64": {
            "asset": "linux_amd64.tar.gz",
            "folder": "linux/amd64",
        },
        "amd64": {
            "asset": "linux_amd64.tar.gz",
            "folder": "linux/amd64",
        },
        "arm64": {
            "asset": "linux_arm64.tar.gz",
            "folder": "linux/arm64",
        },
        "aarch64": {
            "asset": "linux_arm64.tar.gz",
            "folder": "linux/arm64",
        },
        "armv7l": {
            "asset": "linux_arm.tar.gz",
            "folder": "linux/arm",
        },
        "armv6l": {
            "asset": "linux_arm.tar.gz",
            "folder": "linux/arm",
        },
        "arm": {
            "asset": "linux_arm.tar.gz",
            "folder": "linux/arm",
        },
    },
}


def _detected_platform() -> tuple[str, str]:
    system = platform.system().lower()
    machine = platform.machine().lower()
    logger.debug(
        f"[blue]Detected system: {system}, architecture: {machine}[/blue]"
    )
    return system, machine


def _platform_info(system: str, machine: str) -> dict[str, str]:
    system_map = _PLATFORM_MAP.get(system)
    if system_map is None or machine not in system_map:
        raise Exception(f"Unsupported platform: {system} {machine}")
    return system_map[machine]


def _release_layout(
    version: str,
    platform_info: dict[str, str],
) -> tuple[str, str]:
    release_version = version.removeprefix("v")
    file_pattern = f"bdinfo_{release_version}_{platform_info['asset']}"
    folder_path = platform_info["folder"]
    logger.debug(f"[blue]Using file pattern: {file_pattern}[/blue]")
    logger.debug(f"[blue]Target folder: {folder_path}[/blue]")
    return file_pattern, folder_path


def _binary_layout(
    base_dir: str | Path,
    folder_path: str,
    system: str,
    version: str,
) -> tuple[Path, str, Path, Path]:
    bin_dir = tool_install_dir(base_dir, "bdinfo", folder_path)
    logger.debug(f"[blue]Binary directory: {bin_dir}[/blue]")
    binary_name = "bdinfo.exe" if system == "windows" else "bdinfo"
    binary_path = bin_dir / binary_name
    logger.debug(f"[blue]Binary path: {binary_path}[/blue]")
    return bin_dir, binary_name, binary_path, bin_dir / version


def _binary_valid(system: str, binary_path: Path) -> bool:
    if not binary_path.is_file():
        return False
    return system == "windows" or os.access(binary_path, os.X_OK)


def _is_stale_version_file(
    candidate: Path, version: str, binary_name: str
) -> bool:
    if not candidate.is_file():
        return False
    if candidate.name in {version, binary_name}:
        return False
    return candidate.name.startswith("v")


def _remove_version_file(candidate: Path, system: str) -> None:
    if system != "windows":
        candidate.chmod(0o644)
    candidate.unlink()
    logger.debug(f"[blue]Removed old version file at: {candidate}[/blue]")


def _cleanup_old_version_files(
    bin_dir: Path,
    version: str,
    binary_name: str,
    system: str,
) -> None:
    for candidate in bin_dir.iterdir():
        if _is_stale_version_file(candidate, version, binary_name):
            _remove_version_file(candidate, system)


def _cached_binary(
    bin_dir: Path,
    version: str,
    binary_name: str,
    binary_path: Path,
    version_path: Path,
    system: str,
) -> str | None:
    if not version_path.is_file() or not _binary_valid(system, binary_path):
        return None
    _cleanup_old_version_files(bin_dir, version, binary_name, system)
    logger.debug("[blue]bdinfo version is up to date[/blue]")
    return str(binary_path)


def _download_url(version: str, file_pattern: str) -> str:
    return (
        "https://github.com/autobrr/go-bdinfo/releases/download/"
        f"{version}/{file_pattern}"
    )


def _staging_layout(
    bin_dir: Path,
    file_pattern: str,
) -> tuple[Path, Path]:
    temp_archive = bin_dir / f"temp_{file_pattern}"
    staging = bin_dir / ".bdinfo-staging"
    return temp_archive, staging


def _prepare_staging(staging: Path) -> None:
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()


async def _download_archive(
    download_url: str,
    temp_archive: Path,
    file_pattern: str,
) -> None:
    async with httpx.AsyncClient(
        timeout=60.0,
        follow_redirects=True,
    ) as client:
        await download_verified_asset(
            client,
            download_url,
            temp_archive,
            file_pattern,
        )
    logger.debug(f"[green]Downloaded {file_pattern}[/green]")


def _extract_archive(
    temp_archive: Path,
    staging: Path,
    file_pattern: str,
) -> None:
    if file_pattern.endswith(".zip"):
        with zipfile.ZipFile(temp_archive, "r") as zip_ref:
            safe_extract_zip(
                zip_ref,
                staging,
                max_bytes=MAX_EXTRACTED_BYTES,
            )
        return
    if file_pattern.endswith(".tar.gz"):
        with tarfile.open(temp_archive, "r:gz") as tar_ref:
            safe_extract_tar(
                tar_ref,
                staging,
                max_bytes=MAX_EXTRACTED_BYTES,
            )


def _unique_staged_binary(staging: Path, binary_name: str) -> Path:
    candidates = [
        candidate
        for candidate in staging.rglob(binary_name)
        if candidate.is_file()
    ]
    if len(candidates) != 1:
        raise RuntimeError(
            "Downloaded archive does not contain the expected "
            f"{binary_name} executable"
        )
    return candidates[0]


def _make_executable(staged_binary: Path, system: str) -> None:
    if system == "windows":
        return
    staged_binary.chmod(staged_binary.stat().st_mode | stat.S_IEXEC)


async def _write_version_marker(staging: Path, version: str) -> Path:
    staged_version = staging / version
    async with aiofiles.open(
        staged_version,
        "w",
        encoding="utf-8",
    ) as version_file:
        await version_file.write(
            f"autobrr/go-bdinfo version {version} installed successfully."
        )
    return staged_version


def _is_stale_marker(candidate: Path, version_path: Path) -> bool:
    if not candidate.is_file():
        return False
    if not candidate.name.startswith("v"):
        return False
    return candidate != version_path


def _stale_markers(bin_dir: Path, version_path: Path) -> list[Path]:
    return [
        candidate
        for candidate in bin_dir.iterdir()
        if _is_stale_marker(candidate, version_path)
    ]


def _promote_install(
    bin_dir: Path,
    staged_binary: Path,
    binary_path: Path,
    staged_version: Path,
    version_path: Path,
) -> None:
    promote_files_with_rollback(
        [
            (staged_binary, binary_path),
            (staged_version, version_path),
        ],
        bin_dir / ".bdinfo-backup",
        remove_targets=_stale_markers(bin_dir, version_path),
    )


def _cleanup_temp_archive(temp_archive: Path) -> None:
    try:
        if not temp_archive.exists():
            return
        temp_archive.unlink()
        logger.debug(f"[blue]Removed temporary archive: {temp_archive}[/blue]")
    except Exception as unlink_exc:
        logger.debug(
            "[yellow]Warning: Failed to remove temporary archive "
            f"{temp_archive}: {unlink_exc}[/yellow]"
        )


async def _install_downloaded_archive(
    *,
    bin_dir: Path,
    binary_name: str,
    binary_path: Path,
    version_path: Path,
    version: str,
    system: str,
    file_pattern: str,
    temp_archive: Path,
    staging: Path,
) -> str:
    try:
        _extract_archive(temp_archive, staging, file_pattern)
        staged_binary = _unique_staged_binary(staging, binary_name)
        _make_executable(staged_binary, system)
        staged_version = await _write_version_marker(staging, version)
        _promote_install(
            bin_dir,
            staged_binary,
            binary_path,
            staged_version,
            version_path,
        )
        return str(binary_path)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        _cleanup_temp_archive(temp_archive)


async def _download_and_install(
    *,
    bin_dir: Path,
    binary_name: str,
    binary_path: Path,
    version_path: Path,
    version: str,
    system: str,
    file_pattern: str,
) -> str:
    download_url = _download_url(version, file_pattern)
    logger.debug(f"[blue]Download URL: {download_url}[/blue]")
    temp_archive, staging = _staging_layout(bin_dir, file_pattern)
    _prepare_staging(staging)
    await _download_archive(download_url, temp_archive, file_pattern)
    return await _install_downloaded_archive(
        bin_dir=bin_dir,
        binary_name=binary_name,
        binary_path=binary_path,
        version_path=version_path,
        version=version,
        system=system,
        file_pattern=file_pattern,
        temp_archive=temp_archive,
        staging=staging,
    )


class BDInfoBinaryManager:
    """Download autobrr/go-bdinfo binaries for the host architecture."""

    @staticmethod
    async def ensure_bdinfo_binary(
        base_dir: str | Path,
        version: str = "v0.3.1",
    ) -> str:
        system, machine = _detected_platform()
        platform_info = _platform_info(system, machine)
        file_pattern, folder_path = _release_layout(version, platform_info)
        bin_dir, binary_name, binary_path, version_path = _binary_layout(
            base_dir,
            folder_path,
            system,
            version,
        )
        cached = _cached_binary(
            bin_dir,
            version,
            binary_name,
            binary_path,
            version_path,
            system,
        )
        if cached is not None:
            return cached
        try:
            return await _download_and_install(
                bin_dir=bin_dir,
                binary_name=binary_name,
                binary_path=binary_path,
                version_path=version_path,
                version=version,
                system=system,
                file_pattern=file_pattern,
            )
        except httpx.RequestError as error:
            raise Exception(
                f"Failed to download bdinfo binary: {error}"
            ) from error
        except (zipfile.BadZipFile, tarfile.TarError) as error:
            raise Exception(
                f"Failed to extract bdinfo binary: {error}"
            ) from error
