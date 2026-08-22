from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from src.integrations.runtime_tools import bdinfo_docker


def _tar(path: Path, *members: tuple[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, payload in members:
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = 0o755
            archive.addfile(info, io.BytesIO(payload))


def test_download_file_and_secure_extract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, Path]] = []

    def download(url: str, destination: Path) -> None:
        calls.append((url, destination))
        destination.write_bytes(b"download")

    monkeypatch.setattr(bdinfo_docker, "download_bounded_asset_sync", download)
    destination = tmp_path / "asset"
    bdinfo_docker.download_file("https://example.invalid/asset", destination)
    assert calls == [("https://example.invalid/asset", destination)]
    assert destination.read_bytes() == b"download"

    archive = tmp_path / "asset.tar.gz"
    _tar(archive, ("bundle/bdinfo", b"binary"))
    output = tmp_path / "output"
    bdinfo_docker.secure_extract_tar(archive, output)
    assert (output / "bundle" / "bdinfo").read_bytes() == b"binary"


def test_docker_platform_architecture_and_cache_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bdinfo_docker.platform, "system", lambda: "Darwin")
    with pytest.raises(Exception, match="only for Linux"):
        bdinfo_docker.download_bdinfo_for_docker(tmp_path)

    monkeypatch.setattr(bdinfo_docker.platform, "system", lambda: "Linux")
    monkeypatch.setattr(bdinfo_docker.platform, "machine", lambda: "riscv64")
    with pytest.raises(Exception, match="Unsupported architecture"):
        bdinfo_docker.download_bdinfo_for_docker(tmp_path)

    cache_binary = tmp_path / "cache-disabled" / "bdinfo"
    cache_binary.parent.mkdir(parents=True)
    cache_binary.write_bytes(b"cached")
    cache_version = cache_binary.parent / "v0.3.1"
    cache_version.write_text("cached", encoding="utf-8")
    monkeypatch.setattr(bdinfo_docker.os, "access", lambda *_args: False)
    assert (
        bdinfo_docker._cached_binary(cache_binary, cache_version, "v0.3.1")
        is None
    )
    monkeypatch.undo()
    monkeypatch.setattr(bdinfo_docker.platform, "system", lambda: "Linux")

    for machine, folder in (
        ("amd64", "linux/amd64"),
        ("aarch64", "linux/arm64"),
        ("armv7l", "linux/arm"),
    ):
        root = tmp_path / machine
        binary = root / "bin" / "bdinfo" / folder / "bdinfo"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"cached")
        binary.chmod(0o755)
        (binary.parent / "v0.3.1").write_text("cached", encoding="utf-8")
        monkeypatch.setattr(
            bdinfo_docker.platform, "machine", lambda value=machine: value
        )
        assert bdinfo_docker.download_bdinfo_for_docker(root) == str(binary)


def test_docker_install_stale_cleanup_duplicate_and_download_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bdinfo_docker.platform, "system", lambda: "Linux")
    monkeypatch.setattr(bdinfo_docker.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        bdinfo_docker, "verify_downloaded_asset", lambda *_args: None
    )

    def download(_url: str, destination: Path) -> None:
        _tar(destination, ("bundle/bdinfo", b"new"))

    monkeypatch.setattr(bdinfo_docker, "download_file", download)
    bin_dir = tmp_path / "bin" / "bdinfo" / "linux" / "amd64"
    bin_dir.mkdir(parents=True)
    stale = bin_dir / "v0.2.0"
    stale.write_text("stale", encoding="utf-8")
    result = bdinfo_docker.download_bdinfo_for_docker(tmp_path)
    assert Path(result).read_bytes() == b"new"
    assert Path(result).stat().st_mode & 0o111
    assert (bin_dir / "v0.3.1").is_file() and not stale.exists()
    assert not (bin_dir / ".bdinfo-staging").exists()
    assert not any(path.name.startswith("temp_") for path in bin_dir.iterdir())

    duplicate_root = tmp_path / "duplicate"

    def duplicate(_url: str, destination: Path) -> None:
        _tar(destination, ("one/bdinfo", b"one"), ("two/bdinfo", b"two"))

    monkeypatch.setattr(bdinfo_docker, "download_file", duplicate)
    with pytest.raises(Exception, match="exactly one"):
        bdinfo_docker.download_bdinfo_for_docker(duplicate_root)
    assert not (
        duplicate_root
        / "bin"
        / "bdinfo"
        / "linux"
        / "amd64"
        / ".bdinfo-staging"
    ).exists()

    failed_root = tmp_path / "failed"

    def fail_download(_url: str, destination: Path) -> None:
        destination.write_bytes(b"partial")
        raise RuntimeError("download failed")

    monkeypatch.setattr(bdinfo_docker, "download_file", fail_download)
    with pytest.raises(RuntimeError, match="download failed"):
        bdinfo_docker.download_bdinfo_for_docker(failed_root)
    assert not any(
        (failed_root / "bin" / "bdinfo" / "linux" / "amd64").glob("temp_*")
    )


def test_docker_main_success_and_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        bdinfo_docker, "download_bdinfo_for_docker", lambda: "/bin/bdinfo"
    )
    assert bdinfo_docker.main() == 0
    monkeypatch.setattr(
        bdinfo_docker,
        "download_bdinfo_for_docker",
        lambda: (_ for _ in ()).throw(RuntimeError("failed")),
    )
    assert bdinfo_docker.main() == 1
