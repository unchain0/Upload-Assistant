#!/usr/bin/env python3
# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
"""
Docker-specific script to download bdinfo binaries for Linux containers.
"""

import os
import platform
import shutil
from pathlib import Path

from src.integrations.observability.console import logger
from src.integrations.runtime_tools.download_integrity import (
    download_bounded_asset_sync,
    promote_files_with_rollback,
    safe_extract_tar,
    verify_downloaded_asset,
)

BDINFO_VERSION = "v0.3.1"
BASE_RELEASE_URL = "https://github.com/autobrr/go-bdinfo/releases/download"


def download_file(url: str, output_path: Path) -> None:
    logger.info(f"Downloading: {url}", extra={"markup": False})
    download_bounded_asset_sync(url, output_path)
    logger.info(f"Downloaded: {output_path.name}", extra={"markup": False})


def secure_extract_tar(tar_path: Path, extract_to: Path) -> None:
    import tarfile

    with tarfile.open(tar_path, "r:gz") as tar_ref:
        safe_extract_tar(tar_ref, extract_to)


def download_bdinfo_for_docker(
    base_dir: Path = Path("/Upload-Assistant"), version: str = BDINFO_VERSION
) -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    logger.info(
        f"System: {system}, Architecture: {machine}", extra={"markup": False}
    )

    if system != "linux":
        raise Exception(
            f"This script is only for Linux containers, got: {system}"
        )

    if machine in ("amd64", "x86_64"):
        asset = "linux_amd64.tar.gz"
        folder = "linux/amd64"
    elif machine in ("arm64", "aarch64"):
        asset = "linux_arm64.tar.gz"
        folder = "linux/arm64"
    elif machine.startswith("arm"):
        asset = "linux_arm.tar.gz"
        folder = "linux/arm"
    else:
        raise Exception(f"Unsupported architecture: {machine}")

    file_pattern = f"bdinfo_{version.removeprefix('v')}_{asset}"
    bin_dir = base_dir / "bin" / "bdinfo" / folder
    bin_dir.mkdir(parents=True, exist_ok=True)
    binary_path = bin_dir / "bdinfo"
    version_path = bin_dir / version

    if (
        version_path.exists()
        and binary_path.exists()
        and os.access(binary_path, os.X_OK)
    ):
        logger.info(
            f"bdinfo {version} already installed", extra={"markup": False}
        )
        return str(binary_path)

    download_url = f"{BASE_RELEASE_URL}/{version}/{file_pattern}"
    logger.info(
        f"Downloading bdinfo from: {download_url}", extra={"markup": False}
    )

    temp_archive = bin_dir / f"temp_{file_pattern}"
    staging = bin_dir / ".bdinfo-staging"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    try:
        download_file(download_url, temp_archive)
        verify_downloaded_asset(temp_archive, file_pattern)
        logger.info(
            f"Extracting {temp_archive} to {staging}", extra={"markup": False}
        )
        secure_extract_tar(temp_archive, staging)
    finally:
        temp_archive.unlink(missing_ok=True)

    try:
        candidates = [
            candidate
            for candidate in staging.rglob("bdinfo")
            if candidate.is_file()
        ]
        if len(candidates) != 1:
            raise Exception(
                f"Failed to extract exactly one bdinfo binary for {binary_path}"
            )
        staged_binary = candidates[0]
        staged_binary.chmod(0o755)
        staged_version = staging / version
        staged_version.write_text(
            f"autobrr/go-bdinfo version {version} installed successfully.",
            encoding="utf-8",
        )
        stale_markers = [
            candidate
            for candidate in bin_dir.iterdir()
            if candidate.is_file()
            and candidate.name.startswith("v")
            and candidate != version_path
        ]
        promote_files_with_rollback(
            [(staged_binary, binary_path), (staged_version, version_path)],
            bin_dir / ".bdinfo-backup",
            remove_targets=stale_markers,
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    logger.info(f"Installed bdinfo: {binary_path}", extra={"markup": False})
    return str(binary_path)


def main() -> int:
    """Install the Docker bdinfo binary and return a process exit code."""

    try:
        download_bdinfo_for_docker()
        logger.info(
            "bdinfo installation completed successfully!",
            extra={"markup": False},
        )
        return 0
    except Exception as exc:
        logger.info(
            f"ERROR: Failed to install bdinfo: {exc}", extra={"markup": False}
        )
        return 1
