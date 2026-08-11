import asyncio
import io
import tarfile
from pathlib import Path

import pytest

from bin import get_nyuu
from bin.get_nyuu import NyuuBinaryManager


def test_nyuu_archive_without_binary_does_not_write_version_marker(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async def write_invalid_archive(_client, _url, destination, _asset_name):  # type: ignore[no-untyped-def]
        payload = b"not nyuu"
        with tarfile.open(destination, "w:xz") as archive:
            member = tarfile.TarInfo("README.txt")
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))

    monkeypatch.setattr(get_nyuu, "download_verified_asset", write_invalid_archive)
    monkeypatch.setattr(get_nyuu.platform, "system", lambda: "Linux")
    monkeypatch.setattr(get_nyuu.platform, "machine", lambda: "x86_64")

    with pytest.raises(Exception, match="does not contain the expected nyuu executable"):
        asyncio.run(NyuuBinaryManager.ensure_nyuu_binary(tmp_path))

    output = tmp_path / "bin" / "nyuu" / "linux" / "amd64"
    assert not (output / "v0.4.2").exists()  # noqa: S101
    assert not (output / "nyuu").exists()  # noqa: S101
