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
    "dovi": {"command": "dovi_tool", "repository": "quietvoid/dovi_tool", "version": "2.3.3"},
    "hdr10plus": {"command": "hdr10plus_tool", "repository": "quietvoid/hdr10plus_tool", "version": "1.7.2"},
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


def _asset_name(tool: str) -> tuple[str, str]:
    """Return the release asset name and executable extension for this host."""
    system, machine = platform.system().lower(), platform.machine().lower()
    version = TOOLS[tool]["version"]
    if machine in {"arm64", "aarch64"}:
        arch = "aarch64"
    elif machine in {"amd64", "x86_64"}:
        arch = "x86_64"
    else:
        raise RuntimeError(f"Dynamic HDR plots are not supported on {system} {machine}")
    if system == "windows":
        return f"{tool}_tool-{version}-{arch}-pc-windows-msvc.zip", ".exe"
    if system == "darwin":
        return f"{tool}_tool-{version}-universal-macOS.zip", ""
    if system == "linux" and arch in {"x86_64", "aarch64"}:
        return f"{tool}_tool-{version}-{arch}-unknown-linux-musl.tar.gz", ""
    raise RuntimeError(f"Dynamic HDR plots are not supported on {system} {machine}")


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
            safe_extract_zip(contents, destination, max_bytes=MAX_EXTRACTED_BYTES)
    else:
        with tarfile.open(archive, "r:gz") as contents:
            safe_extract_tar(contents, destination, max_bytes=MAX_EXTRACTED_BYTES)


async def get_tool(base_dir: str, tool: str) -> str:
    """Return a PATH tool or download the pinned release below ``bin/``."""
    command = TOOLS[tool]["command"]
    if installed := shutil.which(command):
        return installed

    asset, extension = _asset_name(tool)
    system = platform.system().lower()
    machine = platform.machine().lower()
    target_dir = tool_install_dir(base_dir, command, f"{system}/{machine}")
    binary = target_dir / f"{command}{extension}"
    version_file = target_dir / TOOLS[tool]["version"]
    if binary.is_file() and binary.stat().st_size > 0 and version_file.is_file():
        return str(binary)

    staging = target_dir / ".download"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    archive = staging / asset
    url = f"https://github.com/{TOOLS[tool]['repository']}/releases/download/{TOOLS[tool]['version']}/{asset}"
    logger.info(f"[yellow]Downloading {command} for dynamic HDR plots...[/yellow]")
    try:
        async with httpx.AsyncClient(timeout=90.0, follow_redirects=True) as client:
            await download_bounded_asset(client, url, archive)
        await asyncio.to_thread(_verify_checksum_file, asset, archive)
        await asyncio.to_thread(_safe_extract, archive, staging)
        candidates = [path for path in staging.rglob(f"{command}{extension}") if path.is_file()]
        if not candidates:
            raise RuntimeError(f"{asset} did not contain {command}{extension}")
        staged_binary = staging / f".{command}{extension}.staged"
        shutil.move(str(candidates[0]), staged_binary)
        if system != "windows":
            staged_binary.chmod(staged_binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        staged_version = staging / TOOLS[tool]["version"]
        staged_version.write_text(f"{command} {TOOLS[tool]['version']}\n", encoding="utf-8")
        stale_markers = [candidate for candidate in target_dir.iterdir() if candidate.is_file() and candidate != binary and candidate != version_file]
        promote_files_with_rollback(
            [(staged_binary, binary), (staged_version, version_file)],
            target_dir / ".dynamic-hdr-backup",
            remove_targets=stale_markers,
        )
        return str(binary)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
