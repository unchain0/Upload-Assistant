# Upload Assistant © 2026 Audionut & wastaken7 — Licensed under UAPL v1.0
import os
import platform
import shutil
import stat
import tarfile
from pathlib import Path

import aiofiles
import httpx

from bin.download_integrity import MAX_EXTRACTED_BYTES, download_verified_asset, promote_files_with_rollback, safe_extract_tar

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


class SevenZipBinaryManager:
    """Download 7-Zip binaries for the host architecture."""

    @staticmethod
    async def ensure_7z_binary(base_dir: str | Path, version: str = "26.01") -> str:
        system = platform.system().lower()
        machine = platform.machine().lower()
        logger.debug(f"[blue]7-Zip: Detected system: {system}, architecture: {machine}[/blue]")

        platform_map: dict[str, dict[str, dict[str, str]]] = {
            "windows": {
                "x86_64": {"file": "7zr.exe", "folder": "windows/x86_64"},
                "amd64": {"file": "7zr.exe", "folder": "windows/x86_64"},
                "x86": {"file": "7zr.exe", "folder": "windows/x86"},
                "arm64": {"file": "7zr.exe", "folder": "windows/arm64"},
            },
            "darwin": {
                "arm64": {"file": "7z2601-mac.tar.xz", "folder": "macos/arm64"},
                "x86_64": {"file": "7z2601-mac.tar.xz", "folder": "macos/x86_64"},
                "amd64": {"file": "7z2601-mac.tar.xz", "folder": "macos/x86_64"},
            },
            "linux": {
                "x86_64": {"file": "7z2601-linux-x64.tar.xz", "folder": "linux/amd64"},
                "amd64": {"file": "7z2601-linux-x64.tar.xz", "folder": "linux/amd64"},
                "arm64": {"file": "7z2601-linux-arm64.tar.xz", "folder": "linux/arm64"},
                "aarch64": {"file": "7z2601-linux-arm64.tar.xz", "folder": "linux/arm64"},
                "arm": {"file": "7z2601-linux-arm.tar.xz", "folder": "linux/arm"},
                "armv7l": {"file": "7z2601-linux-arm.tar.xz", "folder": "linux/arm"},
                "armv6l": {"file": "7z2601-linux-arm.tar.xz", "folder": "linux/arm"},
            },
        }

        if system not in platform_map or machine not in platform_map[system]:
            raise Exception(f"Unsupported platform for 7z: {system} {machine}")

        platform_info = platform_map[system][machine]
        file_pattern = platform_info["file"]
        folder_path = platform_info["folder"]

        bin_dir = Path(base_dir) / "bin" / "7z" / folder_path
        bin_dir.mkdir(parents=True, exist_ok=True)

        binary_name = "7zr.exe" if system == "windows" else "7zz"
        binary_path = bin_dir / binary_name
        version_path = bin_dir / version

        binary_exists = binary_path.exists() and binary_path.is_file()
        binary_executable = system == "windows" or os.access(binary_path, os.X_OK)
        binary_valid = binary_exists and binary_executable
        version_markers = [
            candidate
            for candidate in bin_dir.iterdir()
            if candidate.is_file() and candidate != binary_path and not candidate.name.startswith("temp_")
        ]

        if version_path.exists() and version_path.is_file() and binary_valid and version_markers == [version_path]:
            logger.debug("[blue]7-Zip binary is up to date[/blue]")
            return str(binary_path)

        logger.info("[yellow]Binary '7z' not found. Attempting to download automatically...[/yellow]")

        download_url = f"https://github.com/ip7z/7zip/releases/download/{version}/{file_pattern}"
        logger.debug(f"[blue]7-Zip Download URL: {download_url}[/blue]")

        temp_file = bin_dir / f"temp_{file_pattern}"
        staging = bin_dir / ".7z-staging"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir()
        try:
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                integrity_key = f"{version}/{file_pattern}" if file_pattern.endswith(".exe") else file_pattern
                await download_verified_asset(client, download_url, temp_file, integrity_key)

            logger.debug(f"[green]Downloaded 7-Zip package: {file_pattern}[/green]")

            if file_pattern.endswith(".exe"):
                # Windows 7zr.exe is a raw executable
                staged_binary = staging / binary_name
                shutil.move(str(temp_file), str(staged_binary))
            else:
                # Linux/macOS are tar.xz archives
                with tarfile.open(temp_file, "r:xz") as tar_ref:
                    safe_extract_tar(tar_ref, staging, max_bytes=MAX_EXTRACTED_BYTES)
                candidates = [candidate for candidate in staging.rglob(binary_name) if candidate.is_file()]
                if len(candidates) != 1:
                    raise RuntimeError(f"Downloaded archive must contain exactly one {binary_name} executable")
                staged_binary = candidates[0]

            if system != "windows":
                staged_binary.chmod(staged_binary.stat().st_mode | stat.S_IEXEC)

            staged_version = staging / version
            async with aiofiles.open(staged_version, "w", encoding="utf-8") as version_file:
                await version_file.write(f"7-Zip version {version} installed successfully.")
            stale_markers = [candidate for candidate in version_markers if candidate != version_path]
            promote_files_with_rollback(
                [(staged_binary, binary_path), (staged_version, version_path)],
                staging / ".backup",
                remove_targets=stale_markers,
            )

            return str(binary_path)

        except Exception as e:
            raise Exception(f"Failed to setup 7z binary: {e}") from e
        finally:
            temp_file.unlink(missing_ok=True)
            shutil.rmtree(staging, ignore_errors=True)
