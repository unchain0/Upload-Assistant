# ruff: noqa: S101

import asyncio
import os
from pathlib import Path

import pytest

from src.queuemanage import _write_json_file


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes do not apply on Windows")
def test_existing_queue_log_is_restricted_to_owner(tmp_path: Path) -> None:
    log = tmp_path / "queue.log"
    log.write_text("old", encoding="utf-8")
    log.chmod(0o644)

    asyncio.run(_write_json_file(log, ["new"]))

    assert log.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink protections do not apply on Windows")
def test_queue_log_writer_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("protected", encoding="utf-8")
    log = tmp_path / "queue.log"
    log.symlink_to(target)

    with pytest.raises(PermissionError, match="untrusted queue log"):
        asyncio.run(_write_json_file(log, ["new"]))

    assert target.read_text(encoding="utf-8") == "protected"
