# Upload Assistant © 2026 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import hashlib
import io
import os
import platform
import shutil
import stat
import tarfile
import zipfile
from pathlib import Path
from typing import Any, cast

import httpx  # pyright: ignore[reportMissingImports]

HTTPX: Any = cast(Any, httpx)


class ZentagBinaryManager:
    VERSION = "v0.3.0"

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
            archive_response, checksum_response = cast(
                tuple[Any, Any],
                await asyncio.gather(
                client.get(f"{release_url}/{asset}"),
                client.get(f"{release_url}/checksums.txt"),
                ),
            )
        archive_response.raise_for_status()
        checksum_response.raise_for_status()
        expected = next((line.split()[0] for line in checksum_response.text.splitlines() if line.split()[1:] == [asset]), "")
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
