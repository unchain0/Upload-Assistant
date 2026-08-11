# Upload Assistant © 2026 Audionut & wastaken7 — Licensed under UAPL v1.0
import os
import platform
import shutil
import stat
import zipfile
from pathlib import Path

import aiofiles
import httpx

from bin.download_integrity import MAX_EXTRACTED_BYTES, download_verified_asset, promote_files_with_rollback, safe_extract_zip

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


class Par2BinaryManager:
    """Download par2cmdline-turbo binaries for the host architecture."""

    @staticmethod
    async def ensure_par2_binary(base_dir: str | Path, version: str = "v1.4.0") -> str:
        system = platform.system().lower()
        machine = platform.machine().lower()
        logger.debug(f"[blue]PAR2: Detected system: {system}, architecture: {machine}[/blue]")

        # Strip 'v' from version for URLs if needed, but the tag is v1.4.0, while filenames have 1.4.0
        v_num = version.lstrip("v")

        platform_map: dict[str, dict[str, dict[str, str]]] = {
            "windows": {
                "x86_64": {"file": f"par2cmdline-turbo-{v_num}-win-x64.zip", "folder": "windows/x86_64"},
                "amd64": {"file": f"par2cmdline-turbo-{v_num}-win-x64.zip", "folder": "windows/x86_64"},
                "arm64": {"file": f"par2cmdline-turbo-{v_num}-win-arm64.zip", "folder": "windows/arm64"},
            },
            "darwin": {
                "arm64": {"file": f"par2cmdline-turbo-{v_num}-macos-arm64.zip", "folder": "macos/arm64"},
                "x86_64": {"file": f"par2cmdline-turbo-{v_num}-macos-amd64.zip", "folder": "macos/x86_64"},
                "amd64": {"file": f"par2cmdline-turbo-{v_num}-macos-amd64.zip", "folder": "macos/x86_64"},
            },
            "linux": {
                "x86_64": {"file": f"par2cmdline-turbo-{v_num}-linux-amd64.zip", "folder": "linux/amd64"},
                "amd64": {"file": f"par2cmdline-turbo-{v_num}-linux-amd64.zip", "folder": "linux/amd64"},
                "arm64": {"file": f"par2cmdline-turbo-{v_num}-linux-arm64.zip", "folder": "linux/arm64"},
                "aarch64": {"file": f"par2cmdline-turbo-{v_num}-linux-arm64.zip", "folder": "linux/arm64"},
            },
        }

        if system not in platform_map or machine not in platform_map[system]:
            raise Exception(f"Unsupported platform for PAR2: {system} {machine}")

        platform_info = platform_map[system][machine]
        file_pattern = platform_info["file"]
        folder_path = platform_info["folder"]

        bin_dir = Path(base_dir) / "bin" / "par2" / folder_path
        bin_dir.mkdir(parents=True, exist_ok=True)

        binary_name = "par2.exe" if system == "windows" else "par2"
        binary_path = bin_dir / binary_name
        version_path = bin_dir / version

        binary_exists = binary_path.exists() and binary_path.is_file()
        binary_executable = system == "windows" or os.access(binary_path, os.X_OK)
        binary_valid = binary_exists and binary_executable

        if version_path.exists() and version_path.is_file() and binary_valid:
            logger.debug("[blue]PAR2 binary is up to date[/blue]")
            return str(binary_path)

        logger.info("[yellow]Binary 'par2' not found. Attempting to download automatically...[/yellow]")

        download_url = f"https://github.com/animetosho/par2cmdline-turbo/releases/download/{version}/{file_pattern}"
        logger.debug(f"[blue]PAR2 Download URL: {download_url}[/blue]")

        try:
            temp_file = bin_dir / f"temp_{file_pattern}"
            staging = bin_dir / ".par2-staging"
            shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir()
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                await download_verified_asset(client, download_url, temp_file, file_pattern)

            logger.debug(f"[green]Downloaded PAR2 package: {file_pattern}[/green]")

            with zipfile.ZipFile(temp_file, "r") as zip_ref:
                safe_extract_zip(zip_ref, staging, max_bytes=MAX_EXTRACTED_BYTES)
            candidates = [candidate for candidate in staging.rglob(binary_name) if candidate.is_file()]
            if len(candidates) != 1:
                raise RuntimeError(f"Downloaded archive must contain exactly one {binary_name} executable")
            staged_binary = candidates[0]
            if system != "windows":
                staged_binary.chmod(staged_binary.stat().st_mode | stat.S_IEXEC)

            staged_version = staging / version
            async with aiofiles.open(staged_version, "w", encoding="utf-8") as version_file:
                await version_file.write(f"PAR2 version {version} installed successfully.")
            stale_markers = [
                candidate
                for candidate in bin_dir.iterdir()
                if candidate.is_file() and candidate.name.startswith("v") and candidate != version_path
            ]
            promote_files_with_rollback(
                [(staged_binary, binary_path), (staged_version, version_path)],
                staging / ".backup",
                remove_targets=stale_markers,
            )

            return str(binary_path)

        except Exception as e:
            raise Exception(f"Failed to setup PAR2 binary: {e}") from e
        finally:
            temp_file.unlink(missing_ok=True)
            shutil.rmtree(staging, ignore_errors=True)
