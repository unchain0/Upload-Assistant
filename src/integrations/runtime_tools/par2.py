# Upload Assistant © 2026 Audionut & wastaken7 — Licensed under UAPL v1.0
import os
import platform
import shutil
import stat
import zipfile
from pathlib import Path

import aiofiles
import httpx

from src.integrations.observability.console import logger
from src.integrations.runtime_tools.download_integrity import (
    MAX_EXTRACTED_BYTES,
    download_verified_asset,
    promote_files_with_rollback,
    safe_extract_zip,
)
from src.integrations.runtime_tools.runtime_tool_paths import tool_install_dir


def _par2_platform_info(
    system: str, machine: str, version: str
) -> tuple[str, str]:
    version_number = version.lstrip("v")
    platform_map: dict[str, dict[str, tuple[str, str]]] = {
        "windows": {
            "x86_64": (
                f"par2cmdline-turbo-{version_number}-win-x64.zip",
                "windows/x86_64",
            ),
            "amd64": (
                f"par2cmdline-turbo-{version_number}-win-x64.zip",
                "windows/x86_64",
            ),
            "arm64": (
                f"par2cmdline-turbo-{version_number}-win-arm64.zip",
                "windows/arm64",
            ),
        },
        "darwin": {
            "arm64": (
                f"par2cmdline-turbo-{version_number}-macos-arm64.zip",
                "macos/arm64",
            ),
            "x86_64": (
                f"par2cmdline-turbo-{version_number}-macos-amd64.zip",
                "macos/x86_64",
            ),
            "amd64": (
                f"par2cmdline-turbo-{version_number}-macos-amd64.zip",
                "macos/x86_64",
            ),
        },
        "linux": {
            "x86_64": (
                f"par2cmdline-turbo-{version_number}-linux-amd64.zip",
                "linux/amd64",
            ),
            "amd64": (
                f"par2cmdline-turbo-{version_number}-linux-amd64.zip",
                "linux/amd64",
            ),
            "arm64": (
                f"par2cmdline-turbo-{version_number}-linux-arm64.zip",
                "linux/arm64",
            ),
            "aarch64": (
                f"par2cmdline-turbo-{version_number}-linux-arm64.zip",
                "linux/arm64",
            ),
        },
    }
    platform_info = platform_map.get(system, {}).get(machine)
    if platform_info is None:
        raise Exception(f"Unsupported platform for PAR2: {system} {machine}")
    return platform_info


def _par2_paths(
    base_dir: str | Path, folder_path: str, system: str, version: str
) -> tuple[Path, str, Path, Path]:
    bin_dir = tool_install_dir(base_dir, "par2", folder_path)
    binary_name = "par2.exe" if system == "windows" else "par2"
    binary_path = bin_dir / binary_name
    return bin_dir, binary_name, binary_path, bin_dir / version


def _par2_binary_valid(system: str, binary_path: Path) -> bool:
    if not binary_path.exists() or not binary_path.is_file():
        return False
    return system == "windows" or os.access(binary_path, os.X_OK)


def _par2_installation_current(
    system: str, binary_path: Path, version_path: Path
) -> bool:
    if not version_path.exists() or not version_path.is_file():
        return False
    return _par2_binary_valid(system, binary_path)


def _par2_download_url(version: str, file_pattern: str) -> str:
    return (
        "https://github.com/animetosho/par2cmdline-turbo/releases/download/"
        f"{version}/{file_pattern}"
    )


def _prepare_par2_staging(staging: Path) -> None:
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()


async def _download_par2(
    download_url: str, temp_file: Path, file_pattern: str
) -> None:
    async with httpx.AsyncClient(
        timeout=60.0, follow_redirects=True
    ) as client:
        await download_verified_asset(
            client, download_url, temp_file, file_pattern
        )


