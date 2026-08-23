# Upload Assistant © 2026 Audionut & wastaken7 — Licensed under UAPL v1.0
import hashlib
import platform
import stat
import tarfile
import zipfile
from pathlib import Path
from typing import Any, ClassVar, cast

import httpx  # pyright: ignore[reportMissingImports]

from src.integrations.runtime_tools.download_integrity import (
    MAX_ASSET_BYTES,
    download_bounded_asset,
    promote_files_with_rollback,
    sha256_file,
)
from src.integrations.runtime_tools.runtime_tool_paths import tool_install_dir

HTTPX: Any = cast(Any, httpx)


class ZentagBinaryManager:
    VERSION = "v0.3.0"
    CHECKSUMS: ClassVar[dict[str, str]] = {
        "zentag_0.3.0_darwin_amd64.tar.gz": "1e40bde40f50f88ca05f9ad647c2a49e0ace42f4325c413e674d4ae97e381b16",
        "zentag_0.3.0_darwin_arm64.tar.gz": "e4284dce7438f309bdb0b725a2013895d2afec6147aa5dcbbc57c746685013b0",
        "zentag_0.3.0_linux_amd64.tar.gz": "cd2f17ebef2e6c8586e2aef8655c12ca1769c7e2dbf147e6238f487469200cba",
        "zentag_0.3.0_linux_arm64.tar.gz": "7d47677949451b113ab3e9c199230797e1095ad9964afab04d57db319ba107dd",
        "zentag_0.3.0_windows_amd64.zip": "a49112e047361267ad404b664cb7501b4c3acde0dde27f689cc3079bb802cdd0",
        "zentag_0.3.0_windows_arm64.zip": "0acf0fc3b126a0fd654c547894e27eae71720a47bc1d023abf7e26fec494a48e",
    }
    BINARY_CHECKSUMS: ClassVar[dict[str, str]] = {
        "zentag_0.3.0_darwin_amd64.tar.gz": "7f906ed61292397cb4b59135df87908a07faa9560d3b70428df99803d46988aa",
        "zentag_0.3.0_darwin_arm64.tar.gz": "fb1f8c6b4804145569f90951e1383058e894f222af85e8f54f73a9d8a90771bc",
        "zentag_0.3.0_linux_amd64.tar.gz": "e5bc42ef5b9f090e58d703e45eafa3902cf530cdddba78f991979707588bb691",
        "zentag_0.3.0_linux_arm64.tar.gz": "5bddec5a217bc00bf18ebe8f7e39a36f8aca00f51abef208547096760aa62786",
        "zentag_0.3.0_windows_amd64.zip": "40d0e68fd875b59b672e36461a924c3ec862318ee0a188ebbf6d5c3e4ca4abaa",
        "zentag_0.3.0_windows_arm64.zip": "213f586572a4017a6ae6abab65c3478b9fc08578a9d5f6e329bdc57fd5f47a06",
    }

    @classmethod
    def _platform_asset(cls) -> tuple[str, str, str, str]:
        system = platform.system().lower()
        machine = platform.machine().lower()
        arch = {
            "arm64": "arm64",
            "aarch64": "arm64",
            "x86_64": "amd64",
            "amd64": "amd64",
        }.get(machine, "")
        os_name = system
        if os_name not in {"linux", "darwin", "windows"} or not arch:
            raise RuntimeError(
                f"Unsupported zentag platform: {system} {machine}"
            )
        extension = "zip" if os_name == "windows" else "tar.gz"
        version_number = cls.VERSION.lstrip("v")
        asset = f"zentag_{version_number}_{os_name}_{arch}.{extension}"
        return os_name, arch, extension, asset

    @classmethod
    def _install_paths(
        cls, base_dir: str | Path, os_name: str, arch: str
    ) -> tuple[Path, Path, Path]:
        target_dir = tool_install_dir(base_dir, "zentag", f"{os_name}/{arch}")
        binary = target_dir / (
            "zentag.exe" if os_name == "windows" else "zentag"
        )
        marker = target_dir / cls.VERSION
        return target_dir, binary, marker

    @classmethod
    def _is_cached_binary(cls, binary: Path, marker: Path, asset: str) -> bool:
        expected_binary = cls.BINARY_CHECKSUMS.get(asset, "")
        if not expected_binary:
            return False
        if not binary.is_file():
            return False
        if sha256_file(binary) != expected_binary:
            return False
        if not marker.is_file():
            return False
        return marker.read_text(encoding="utf-8").strip() == cls.VERSION

    @classmethod
    def _verify_archive_checksum(cls, archive_path: Path, asset: str) -> None:
        expected = cls.CHECKSUMS.get(asset, "")
        if not expected or sha256_file(archive_path) != expected:
            raise RuntimeError(
                f"zentag checksum verification failed for {asset}"
            )

    @staticmethod
    def _zip_member(
        archive: zipfile.ZipFile, binary_name: str
    ) -> zipfile.ZipInfo | None:
        for item in archive.infolist():
            if Path(item.filename).name == binary_name:
                return item
        return None

    @classmethod
    def _zip_payload(
        cls, archive_path: Path, binary: Path, asset: str
    ) -> bytes:
        with zipfile.ZipFile(archive_path) as archive:
            member = cls._zip_member(archive, binary.name)
            if member is None:
                raise RuntimeError(f"zentag binary not found in {asset}")
            if member.file_size > MAX_ASSET_BYTES:
                raise RuntimeError(
                    f"zentag binary exceeds the {MAX_ASSET_BYTES}-byte limit"
                )
            return archive.read(member)

    @staticmethod
    def _tar_member(
        archive: tarfile.TarFile, binary_name: str
    ) -> tarfile.TarInfo | None:
        for item in archive.getmembers():
            if not item.isfile():
                continue
            if Path(item.name).name == binary_name:
                return item
        return None

    @classmethod
    def _tar_payload(
        cls, archive_path: Path, binary: Path, asset: str
    ) -> bytes:
        with tarfile.open(archive_path, mode="r:gz") as archive:
            member = cls._tar_member(archive, binary.name)
            if member is None:
                raise RuntimeError(f"zentag binary not found in {asset}")
            if member.size > MAX_ASSET_BYTES:
                raise RuntimeError(
                    f"zentag binary exceeds the {MAX_ASSET_BYTES}-byte limit"
                )
            extracted = archive.extractfile(member)
            if extracted is None:
                raise RuntimeError(f"zentag binary not found in {asset}")
            payload = extracted.read(MAX_ASSET_BYTES + 1)
            if len(payload) > MAX_ASSET_BYTES:
                raise RuntimeError(
                    f"zentag binary exceeds the {MAX_ASSET_BYTES}-byte limit"
                )
            return payload

    @classmethod
    def _archive_payload(
        cls,
        extension: str,
        archive_path: Path,
        binary: Path,
        asset: str,
    ) -> bytes:
        if extension == "zip":
            return cls._zip_payload(archive_path, binary, asset)
        return cls._tar_payload(archive_path, binary, asset)

    @classmethod
    async def _download_payload(
        cls,
        target_dir: Path,
        binary: Path,
        extension: str,
        asset: str,
    ) -> bytes:
        release_url = f"https://github.com/znth-cx/zentag/releases/download/{cls.VERSION}"
        downloaded_archive = target_dir / f".{asset}.download"
        try:
            async with HTTPX.AsyncClient(
                timeout=120.0, follow_redirects=True
            ) as client:
                await download_bounded_asset(
                    client, f"{release_url}/{asset}", downloaded_archive
                )
            cls._verify_archive_checksum(downloaded_archive, asset)
            return cls._archive_payload(
                extension, downloaded_archive, binary, asset
            )
        finally:
            downloaded_archive.unlink(missing_ok=True)

    @classmethod
    def _verify_binary_payload(cls, payload: bytes, asset: str) -> None:
        expected_binary = cls.BINARY_CHECKSUMS.get(asset, "")
        if (
            not expected_binary
            or hashlib.sha256(payload).hexdigest() != expected_binary
        ):
            raise RuntimeError(
                f"zentag binary checksum verification failed for {asset}"
            )

    @staticmethod
    def _stale_markers(target_dir: Path, marker: Path) -> list[Path]:
        stale: list[Path] = []
        for candidate in target_dir.iterdir():
            if not candidate.is_file():
                continue
            if not candidate.name.startswith("v"):
                continue
            if candidate == marker:
                continue
            stale.append(candidate)
        return stale

    @classmethod
    def _stage_payload(
        cls,
        target_dir: Path,
        binary: Path,
        marker: Path,
        os_name: str,
        payload: bytes,
    ) -> None:
        staged_binary = target_dir / f".{binary.name}.staged"
        staged_marker = target_dir / f".{cls.VERSION}.staged"
        staged_binary.write_bytes(payload)
        if os_name != "windows":
            staged_binary.chmod(staged_binary.stat().st_mode | stat.S_IEXEC)
        staged_marker.write_text(cls.VERSION, encoding="utf-8")
        promote_files_with_rollback(
            [(staged_binary, binary), (staged_marker, marker)],
            target_dir / ".zentag-backup",
            remove_targets=cls._stale_markers(target_dir, marker),
        )

    @classmethod
    async def ensure_binary(cls, base_dir: str | Path) -> str:
        os_name, arch, extension, asset = cls._platform_asset()
        target_dir, binary, marker = cls._install_paths(
            base_dir, os_name, arch
        )
        if cls._is_cached_binary(binary, marker, asset):
            return str(binary)
        payload = await cls._download_payload(
            target_dir, binary, extension, asset
        )
        cls._verify_binary_payload(payload, asset)
        cls._stage_payload(target_dir, binary, marker, os_name, payload)
        return str(binary)
