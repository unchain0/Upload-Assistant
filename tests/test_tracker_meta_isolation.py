# ruff: noqa: S101

from pathlib import Path
from typing import Any

import pytest

import src.trackerhandle as trackerhandle
from src.meta import Meta


@pytest.mark.asyncio
async def test_zentag_preparation_is_isolated_from_other_trackers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = tmp_path / "readarr" / "Book.m4b"
    original.parent.mkdir()
    original.write_bytes(b"original")
    prepared = tmp_path / "zentag-output" / "Author - Book"
    prepared.mkdir(parents=True)
    (prepared / "01 - Book.m4b").write_bytes(b"retagged")
    shared_status: dict[str, dict[str, Any]] = {}
    meta = Meta(
        path=str(original),
        filelist=[str(original)],
        trackers=["ZENITH", "PEERGARDEN"],
        tracker_status=shared_status,
        base_dir=str(tmp_path),
        uuid="release-id",
        category="BOOK",
        unattended=True,
    )

    async def prepare_stub(*_args: Any, **_kwargs: Any) -> str:
        return str(prepared)

    class PrepStub:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def gather_prep(self, meta: Meta, mode: str) -> Meta:
            assert mode == "cli"
            meta.filelist = [str(prepared / "01 - Book.m4b")]
            return meta

    monkeypatch.setattr(trackerhandle, "prepare_zenith_audiobook", prepare_stub)
    monkeypatch.setattr(trackerhandle, "Prep", PrepStub)

    peergarden_meta = await trackerhandle.prepare_tracker_meta(meta, "PEERGARDEN", {"DEFAULT": {}})
    zenith_meta = await trackerhandle.prepare_tracker_meta(meta, "ZENITH", {"DEFAULT": {}})

    assert peergarden_meta.path == str(original)
    assert peergarden_meta.filelist == [str(original)]
    assert peergarden_meta.keep_folder is False
    assert zenith_meta.path == str(prepared)
    assert zenith_meta.filelist == [str(prepared / "01 - Book.m4b")]
    assert zenith_meta.keep_folder is True
    assert zenith_meta.uuid == "release-id-zenith"
    assert zenith_meta.tracker_status is shared_status
    assert meta.path == str(original)
    assert meta.filelist == [str(original)]
