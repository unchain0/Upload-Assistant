from __future__ import annotations

import asyncio
import hashlib
import io
import stat
import tarfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from src.integrations.runtime_tools import download_integrity as integrity


def _zip_info(name: str, *, mode: int, size: int = 0) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.external_attr = mode << 16
    info.file_size = size
    return info


def test_safe_destination_rejects_absolute_traversal_and_symlink_escape(
    tmp_path: Path,
) -> None:
    base = tmp_path / "base"
    base.mkdir()
    with pytest.raises(RuntimeError, match="Unsafe archive member"):
        integrity._safe_destination(base, "/absolute")
    with pytest.raises(RuntimeError, match="Unsafe archive member"):
        integrity._safe_destination(base, "../escape")

    outside = tmp_path / "outside"
    outside.mkdir()
    (base / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(RuntimeError, match="escapes destination"):
        integrity._safe_destination(base, "link/file")


def test_safe_extract_zip_directories_links_special_files_and_regular_member(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "asset.zip"
    directory = _zip_info("directory/", mode=stat.S_IFDIR | 0o755)
    file_info = _zip_info("directory/tool", mode=stat.S_IFREG | 0o644)
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(directory, b"")
        archive.writestr(file_info, b"tool")
    with zipfile.ZipFile(archive_path) as archive:
        integrity.safe_extract_zip(archive, tmp_path / "output")
    assert (tmp_path / "output" / "directory" / "tool").read_bytes() == b"tool"

    link_path = tmp_path / "link.zip"
    with zipfile.ZipFile(link_path, "w") as archive:
        archive.writestr(
            _zip_info("link", mode=stat.S_IFLNK | 0o777), b"target"
        )
    with (
        zipfile.ZipFile(link_path) as archive,
        pytest.raises(RuntimeError, match="links are not allowed"),
    ):
        integrity.safe_extract_zip(archive, tmp_path / "link-output")

    special_path = tmp_path / "special.zip"
    with zipfile.ZipFile(special_path, "w") as archive:
        archive.writestr(
            _zip_info("fifo", mode=stat.S_IFIFO | 0o644), b"value"
        )
    with (
        zipfile.ZipFile(special_path) as archive,
        pytest.raises(RuntimeError, match="Unsupported archive member"),
    ):
        integrity.safe_extract_zip(archive, tmp_path / "special-output")

    large_path = tmp_path / "large.zip"
    with zipfile.ZipFile(large_path, "w") as archive:
        archive.writestr("large", b"12345")
    with (
        zipfile.ZipFile(large_path) as archive,
        pytest.raises(RuntimeError, match="expanded-size limit"),
    ):
        integrity.safe_extract_zip(
            archive, tmp_path / "large-output", max_bytes=4
        )


def test_extract_zip_regular_member_rejects_invalid_and_extracts(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "asset.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("tool", b"binary")
        archive.writestr("directory/", b"")
    with zipfile.ZipFile(archive_path) as archive:
        destination = tmp_path / "installed" / "tool"
        integrity.extract_zip_regular_member(archive, "tool", destination)
        assert destination.read_bytes() == b"binary"
        with pytest.raises(RuntimeError, match="not a regular file"):
            integrity.extract_zip_regular_member(
                archive, "directory/", tmp_path / "directory"
            )
        with pytest.raises(RuntimeError, match="expanded-size limit"):
            integrity.extract_zip_regular_member(
                archive, "tool", tmp_path / "too-large", max_bytes=2
            )


def test_safe_extract_tar_directory_regular_limit_and_missing_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_path = tmp_path / "asset.tar"
    with tarfile.open(archive_path, "w") as archive:
        directory = tarfile.TarInfo("directory")
        directory.type = tarfile.DIRTYPE
        archive.addfile(directory)
        member = tarfile.TarInfo("directory/tool")
        member.size = 4
        archive.addfile(member, io.BytesIO(b"tool"))
    with tarfile.open(archive_path) as archive:
        integrity.safe_extract_tar(archive, tmp_path / "output")
    assert (tmp_path / "output" / "directory" / "tool").read_bytes() == b"tool"

    with (
        tarfile.open(archive_path) as archive,
        pytest.raises(RuntimeError, match="expanded-size limit"),
    ):
        integrity.safe_extract_tar(archive, tmp_path / "limited", max_bytes=3)

    with tarfile.open(archive_path) as archive:
        monkeypatch.setattr(archive, "extractfile", lambda _member: None)
        with pytest.raises(RuntimeError, match="Unable to read"):
            integrity.safe_extract_tar(archive, tmp_path / "missing-stream")


def test_safe_extract_tar_rejects_special_member(tmp_path: Path) -> None:
    archive_path = tmp_path / "special.tar"
    with tarfile.open(archive_path, "w") as archive:
        member = tarfile.TarInfo("link")
        member.type = tarfile.SYMTYPE
        member.linkname = "target"
        archive.addfile(member)

    with (
        tarfile.open(archive_path) as archive,
        pytest.raises(RuntimeError, match="Unsupported archive member"),
    ):
        integrity.safe_extract_tar(archive, tmp_path / "output")


def test_promotion_helper_error_edges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    first_target = tmp_path / "one" / "tool"
    duplicate_target = tmp_path / "two" / "tool"
    with pytest.raises(ValueError, match="unique filenames"):
        integrity._promotion_targets(
            [(source, first_target)], [duplicate_target]
        )

    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    with pytest.raises(RuntimeError, match="Recovery backup"):
        integrity._prepare_backup_dir(backup_dir)

    backup = tmp_path / "saved"
    target = tmp_path / "restored"
    backup.write_bytes(b"old")
    original_replace = Path.replace

    def fail_restore(path: Path, destination: Path) -> Path:
        if path == backup:
            raise OSError("restore failed")
        return original_replace(path, destination)

    monkeypatch.setattr(Path, "replace", fail_restore)
    errors = integrity._restore_backup_files([(backup, target)])
    assert len(errors) == 1
    assert str(errors[0]) == "restore failed"


def test_promote_success_remove_target_and_complete_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "staged-tool"
    source.write_bytes(b"new")
    target = tmp_path / "tool"
    target.write_bytes(b"old")
    stale = tmp_path / "stale"
    stale.write_bytes(b"stale")
    backup = tmp_path / ".backup"

    missing = tmp_path / "missing"
    integrity.promote_files_with_rollback(
        [(source, target)], backup, remove_targets=[stale, missing]
    )
    assert target.read_bytes() == b"new"
    assert not stale.exists() and not backup.exists()

    source = tmp_path / "staged-again"
    source.write_bytes(b"again")
    target.write_bytes(b"working")
    backup = tmp_path / ".rollback"
    original_replace = Path.replace

    def fail_only_promotion(path: Path, destination: Path) -> Path:
        if path == source:
            raise OSError("promotion failed")
        return original_replace(path, destination)

    monkeypatch.setattr(Path, "replace", fail_only_promotion)
    with pytest.raises(OSError, match="promotion failed"):
        integrity.promote_files_with_rollback([(source, target)], backup)
    assert target.read_bytes() == b"working"
    assert not backup.exists()


def test_hash_verification_missing_mismatch_and_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asset = tmp_path / "asset"
    asset.write_bytes(b"tool")
    assert integrity.sha256_file(asset) == hashlib.sha256(b"tool").hexdigest()
    with pytest.raises(RuntimeError, match="No SHA-256"):
        integrity.verify_downloaded_asset(asset, "missing")
    monkeypatch.setitem(integrity.SHA256_BY_ASSET, "asset", "0" * 64)
    with pytest.raises(RuntimeError, match="mismatch"):
        integrity.verify_downloaded_asset(asset, "asset")
    monkeypatch.setitem(
        integrity.SHA256_BY_ASSET, "asset", integrity.sha256_file(asset)
    )
    integrity.verify_downloaded_asset(asset, "asset")


def test_declared_size_none_invalid_valid_and_excessive() -> None:
    integrity._validate_declared_size(SimpleNamespace(headers={}), 10)
    integrity._validate_declared_size(
        SimpleNamespace(headers={"content-length": "bad"}), 10
    )
    integrity._validate_declared_size(
        SimpleNamespace(headers={"content-length": None}), 10
    )
    integrity._validate_declared_size(
        SimpleNamespace(headers={"content-length": "10"}), 10
    )
    with pytest.raises(RuntimeError, match="10-byte limit"):
        integrity._validate_declared_size(
            SimpleNamespace(headers={"content-length": "11"}), 10
        )


def test_bounded_async_download_success_and_oversize_cleanup(
    tmp_path: Path,
) -> None:
    class Response:
        status_code = 200

        def __init__(self) -> None:
            self.headers = {"content-length": "4"}

        def raise_for_status(self) -> None:
            return None

        async def aiter_bytes(self, chunk_size: int):
            assert chunk_size == 8192
            yield b"tool"

    class Stream:
        async def __aenter__(self):
            return Response()

        async def __aexit__(self, *_args: object) -> None:
            return None

    class Client:
        def stream(self, method: str, url: str, timeout: float):
            assert method == "GET"
            assert url == "https://example.invalid/tool"
            assert timeout == 5.0
            return Stream()

    destination = tmp_path / "asset"
    destination.write_bytes(b"stale")
    asyncio.run(
        integrity.download_bounded_asset(
            Client(),
            "https://example.invalid/tool",
            destination,
            max_bytes=4,
            timeout_seconds=5.0,
        )
    )
    assert destination.read_bytes() == b"tool"

    class OversizeResponse(Response):
        def __init__(self) -> None:
            self.headers = {}

        async def aiter_bytes(self, chunk_size: int):
            assert chunk_size == 8192
            yield b"123"
            yield b"45"

    class OversizeStream(Stream):
        async def __aenter__(self):
            return OversizeResponse()

    class OversizeClient(Client):
        def stream(self, method: str, url: str, timeout: float):
            del method, url, timeout
            return OversizeStream()

    with pytest.raises(RuntimeError, match="4-byte limit"):
        asyncio.run(
            integrity.download_bounded_asset(
                OversizeClient(),
                "https://example.invalid/tool",
                destination,
                max_bytes=4,
                timeout_seconds=5.0,
            )
        )
    assert not destination.exists()


def test_bounded_sync_download_delegates_to_async(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, Path, int, float]] = []

    class FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    async def fake_download(
        _client: object,
        url: str,
        destination: Path,
        *,
        max_bytes: int,
        timeout_seconds: float,
    ) -> None:
        calls.append((url, destination, max_bytes, timeout_seconds))

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(integrity, "download_bounded_asset", fake_download)
    destination = tmp_path / "asset"
    integrity.download_bounded_asset_sync(
        "https://example.invalid/tool",
        destination,
        max_bytes=123,
        timeout_seconds=7.0,
    )
    assert calls == [("https://example.invalid/tool", destination, 123, 7.0)]


@pytest.mark.asyncio
async def test_sync_download_rejects_active_event_loop_before_creating_coroutine(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "asset"

    with pytest.raises(RuntimeError, match="active event loop"):
        integrity.download_bounded_asset_sync(
            "https://example.invalid/tool", destination
        )


def test_verified_download_success_failure_cleanup_async_and_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "asset"
    digest = hashlib.sha256(b"tool").hexdigest()
    monkeypatch.setitem(integrity.SHA256_BY_ASSET, "asset", digest)

    async def good_download(_client, _url, target: Path) -> None:
        target.write_bytes(b"tool")

    monkeypatch.setattr(integrity, "download_bounded_asset", good_download)
    asyncio.run(
        integrity.download_verified_asset(
            object(), "https://example.invalid", destination, "asset"
        )
    )
    assert destination.read_bytes() == b"tool"

    monkeypatch.setitem(integrity.SHA256_BY_ASSET, "asset", "0" * 64)
    with pytest.raises(RuntimeError, match="mismatch"):
        asyncio.run(
            integrity.download_verified_asset(
                object(), "https://example.invalid", destination, "asset"
            )
        )
    assert not destination.exists()

    def good_sync(_url: str, target: Path, **_kwargs: object) -> None:
        target.write_bytes(b"tool")

    monkeypatch.setattr(integrity, "download_bounded_asset_sync", good_sync)
    monkeypatch.setitem(integrity.SHA256_BY_ASSET, "asset", digest)
    integrity.download_verified_asset_sync(
        "https://example.invalid", destination, "asset"
    )
    assert destination.exists()
    monkeypatch.setitem(integrity.SHA256_BY_ASSET, "asset", "0" * 64)
    with pytest.raises(RuntimeError, match="mismatch"):
        integrity.download_verified_asset_sync(
            "https://example.invalid", destination, "asset"
        )
    assert not destination.exists()


def test_promote_rolls_back_already_promoted_target_and_reports_unlink_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_source = tmp_path / "first.staged"
    second_source = tmp_path / "second.staged"
    first_target = tmp_path / "first"
    second_target = tmp_path / "second"
    for path, data in (
        (first_source, b"new-one"),
        (second_source, b"new-two"),
        (first_target, b"old-one"),
        (second_target, b"old-two"),
    ):
        path.write_bytes(data)
    backup = tmp_path / ".rollback"
    original_replace = Path.replace

    def fail_second(path: Path, destination: Path) -> Path:
        if path == second_source:
            raise OSError("second promotion failed")
        return original_replace(path, destination)

    monkeypatch.setattr(Path, "replace", fail_second)
    with pytest.raises(OSError, match="second promotion failed"):
        integrity.promote_files_with_rollback(
            [(first_source, first_target), (second_source, second_target)],
            backup,
        )
    assert first_target.read_bytes() == b"old-one"
    assert second_target.read_bytes() == b"old-two"

    first_source.write_bytes(b"new-one")
    second_source.write_bytes(b"new-two")
    backup = tmp_path / ".incomplete"
    original_unlink = Path.unlink

    def fail_promoted_unlink(
        path: Path, *args: object, **kwargs: object
    ) -> None:
        if path == first_target:
            raise OSError("cannot remove promoted target")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_promoted_unlink)
    with pytest.raises(BaseExceptionGroup, match="rollback was incomplete"):
        integrity.promote_files_with_rollback(
            [(first_source, first_target), (second_source, second_target)],
            backup,
        )
