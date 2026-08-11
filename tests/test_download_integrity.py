import asyncio
import io
import tarfile
import zipfile
from pathlib import Path

import httpx
import pytest

from bin.download_integrity import download_bounded_asset, download_bounded_asset_sync, safe_extract_tar, safe_extract_zip


class _Response:
    def __init__(self, chunks: list[bytes], content_length: int | None = None, delay: float = 0) -> None:
        self.chunks = chunks
        self.headers = {} if content_length is None else {"content-length": str(content_length)}
        self.delay = delay

    async def __aenter__(self):  # type: ignore[no-untyped-def]
        return self

    async def __aexit__(self, *_args):  # type: ignore[no-untyped-def]
        return None

    def raise_for_status(self) -> None:
        return None

    async def aiter_bytes(self, chunk_size: int):  # type: ignore[no-untyped-def]  # noqa: ARG002
        for chunk in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield chunk


class _Client:
    def __init__(self, response: _Response) -> None:
        self.response = response

    def stream(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        return self.response

    async def __aenter__(self):  # type: ignore[no-untyped-def]
        return self

    async def __aexit__(self, *_args):  # type: ignore[no-untyped-def]
        return None


def test_bounded_download_rejects_oversized_content_length(tmp_path: Path) -> None:
    destination = tmp_path / "asset"
    response = _Response([b"small"], content_length=11)

    with pytest.raises(RuntimeError, match="10-byte limit"):
        asyncio.run(download_bounded_asset(_Client(response), "https://example.invalid/asset", destination, max_bytes=10))

    assert not destination.exists()  # noqa: S101


def test_bounded_download_rejects_stream_that_exceeds_limit(tmp_path: Path) -> None:
    destination = tmp_path / "asset"
    response = _Response([b"12345", b"678901"])

    with pytest.raises(RuntimeError, match="10-byte limit"):
        asyncio.run(download_bounded_asset(_Client(response), "https://example.invalid/asset", destination, max_bytes=10))

    assert not destination.exists()  # noqa: S101


def test_bounded_download_enforces_total_timeout(tmp_path: Path) -> None:
    destination = tmp_path / "asset"
    response = _Response([b"slow"], delay=0.05)

    with pytest.raises(TimeoutError):
        asyncio.run(download_bounded_asset(_Client(response), "https://example.invalid/asset", destination, timeout_seconds=0.01))

    assert not destination.exists()  # noqa: S101


def test_sync_bootstrap_uses_interruptible_total_timeout(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    destination = tmp_path / "asset"
    response = _Response([b"slow"], delay=0.05)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: _Client(response))

    with pytest.raises(TimeoutError):
        download_bounded_asset_sync("https://example.invalid/asset", destination, timeout_seconds=0.01)

    assert not destination.exists()  # noqa: S101


def test_safe_extract_zip_rejects_cumulative_expanded_size(tmp_path: Path) -> None:
    archive_path = tmp_path / "asset.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("one", b"123456")
        archive.writestr("two", b"123456")

    with zipfile.ZipFile(archive_path) as archive, pytest.raises(RuntimeError, match="expanded-size limit"):
        safe_extract_zip(archive, tmp_path / "output", max_bytes=10)


def test_safe_extract_tar_rejects_links(tmp_path: Path) -> None:
    archive_path = tmp_path / "asset.tar"
    with tarfile.open(archive_path, "w") as archive:
        member = tarfile.TarInfo("link")
        member.type = tarfile.SYMTYPE
        member.linkname = "target"
        archive.addfile(member, io.BytesIO())

    with tarfile.open(archive_path) as archive, pytest.raises(RuntimeError, match="Unsupported archive member"):
        safe_extract_tar(archive, tmp_path / "output")
