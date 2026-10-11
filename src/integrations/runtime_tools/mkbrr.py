# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import os
import platform
import shutil
import stat
import tarfile
import zipfile
from pathlib import Path
from typing import ClassVar

import aiofiles
import httpx

from src.integrations.observability.console import logger
from src.integrations.runtime_tools.download_integrity import (
    MAX_EXTRACTED_BYTES,
    download_verified_asset,
    download_verified_asset_sync,
    promote_files_with_rollback,
    safe_extract_tar,
    safe_extract_zip,
)
from src.integrations.runtime_tools.runtime_tool_paths import (
    tool_install_dir,
    trusted_executable,
)

PlatformInfo = dict[str, str]


class MkbrrBinaryManager:
    platform_map: ClassVar[dict[str, dict[str, PlatformInfo]]] = {
        "windows": {
            "x86_64": {
                "file": "windows_x86_64.zip",
                "folder": "windows/x86_64",
            },
            "amd64": {
                "file": "windows_x86_64.zip",
                "folder": "windows/x86_64",
            },
            "arm64": {
                "file": "windows_x86_64.zip",
                "folder": "windows/x86_64",
            },
            "aarch64": {
                "file": "windows_x86_64.zip",
                "folder": "windows/x86_64",
            },
        },
        "darwin": {
            "arm64": {"file": "darwin_arm64.tar.gz", "folder": "macos/arm64"},
            "x86_64": {
                "file": "darwin_x86_64.tar.gz",
                "folder": "macos/x86_64",
            },
            "amd64": {
                "file": "darwin_x86_64.tar.gz",
                "folder": "macos/x86_64",
            },
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
            "x86_64": {
                "file": "freebsd_x86_64.tar.gz",
                "folder": "freebsd/x86_64",
            },
            "amd64": {
                "file": "freebsd_x86_64.tar.gz",
                "folder": "freebsd/x86_64",
            },
        },
    }

    @staticmethod
    def _system_machine() -> tuple[str, str]:
        return platform.system().lower(), platform.machine().lower()

    @staticmethod
    def _binary_name(system: str) -> str:
        return "mkbrr.exe" if system == "windows" else "mkbrr"

    @classmethod
    def _platform_info(cls, system: str, machine: str) -> PlatformInfo | None:
        return cls.platform_map.get(system, {}).get(machine)

    @staticmethod
    def _managed_candidate(
        bin_root: Path,
        platform_info: PlatformInfo | None,
        version: str | None,
        binary_name: str,
    ) -> Path | None:
        if platform_info is None:
            return None
        managed_dir = bin_root / "mkbrr" / platform_info["folder"]
        if version is not None and not (managed_dir / version).is_file():
            return None
        return managed_dir / binary_name

    @classmethod
    def _existing_candidates(
        cls,
        bin_root: Path,
        platform_info: PlatformInfo | None,
        version: str | None,
        binary_name: str,
    ) -> list[Path]:
        candidates = [bin_root / binary_name, bin_root / "mkbrr" / binary_name]
        managed = cls._managed_candidate(
            bin_root, platform_info, version, binary_name
        )
        if managed is not None:
            candidates.append(managed)
        return candidates

    @classmethod
    def find_existing_binary(
        cls, base_dir: str | Path, version: str | None = None
    ) -> str | None:
        """Return an existing mkbrr binary, version-checking the managed cache when requested."""
        system, machine = cls._system_machine()
        binary_name = cls._binary_name(system)
        bin_root = Path(base_dir) / "bin"
        candidates = cls._existing_candidates(
            bin_root,
            cls._platform_info(system, machine),
            version,
            binary_name,
        )
        for binary_path in candidates:
            if trusted_executable(binary_path):
                logger.debug(
                    f"[blue]Using existing mkbrr binary: {binary_path}[/blue]"
                )
                return str(binary_path)
        return shutil.which("mkbrr")

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
            f"https://github.com/autobrr/mkbrr/releases/download/{version}/"
            f"mkbrr_{version[1:]}_{file_pattern}"
        )

    @staticmethod
    def _reset_staging(staging: Path) -> None:
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir()

    @staticmethod
    def _cleanup_install(temp_archive: Path, staging: Path) -> None:
        temp_archive.unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)

    @staticmethod
    def _extract_archive(
        file_pattern: str, temp_archive: Path, staging: Path
    ) -> None:
        if file_pattern.endswith(".zip"):
            with zipfile.ZipFile(temp_archive, "r") as zip_ref:
                safe_extract_zip(
                    zip_ref, staging, max_bytes=MAX_EXTRACTED_BYTES
                )
            return
        if file_pattern.endswith(".tar.gz"):
            with tarfile.open(temp_archive, "r:gz") as tar_ref:
                safe_extract_tar(
                    tar_ref, staging, max_bytes=MAX_EXTRACTED_BYTES
                )

    @staticmethod
    def _staged_binary(
        staging: Path, binary_name: str, failure_message: str
    ) -> Path:
        candidates = [
            candidate
            for candidate in staging.rglob(binary_name)
            if candidate.is_file()
        ]
        if len(candidates) != 1:
            raise Exception(failure_message)
        return candidates[0]

    @staticmethod
    def _make_executable(staged_binary: Path, system: str) -> None:
        if system == "windows":
            return
        staged_binary.chmod(staged_binary.stat().st_mode | stat.S_IEXEC)

    @staticmethod
    async def _write_version_marker_async(
        staged_version: Path, version: str
    ) -> None:
        async with aiofiles.open(
            staged_version, "w", encoding="utf-8"
        ) as version_file:
            await version_file.write(
                f"mkbrr version {version} installed successfully."
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
    def _promote_install(
        cls,
        staged_binary: Path,
        binary_path: Path,
        staged_version: Path,
        version_path: Path,
        bin_dir: Path,
        version_markers: list[Path],
    ) -> None:
        promote_files_with_rollback(
            [(staged_binary, binary_path), (staged_version, version_path)],
            bin_dir / ".mkbrr-backup",
            remove_targets=cls._stale_markers(version_markers, version_path),
        )

    @classmethod
    async def _download_async_archive(
        cls,
        download_url: str,
        temp_archive: Path,
        version: str,
        file_pattern: str,
    ) -> None:
        async with httpx.AsyncClient(
            timeout=60.0, follow_redirects=True
        ) as client:
            await download_verified_asset(
                client,
                download_url,
                temp_archive,
                f"mkbrr_{version[1:]}_{file_pattern}",
            )

    @classmethod
    async def _install_async_binary(
        cls,
        system: str,
        version: str,
        file_pattern: str,
        bin_dir: Path,
        binary_path: Path,
        version_path: Path,
        version_markers: list[Path],
    ) -> str:
        download_url = cls._download_url(version, file_pattern)
        logger.debug(f"[blue]Download URL: {download_url}[/blue]")
        temp_archive = bin_dir / f"temp_{file_pattern}"
        staging = bin_dir / ".mkbrr-staging"
        cls._reset_staging(staging)
        try:
            await cls._download_async_archive(
                download_url, temp_archive, version, file_pattern
            )
            logger.debug(f"[green]Downloaded {file_pattern}[/green]")
            cls._extract_archive(file_pattern, temp_archive, staging)
            staged_binary = cls._staged_binary(
                staging,
                binary_path.name,
                f"Failed to extract mkbrr binary to {binary_path}",
            )
            cls._make_executable(staged_binary, system)
            staged_version = staging / version
            await cls._write_version_marker_async(staged_version, version)
            cls._promote_install(
                staged_binary,
                binary_path,
                staged_version,
                version_path,
                bin_dir,
                version_markers,
            )
            return str(binary_path)
        finally:
            cls._cleanup_install(temp_archive, staging)

    @classmethod
    async def _install_with_translated_errors(
        cls,
        system: str,
        version: str,
        file_pattern: str,
        bin_dir: Path,
        binary_path: Path,
        version_path: Path,
        version_markers: list[Path],
    ) -> str:
        try:
            return await cls._install_async_binary(
                system,
                version,
                file_pattern,
                bin_dir,
                binary_path,
                version_path,
                version_markers,
            )
        except httpx.RequestError as exc:
            raise Exception(f"Failed to download mkbrr binary: {exc}") from exc
        except (zipfile.BadZipFile, tarfile.TarError) as exc:
            raise Exception(f"Failed to extract mkbrr binary: {exc}") from exc

    @classmethod
    async def ensure_mkbrr_binary(
        cls, base_dir: str | Path, version: str
    ) -> str:
        existing_binary = cls.find_existing_binary(base_dir, version)
        if existing_binary:
            return existing_binary

        system, machine = cls._system_machine()
        logger.debug(
            f"[blue]Detected system: {system}, architecture: {machine}[/blue]"
        )
        platform_info = cls._platform_info(system, machine)
        if platform_info is None:
            raise Exception(f"Unsupported platform: {system} {machine}")

        file_pattern = platform_info["file"]
        folder_path = platform_info["folder"]
        logger.debug(f"[blue]Using file pattern: {file_pattern}[/blue]")
        logger.debug(f"[blue]Target folder: {folder_path}[/blue]")
        bin_dir = tool_install_dir(base_dir, "mkbrr", folder_path)
        logger.debug(f"[blue]Binary directory: {bin_dir}[/blue]")
        binary_path = bin_dir / cls._binary_name(system)
        logger.debug(f"[blue]Binary path: {binary_path}[/blue]")
        version_path = bin_dir / version
        version_markers = cls._version_markers(bin_dir)
        if cls._cache_is_current(
            binary_path, version_path, version_markers, system
        ):
            logger.debug("[blue]mkbrr version is up to date[/blue]")
            return str(binary_path)

        return await cls._install_with_translated_errors(
            system,
            version,
            file_pattern,
            bin_dir,
            binary_path,
            version_path,
            version_markers,
        )

    @staticmethod
    def _docker_platform_info(system: str, machine: str) -> PlatformInfo:
        if system != "linux":
            raise Exception(
                f"This script is for Docker/Linux only, detected: {system}"
            )
        platform_info = MkbrrBinaryManager.platform_map["linux"].get(machine)
        if platform_info is None:
            raise Exception(f"Unsupported architecture: {machine}")
        return platform_info

    @staticmethod
    def _docker_paths(
        base_dir: str | Path, folder_path: str, version: str
    ) -> tuple[Path, Path, Path]:
        bin_dir = Path(base_dir) / "bin" / "mkbrr" / folder_path
        bin_dir.mkdir(parents=True, exist_ok=True)
        return bin_dir, bin_dir / "mkbrr", bin_dir / version

    @classmethod
    def _install_docker_binary(
        cls,
        download_url: str,
        temp_archive: Path,
        staging: Path,
        file_pattern: str,
        version: str,
        bin_dir: Path,
        binary_path: Path,
        version_path: Path,
        version_markers: list[Path],
    ) -> str:
        download_verified_asset_sync(
            download_url,
            temp_archive,
            f"mkbrr_{version[1:]}_{file_pattern}",
        )
        logger.info(f"Downloaded {file_pattern}", extra={"markup": False})
        cls._extract_archive(file_pattern, temp_archive, staging)
        staged_binary = cls._staged_binary(
            staging,
            "mkbrr",
            f"Failed to extract exactly one mkbrr binary for {binary_path}",
        )
        staged_binary.chmod(0o700)
        staged_version = staging / version
        staged_version.write_text(
            f"mkbrr version {version} installed successfully.",
            encoding="utf-8",
        )
        cls._promote_install(
            staged_binary,
            binary_path,
            staged_version,
            version_path,
            bin_dir,
            version_markers,
        )
        logger.info(
            f"mkbrr binary ready at: {binary_path}", extra={"markup": False}
        )
        return str(binary_path)

    @classmethod
    def download_mkbrr_for_docker(
        cls, base_dir: str | Path = ".", version: str = "v1.18.0"
    ) -> str:
        """Download mkbrr binary for Docker/Linux - synchronous version."""
        system, machine = cls._system_machine()
        logger.info(
            f"Detected system: {system}, architecture: {machine}",
            extra={"markup": False},
        )
        platform_info = cls._docker_platform_info(system, machine)
        file_pattern = platform_info["file"]
        folder_path = platform_info["folder"]
        logger.info(
            f"Using file pattern: {file_pattern}", extra={"markup": False}
        )
        logger.info(f"Target folder: {folder_path}", extra={"markup": False})
        bin_dir, binary_path, version_path = cls._docker_paths(
            base_dir, folder_path, version
        )
        version_markers = cls._version_markers(bin_dir)
        if cls._cache_is_current(
            binary_path, version_path, version_markers, system
        ):
            logger.info(
                f"mkbrr {version} already exists, skipping download",
                extra={"markup": False},
            )
            return str(binary_path)

        download_url = cls._download_url(version, file_pattern)
        logger.info(
            f"Downloading from: {download_url}", extra={"markup": False}
        )
        temp_archive = bin_dir / f"temp_{file_pattern}"
        staging = bin_dir / ".mkbrr-staging"
        cls._reset_staging(staging)
        try:
            return cls._install_docker_binary(
                download_url,
                temp_archive,
                staging,
                file_pattern,
                version,
                bin_dir,
                binary_path,
                version_path,
                version_markers,
            )
        except Exception as exc:
            raise Exception(f"Error downloading mkbrr: {exc}") from exc
        finally:
            cls._cleanup_install(temp_archive, staging)
