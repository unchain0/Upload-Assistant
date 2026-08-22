from __future__ import annotations

import os
import stat
import tempfile
from contextlib import suppress
from pathlib import Path

_private_root: Path | None = None


def _path_is_real_directory(path: Path) -> bool:
    return not path.is_symlink() and path.is_dir()


def _posix_directory_is_private(path: Path) -> bool:
    attributes = path.stat()
    shared_write = stat.S_IWGRP | stat.S_IWOTH
    owned = attributes.st_uid == os.geteuid()
    private_mode = not attributes.st_mode & shared_write
    return bool(owned and private_mode and os.access(path, os.W_OK | os.X_OK))


def _is_private_writable_directory(path: Path) -> bool:
    if not _path_is_real_directory(path):
        return False
    if os.name == "nt":
        return os.access(path, os.W_OK | os.X_OK)
    return _posix_directory_is_private(path)


def _private_tool_root() -> Path:
    global _private_root
    if _private_root is None:
        _private_root = Path(
            tempfile.mkdtemp(prefix="upload-assistant-tools-")
        )
        _private_root.chmod(0o700)
    return _private_root


def tool_install_dir(base_dir: str | Path, tool: str, folder: str) -> Path:
    preferred = Path(base_dir) / "bin" / tool / folder
    with suppress(PermissionError):
        preferred.mkdir(parents=True, mode=0o700, exist_ok=True)
    if _is_private_writable_directory(preferred):
        return preferred

    private = _private_tool_root() / tool / folder
    private.mkdir(parents=True, mode=0o700, exist_ok=True)
    private.chmod(0o700)
    return private


def _basic_executable_check(path: Path) -> bool:
    if not path.is_file():
        return False
    return os.name == "nt" or os.access(path, os.X_OK)


def _posix_executable_is_trusted(path: Path) -> bool:
    shared_write = stat.S_IWGRP | stat.S_IWOTH
    if path.stat().st_mode & shared_write:
        return False
    return not path.parent.stat().st_mode & shared_write


def trusted_executable(path: Path) -> bool:
    if not _basic_executable_check(path):
        return False
    if os.name == "nt":
        return True
    return _posix_executable_is_trusted(path)
