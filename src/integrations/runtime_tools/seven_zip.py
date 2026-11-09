# Upload Assistant © 2026 Audionut & wastaken7 — Licensed under UAPL v1.0
import os
import platform
import shutil
import stat
import tarfile
from pathlib import Path

import aiofiles
import httpx

from src.integrations.observability.console import logger
from src.integrations.runtime_tools.download_integrity import (
    MAX_EXTRACTED_BYTES,
    download_verified_asset,
    promote_files_with_rollback,
    safe_extract_tar,
)
from src.integrations.runtime_tools.runtime_tool_paths import tool_install_dir


def _seven_zip_platform_info(system: str, machine: str) -> tuple[str, str]:
    platform_map: dict[str, dict[str, tuple[str, str]]] = {
        "windows": {
            "x86_64": ("7zr.exe", "windows/x86_64"),
            "amd64": ("7zr.exe", "windows/x86_64"),
            "x86": ("7zr.exe", "windows/x86"),
            "arm64": ("7zr.exe", "windows/arm64"),
        },
        "darwin": {
            "arm64": ("7z2601-mac.tar.xz", "macos/arm64"),
            "x86_64": ("7z2601-mac.tar.xz", "macos/x86_64"),
            "amd64": ("7z2601-mac.tar.xz", "macos/x86_64"),
        },
        "linux": {
            "x86_64": ("7z2601-linux-x64.tar.xz", "linux/amd64"),
            "amd64": ("7z2601-linux-x64.tar.xz", "linux/amd64"),
            "arm64": ("7z2601-linux-arm64.tar.xz", "linux/arm64"),
            "aarch64": ("7z2601-linux-arm64.tar.xz", "linux/arm64"),
            "arm": ("7z2601-linux-arm.tar.xz", "linux/arm"),
            "armv7l": ("7z2601-linux-arm.tar.xz", "linux/arm"),
            "armv6l": ("7z2601-linux-arm.tar.xz", "linux/arm"),
        },
    }
    platform_info = platform_map.get(system, {}).get(machine)
    if platform_info is None:
        raise Exception(f"Unsupported platform for 7z: {system} {machine}")
    return platform_info


def _seven_zip_paths(
    base_dir: str | Path,
    folder_path: str,
    system: str,
    version: str,
) -> tuple[Path, str, Path, Path]:
    bin_dir = tool_install_dir(base_dir, "7z", folder_path)
    binary_name = "7zr.exe" if system == "windows" else "7zz"
    binary_path = bin_dir / binary_name
    return bin_dir, binary_name, binary_path, bin_dir / version


def _seven_zip_version_markers(bin_dir: Path, binary_path: Path) -> list[Path]:
    return [
        candidate
        for candidate in bin_dir.iterdir()
        if candidate.is_file()
        and candidate != binary_path
        and not candidate.name.startswith("temp_")
    ]


def _seven_zip_binary_valid(system: str, binary_path: Path) -> bool:
    if not binary_path.exists() or not binary_path.is_file():
        return False
    return system == "windows" or os.access(binary_path, os.X_OK)


def _seven_zip_installation_current(
    system: str,
    binary_path: Path,
    version_path: Path,
    version_markers: list[Path],
) -> bool:
    if not version_path.exists() or not version_path.is_file():
        return False
    if not _seven_zip_binary_valid(system, binary_path):
        return False
    return version_markers == [version_path]


def _seven_zip_download_url(version: str, file_pattern: str) -> str:
    return (
        "https://github.com/ip7z/7zip/releases/download/"
        f"{version}/{file_pattern}"
    )


def _seven_zip_integrity_key(version: str, file_pattern: str) -> str:
    if file_pattern.endswith(".exe"):
        return f"{version}/{file_pattern}"
    return file_pattern


def _prepare_seven_zip_staging(staging: Path) -> None:
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()


async def _download_seven_zip(
    download_url: str,
    temp_file: Path,
    version: str,
    file_pattern: str,
) -> None:
    integrity_key = _seven_zip_integrity_key(version, file_pattern)
    async with httpx.AsyncClient(
        timeout=60.0, follow_redirects=True
    ) as client:
        await download_verified_asset(
            client,
            download_url,
            temp_file,
            integrity_key,
        )


def _unique_seven_zip_binary(staging: Path, binary_name: str) -> Path:
    candidates = [
        candidate
        for candidate in staging.rglob(binary_name)
        if candidate.is_file()
    ]
    if len(candidates) != 1:
        raise RuntimeError(
            "Downloaded archive must contain exactly one "
            f"{binary_name} executable"
        )
    return candidates[0]


