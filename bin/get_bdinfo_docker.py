#!/usr/bin/env python3
# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
"""
Docker-specific script to download bdinfo binaries for Linux containers.
"""

import os
import platform
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bin.download_integrity import download_bounded_asset_sync, safe_extract_tar, verify_downloaded_asset

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


def download_bdinfo_for_docker(base_dir: Path = Path("/Upload-Assistant"), version: str = BDINFO_VERSION) -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    logger.info(f"System: {system}, Architecture: {machine}", extra={"markup": False})

    if system != "linux":
        raise Exception(f"This script is only for Linux containers, got: {system}")

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

    if version_path.exists() and binary_path.exists() and os.access(binary_path, os.X_OK):
        logger.info(f"bdinfo {version} already installed", extra={"markup": False})
        return str(binary_path)

    download_url = f"{BASE_RELEASE_URL}/{version}/{file_pattern}"
    logger.info(f"Downloading bdinfo from: {download_url}", extra={"markup": False})

    temp_archive = bin_dir / f"temp_{file_pattern}"
    try:
        download_file(download_url, temp_archive)
        verify_downloaded_asset(temp_archive, file_pattern)
        logger.info(f"Extracting {temp_archive} to {bin_dir}", extra={"markup": False})
        secure_extract_tar(temp_archive, bin_dir)
    finally:
        temp_archive.unlink(missing_ok=True)

    # Search for extracted bdinfo executable and move it into place if necessary
    if not binary_path.exists():
        found = None
        for p in bin_dir.rglob("bdinfo"):
            if p.is_file():
                found = p
                break
        if found:
            shutil.move(str(found), str(binary_path))

    if not binary_path.exists():
        raise Exception(f"Failed to extract bdinfo binary to {binary_path}")

    Path(binary_path).chmod(0o700)

    with Path(version_path).open("w", encoding="utf-8") as vf:
        vf.write(f"autobrr/go-bdinfo version {version} installed successfully.")

    logger.info(f"Installed bdinfo: {binary_path}", extra={"markup": False})
    return str(binary_path)


if __name__ == "__main__":
    try:
        download_bdinfo_for_docker()
        logger.info("bdinfo installation completed successfully!", extra={"markup": False})
    except Exception as exc:
        logger.info(f"ERROR: Failed to install bdinfo: {exc}", extra={"markup": False})
        raise SystemExit(1) from exc
