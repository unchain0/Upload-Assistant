# Upload Assistant © 2026 Audionut & wastaken7 — Licensed under UAPL v1.0
import hashlib
import io
import os
import platform
import shutil
import stat
import tarfile
import zipfile
from pathlib import Path
from typing import Any, ClassVar, cast

import httpx  # pyright: ignore[reportMissingImports]

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

    @classmethod
    async def ensure_binary(cls, base_dir: str | Path) -> str:
        installed = shutil.which("zentag")
        if installed:
            return installed

        system = platform.system().lower()
        machine = platform.machine().lower()
        arch = "arm64" if machine in {"arm64", "aarch64"} else "amd64" if machine in {"x86_64", "amd64"} else ""
        os_name = "darwin" if system == "darwin" else system
        if os_name not in {"linux", "darwin", "windows"} or not arch:
            raise RuntimeError(f"Unsupported zentag platform: {system} {machine}")

        version_number = cls.VERSION.lstrip("v")
        extension = "zip" if os_name == "windows" else "tar.gz"
        asset = f"zentag_{version_number}_{os_name}_{arch}.{extension}"
        target_dir = Path(base_dir) / "bin" / "zentag" / os_name / arch
        target_dir.mkdir(parents=True, exist_ok=True)
        binary = target_dir / ("zentag.exe" if os_name == "windows" else "zentag")
        marker = target_dir / cls.VERSION
        if binary.is_file() and marker.is_file() and (os_name == "windows" or os.access(binary, os.X_OK)):
            return str(binary)

        release_url = f"https://github.com/znth-cx/zentag/releases/download/{cls.VERSION}"
        async with HTTPX.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            archive_response = await client.get(f"{release_url}/{asset}")
        archive_response.raise_for_status()
        expected = cls.CHECKSUMS.get(asset, "")
        actual = hashlib.sha256(archive_response.content).hexdigest()
        if not expected or actual != expected:
            raise RuntimeError(f"zentag checksum verification failed for {asset}")

        if extension == "zip":
            with zipfile.ZipFile(io.BytesIO(archive_response.content)) as archive:
                member = next((name for name in archive.namelist() if Path(name).name == binary.name), "")
                if not member:
                    raise RuntimeError(f"zentag binary not found in {asset}")
                payload = archive.read(member)
        else:
            with tarfile.open(fileobj=io.BytesIO(archive_response.content), mode="r:gz") as archive:
                member = next((item for item in archive.getmembers() if item.isfile() and Path(item.name).name == binary.name), None)
                extracted = archive.extractfile(member) if member is not None else None
                if extracted is None:
                    raise RuntimeError(f"zentag binary not found in {asset}")
                payload = extracted.read()

        binary.write_bytes(payload)
        if os_name != "windows":
            binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
        marker.write_text(cls.VERSION, encoding="utf-8")
        return str(binary)
