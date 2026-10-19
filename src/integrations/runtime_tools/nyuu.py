# Upload Assistant © 2026 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import os
import platform
import shutil
import stat
import subprocess  # nosec B404 -- local CLI uses fixed argv with shell disabled
import tarfile
from contextlib import suppress
from pathlib import Path
from typing import ClassVar

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

PlatformInfo = dict[str, str]


class NyuuBinaryManager:
    """Download Nyuu binaries for the host architecture."""

    platform_map: ClassVar[dict[str, dict[str, PlatformInfo]]] = {
        "windows": {
            "x86_64": {
                "file": "nyuu-v0.4.2-win32.7z",
                "folder": "windows/x86_64",
            },
            "amd64": {
                "file": "nyuu-v0.4.2-win32.7z",
                "folder": "windows/x86_64",
            },
            "x86": {
                "file": "nyuu-v0.4.2-win32.7z",
                "folder": "windows/x86",
            },
            "arm64": {
                "file": "nyuu-v0.4.2-win32.7z",
                "folder": "windows/arm64",
            },
        },
        "darwin": {
            "arm64": {
                "file": "nyuu-v0.4.2-macos-x64.tar.xz",
                "folder": "macos/arm64",
            },
            "x86_64": {
                "file": "nyuu-v0.4.2-macos-x64.tar.xz",
                "folder": "macos/x86_64",
            },
            "amd64": {
                "file": "nyuu-v0.4.2-macos-x64.tar.xz",
                "folder": "macos/x86_64",
            },
        },
        "linux": {
            "x86_64": {
                "file": "nyuu-v0.4.2-linux-amd64.tar.xz",
                "folder": "linux/amd64",
            },
            "amd64": {
                "file": "nyuu-v0.4.2-linux-amd64.tar.xz",
                "folder": "linux/amd64",
            },
            "arm64": {
                "file": "nyuu-v0.4.2-linux-aarch64.tar.xz",
                "folder": "linux/arm64",
            },
            "aarch64": {
                "file": "nyuu-v0.4.2-linux-aarch64.tar.xz",
                "folder": "linux/arm64",
            },
        },
    }

    @staticmethod
    def _system_machine() -> tuple[str, str]:
        return platform.system().lower(), platform.machine().lower()

    @classmethod
    def _platform_info(cls, system: str, machine: str) -> PlatformInfo:
        info = cls.platform_map.get(system, {}).get(machine)
        if info is None:
            raise Exception(
                f"Unsupported platform for Nyuu: {system} {machine}"
            )
        return info

    @staticmethod
    def _binary_name(system: str) -> str:
        return "nyuu.exe" if system == "windows" else "nyuu"

    @staticmethod
    def _version_markers(bin_dir: Path) -> list[Path]:
        return [
            candidate
            for candidate in bin_dir.glob("v*")
            if candidate.is_file()
        ]

    @staticmethod
    def _binary_is_valid(binary_path: Path, system: str) -> bool:
        if not binary_path.exists() or not binary_path.is_file():
            return False
        return system == "windows" or os.access(binary_path, os.X_OK)

    @classmethod
    def _cache_is_current(
        cls,
        binary_path: Path,
        version_path: Path,
        version_markers: list[Path],
        system: str,
    ) -> bool:
        if not version_path.exists() or not version_path.is_file():
            return False
        if not cls._binary_is_valid(binary_path, system):
            return False
        return version_markers == [version_path]

    @staticmethod
    def _download_url(version: str, file_pattern: str) -> str:
        return (
            "https://github.com/animetosho/Nyuu/releases/download/"
            f"{version}/{file_pattern}"
        )

    @staticmethod
    def _reset_staging(staging_dir: Path) -> None:
        shutil.rmtree(staging_dir, ignore_errors=True)
        staging_dir.mkdir()

    @staticmethod
    def _cleanup_install(temp_file: Path, staging_dir: Path) -> None:
        temp_file.unlink(missing_ok=True)
        shutil.rmtree(staging_dir, ignore_errors=True)

    @staticmethod
    async def _download_archive(
        download_url: str, temp_file: Path, file_pattern: str
    ) -> None:
        async with httpx.AsyncClient(
            timeout=60.0, follow_redirects=True
        ) as client:
            await download_verified_asset(
                client, download_url, temp_file, file_pattern
            )

    @staticmethod
    async def _resolved_7z_path(
        base_dir: str | Path, path_7z: str | None
    ) -> str:
        if path_7z:
            return path_7z
        from src.integrations.runtime_tools.seven_zip import (
            SevenZipBinaryManager,
        )

        return await SevenZipBinaryManager.ensure_7z_binary(base_dir)

    @classmethod
    async def _communicate_7z(
        cls, process: asyncio.subprocess.Process
    ) -> bytes:
        try:
            _stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=120
            )
        except TimeoutError:
            await cls._terminate_process_tree(process)
            raise RuntimeError(
                "7z extraction timed out after 120 seconds"
            ) from None
        except BaseException:
            await cls._terminate_process_tree(process)
            raise
        return stderr

    @classmethod
    async def _extract_7z(
        cls,
        base_dir: str | Path,
        path_7z: str | None,
        temp_file: Path,
        staging_dir: Path,
    ) -> None:
        executable = await cls._resolved_7z_path(base_dir, path_7z)
        command = [
            executable,
            "e",
            "-y",
            "-r",
            f"-o{staging_dir}",
            str(temp_file),
            "nyuu.exe",
        ]
        # 7z executable is a validated configured/managed local binary; argv is exec-form.
        process = await asyncio.create_subprocess_exec(  # nosemgrep: dangerous-asyncio-create-exec-audit
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        stderr = await cls._communicate_7z(process)
        if process.returncode != 0:
            raise RuntimeError(
                f"7z extraction failed: {stderr.decode(errors='replace')}"
            )

    @staticmethod
    def _extract_tar(temp_file: Path, staging_dir: Path) -> None:
        with tarfile.open(temp_file, "r:xz") as tar_ref:
            safe_extract_tar(
                tar_ref, staging_dir, max_bytes=MAX_EXTRACTED_BYTES
            )

    @classmethod
    async def _extract_archive(
        cls,
        base_dir: str | Path,
        path_7z: str | None,
        file_pattern: str,
        temp_file: Path,
        staging_dir: Path,
    ) -> None:
        if file_pattern.endswith(".7z"):
            await cls._extract_7z(base_dir, path_7z, temp_file, staging_dir)
            return
        cls._extract_tar(temp_file, staging_dir)

    @staticmethod
    def _staged_binary(staging_dir: Path, binary_name: str) -> Path:
        extracted = [
            candidate
            for candidate in staging_dir.rglob(binary_name)
            if candidate.is_file()
        ]
        if len(extracted) != 1:
            raise RuntimeError(
                f"Downloaded archive does not contain the expected {binary_name} executable"
            )
        staged_binary = extracted[0]
        if staged_binary.stat().st_size > MAX_EXTRACTED_BYTES:
            raise RuntimeError(
                f"Extracted {binary_name} exceeds the allowed size"
            )
        return staged_binary

    @staticmethod
    def _make_executable(staged_binary: Path, system: str) -> None:
        if system == "windows":
            return
        staged_binary.chmod(staged_binary.stat().st_mode | stat.S_IEXEC)

    @staticmethod
    async def _write_version_marker(
        staged_version: Path, version: str
    ) -> None:
        async with aiofiles.open(
            staged_version, "w", encoding="utf-8"
        ) as version_file:
            await version_file.write(
                f"Nyuu version {version} installed successfully."
            )

    @staticmethod
    def _stale_markers(
        version_markers: list[Path], version_path: Path
    ) -> list[Path]:
        return [
            candidate
            for candidate in version_markers
            if candidate != version_path
        ]

    @classmethod
    async def _stage_install(
        cls,
        system: str,
        version: str,
        binary_name: str,
        binary_path: Path,
        version_path: Path,
        version_markers: list[Path],
        bin_dir: Path,
        staging_dir: Path,
    ) -> None:
        staged_binary = cls._staged_binary(staging_dir, binary_name)
        cls._make_executable(staged_binary, system)
        staged_version = staging_dir / version
        await cls._write_version_marker(staged_version, version)
        promote_files_with_rollback(
            [(staged_binary, binary_path), (staged_version, version_path)],
            bin_dir / ".nyuu-backup",
            remove_targets=cls._stale_markers(version_markers, version_path),
        )

    @classmethod
    async def _install_binary(
        cls,
        base_dir: str | Path,
        path_7z: str | None,
        system: str,
        version: str,
        file_pattern: str,
        bin_dir: Path,
        binary_path: Path,
        version_path: Path,
        version_markers: list[Path],
    ) -> str:
        download_url = cls._download_url(version, file_pattern)
        logger.debug(f"[blue]Nyuu Download URL: {download_url}[/blue]")
        temp_file = bin_dir / f"temp_{file_pattern}"
        staging_dir = bin_dir / ".nyuu-staging"
        cls._reset_staging(staging_dir)
        try:
            await cls._download_archive(download_url, temp_file, file_pattern)
            logger.debug(
                f"[green]Downloaded Nyuu package: {file_pattern}[/green]"
            )
            await cls._extract_archive(
                base_dir, path_7z, file_pattern, temp_file, staging_dir
            )
            await cls._stage_install(
                system,
                version,
                cls._binary_name(system),
                binary_path,
                version_path,
                version_markers,
                bin_dir,
                staging_dir,
            )
            return str(binary_path)
        finally:
            cls._cleanup_install(temp_file, staging_dir)

    @classmethod
    async def ensure_nyuu_binary(
        cls,
        base_dir: str | Path,
        path_7z: str | None = None,
        version: str = "v0.4.2",
    ) -> str:
        system, machine = cls._system_machine()
        logger.debug(
            f"[blue]Nyuu: Detected system: {system}, architecture: {machine}[/blue]"
        )
        platform_info = cls._platform_info(system, machine)
        file_pattern = platform_info["file"]
        bin_dir = tool_install_dir(base_dir, "nyuu", platform_info["folder"])
        binary_path = bin_dir / cls._binary_name(system)
        version_path = bin_dir / version
        version_markers = cls._version_markers(bin_dir)
        if cls._cache_is_current(
            binary_path, version_path, version_markers, system
        ):
            logger.debug("[blue]Nyuu binary is up to date[/blue]")
            return str(binary_path)
        logger.info(
            "[yellow]Binary 'nyuu' not found. Attempting to download automatically...[/yellow]"
        )
        try:
            return await cls._install_binary(
                base_dir,
                path_7z,
                system,
                version,
                file_pattern,
                bin_dir,
                binary_path,
                version_path,
                version_markers,
            )
        except Exception as exc:
            raise Exception(f"Failed to setup Nyuu binary: {exc}") from exc

    @staticmethod
    def _should_taskkill(process: asyncio.subprocess.Process) -> bool:
        pid = getattr(process, "pid", None)
        return platform.system().lower() == "windows" and pid is not None

    @staticmethod
    async def _cleanup_taskkill_timeout(
        tree_killer: asyncio.subprocess.Process,
    ) -> None:
        if tree_killer.returncode is not None:
            return
        with suppress(ProcessLookupError):
            tree_killer.kill()
        with suppress(ProcessLookupError, TimeoutError):
            await asyncio.wait_for(tree_killer.communicate(), timeout=10)

    @classmethod
    async def _wait_taskkill(
        cls, tree_killer: asyncio.subprocess.Process
    ) -> None:
        try:
            await asyncio.wait_for(tree_killer.communicate(), timeout=10)
        except TimeoutError:
            await cls._cleanup_taskkill_timeout(tree_killer)
            return
        if tree_killer.returncode != 0:
            logger.warning(
                "taskkill could not terminate the complete 7z process tree"
            )

    @classmethod
    async def _run_taskkill(cls, process: asyncio.subprocess.Process) -> None:
        if not cls._should_taskkill(process):
            return
        pid = getattr(process, "pid", None)
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
        except OSError:
            return
        await cls._wait_taskkill(tree_killer)

    @staticmethod
    def _kill_owned_process(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        with suppress(ProcessLookupError):
            process.kill()

    @staticmethod
    async def _reap_owned_process(
        process: asyncio.subprocess.Process,
    ) -> None:
        with suppress(ProcessLookupError, TimeoutError):
            await asyncio.wait_for(process.communicate(), timeout=10)

    @classmethod
    async def _terminate_process_tree(
        cls, process: asyncio.subprocess.Process
    ) -> None:
        await cls._run_taskkill(process)
        cls._kill_owned_process(process)
        await cls._reap_owned_process(process)
