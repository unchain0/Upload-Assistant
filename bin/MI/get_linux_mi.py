#!/usr/bin/env python3
# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import platform
import shutil
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from bin.download_integrity import download_verified_asset_sync, extract_zip_regular_member, promote_files_with_rollback
from src.console import logger

MEDIAINFO_VERSION = "23.04"
MEDIAINFO_CLI_BASE_URL = "https://mediaarea.net/download/binary/mediainfo"
MEDIAINFO_LIB_BASE_URL = "https://mediaarea.net/download/binary/libmediainfo0"


def get_filename(system: str, arch: str, library_type: str = "cli") -> str:
    if system == "linux":
        if library_type == "cli":
            # MediaInfo CLI uses Lambda (pre-compiled) version
            return f"MediaInfo_CLI_{MEDIAINFO_VERSION}_Lambda_{arch}.zip"
        if library_type == "lib":
            # MediaInfo library uses DLL version
            return f"MediaInfo_DLL_{MEDIAINFO_VERSION}_Lambda_{arch}.zip"
        raise ValueError(f"Unknown library_type: {library_type}")
    return ""


def get_url(system: str, arch: str, library_type: str = "cli") -> str:
    filename = get_filename(system, arch, library_type)
    if library_type == "cli":
        return f"{MEDIAINFO_CLI_BASE_URL}/{MEDIAINFO_VERSION}/{filename}"
    if library_type == "lib":
        return f"{MEDIAINFO_LIB_BASE_URL}/{MEDIAINFO_VERSION}/{filename}"
    raise ValueError(f"Unknown library_type: {library_type}")


def download_file(url: str, output_path: Path) -> None:
    download_verified_asset_sync(url, output_path, output_path.name)


def extract_linux(cli_archive: Path, lib_archive: Path, output_dir: Path) -> None:
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
        staged_version = staging / f"version_{MEDIAINFO_VERSION}"
        staged_version.write_text(f"MediaInfo {MEDIAINFO_VERSION}")
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



def download_dvd_mediainfo(base_dir: str) -> str | None:
    system = platform.system().lower()
    machine = platform.machine().lower()

    logger.debug(f"[blue]System: {system}, arch: {machine}[/blue]")

    if system not in ["linux"]:
        return None

    if system == "linux" and machine not in ["x86_64", "arm64"]:
        return None

    if machine == "amd64":
        machine = "x86_64"

    platform_dir = "linux"
    output_dir = Path(base_dir) / "bin" / "MI" / platform_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.debug(f"[blue]Output: {output_dir}[/blue]")

    cli_file = output_dir / "mediainfo"
    lib_file = output_dir / "libmediainfo.so.0"
    version_file = output_dir / f"version_{MEDIAINFO_VERSION}"

    if cli_file.exists() and lib_file.exists() and version_file.exists():
        logger.debug(f"[blue]MediaInfo CLI and Library {MEDIAINFO_VERSION} exist[/blue]")
        return str(cli_file)
    logger.info(f"[yellow]Downloading specific MediaInfo CLI and Library for DVD processing: {MEDIAINFO_VERSION}...[/yellow]")
    # Download MediaInfo CLI
    cli_url = get_url(system, machine, "cli")
    cli_filename = get_filename(system, machine, "cli")

    # Download MediaInfo Library
    lib_url = get_url(system, machine, "lib")
    lib_filename = get_filename(system, machine, "lib")

    logger.debug(f"[blue]MediaInfo CLI URL: {cli_url}[/blue]")
    logger.debug(f"[blue]MediaInfo CLI filename: {cli_filename}[/blue]")
    logger.debug(f"[blue]MediaInfo Library URL: {lib_url}[/blue]")
    logger.debug(f"[blue]MediaInfo Library filename: {lib_filename}[/blue]")

    with TemporaryDirectory() as tmp_dir:
        tmp_dir_path = Path(tmp_dir)
        cli_archive = tmp_dir_path / cli_filename
        lib_archive = tmp_dir_path / lib_filename

        # Download both archives
        download_file(cli_url, cli_archive)
        logger.debug(f"[green]Downloaded {cli_filename}[/green]")

        download_file(lib_url, lib_archive)
        logger.debug(f"[green]Downloaded {lib_filename}[/green]")

        extract_linux(cli_archive, lib_archive, output_dir)

        logger.debug("[green]Extracted library[/green]")

    if not cli_file.exists():
        raise Exception(f"Failed to extract CLI binary to {cli_file}")
    if not lib_file.exists():
        raise Exception(f"Failed to extract library to {lib_file}")

    return str(cli_file)
