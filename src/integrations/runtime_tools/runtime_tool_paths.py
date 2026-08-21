from __future__ import annotations

import os
import stat
import tempfile
from contextlib import suppress
from pathlib import Path

_private_root: Path | None = None


def _is_private_writable_directory(path: Path) -> bool:
    if path.is_symlink() or not path.is_dir():
        return False
    if os.name == "nt":
        return os.access(path, os.W_OK | os.X_OK)
    attributes = path.stat()
    shared_write = stat.S_IWGRP | stat.S_IWOTH
    return (
        attributes.st_uid == os.geteuid()
        and not attributes.st_mode & shared_write
        and os.access(path, os.W_OK | os.X_OK)
    )


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


def trusted_executable(path: Path) -> bool:
    if not path.is_file() or (
        os.name != "nt" and not os.access(path, os.X_OK)
    ):
        return False
    if os.name == "nt":
        return True
    if path.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return False
    parent_mode = path.parent.stat().st_mode
    return not parent_mode & (stat.S_IWGRP | stat.S_IWOTH)
