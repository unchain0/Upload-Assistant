"""Lazy downloader for the third-party dynamic HDR metadata tools."""

from __future__ import annotations

import asyncio
import platform
import shutil
import stat
import tarfile
import zipfile
from pathlib import Path

import httpx

from src.integrations.observability.console import logger
from src.integrations.runtime_tools.download_integrity import (
    MAX_EXTRACTED_BYTES,
    download_bounded_asset,
    promote_files_with_rollback,
    safe_extract_tar,
    safe_extract_zip,
    sha256_file,
)
from src.integrations.runtime_tools.runtime_tool_paths import tool_install_dir

TOOLS = {
    "dovi": {
        "command": "dovi_tool",
        "repository": "quietvoid/dovi_tool",
        "version": "2.3.3",
    },
    "hdr10plus": {
        "command": "hdr10plus_tool",
        "repository": "quietvoid/hdr10plus_tool",
        "version": "1.7.2",
    },
}

ASSET_SHA256 = {
    "dovi_tool-2.3.3-aarch64-pc-windows-msvc.zip": "559ed634ef0b956ab89ed965b4920371bb228983f105d99fafeb372a8190c872",
    "dovi_tool-2.3.3-aarch64-unknown-linux-musl.tar.gz": "daf538c275f4e702219ce8eb61db28382193ac9d0126e1ef4185a88303af4485",
    "dovi_tool-2.3.3-universal-macOS.zip": "b113c83fed2d8d7ed9e43f0428d02fa0d0030e20965fc24a3cd4d48597d88685",
    "dovi_tool-2.3.3-x86_64-pc-windows-msvc.zip": "37ae198f2a535c910befad39fc09c21cded76bf3ef2d5459d542e58c2c158311",
    "dovi_tool-2.3.3-x86_64-unknown-linux-musl.tar.gz": "5dae82cb2becd3b9fd726127f936a8d32635e60746d16238fdfded12aa05988c",
    "hdr10plus_tool-1.7.2-aarch64-pc-windows-msvc.zip": "0cc1cd6ae9fb1115e5dc3d1f6daed47c486410ad5731f60a298e5d78fe995d6b",
    "hdr10plus_tool-1.7.2-aarch64-unknown-linux-musl.tar.gz": "5fb90607cd94296640f1fc2355207b8107b67baac96d37481423d08a9fce437d",
    "hdr10plus_tool-1.7.2-universal-macOS.zip": "d76977ed2ea90f8d6bce9035e37ea9dbffcead4725ceb1acf455c25d8658ff28",
    "hdr10plus_tool-1.7.2-x86_64-pc-windows-msvc.zip": "82b2d560073941b14c6511a431f429e33e134e5caefb60d7e8f6f6e6da8e16ba",
    "hdr10plus_tool-1.7.2-x86_64-unknown-linux-musl.tar.gz": "06385f37a639d61ba21d4be3150c863846933bc3b58110e094d8fc8f1c2249f2",
}


def _machine_arch(system: str, machine: str) -> str:
    if machine in {"arm64", "aarch64"}:
        return "aarch64"
    if machine in {"amd64", "x86_64"}:
        return "x86_64"
    raise RuntimeError(
        f"Dynamic HDR plots are not supported on {system} {machine}"
    )


def _platform_asset(
    tool: str,
    version: str,
    system: str,
    machine: str,
    arch: str,
) -> tuple[str, str]:
    if system == "windows":
        return f"{tool}_tool-{version}-{arch}-pc-windows-msvc.zip", ".exe"
    if system == "darwin":
        return f"{tool}_tool-{version}-universal-macOS.zip", ""
    if system == "linux":
        return f"{tool}_tool-{version}-{arch}-unknown-linux-musl.tar.gz", ""
    raise RuntimeError(
        f"Dynamic HDR plots are not supported on {system} {machine}"
    )


