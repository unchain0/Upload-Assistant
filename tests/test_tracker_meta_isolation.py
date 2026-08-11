# ruff: noqa: S101

from pathlib import Path
from typing import Any

import pytest

import src.trackerhandle as trackerhandle
from src.meta import Meta
from src.upload_safety import book_metadata_cjk_fields


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
            assert meta.get("trusted_book_layout") is True
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
    assert zenith_meta.get("zentag_prepared") is True
    assert zenith_meta.tracker_status is shared_status
    assert meta.path == str(original)
    assert meta.filelist == [str(original)]


@pytest.mark.asyncio
async def test_tracker_prepared_metadata_is_reused_for_upload() -> None:
    original = Meta(
        author="宮沢 賢治",
        title="宮沢賢治童話全集",
        trackers=["YUSCENE"],
        tracker_status={},
        tracker_prepared_meta={
            "YUSCENE": Meta(author="Kenji Miyazawa", title="Complete Collection of Children's Stories")
        },
    )

    prepared = await trackerhandle.prepare_tracker_meta(original, "YUSCENE", {"DEFAULT": {}})

    assert prepared.author == "Kenji Miyazawa"
    assert prepared.title == "Complete Collection of Children's Stories"
    assert original.author == "宮沢 賢治"


def test_cjk_book_metadata_is_detected_before_upload() -> None:
    meta = Meta(
        category="BOOK",
        name="宮沢 賢治 - 宮沢賢治童話全集 2016 JAPANESE AUDIOBOOK",
        author="宮沢 賢治",
        title="宮沢賢治童話全集",
        book_overview="Japanese fairy tales.",
    )

    assert book_metadata_cjk_fields(meta) == ["release name", "author", "title"]


@pytest.mark.asyncio
async def test_failed_required_zentag_preparation_disables_zenith(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "Book.m4b"
    source.write_bytes(b"m4b")
    status: dict[str, dict[str, Any]] = {"ZENITH": {"upload": True}}
    meta = Meta(
        path=str(source),
        filelist=[str(source)],
        trackers=["ZENITH"],
        tracker_status=status,
        base_dir=str(tmp_path),
        category="BOOK",
        unattended=True,
    )

    async def failed_prepare(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(trackerhandle, "prepare_zenith_audiobook", failed_prepare)

    prepared = await trackerhandle.prepare_tracker_meta(meta, "ZENITH", {"DEFAULT": {"auto_zentag": True}})

    assert prepared.path == str(source)
    assert status["ZENITH"]["upload"] is False
    assert status["ZENITH"]["skipped"] is True
