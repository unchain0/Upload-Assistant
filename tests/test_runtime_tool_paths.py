# ruff: noqa: S101

import os
from pathlib import Path

import pytest

from bin import runtime_tool_paths


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership checks do not apply on Windows")
def test_tool_install_dir_falls_back_from_shared_writable_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    preferred = tmp_path / "bin" / "mkbrr" / "linux" / "amd64"
    preferred.mkdir(parents=True)
    preferred.chmod(0o777)
    private_root = tmp_path / "private"
    private_root.mkdir(mode=0o700)
    monkeypatch.setattr(runtime_tool_paths, "_private_root", private_root)

    selected = runtime_tool_paths.tool_install_dir(tmp_path, "mkbrr", "linux/amd64")

    assert selected == private_root / "mkbrr" / "linux" / "amd64"
    assert selected.stat().st_mode & 0o777 == 0o700


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode checks do not apply on Windows")
def test_trusted_executable_rejects_shared_writable_parent(tmp_path: Path) -> None:
    directory = tmp_path / "bin"
    directory.mkdir(mode=0o777)
    directory.chmod(0o777)
    binary = directory / "mkbrr"
    binary.touch(mode=0o755)

    assert runtime_tool_paths.trusted_executable(binary) is False


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode checks do not apply on Windows")
def test_trusted_executable_rejects_shared_writable_file(tmp_path: Path) -> None:
    binary = tmp_path / "mkbrr"
    binary.touch(mode=0o755)
    binary.chmod(0o777)

    assert runtime_tool_paths.trusted_executable(binary) is False
