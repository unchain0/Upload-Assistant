import os
from pathlib import Path

import pytest

from src.integrations.runtime_tools import runtime_tool_paths


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX ownership checks do not apply on Windows"
)
def test_tool_install_dir_falls_back_from_shared_writable_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preferred = tmp_path / "bin" / "mkbrr" / "linux" / "amd64"
    preferred.mkdir(parents=True)
    preferred.chmod(0o777)
    private_root = tmp_path / "private"
    private_root.mkdir(mode=0o700)
    monkeypatch.setattr(runtime_tool_paths, "_private_root", private_root)

    selected = runtime_tool_paths.tool_install_dir(
        tmp_path, "mkbrr", "linux/amd64"
    )

    assert selected == private_root / "mkbrr" / "linux" / "amd64"
    assert selected.stat().st_mode & 0o777 == 0o700


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX mode checks do not apply on Windows"
)
def test_trusted_executable_rejects_shared_writable_parent(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "bin"
    directory.mkdir(mode=0o777)
    directory.chmod(0o777)
    binary = directory / "mkbrr"
    binary.touch(mode=0o755)

    assert runtime_tool_paths.trusted_executable(binary) is False


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX mode checks do not apply on Windows"
)
def test_trusted_executable_rejects_shared_writable_file(
    tmp_path: Path,
) -> None:
    binary = tmp_path / "mkbrr"
    binary.touch(mode=0o755)
    binary.chmod(0o777)

    assert runtime_tool_paths.trusted_executable(binary) is False


def test_trusted_executable_rejects_missing_path(tmp_path: Path) -> None:
    assert not runtime_tool_paths.trusted_executable(tmp_path / "missing")


def test_windows_runtime_tool_path_checks_use_access_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = tmp_path / "tool.exe"
    binary.write_bytes(b"tool")
    directory = tmp_path / "directory"
    directory.mkdir()
    fake_os = type(
        "FakeOS",
        (),
        {
            "name": "nt",
            "W_OK": os.W_OK,
            "X_OK": os.X_OK,
            "access": staticmethod(lambda _path, _mode: True),
        },
    )()
    monkeypatch.setattr(runtime_tool_paths, "os", fake_os)

    assert runtime_tool_paths._is_private_writable_directory(directory)
    assert runtime_tool_paths.trusted_executable(binary)