def _extract_par2_binary(
    temp_file: Path, staging: Path, binary_name: str
) -> Path:
    with zipfile.ZipFile(temp_file, "r") as zip_ref:
        safe_extract_zip(zip_ref, staging, max_bytes=MAX_EXTRACTED_BYTES)
    candidates = [
        candidate
        for candidate in staging.rglob(binary_name)
        if candidate.is_file()
    ]
    if len(candidates) != 1:
        raise RuntimeError(
            f"Downloaded archive must contain exactly one {binary_name} executable"
        )
    return candidates[0]


def _make_par2_executable(staged_binary: Path, system: str) -> None:
    if system == "windows":
        return
    staged_binary.chmod(staged_binary.stat().st_mode | stat.S_IEXEC)


async def _stage_par2_version(staging: Path, version: str) -> Path:
    staged_version = staging / version
    async with aiofiles.open(
        staged_version, "w", encoding="utf-8"
    ) as version_file:
        await version_file.write(
            f"PAR2 version {version} installed successfully."
        )
    return staged_version


def _par2_stale_markers(bin_dir: Path, version_path: Path) -> list[Path]:
    return [
        candidate
        for candidate in bin_dir.iterdir()
        if candidate.is_file()
        and candidate.name.startswith("v")
        and candidate != version_path
    ]


def _promote_par2(
    bin_dir: Path,
    staged_binary: Path,
    binary_path: Path,
    staged_version: Path,
    version_path: Path,
) -> None:
    promote_files_with_rollback(
        [(staged_binary, binary_path), (staged_version, version_path)],
        bin_dir / ".par2-backup",
        remove_targets=_par2_stale_markers(bin_dir, version_path),
    )


def _cleanup_par2(temp_file: Path, staging: Path) -> None:
    temp_file.unlink(missing_ok=True)
    shutil.rmtree(staging, ignore_errors=True)


async def _install_par2(
    *,
    system: str,
    version: str,
    file_pattern: str,
    bin_dir: Path,
    binary_name: str,
    binary_path: Path,
    version_path: Path,
) -> str:
    download_url = _par2_download_url(version, file_pattern)
    logger.debug(f"[blue]PAR2 Download URL: {download_url}[/blue]")
    temp_file = bin_dir / f"temp_{file_pattern}"
    staging = bin_dir / ".par2-staging"
    try:
        _prepare_par2_staging(staging)
        await _download_par2(download_url, temp_file, file_pattern)
        logger.debug(f"[green]Downloaded PAR2 package: {file_pattern}[/green]")
        staged_binary = _extract_par2_binary(temp_file, staging, binary_name)
        _make_par2_executable(staged_binary, system)
        staged_version = await _stage_par2_version(staging, version)
        _promote_par2(
            bin_dir,
            staged_binary,
            binary_path,
            staged_version,
            version_path,
        )
        return str(binary_path)
    except Exception as error:
        raise Exception(f"Failed to setup PAR2 binary: {error}") from error
    finally:
        _cleanup_par2(temp_file, staging)


class Par2BinaryManager:
    """Download par2cmdline-turbo binaries for the host architecture."""

    @staticmethod
    async def ensure_par2_binary(
        base_dir: str | Path, version: str = "v1.4.0"
    ) -> str:
        system = platform.system().lower()
        machine = platform.machine().lower()
        logger.debug(
            f"[blue]PAR2: Detected system: {system}, architecture: {machine}[/blue]"
        )
        file_pattern, folder_path = _par2_platform_info(
            system, machine, version
        )
        bin_dir, binary_name, binary_path, version_path = _par2_paths(
            base_dir, folder_path, system, version
        )
        if _par2_installation_current(system, binary_path, version_path):
            logger.debug("[blue]PAR2 binary is up to date[/blue]")
            return str(binary_path)
        logger.info(
            "[yellow]Binary 'par2' not found. Attempting to download "
            "automatically...[/yellow]"
        )
        return await _install_par2(
            system=system,
            version=version,
            file_pattern=file_pattern,
            bin_dir=bin_dir,
            binary_name=binary_name,
            binary_path=binary_path,
            version_path=version_path,
        )
