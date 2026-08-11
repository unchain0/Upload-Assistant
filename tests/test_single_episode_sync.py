# ruff: noqa: S101
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

import src.getseasonep as season_episode
import upload
from src.get_name import NameManager
from src.getseasonep import sync_single_episode_from_filename
from src.meta import Meta
from src.trackers.UNIT3D.darkpeers import DarkPeers

REPACK_FILE = (
    "I.Became.a.Legend.After.My.10.Year-Long.Last.Stand."
    "S01E05.After.Ten.Years.I.Was.Told.to.Get.Lost."
    "REPACK.1080p.CR.WEB-DL.DDP2.0.H.264-Kitsune.mkv"
)


def _stale_meta(filename: str = REPACK_FILE, **overrides: object) -> Meta:
    values: dict[str, object] = {
        "category": "TV",
        "filelist": [filename],
        "season": "S01",
        "episode": "E01",
        "season_int": 1,
        "episode_int": 1,
        "tv_pack": False,
        "is_disc": "",
    }
    values.update(overrides)
    return Meta(values)


def test_sync_single_episode_uses_the_only_video_filename():
    meta = _stale_meta(
        title="I Became a Legend After My 10 Year-Long Last Stand",
        type="WEBDL",
        resolution="1080p",
        service="CR",
        audio="DD+ 2.0",
        video_encode="H.264",
        tag="-Kitsune",
    )

    changed = sync_single_episode_from_filename(meta)
    meta.name_notag, meta.name, meta.clean_name, meta.potential_missing = asyncio.run(NameManager({}).get_name(meta))
    episode_settings = asyncio.run(DarkPeers({"DEFAULT": {"tmdb_api": "test-key"}, "TRACKERS": {"DARKPEERS": {}}}).get_episode_number(meta))

    assert changed is True
    assert meta.season == "S01"
    assert meta.episode == "E05"
    assert meta.season_int == 1
    assert meta.episode_int == 5
    assert "S01E05" in meta.name
    assert episode_settings == {"episode_number": "5"}


def test_sync_single_episode_preserves_explicit_manual_override():
    meta = _stale_meta(manual_episode=1)

    assert sync_single_episode_from_filename(meta) is False
    assert meta.episode == "E01"
    assert meta.episode_int == 1


def test_sync_single_episode_ignores_season_packs():
    meta = _stale_meta(tv_pack=True)

    assert sync_single_episode_from_filename(meta) is False
    assert meta.episode == "E01"


def test_sync_single_episode_ignores_multi_episode_filenames():
    filenames = (
        "Example.Show.S01E05-E06.1080p.WEB-DL.mkv",
        "Example.Show.S01E05-06.1080p.WEB-DL.mkv",
        "Example.Show.S01E05_06.1080p.WEB-DL.mkv",
        "Example.Show.S01E05.06.1080p.WEB-DL.mkv",
        "Example.Show.S01E05 06.1080p.WEB-DL.mkv",
    )

    for filename in filenames:
        meta = _stale_meta(filename=filename)

        assert sync_single_episode_from_filename(meta) is False
        assert meta.episode == "E01"


def test_sync_single_episode_accepts_supported_video_containers():
    for extension in ("avi", "m2ts", "m4v"):
        meta = _stale_meta(filename=f"Example.Show.S01E05.1080p.WEB-DL.{extension}")

        assert sync_single_episode_from_filename(meta) is True
        assert meta.episode == "E05"


def test_sync_single_episode_ignores_malformed_filelist_entries():
    meta = _stale_meta()
    meta.filelist = [None, 123]

    assert sync_single_episode_from_filename(meta) is False
    assert meta.episode == "E01"

    meta.update({"filelist": None})
    assert sync_single_episode_from_filename(meta) is False


def test_sync_single_episode_rejects_untrusted_filename_shapes(monkeypatch: pytest.MonkeyPatch):
    for filename in (f"{'A' * 1100}.S01E05.mkv", "Show.éS01E05é.mkv", "Show.\uff11S01E05\uff12.mkv"):
        assert sync_single_episode_from_filename(_stale_meta(filename=filename)) is False

    monkeypatch.setattr(season_episode, "_guessit_data", Mock(side_effect=ValueError("invalid filename")))
    assert sync_single_episode_from_filename(_stale_meta()) is False


@pytest.mark.asyncio
async def test_process_meta_syncs_before_metadata_gather(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    class FakePrep:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def gather_prep(self, meta: Meta, mode: str) -> Meta:
            assert mode == "cli"
            events.append("gather")
            meta.category = "TV"
            meta.title = ""
            meta.tmdb = None
            meta.imdb = ""
            return meta

    def record_sync(_meta: Meta) -> None:
        events.append("sync")

    monkeypatch.setattr(upload, "Prep", FakePrep)
    monkeypatch.setattr(upload, "_sync_single_episode", record_sync)
    monkeypatch.setattr(upload, "cancel_and_drain_early_artifact_tasks", AsyncMock(return_value=None))
    meta = Meta(base_dir=str(tmp_path), uuid="single-episode", imghost="imgbb", unattended=True, trackers=["DARKPEERS"])

    assert await upload.process_meta(meta, str(tmp_path)) is False
    assert events == ["sync", "gather"]
