from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.domain_models.release import Meta
from src.integrations.image_hosts import uploader


def test_registered_screenshot_through_a_runtime_symlink_stays_usable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_dir = tmp_path / "real-runtime" / "tmp" / "release" / "screenshots"
    real_dir.mkdir(parents=True)
    image = real_dir / "shot.png"
    image.write_bytes(b"png")
    runtime = tmp_path / "fork" / "runtime"
    runtime.parent.mkdir()
    runtime.symlink_to(tmp_path / "real-runtime")
    logical = runtime / "tmp" / "release" / "screenshots" / "shot.png"
    monkeypatch.chdir(logical.parent)
    monkeypatch.setattr(uploader, "manifest_files", lambda *_args: [logical])

    files = asyncio.run(
        uploader._main_screenshot_files(Meta(base_dir="runtime", uuid="release"))
    )

    assert files == ["shot.png"]
    assert Path(files[0]).read_bytes() == b"png"
