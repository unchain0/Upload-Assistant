# Upload Assistant © 2026 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import os
import platform
import shutil
import stat
import subprocess
import tarfile
from contextlib import suppress
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


class NyuuBinaryManager:
    """Download Nyuu binaries for the host architecture."""

    @staticmethod
    async def ensure_nyuu_binary(base_dir: str | Path, path_7z: str | None = None, version: str = "v0.4.2") -> str:
        system = platform.system().lower()
        machine = platform.machine().lower()
        logger.debug(f"[blue]Nyuu: Detected system: {system}, architecture: {machine}[/blue]")

        platform_map: dict[str, dict[str, dict[str, str]]] = {
            "windows": {
                "x86_64": {"file": "nyuu-v0.4.2-win32.7z", "folder": "windows/x86_64"},
                "amd64": {"file": "nyuu-v0.4.2-win32.7z", "folder": "windows/x86_64"},
                "x86": {"file": "nyuu-v0.4.2-win32.7z", "folder": "windows/x86"},
                "arm64": {"file": "nyuu-v0.4.2-win32.7z", "folder": "windows/arm64"},
            },
            "darwin": {
                "arm64": {"file": "nyuu-v0.4.2-macos-x64.tar.xz", "folder": "macos/arm64"},
                "x86_64": {"file": "nyuu-v0.4.2-macos-x64.tar.xz", "folder": "macos/x86_64"},
                "amd64": {"file": "nyuu-v0.4.2-macos-x64.tar.xz", "folder": "macos/x86_64"},
            },
            "linux": {
                "x86_64": {"file": "nyuu-v0.4.2-linux-amd64.tar.xz", "folder": "linux/amd64"},
                "amd64": {"file": "nyuu-v0.4.2-linux-amd64.tar.xz", "folder": "linux/amd64"},
                "arm64": {"file": "nyuu-v0.4.2-linux-aarch64.tar.xz", "folder": "linux/arm64"},
                "aarch64": {"file": "nyuu-v0.4.2-linux-aarch64.tar.xz", "folder": "linux/arm64"},
            },
        }

        if system not in platform_map or machine not in platform_map[system]:
            raise Exception(f"Unsupported platform for Nyuu: {system} {machine}")

        platform_info = platform_map[system][machine]
        file_pattern = platform_info["file"]
        folder_path = platform_info["folder"]

        bin_dir = Path(base_dir) / "bin" / "nyuu" / folder_path
        bin_dir.mkdir(parents=True, exist_ok=True)

        binary_name = "nyuu.exe" if system == "windows" else "nyuu"
        binary_path = bin_dir / binary_name
        version_path = bin_dir / version

        binary_exists = binary_path.exists() and binary_path.is_file()
        binary_executable = system == "windows" or os.access(binary_path, os.X_OK)
        binary_valid = binary_exists and binary_executable

        if version_path.exists() and version_path.is_file() and binary_valid:
            logger.debug("[blue]Nyuu binary is up to date[/blue]")
            return str(binary_path)

        logger.info("[yellow]Binary 'nyuu' not found. Attempting to download automatically...[/yellow]")

        download_url = f"https://github.com/animetosho/Nyuu/releases/download/{version}/{file_pattern}"
        logger.debug(f"[blue]Nyuu Download URL: {download_url}[/blue]")

        temp_file = bin_dir / f"temp_{file_pattern}"
        staging_dir = bin_dir / ".nyuu-staging"
        shutil.rmtree(staging_dir, ignore_errors=True)
        staging_dir.mkdir()
        try:
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                await download_verified_asset(client, download_url, temp_file, file_pattern)

            logger.debug(f"[green]Downloaded Nyuu package: {file_pattern}[/green]")

            if file_pattern.endswith(".7z"):
                if not path_7z:
                    from bin.get_7z import SevenZipBinaryManager

                    path_7z = await SevenZipBinaryManager.ensure_7z_binary(base_dir)
                command = [path_7z, "e", "-y", "-r", f"-o{staging_dir}", str(temp_file), "nyuu.exe"]
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                )
                try:
                    _stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=120)
                except TimeoutError:
                    await NyuuBinaryManager._terminate_process_tree(process)
                    raise RuntimeError("7z extraction timed out after 120 seconds") from None
                except BaseException:
                    await NyuuBinaryManager._terminate_process_tree(process)
                    raise
                if process.returncode != 0:
                    raise RuntimeError(f"7z extraction failed: {stderr.decode(errors='replace')}")
            else:
                with tarfile.open(temp_file, "r:xz") as tar_ref:
                    safe_extract_tar(tar_ref, staging_dir, max_bytes=MAX_EXTRACTED_BYTES)

            extracted = [candidate for candidate in staging_dir.rglob(binary_name) if candidate.is_file()]
            if len(extracted) != 1:
                raise RuntimeError(f"Downloaded archive does not contain the expected {binary_name} executable")
            staged_binary = extracted[0]
            if staged_binary.stat().st_size > MAX_EXTRACTED_BYTES:
                raise RuntimeError(f"Extracted {binary_name} exceeds the allowed size")

            if system != "windows":
                staged_binary.chmod(staged_binary.stat().st_mode | stat.S_IEXEC)

            staged_version = staging_dir / version
            async with aiofiles.open(staged_version, "w", encoding="utf-8") as version_file:
                await version_file.write(f"Nyuu version {version} installed successfully.")
            promote_files_with_rollback(
                [(staged_binary, binary_path), (staged_version, version_path)],
                staging_dir / ".backup",
            )

            return str(binary_path)

        except Exception as e:
            raise Exception(f"Failed to setup Nyuu binary: {e}") from e
        finally:
            temp_file.unlink(missing_ok=True)
            shutil.rmtree(staging_dir, ignore_errors=True)

    @staticmethod
    async def _terminate_process_tree(process: asyncio.subprocess.Process) -> None:
        pid = getattr(process, "pid", None)
        if platform.system().lower() == "windows" and pid is not None:
            try:
                tree_killer = await asyncio.create_subprocess_exec(
                    "taskkill",
                    "/F",
                    "/T",
                    "/PID",
                    str(pid),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(tree_killer.communicate(), timeout=10)
            except (OSError, TimeoutError):
                pass
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
        with suppress(ProcessLookupError, TimeoutError):
            await asyncio.wait_for(process.communicate(), timeout=10)
