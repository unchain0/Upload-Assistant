#!/usr/bin/env python3
# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
"""
Docker-specific script to download DVD-capable MediaInfo binaries for Linux.
This script downloads specialized MediaInfo CLI and library binaries that
support DVD IFO/VOB file parsing with language information.
"""

import platform
import shutil
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from bin.download_integrity import download_verified_asset_sync, extract_zip_regular_member, promote_files_with_rollback

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

MEDIAINFO_VERSION = "23.04"
MEDIAINFO_CLI_BASE_URL = "https://mediaarea.net/download/binary/mediainfo"
MEDIAINFO_LIB_BASE_URL = "https://mediaarea.net/download/binary/libmediainfo0"


def get_filename(system: str, arch: str, library_type: str = "cli") -> str:
    """Get the appropriate filename for MediaInfo download based on system and architecture."""
    if system == "linux":
        if library_type == "cli":
            # MediaInfo CLI uses Lambda (pre-compiled) version for better DVD support
            return f"MediaInfo_CLI_{MEDIAINFO_VERSION}_Lambda_{arch}.zip"
        if library_type == "lib":
            # MediaInfo library uses DLL version for better compatibility
            return f"MediaInfo_DLL_{MEDIAINFO_VERSION}_Lambda_{arch}.zip"
        raise ValueError(f"Unknown library_type: {library_type}")
    raise ValueError(f"Unsupported system: {system}")


def get_url(system: str, arch: str, library_type: str = "cli") -> str:
    """Construct download URL for MediaInfo components."""
    filename = get_filename(system, arch, library_type)
    if library_type == "cli":
        return f"{MEDIAINFO_CLI_BASE_URL}/{MEDIAINFO_VERSION}/{filename}"
    if library_type == "lib":
        return f"{MEDIAINFO_LIB_BASE_URL}/{MEDIAINFO_VERSION}/{filename}"
    raise ValueError(f"Unknown library_type: {library_type}")


def download_file(url: str, output_path: Path) -> None:
    """Download a file from URL to specified path."""
    logger.info(f"Downloading: {url}", extra={"markup": False})
    download_verified_asset_sync(url, output_path, output_path.name)
    logger.info(f"Downloaded: {output_path.name}", extra={"markup": False})


def extract_linux_binaries(cli_archive: Path, lib_archive: Path, output_dir: Path) -> None:
    """Extract MediaInfo CLI and library from downloaded archives."""
    logger.info("Extracting MediaInfo binaries...", extra={"markup": False})

    staging = output_dir / ".mediainfo-staging"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    try:
        with zipfile.ZipFile(cli_archive, "r") as archive:
            cli_members = [name for name in archive.namelist() if name.endswith("/mediainfo") or name == "mediainfo"]
            if len(cli_members) != 1:
                raise RuntimeError("MediaInfo archive must contain exactly one CLI binary")
            extract_zip_regular_member(archive, cli_members[0], staging / "mediainfo")
        with zipfile.ZipFile(lib_archive, "r") as archive:
            library_member = "lib/libmediainfo.so.0.0.0"
            if library_member not in archive.namelist():
                raise RuntimeError("MediaInfo archive does not contain the required library")
            extract_zip_regular_member(archive, library_member, staging / "libmediainfo.so.0")
        (staging / "mediainfo").chmod(0o700)
        (staging / "libmediainfo.so.0").chmod(0o644)
        staged_version = staging / f"version_{MEDIAINFO_VERSION}"
        staged_version.write_text(f"MediaInfo {MEDIAINFO_VERSION} - DVD Support")
        promote_files_with_rollback(
            [
                (staging / "mediainfo", output_dir / "mediainfo"),
                (staging / "libmediainfo.so.0", output_dir / "libmediainfo.so.0"),
                (staged_version, output_dir / staged_version.name),
            ],
            staging / ".backup",
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def download_dvd_mediainfo_docker():
    """Download DVD-specific MediaInfo binaries for Docker container."""
    system = platform.system().lower()
    machine = platform.machine().lower()

    logger.info(f"System: {system}, Architecture: {machine}", extra={"markup": False})

    if system != "linux":
        raise Exception(f"This script is only for Linux containers, got: {system}")

    # Normalize architecture names
    if machine in ["amd64", "x86_64"]:
        arch = "x86_64"
    elif machine in ["arm64", "aarch64"]:
        arch = "arm64"
    else:
        raise Exception(f"Unsupported architecture: {machine}")

    # Set up output directory in the container
    base_dir = Path("/Upload-Assistant")
    output_dir = base_dir / "bin" / "MI" / "linux"
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Installing DVD MediaInfo to: {output_dir}", extra={"markup": False})

    cli_file = output_dir / "mediainfo"
    lib_file = output_dir / "libmediainfo.so.0"
    version_file = output_dir / f"version_{MEDIAINFO_VERSION}"

    # Check if already installed
    if cli_file.exists() and lib_file.exists() and version_file.exists():
        logger.info(f"DVD MediaInfo {MEDIAINFO_VERSION} already installed", extra={"markup": False})
        return str(cli_file)

    logger.info(f"Downloading DVD-specific MediaInfo CLI and Library: {MEDIAINFO_VERSION}", extra={"markup": False})

    # Get download URLs
    cli_url = get_url(system, arch, "cli")
    lib_url = get_url(system, arch, "lib")

    cli_filename = get_filename(system, arch, "cli")
    lib_filename = get_filename(system, arch, "lib")

    logger.info(f"CLI URL: {cli_url}", extra={"markup": False})
    logger.info(f"Library URL: {lib_url}", extra={"markup": False})

    # Download and extract in temporary directory
    with TemporaryDirectory() as tmp_dir:
        cli_archive = Path(tmp_dir) / cli_filename
        lib_archive = Path(tmp_dir) / lib_filename

        # Download both archives
        download_file(cli_url, cli_archive)
        download_file(lib_url, lib_archive)

        # Extract binaries
        extract_linux_binaries(cli_archive, lib_archive, output_dir)

        # Verify CLI permissions
        if cli_file.exists():
            file_stat = cli_file.stat()
            is_executable = bool(file_stat.st_mode & 0o100)  # Check if owner execute bit is set
            if is_executable:
                logger.info(f"✓ Set secure executable permissions on: {cli_file} (mode: {oct(file_stat.st_mode)})", extra={"markup": False})
            else:
                raise Exception(f"Failed to set executable permissions on: {cli_file}")
        else:
            raise Exception(f"CLI binary not found for permission setting: {cli_file}")

    # Verify installation and permissions
    if not cli_file.exists():
        raise Exception(f"Failed to install CLI binary: {cli_file}")
    if not lib_file.exists():
        raise Exception(f"Failed to install library: {lib_file}")

    # Final executable verification
    cli_stat = cli_file.stat()
    if not (cli_stat.st_mode & 0o111):
        raise Exception(f"CLI binary is not executable: {cli_file}")
    logger.info(f"✓ CLI binary is executable: {oct(cli_stat.st_mode)}", extra={"markup": False})

    logger.info(f"Successfully installed DVD MediaInfo {MEDIAINFO_VERSION}", extra={"markup": False})
    logger.info(f"CLI: {cli_file}", extra={"markup": False})
    logger.info(f"Library: {lib_file}", extra={"markup": False})

    return str(cli_file)


if __name__ == "__main__":
    try:
        download_dvd_mediainfo_docker()
        logger.info("DVD MediaInfo installation completed successfully!", extra={"markup": False})
    except Exception as e:
        logger.info(f"ERROR: Failed to install DVD MediaInfo: {e}", extra={"markup": False})
        exit(1)
