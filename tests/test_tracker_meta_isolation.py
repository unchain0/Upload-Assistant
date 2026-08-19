from pathlib import Path
from typing import Any

import pytest

import src.services.tracker_upload_service as trackerhandle
from src.domain_models.release import Meta
from src.engines.upload_safety_policy import book_metadata_cjk_fields


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["m4b", "pdf"])
async def test_zentag_preparation_is_isolated_from_other_trackers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str) -> None:
    original = tmp_path / "readarr" / f"Book.{suffix}"
    original.parent.mkdir()
    original.write_bytes(b"original")
    prepared = tmp_path / "zentag-output" / "Author - Book"
    prepared.mkdir(parents=True)
    prepared_file = prepared / f"01 - Book.{suffix}"
    prepared_file.write_bytes(b"retagged")
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

    async def prepare_audio_stub(*_args: Any, **_kwargs: Any) -> str | None:
        return str(prepared) if suffix == "m4b" else None

    async def prepare_ebook_stub(*_args: Any, **_kwargs: Any) -> str | None:
        return str(prepared) if suffix == "pdf" else None

    class PrepStub:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def gather_prep(self, meta: Meta, mode: str) -> Meta:
            assert mode == "cli"
            assert meta.get("trusted_book_layout") is True
            meta.filelist = [str(prepared_file)]
            return meta

    monkeypatch.setattr(trackerhandle, "prepare_zenith_audiobook", prepare_audio_stub)
    monkeypatch.setattr(trackerhandle, "prepare_zenith_ebook", prepare_ebook_stub)
    monkeypatch.setattr(trackerhandle, "Prep", PrepStub)

    peergarden_meta = await trackerhandle.prepare_tracker_meta(meta, "PEERGARDEN", {"DEFAULT": {}})
    zenith_meta = await trackerhandle.prepare_tracker_meta(meta, "ZENITH", {"DEFAULT": {}})

    assert peergarden_meta.path == str(original)
    assert peergarden_meta.filelist == [str(original)]
    assert peergarden_meta.keep_folder is False
    assert zenith_meta.path == str(prepared)
    assert zenith_meta.filelist == [str(prepared_file)]
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
        tracker_prepared_meta={"YUSCENE": Meta(author="Kenji Miyazawa", title="Complete Collection of Children's Stories")},
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
@pytest.mark.parametrize("suffix", ["m4b", "pdf"])
async def test_failed_required_zentag_preparation_disables_zenith(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str) -> None:
    source = tmp_path / f"Book.{suffix}"
    source.write_bytes(suffix.encode())
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
    monkeypatch.setattr(trackerhandle, "prepare_zenith_ebook", failed_prepare)

    prepared = await trackerhandle.prepare_tracker_meta(meta, "ZENITH", {"DEFAULT": {"auto_zentag": True}})

    assert prepared.path == str(source)
    assert status["ZENITH"]["upload"] is False
    assert status["ZENITH"]["skipped"] is True
