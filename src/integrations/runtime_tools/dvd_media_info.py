#!/usr/bin/env python3
# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
"""Provision the legacy MediaInfo CLI required for DVD parsing."""

import platform
import shutil
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from src.integrations.observability.console import logger
from src.integrations.runtime_tools.download_integrity import (
    download_verified_asset_sync,
    extract_zip_regular_member,
    promote_files_with_rollback,
)

MEDIAINFO_VERSION = "23.04"
MEDIAINFO_CLI_BASE_URL = "https://mediaarea.net/download/binary/mediainfo"
MEDIAINFO_LIB_BASE_URL = "https://mediaarea.net/download/binary/libmediainfo0"


def _windows_filename(arch: str, library_type: str) -> str:
    if library_type == "cli" and arch == "x86_64":
        return f"MediaInfo_CLI_{MEDIAINFO_VERSION}_Windows_x64.zip"
    return ""


def _linux_filename(arch: str, library_type: str) -> str:
    if library_type == "cli":
        return f"MediaInfo_CLI_{MEDIAINFO_VERSION}_Lambda_{arch}.zip"
    if library_type == "lib":
        return f"MediaInfo_DLL_{MEDIAINFO_VERSION}_Lambda_{arch}.zip"
    raise ValueError(f"Unknown library_type: {library_type}")


def get_filename(system: str, arch: str, library_type: str = "cli") -> str:
    if system == "windows":
        return _windows_filename(arch, library_type)
    if system == "linux":
        return _linux_filename(arch, library_type)
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


def _cli_archive_member(archive: zipfile.ZipFile) -> str:
    members = [
        name
        for name in archive.namelist()
        if name.endswith("/mediainfo") or name == "mediainfo"
    ]
    if len(members) != 1:
        raise RuntimeError(
            "MediaInfo archive must contain exactly one CLI binary"
        )
    return members[0]


def _extract_linux_cli(cli_archive: Path, staging: Path) -> None:
    with zipfile.ZipFile(cli_archive, "r") as archive:
        extract_zip_regular_member(
            archive, _cli_archive_member(archive), staging / "mediainfo"
        )


def _extract_linux_library(lib_archive: Path, staging: Path) -> None:
    library_member = "lib/libmediainfo.so.0.0.0"
    with zipfile.ZipFile(lib_archive, "r") as archive:
        if library_member not in archive.namelist():
            raise RuntimeError(
                "MediaInfo library archive does not contain the required library"
            )
        extract_zip_regular_member(
            archive, library_member, staging / "libmediainfo.so.0"
        )


def _promote_linux_files(staging: Path, output_dir: Path) -> None:
    (staging / "mediainfo").chmod(0o700)
    staged_version = staging / f"version_{MEDIAINFO_VERSION}"
    staged_version.write_text(f"MediaInfo {MEDIAINFO_VERSION}")
    promote_files_with_rollback(
        [
            (staging / "mediainfo", output_dir / "mediainfo"),
            (staging / "libmediainfo.so.0", output_dir / "libmediainfo.so.0"),
            (staged_version, output_dir / staged_version.name),
        ],
        output_dir / ".mediainfo-backup",
    )


def extract_linux(
    cli_archive: Path, lib_archive: Path, output_dir: Path
) -> None:
    staging = output_dir / ".mediainfo-staging"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    try:
        _extract_linux_cli(cli_archive, staging)
        _extract_linux_library(lib_archive, staging)
        _promote_linux_files(staging, output_dir)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def extract_windows(cli_archive: Path, output_dir: Path) -> None:
    """Extract the legacy Windows DVD CLI without unpacking arbitrary archive members."""
    with zipfile.ZipFile(cli_archive, "r") as zip_ref:
        members = [
            name
            for name in zip_ref.namelist()
            if Path(name).name == "MediaInfo.exe"
        ]
        if len(members) != 1:
            raise RuntimeError(
                "MediaInfo archive must contain exactly one MediaInfo.exe"
            )
        extract_zip_regular_member(
            zip_ref, members[0], output_dir / "MediaInfo.exe"
        )


def _normalized_machine() -> str:
    machine = platform.machine().lower()
    return "x86_64" if machine == "amd64" else machine


def _platform_identity() -> tuple[str, str]:
    system = platform.system().lower()
    machine = _normalized_machine()
    logger.debug(f"[blue]System: {system}, arch: {machine}[/blue]")
    return system, machine


def _windows_paths(base_dir: str) -> tuple[Path, Path, Path]:
    output_dir = Path(base_dir) / "bin" / "MI" / "windows" / "dvd"
    cli_file = output_dir / "MediaInfo.exe"
    version_file = output_dir / f"version_{MEDIAINFO_VERSION}"
    return output_dir, cli_file, version_file


def _windows_cached(cli_file: Path, version_file: Path) -> bool:
    return cli_file.is_file() and version_file.is_file()


