# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import os
import platform
import stat
import tarfile
import zipfile
from pathlib import Path

import aiofiles
import httpx

from bin.download_integrity import MAX_EXTRACTED_BYTES, download_verified_asset, download_verified_asset_sync, safe_extract_tar, safe_extract_zip

try:
    from src.console import console, logger
except ImportError:
    # Fallback for Docker builds where rich is not yet installed
    import logging

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    class SimpleConsole:
        def print(self, message: str, markup: bool = False) -> None:  # noqa: ARG002
            print(message)

    console = SimpleConsole()
    logger = logging.getLogger(__name__)


class MkbrrBinaryManager:
    @staticmethod
    def find_existing_binary(base_dir: str | Path) -> str | None:
        """Return a user-provided mkbrr binary before attempting a download."""
        binary_name = "mkbrr.exe" if platform.system().lower() == "windows" else "mkbrr"
        bin_root = Path(base_dir) / "bin"
        candidates = (bin_root / binary_name, bin_root / "mkbrr" / binary_name)

        for binary_path in candidates:
            if binary_path.is_file() and (binary_name.endswith(".exe") or os.access(binary_path, os.X_OK)):
                logger.debug(f"[blue]Using existing mkbrr binary: {binary_path}[/blue]")
                return str(binary_path)

        return None

    @staticmethod
    async def ensure_mkbrr_binary(base_dir: str | Path, version: str) -> str:
        existing_binary = MkbrrBinaryManager.find_existing_binary(base_dir)
        if existing_binary:
            return existing_binary

        system = platform.system().lower()
        machine = platform.machine().lower()
        logger.debug(f"[blue]Detected system: {system}, architecture: {machine}[/blue]")

        platform_map: dict[str, dict[str, dict[str, str]]] = {
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

        if system not in platform_map or machine not in platform_map[system]:
            raise Exception(f"Unsupported platform: {system} {machine}")

        platform_info = platform_map[system][machine]
        file_pattern = platform_info["file"]
        folder_path = platform_info["folder"]
        logger.debug(f"[blue]Using file pattern: {file_pattern}[/blue]")
        logger.debug(f"[blue]Target folder: {folder_path}[/blue]")

        bin_dir = Path(base_dir) / "bin" / "mkbrr" / folder_path
        bin_dir.mkdir(parents=True, exist_ok=True)
        logger.debug(f"[blue]Binary directory: {bin_dir}[/blue]")

        binary_name = "mkbrr.exe" if system == "windows" else "mkbrr"
        binary_path = bin_dir / binary_name
        logger.debug(f"[blue]Binary path: {binary_path}[/blue]")

        wrong_version = False
        version_path = bin_dir / version
        binary_exists = binary_path.exists() and binary_path.is_file()
        binary_executable = system == "windows" or os.access(binary_path, os.X_OK)
        binary_valid = binary_exists and binary_executable
        if version_path.exists() and version_path.is_file() and binary_valid:
            logger.debug("[blue]mkbrr version is up to date[/blue]")
            return str(binary_path)
        wrong_version = True

        if binary_path.exists() and binary_path.is_file():
            if system != "windows":
                # Set secure permissions before removal
                Path(binary_path).chmod(0o600)
            binary_path.unlink()
            logger.debug(f"[blue]Removed existing binary at: {binary_path}[/blue]")

        if wrong_version and version_path.exists():
            if system != "windows":
                Path(version_path).chmod(0o644)
            version_path.unlink()
            logger.debug(f"[blue]Removed existing version file at: {version_path}[/blue]")

        download_url = f"https://github.com/autobrr/mkbrr/releases/download/{version}/mkbrr_{version[1:]}_{file_pattern}"
        logger.debug(f"[blue]Download URL: {download_url}[/blue]")

        temp_archive = bin_dir / f"temp_{file_pattern}"
        try:
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                await download_verified_asset(client, download_url, temp_archive, f"mkbrr_{version[1:]}_{file_pattern}")
            logger.debug(f"[green]Downloaded {file_pattern}[/green]")

            if file_pattern.endswith(".zip"):
                with zipfile.ZipFile(temp_archive, "r") as zip_ref:
                    safe_extract_zip(zip_ref, bin_dir, max_bytes=MAX_EXTRACTED_BYTES)

            elif file_pattern.endswith(".tar.gz"):
                with tarfile.open(temp_archive, "r:gz") as tar_ref:
                    safe_extract_tar(tar_ref, bin_dir, max_bytes=MAX_EXTRACTED_BYTES)

            if system != "windows" and binary_path.exists():
                binary_path.chmod(binary_path.stat().st_mode | stat.S_IEXEC)

            if not binary_path.exists():
                raise Exception(f"Failed to extract mkbrr binary to {binary_path}")

            async with aiofiles.open(version_path, "w", encoding="utf-8") as version_file:
                await version_file.write(f"mkbrr version {version} installed successfully.")
            return str(binary_path)

        except httpx.RequestError as e:
            raise Exception(f"Failed to download mkbrr binary: {e}") from e
        except (zipfile.BadZipFile, tarfile.TarError) as e:
            raise Exception(f"Failed to extract mkbrr binary: {e}") from e
        finally:
            temp_archive.unlink(missing_ok=True)

    @staticmethod
    def download_mkbrr_for_docker(base_dir: str | Path = ".", version: str = "v1.18.0") -> str:
        """Download mkbrr binary for Docker/Linux - synchronous version."""
        system = platform.system().lower()
        machine = platform.machine().lower()
        logger.info(f"Detected system: {system}, architecture: {machine}", extra={"markup": False})

        if system != "linux":
            raise Exception(f"This script is for Docker/Linux only, detected: {system}")

        platform_map = {
            "x86_64": {"file": "linux_x86_64.tar.gz", "folder": "linux/amd64"},
            "amd64": {"file": "linux_x86_64.tar.gz", "folder": "linux/amd64"},
            "arm64": {"file": "linux_arm64.tar.gz", "folder": "linux/arm64"},
            "aarch64": {"file": "linux_arm64.tar.gz", "folder": "linux/arm64"},
            "armv7l": {"file": "linux_arm.tar.gz", "folder": "linux/arm"},
            "arm": {"file": "linux_arm.tar.gz", "folder": "linux/arm"},
        }

        if machine not in platform_map:
            raise Exception(f"Unsupported architecture: {machine}")

        platform_info = platform_map[machine]
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
        if version_path.exists() and version_path.is_file() and binary_valid:
            logger.info(f"mkbrr {version} already exists, skipping download", extra={"markup": False})
            return str(binary_path)

        if binary_path.exists():
            binary_path.unlink()

        download_url = f"https://github.com/autobrr/mkbrr/releases/download/{version}/mkbrr_{version[1:]}_{file_pattern}"
        logger.info(f"Downloading from: {download_url}", extra={"markup": False})

        temp_archive = bin_dir / f"temp_{file_pattern}"
        try:
            download_verified_asset_sync(download_url, temp_archive, f"mkbrr_{version[1:]}_{file_pattern}")

            logger.info(f"Downloaded {file_pattern}", extra={"markup": False})

            with tarfile.open(temp_archive, "r:gz") as tar_ref:
                safe_extract_tar(tar_ref, bin_dir, max_bytes=MAX_EXTRACTED_BYTES)

            if binary_path.exists():
                Path(binary_path).chmod(0o700)
                logger.info(f"mkbrr binary ready at: {binary_path}", extra={"markup": False})

                with Path(version_path).open("w", encoding="utf-8") as version_file:
                    version_file.write(f"mkbrr version {version} installed successfully.")

                return str(binary_path)

            raise Exception(f"Failed to extract mkbrr binary to {binary_path}")

        except Exception as e:
            raise Exception(f"Error downloading mkbrr: {e}") from e
        finally:
            temp_archive.unlink(missing_ok=True)
