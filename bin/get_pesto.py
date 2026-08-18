# Upload Assistant © 2026 Audionut & wastaken7 — Licensed under UAPL v1.0
import os
import platform
import stat
from pathlib import Path

import aiofiles
import httpx

from bin.download_integrity import download_verified_asset, promote_files_with_rollback
from bin.runtime_tool_paths import tool_install_dir

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


class PestoBinaryManager:
    """Download Pesto binaries for the host architecture."""

    @staticmethod
    async def ensure_pesto_binary(base_dir: str | Path, version: str = "pesto-v0.6.0") -> str:
        system = platform.system().lower()
        machine = platform.machine().lower()
        logger.debug(f"[blue]Pesto: Detected system: {system}, architecture: {machine}[/blue]")

        platform_map: dict[str, dict[str, dict[str, str]]] = {
            "windows": {
                "x86_64": {"file": "pesto-windows-x86_64.exe", "folder": "windows/x86_64"},
                "amd64": {"file": "pesto-windows-x86_64.exe", "folder": "windows/x86_64"},
            },
            "linux": {
                "x86_64": {"file": "pesto-linux-x86_64", "folder": "linux/amd64"},
                "amd64": {"file": "pesto-linux-x86_64", "folder": "linux/amd64"},
            },
        }

        if system not in platform_map or machine not in platform_map[system]:
            raise Exception(f"Unsupported platform for Pesto: {system} {machine}")

        platform_info = platform_map[system][machine]
        file_pattern = platform_info["file"]
        folder_path = platform_info["folder"]

        bin_dir = tool_install_dir(base_dir, "pesto", folder_path)

        binary_name = "pesto.exe" if system == "windows" else "pesto"
        binary_path = bin_dir / binary_name
        version_path = bin_dir / version

        binary_exists = binary_path.exists() and binary_path.is_file()
        binary_executable = system == "windows" or os.access(binary_path, os.X_OK)
        binary_valid = binary_exists and binary_executable
        version_markers = [candidate for candidate in bin_dir.glob("pesto-v*") if candidate.is_file()]

        if version_path.exists() and version_path.is_file() and binary_valid and version_markers == [version_path]:
            logger.debug("[blue]Pesto binary is up to date[/blue]")
            return str(binary_path)

        logger.info("[yellow]Binary 'pesto' not found. Attempting to download automatically...[/yellow]")

        download_url = f"https://github.com/franzopl/pesto/releases/download/{version}/{file_pattern}"
        logger.debug(f"[blue]Pesto Download URL: {download_url}[/blue]")

        temp_file = bin_dir / f"temp_{file_pattern}"
        try:
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                await download_verified_asset(client, download_url, temp_file, f"{version}/{file_pattern}")

            logger.debug(f"[green]Downloaded Pesto package: {file_pattern}[/green]")
            staged_binary = bin_dir / f".{binary_name}.staged"
            temp_file.replace(staged_binary)
            if system != "windows":
                staged_binary.chmod(staged_binary.stat().st_mode | stat.S_IEXEC)

            staged_version = bin_dir / f".{version}.staged"
            async with aiofiles.open(staged_version, "w", encoding="utf-8") as version_file:
                await version_file.write(f"Pesto version {version} installed successfully.")
            stale_markers = [candidate for candidate in version_markers if candidate != version_path]
            promote_files_with_rollback(
                [(staged_binary, binary_path), (staged_version, version_path)],
                bin_dir / ".pesto-backup",
                remove_targets=stale_markers,
            )

            return str(binary_path)

        except Exception as e:
            raise Exception(f"Failed to setup Pesto binary: {e}") from e
        finally:
            temp_file.unlink(missing_ok=True)
            (bin_dir / f".{binary_name}.staged").unlink(missing_ok=True)
            (bin_dir / f".{version}.staged").unlink(missing_ok=True)
