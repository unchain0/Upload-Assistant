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
    async def ensure_binary(cls, base_dir: str | Path) -> str:
        system = platform.system().lower()
        machine = platform.machine().lower()
        arch = (
            "arm64"
            if machine in {"arm64", "aarch64"}
            else "amd64"
            if machine in {"x86_64", "amd64"}
            else ""
        )
        os_name = "darwin" if system == "darwin" else system
        if os_name not in {"linux", "darwin", "windows"} or not arch:
            raise RuntimeError(
                f"Unsupported zentag platform: {system} {machine}"
            )

        version_number = cls.VERSION.lstrip("v")
        extension = "zip" if os_name == "windows" else "tar.gz"
        asset = f"zentag_{version_number}_{os_name}_{arch}.{extension}"
        target_dir = tool_install_dir(base_dir, "zentag", f"{os_name}/{arch}")
        binary = target_dir / (
            "zentag.exe" if os_name == "windows" else "zentag"
        )
        marker = target_dir / cls.VERSION
        expected_binary = cls.BINARY_CHECKSUMS.get(asset, "")
        cached_digest = sha256_file(binary) if binary.is_file() else ""
        if (
            expected_binary
            and cached_digest == expected_binary
            and marker.is_file()
            and marker.read_text(encoding="utf-8").strip() == cls.VERSION
        ):
            return str(binary)

        release_url = f"https://github.com/znth-cx/zentag/releases/download/{cls.VERSION}"
        downloaded_archive = target_dir / f".{asset}.download"
        try:
            async with HTTPX.AsyncClient(
                timeout=120.0, follow_redirects=True
            ) as client:
                await download_bounded_asset(
                    client, f"{release_url}/{asset}", downloaded_archive
                )
            expected = cls.CHECKSUMS.get(asset, "")
            if not expected or sha256_file(downloaded_archive) != expected:
                raise RuntimeError(
                    f"zentag checksum verification failed for {asset}"
                )

            if extension == "zip":
                with zipfile.ZipFile(downloaded_archive) as archive:
                    member = next(
                        (
                            item
                            for item in archive.infolist()
                            if Path(item.filename).name == binary.name
                        ),
                        None,
                    )
                    if member is None:
                        raise RuntimeError(
                            f"zentag binary not found in {asset}"
                        )
                    if member.file_size > MAX_ASSET_BYTES:
                        raise RuntimeError(
                            f"zentag binary exceeds the {MAX_ASSET_BYTES}-byte limit"
                        )
                    payload = archive.read(member)
            else:
                with tarfile.open(downloaded_archive, mode="r:gz") as archive:
                    member = next(
                        (
                            item
                            for item in archive.getmembers()
                            if item.isfile()
                            and Path(item.name).name == binary.name
                        ),
                        None,
                    )
                    if member is not None and member.size > MAX_ASSET_BYTES:
                        raise RuntimeError(
                            f"zentag binary exceeds the {MAX_ASSET_BYTES}-byte limit"
                        )
                    extracted = (
                        archive.extractfile(member)
                        if member is not None
                        else None
                    )
                    if extracted is None:
                        raise RuntimeError(
                            f"zentag binary not found in {asset}"
                        )
                    payload = extracted.read(MAX_ASSET_BYTES + 1)
                    if len(payload) > MAX_ASSET_BYTES:
                        raise RuntimeError(
                            f"zentag binary exceeds the {MAX_ASSET_BYTES}-byte limit"
                        )
        finally:
            downloaded_archive.unlink(missing_ok=True)

        if (
            not expected_binary
            or hashlib.sha256(payload).hexdigest() != expected_binary
        ):
            raise RuntimeError(
                f"zentag binary checksum verification failed for {asset}"
            )

        staged_binary = target_dir / f".{binary.name}.staged"
        staged_marker = target_dir / f".{cls.VERSION}.staged"
        staged_binary.write_bytes(payload)
        if os_name != "windows":
            staged_binary.chmod(staged_binary.stat().st_mode | stat.S_IEXEC)
        staged_marker.write_text(cls.VERSION, encoding="utf-8")
        stale_markers = [
            candidate
            for candidate in target_dir.iterdir()
            if candidate.is_file()
            and candidate.name.startswith("v")
            and candidate != marker
        ]
        promote_files_with_rollback(
            [(staged_binary, binary), (staged_marker, marker)],
            target_dir / ".zentag-backup",
            remove_targets=stale_markers,
        )
        return str(binary)