def _stage_seven_zip_binary(
    temp_file: Path,
    staging: Path,
    binary_name: str,
    file_pattern: str,
) -> Path:
    if file_pattern.endswith(".exe"):
        staged_binary = staging / binary_name
        shutil.move(str(temp_file), str(staged_binary))
        return staged_binary
    with tarfile.open(temp_file, "r:xz") as tar_ref:
        safe_extract_tar(tar_ref, staging, max_bytes=MAX_EXTRACTED_BYTES)
    return _unique_seven_zip_binary(staging, binary_name)


def _make_seven_zip_executable(staged_binary: Path, system: str) -> None:
    if system == "windows":
        return
    staged_binary.chmod(staged_binary.stat().st_mode | stat.S_IEXEC)


async def _stage_seven_zip_version(staging: Path, version: str) -> Path:
    staged_version = staging / version
    async with aiofiles.open(
        staged_version,
        "w",
        encoding="utf-8",
    ) as version_file:
        await version_file.write(
            f"7-Zip version {version} installed successfully."
        )
    return staged_version


def _seven_zip_stale_markers(
    version_markers: list[Path], version_path: Path
) -> list[Path]:
    return [
        candidate for candidate in version_markers if candidate != version_path
    ]


def _promote_seven_zip(
    bin_dir: Path,
    staged_binary: Path,
    binary_path: Path,
    staged_version: Path,
    version_path: Path,
    version_markers: list[Path],
) -> None:
    promote_files_with_rollback(
        [(staged_binary, binary_path), (staged_version, version_path)],
        bin_dir / ".7z-backup",
        remove_targets=_seven_zip_stale_markers(
            version_markers,
            version_path,
        ),
    )


def _cleanup_seven_zip(temp_file: Path, staging: Path) -> None:
    temp_file.unlink(missing_ok=True)
    shutil.rmtree(staging, ignore_errors=True)


async def _install_seven_zip(
    *,
    system: str,
    version: str,
    file_pattern: str,
    bin_dir: Path,
    binary_name: str,
    binary_path: Path,
    version_path: Path,
    version_markers: list[Path],
    staging: Path,
) -> str:
    download_url = _seven_zip_download_url(version, file_pattern)
    logger.debug(f"[blue]7-Zip Download URL: {download_url}[/blue]")
    temp_file = bin_dir / f"temp_{file_pattern}"
    try:
        await _download_seven_zip(
            download_url,
            temp_file,
            version,
            file_pattern,
        )
        logger.debug(
            f"[green]Downloaded 7-Zip package: {file_pattern}[/green]"
        )
        staged_binary = _stage_seven_zip_binary(
            temp_file,
            staging,
            binary_name,
            file_pattern,
        )
        _make_seven_zip_executable(staged_binary, system)
        staged_version = await _stage_seven_zip_version(staging, version)
        _promote_seven_zip(
            bin_dir,
            staged_binary,
            binary_path,
            staged_version,
            version_path,
            version_markers,
        )
        return str(binary_path)
    except Exception as error:
        raise Exception(f"Failed to setup 7z binary: {error}") from error
    finally:
        _cleanup_seven_zip(temp_file, staging)


class SevenZipBinaryManager:
    """Download 7-Zip binaries for the host architecture."""

    @staticmethod
    async def ensure_7z_binary(
        base_dir: str | Path,
        version: str = "26.01",
    ) -> str:
        system = platform.system().lower()
        machine = platform.machine().lower()
        logger.debug(
            f"[blue]7-Zip: Detected system: {system}, "
            f"architecture: {machine}[/blue]"
        )
        file_pattern, folder_path = _seven_zip_platform_info(system, machine)
        bin_dir, binary_name, binary_path, version_path = _seven_zip_paths(
            base_dir,
            folder_path,
            system,
            version,
        )
        version_markers = _seven_zip_version_markers(bin_dir, binary_path)
        if _seven_zip_installation_current(
            system,
            binary_path,
            version_path,
            version_markers,
        ):
            logger.debug("[blue]7-Zip binary is up to date[/blue]")
            return str(binary_path)
        logger.info(
            "[yellow]Binary '7z' not found. Attempting to download "
            "automatically...[/yellow]"
        )
        staging = bin_dir / ".7z-staging"
        _prepare_seven_zip_staging(staging)
        return await _install_seven_zip(
            system=system,
            version=version,
            file_pattern=file_pattern,
            bin_dir=bin_dir,
            binary_name=binary_name,
            binary_path=binary_path,
            version_path=version_path,
            version_markers=version_markers,
            staging=staging,
        )
