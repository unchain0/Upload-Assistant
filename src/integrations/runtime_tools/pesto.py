# Upload Assistant © 2026 Audionut & wastaken7 — Licensed under UAPL v1.0
import os
import platform
import stat
from pathlib import Path

import aiofiles
import httpx

from src.integrations.observability.console import logger
from src.integrations.runtime_tools.download_integrity import (
    download_verified_asset,
    promote_files_with_rollback,
)
from src.integrations.runtime_tools.runtime_tool_paths import tool_install_dir


def _pesto_platform_info(system: str, machine: str) -> tuple[str, str]:
    platform_map: dict[str, dict[str, tuple[str, str]]] = {
        "windows": {
            "x86_64": ("pesto-windows-x86_64.exe", "windows/x86_64"),
            "amd64": ("pesto-windows-x86_64.exe", "windows/x86_64"),
        },
        "linux": {
            "x86_64": ("pesto-linux-x86_64", "linux/amd64"),
            "amd64": ("pesto-linux-x86_64", "linux/amd64"),
        },
    }
    platform_info = platform_map.get(system, {}).get(machine)
    if platform_info is None:
        raise Exception(f"Unsupported platform for Pesto: {system} {machine}")
    return platform_info


def _pesto_paths(
    base_dir: str | Path, folder_path: str, system: str, version: str
) -> tuple[Path, str, Path, Path]:
    bin_dir = tool_install_dir(base_dir, "pesto", folder_path)
    binary_name = "pesto.exe" if system == "windows" else "pesto"
    binary_path = bin_dir / binary_name
    return bin_dir, binary_name, binary_path, bin_dir / version


def _pesto_version_markers(bin_dir: Path) -> list[Path]:
    return [
        candidate
        for candidate in bin_dir.glob("pesto-v*")
        if candidate.is_file()
    ]


def _pesto_binary_valid(system: str, binary_path: Path) -> bool:
    if not binary_path.exists() or not binary_path.is_file():
        return False
    return system == "windows" or os.access(binary_path, os.X_OK)


def _pesto_installation_current(
    system: str,
    binary_path: Path,
    version_path: Path,
    version_markers: list[Path],
) -> bool:
    if not version_path.exists() or not version_path.is_file():
        return False
    if not _pesto_binary_valid(system, binary_path):
        return False
    return version_markers == [version_path]


def _pesto_download_url(version: str, file_pattern: str) -> str:
    return (
        "https://github.com/franzopl/pesto/releases/download/"
        f"{version}/{file_pattern}"
    )


async def _download_pesto(
    download_url: str,
    temp_file: Path,
    version: str,
    file_pattern: str,
) -> None:
    async with httpx.AsyncClient(
        timeout=60.0, follow_redirects=True
    ) as client:
        await download_verified_asset(
            client,
            download_url,
            temp_file,
            f"{version}/{file_pattern}",
        )


def _stage_pesto_binary(
    temp_file: Path, bin_dir: Path, binary_name: str, system: str
) -> Path:
    staged_binary = bin_dir / f".{binary_name}.staged"
    temp_file.replace(staged_binary)
    if system != "windows":
        staged_binary.chmod(staged_binary.stat().st_mode | stat.S_IEXEC)
    return staged_binary


async def _stage_pesto_version(bin_dir: Path, version: str) -> Path:
    staged_version = bin_dir / f".{version}.staged"
    async with aiofiles.open(
        staged_version, "w", encoding="utf-8"
    ) as version_file:
        await version_file.write(
            f"Pesto version {version} installed successfully."
        )
    return staged_version


def _pesto_stale_markers(
    version_markers: list[Path], version_path: Path
) -> list[Path]:
    return [
        candidate for candidate in version_markers if candidate != version_path
    ]


def _promote_pesto(
    bin_dir: Path,
    staged_binary: Path,
    binary_path: Path,
    staged_version: Path,
    version_path: Path,
    version_markers: list[Path],
) -> None:
    promote_files_with_rollback(
        [(staged_binary, binary_path), (staged_version, version_path)],
        bin_dir / ".pesto-backup",
        remove_targets=_pesto_stale_markers(version_markers, version_path),
    )


def _cleanup_pesto_staging(
    temp_file: Path, bin_dir: Path, binary_name: str, version: str
) -> None:
    temp_file.unlink(missing_ok=True)
    (bin_dir / f".{binary_name}.staged").unlink(missing_ok=True)
    (bin_dir / f".{version}.staged").unlink(missing_ok=True)


async def _install_pesto(
    *,
    system: str,
    version: str,
    file_pattern: str,
    bin_dir: Path,
    binary_name: str,
    binary_path: Path,
    version_path: Path,
    version_markers: list[Path],
) -> str:
    download_url = _pesto_download_url(version, file_pattern)
    logger.debug(f"[blue]Pesto Download URL: {download_url}[/blue]")
    temp_file = bin_dir / f"temp_{file_pattern}"
    try:
        await _download_pesto(download_url, temp_file, version, file_pattern)
        logger.debug(
            f"[green]Downloaded Pesto package: {file_pattern}[/green]"
        )
        staged_binary = _stage_pesto_binary(
            temp_file, bin_dir, binary_name, system
        )
        staged_version = await _stage_pesto_version(bin_dir, version)
        _promote_pesto(
            bin_dir,
            staged_binary,
            binary_path,
            staged_version,
            version_path,
            version_markers,
        )
        return str(binary_path)
    except Exception as error:
        raise Exception(f"Failed to setup Pesto binary: {error}") from error
    finally:
        _cleanup_pesto_staging(temp_file, bin_dir, binary_name, version)


class PestoBinaryManager:
    """Download Pesto binaries for the host architecture."""

    @staticmethod
    async def ensure_pesto_binary(
        base_dir: str | Path, version: str = "pesto-v0.6.0"
    ) -> str:
        system = platform.system().lower()
        machine = platform.machine().lower()
        logger.debug(
            f"[blue]Pesto: Detected system: {system}, architecture: {machine}[/blue]"
        )
        file_pattern, folder_path = _pesto_platform_info(system, machine)
        bin_dir, binary_name, binary_path, version_path = _pesto_paths(
            base_dir, folder_path, system, version
        )
        version_markers = _pesto_version_markers(bin_dir)
        if _pesto_installation_current(
            system, binary_path, version_path, version_markers
        ):
            logger.debug("[blue]Pesto binary is up to date[/blue]")
            return str(binary_path)
        logger.info(
            "[yellow]Binary 'pesto' not found. Attempting to download "
            "automatically...[/yellow]"
        )
        return await _install_pesto(
            system=system,
            version=version,
            file_pattern=file_pattern,
            bin_dir=bin_dir,
            binary_name=binary_name,
            binary_path=binary_path,
            version_path=version_path,
            version_markers=version_markers,
        )
