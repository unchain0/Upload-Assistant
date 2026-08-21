import asyncio
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.services.queue_service import (
    QueueManager,
    _trusted_existing_queue_log,
    _write_json_file,
)


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX file modes do not apply on Windows"
)
def test_existing_queue_log_is_restricted_to_owner(tmp_path: Path) -> None:
    log = tmp_path / "queue.log"
    log.write_text("old", encoding="utf-8")
    log.chmod(0o644)

    asyncio.run(_write_json_file(log, ["new"]))

    assert log.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX symlink protections do not apply on Windows"
)
def test_queue_log_writer_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("protected", encoding="utf-8")
    log = tmp_path / "queue.log"
    log.symlink_to(target)

    with pytest.raises(PermissionError, match="untrusted queue log"):
        asyncio.run(_write_json_file(log, ["new"]))

    assert target.read_text(encoding="utf-8") == "protected"


def test_windows_reparse_point_is_not_a_trusted_queue_log() -> None:
    attributes = SimpleNamespace(
        st_mode=stat.S_IFREG,
        st_file_attributes=getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
        ),
        st_uid=0,
    )

    assert not _trusted_existing_queue_log(attributes, windows=True)


def test_queue_name_cannot_escape_temp_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Invalid queue name"):
        asyncio.run(QueueManager.get_log_file(str(tmp_path), "../escape"))