def _install_windows_cli(
    system: str,
    machine: str,
    output_dir: Path,
    cli_file: Path,
) -> None:
    cli_filename = get_filename(system, machine)
    with TemporaryDirectory() as tmp_dir:
        cli_archive = Path(tmp_dir) / cli_filename
        download_file(get_url(system, machine), cli_archive)
        with TemporaryDirectory(
            dir=output_dir.parent,
            prefix="mediainfo-dvd-",
        ) as staging_dir:
            staging_path = Path(staging_dir)
            extract_windows(cli_archive, staging_path)
            staged_cli = staging_path / "MediaInfo.exe"
            if not staged_cli.is_file():
                raise RuntimeError(
                    "Failed to extract MediaInfo CLI for DVD processing"
                )
            staged_cli.replace(cli_file)


def _download_windows_mediainfo(
    base_dir: str,
    system: str,
    machine: str,
) -> str:
    if machine != "x86_64":
        raise RuntimeError(
            "MediaInfo 23.04 is unavailable for Windows ARM64; DVD language "
            "parsing cannot use the newer CLI"
        )
    output_dir, cli_file, version_file = _windows_paths(base_dir)
    if _windows_cached(cli_file, version_file):
        return str(cli_file)
    output_dir.mkdir(parents=True, exist_ok=True)
    _install_windows_cli(system, machine, output_dir, cli_file)
    version_file.write_text(
        f"MediaInfo {MEDIAINFO_VERSION}\n",
        encoding="utf-8",
    )
    return str(cli_file)


def _linux_supported(machine: str) -> bool:
    return machine in {"x86_64", "arm64"}


def _linux_paths(base_dir: str) -> tuple[Path, Path, Path, Path]:
    output_dir = Path(base_dir) / "bin" / "MI" / "linux" / "dvd"
    output_dir.mkdir(parents=True, exist_ok=True)
    logger.debug(f"[blue]Output: {output_dir}[/blue]")
    cli_file = output_dir / "mediainfo"
    lib_file = output_dir / "libmediainfo.so.0"
    version_file = output_dir / f"version_{MEDIAINFO_VERSION}"
    return output_dir, cli_file, lib_file, version_file


def _linux_cached(
    cli_file: Path,
    lib_file: Path,
    version_file: Path,
) -> bool:
    return cli_file.exists() and lib_file.exists() and version_file.exists()


def _linux_download_specs(
    system: str,
    machine: str,
) -> tuple[str, str, str, str]:
    cli_url = get_url(system, machine, "cli")
    cli_filename = get_filename(system, machine, "cli")
    lib_url = get_url(system, machine, "lib")
    lib_filename = get_filename(system, machine, "lib")
    logger.debug(f"[blue]MediaInfo CLI URL: {cli_url}[/blue]")
    logger.debug(f"[blue]MediaInfo CLI filename: {cli_filename}[/blue]")
    logger.debug(f"[blue]MediaInfo Library URL: {lib_url}[/blue]")
    logger.debug(f"[blue]MediaInfo Library filename: {lib_filename}[/blue]")
    return cli_url, cli_filename, lib_url, lib_filename


def _download_linux_archives(
    system: str,
    machine: str,
    output_dir: Path,
) -> None:
    cli_url, cli_filename, lib_url, lib_filename = _linux_download_specs(
        system,
        machine,
    )
    with TemporaryDirectory() as tmp_dir:
        temp_dir = Path(tmp_dir)
        cli_archive = temp_dir / cli_filename
        lib_archive = temp_dir / lib_filename
        download_file(cli_url, cli_archive)
        logger.debug(f"[green]Downloaded {cli_filename}[/green]")
        download_file(lib_url, lib_archive)
        logger.debug(f"[green]Downloaded {lib_filename}[/green]")
        extract_linux(cli_archive, lib_archive, output_dir)
        logger.debug("[green]Extracted library[/green]")


def _validate_linux_install(cli_file: Path, lib_file: Path) -> None:
    if not cli_file.exists():
        raise Exception(f"Failed to extract CLI binary to {cli_file}")
    if not lib_file.exists():
        raise Exception(f"Failed to extract library to {lib_file}")


def _download_linux_mediainfo(
    base_dir: str,
    system: str,
    machine: str,
) -> str | None:
    if not _linux_supported(machine):
        return None
    output_dir, cli_file, lib_file, version_file = _linux_paths(base_dir)
    if _linux_cached(cli_file, lib_file, version_file):
        logger.debug(
            f"[blue]MediaInfo CLI and Library {MEDIAINFO_VERSION} exist[/blue]"
        )
        return str(cli_file)
    logger.info(
        "[yellow]Downloading specific MediaInfo CLI and Library for DVD "
        f"processing: {MEDIAINFO_VERSION}...[/yellow]"
    )
    _download_linux_archives(system, machine, output_dir)
    _validate_linux_install(cli_file, lib_file)
    return str(cli_file)


def download_dvd_mediainfo(base_dir: str) -> str | None:
    system, machine = _platform_identity()
    if system == "windows":
        return _download_windows_mediainfo(base_dir, system, machine)
    if system != "linux":
        return None
    return _download_linux_mediainfo(base_dir, system, machine)