def _asset_name(tool: str) -> tuple[str, str]:
    """Return the release asset name and executable extension for this host."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    version = TOOLS[tool]["version"]
    arch = _machine_arch(system, machine)
    return _platform_asset(tool, version, system, machine, arch)


def _verify_checksum_file(asset: str, path: Path) -> None:
    expected_checksum = ASSET_SHA256.get(asset)
    if expected_checksum is None:
        raise RuntimeError(f"Missing checksum for {asset}")
    actual_checksum = sha256_file(path)
    if actual_checksum != expected_checksum:
        raise RuntimeError(f"Checksum mismatch for {asset}")


def _safe_extract(archive: Path, destination: Path) -> None:
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as contents:
            safe_extract_zip(
                contents, destination, max_bytes=MAX_EXTRACTED_BYTES
            )
    else:
        with tarfile.open(archive, "r:gz") as contents:
            safe_extract_tar(
                contents, destination, max_bytes=MAX_EXTRACTED_BYTES
            )


def _cached_tool_path(binary: Path, version_file: Path) -> str | None:
    if not binary.is_file() or binary.stat().st_size <= 0:
        return None
    return str(binary) if version_file.is_file() else None


def _tool_target_paths(
    base_dir: str,
    command: str,
    extension: str,
    version: str,
) -> tuple[str, Path, Path, Path]:
    system = platform.system().lower()
    machine = platform.machine().lower()
    target_dir = tool_install_dir(base_dir, command, f"{system}/{machine}")
    binary = target_dir / f"{command}{extension}"
    return system, target_dir, binary, target_dir / version


def _prepare_download_dir(target_dir: Path) -> Path:
    staging = target_dir / ".download"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    return staging


def _release_url(tool: str, asset: str) -> str:
    definition = TOOLS[tool]
    return (
        f"https://github.com/{definition['repository']}/releases/download/"
        f"{definition['version']}/{asset}"
    )


async def _download_tool_archive(
    tool: str,
    command: str,
    asset: str,
    archive: Path,
) -> None:
    logger.info(
        f"[yellow]Downloading {command} for dynamic HDR plots...[/yellow]"
    )
    async with httpx.AsyncClient(
        timeout=90.0, follow_redirects=True
    ) as client:
        await download_bounded_asset(
            client, _release_url(tool, asset), archive
        )
    await asyncio.to_thread(_verify_checksum_file, asset, archive)


def _extracted_tool_candidate(
    staging: Path, command: str, extension: str, asset: str
) -> Path:
    candidates = [
        path
        for path in staging.rglob(f"{command}{extension}")
        if path.is_file()
    ]
    if not candidates:
        raise RuntimeError(f"{asset} did not contain {command}{extension}")
    return candidates[0]


def _stage_tool_binary(
    candidate: Path,
    staging: Path,
    command: str,
    extension: str,
    system: str,
) -> Path:
    staged_binary = staging / f".{command}{extension}.staged"
    shutil.move(str(candidate), staged_binary)
    if system != "windows":
        staged_binary.chmod(
            staged_binary.stat().st_mode
            | stat.S_IXUSR
            | stat.S_IXGRP
            | stat.S_IXOTH
        )
    return staged_binary


def _stage_tool_version(staging: Path, tool: str, command: str) -> Path:
    version = TOOLS[tool]["version"]
    staged_version = staging / version
    staged_version.write_text(f"{command} {version}\n", encoding="utf-8")
    return staged_version


def _stale_tool_markers(
    target_dir: Path, binary: Path, version_file: Path
) -> list[Path]:
    return [
        candidate
        for candidate in target_dir.iterdir()
        if candidate.is_file()
        and candidate != binary
        and candidate != version_file
    ]


def _promote_tool(
    target_dir: Path,
    binary: Path,
    version_file: Path,
    staged_binary: Path,
    staged_version: Path,
) -> None:
    promote_files_with_rollback(
        [(staged_binary, binary), (staged_version, version_file)],
        target_dir / ".dynamic-hdr-backup",
        remove_targets=_stale_tool_markers(target_dir, binary, version_file),
    )


async def _install_tool(
    tool: str,
    command: str,
    asset: str,
    extension: str,
    system: str,
    target_dir: Path,
    binary: Path,
    version_file: Path,
) -> str:
    staging = _prepare_download_dir(target_dir)
    archive = staging / asset
    try:
        await _download_tool_archive(tool, command, asset, archive)
        await asyncio.to_thread(_safe_extract, archive, staging)
        candidate = _extracted_tool_candidate(
            staging, command, extension, asset
        )
        staged_binary = _stage_tool_binary(
            candidate, staging, command, extension, system
        )
        staged_version = _stage_tool_version(staging, tool, command)
        _promote_tool(
            target_dir, binary, version_file, staged_binary, staged_version
        )
        return str(binary)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


async def get_tool(base_dir: str, tool: str) -> str:
    """Return a PATH tool or download the pinned release below ``bin/``."""
    command = TOOLS[tool]["command"]
    if installed := shutil.which(command):
        return installed
    asset, extension = _asset_name(tool)
    version = TOOLS[tool]["version"]
    system, target_dir, binary, version_file = _tool_target_paths(
        base_dir, command, extension, version
    )
    cached = _cached_tool_path(binary, version_file)
    if cached is not None:
        return cached
    return await _install_tool(
        tool,
        command,
        asset,
        extension,
        system,
        target_dir,
        binary,
        version_file,
    )
