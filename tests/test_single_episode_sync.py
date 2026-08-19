import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

import src.services.episode_service as season_episode
import src.services.preparation_service as prep_module
import upload
from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.darkpeers import DarkPeers
from src.services.episode_service import SeasonEpisodeManager, sync_single_episode_from_filename
from src.services.release_naming_service import NameManager

REPACK_FILE = "I.Became.a.Legend.After.My.10.Year-Long.Last.Stand.S01E05.After.Ten.Years.I.Was.Told.to.Get.Lost.REPACK.1080p.CR.WEB-DL.DDP2.0.H.264-Kitsune.mkv"


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
    for extension in ("avi", "m2ts", "m4v", "mpeg", "mpg", "vob"):
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


def test_sync_single_episode_clears_stale_episode_metadata() -> None:
    meta = _stale_meta(
        auto_episode_title="Old Episode One",
        overview_meta="Old E01 overview",
        tmdb_episode_data={"name": "Old Episode One"},
        tvdb_episode_data={"episode": 1},
        tvdb_imdb_id="tt0000101",
        tvdb_season_name="Season One",
        tvmaze_episode_data={"name": "Old Episode One"},
        we_checked_tmdb=True,
        we_checked_tvdb=True,
        we_asked_tvmaze=True,
    )

    assert sync_single_episode_from_filename(meta) is True
    assert meta.auto_episode_title is None
    assert meta.overview_meta is None
    assert meta.tmdb_episode_data is None
    assert meta.tvdb_episode_data == {}
    assert meta.tvdb_imdb_id is None
    assert meta.tvdb_season_name == ""
    assert meta.tvmaze_episode_data == {}
    assert meta.we_checked_tmdb is False
    assert meta.we_checked_tvdb is False
    assert meta.we_asked_tvmaze is False


@pytest.mark.asyncio
async def test_daily_mapping_clears_metadata_for_previous_episode(monkeypatch: pytest.MonkeyPatch) -> None:
    meta = _stale_meta(
        filename="Daily.Show.2026.08.11.S01E05.1080p.WEB-DL.mkv",
        tmdb_id=123,
        auto_episode_title="Old Episode Five",
        overview_meta="Old E05 overview",
        tmdb_episode_data={"name": "Old Episode Five"},
    )
    manager = SeasonEpisodeManager({"DEFAULT": {"tmdb_api": "test-key"}})
    monkeypatch.setattr(manager.tmdb_manager, "daily_to_tmdb_season_episode", AsyncMock(return_value=(12, 34)))

    await manager.get_season_episode(meta.filelist[0], meta)

    assert meta.manual_date == "2026-08-11"
    assert (meta.season_int, meta.episode_int) == (12, 34)
    assert meta.daily_episode_title == "2026-08-11"
    assert meta.auto_episode_title is None
    assert meta.overview_meta is None
    assert meta.tmdb_episode_data is None


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


@pytest.mark.asyncio
async def test_gather_prep_syncs_discovered_file_before_metadata_search(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    filename = "Example.Show.S01E05.1080p.WEB-DL.mkv"
    observed: list[tuple[str, int, str | None]] = []
    meta = _stale_meta(
        filename=filename,
        filelist=[],
        base_dir=str(tmp_path),
        uuid="prep-order",
        path=str(tmp_path / filename),
        keep_images=True,
        hdr="SDR",
        auto_episode_title="Old Episode One",
    )
    prep = object.__new__(prep_module.Prep)
    prep.config = {"DEFAULT": {}}
    prep.publish_preview = None

    monkeypatch.setattr(prep_module.prep_helpers, "init_meta", Mock(return_value=(False, False, None, False, {}, {})))
    monkeypatch.setattr(prep_module.prep_helpers, "detect_disc_and_category", AsyncMock(return_value=(filename, {})))

    async def discover_media(_prep: object, discovered_meta: Meta, _videoloc: str, _bdinfo: object) -> tuple[object, ...]:
        discovered_meta.filelist = [filename]
        return filename, filename, filename, "", "", {}, {}

    async def observe_search(_prep: object, searched_meta: Meta, *_args: object) -> None:
        observed.append((searched_meta.episode, searched_meta.episode_int, searched_meta.auto_episode_title))

    monkeypatch.setattr(prep_module.prep_helpers, "process_media_files", discover_media)
    monkeypatch.setattr(prep_module.prep_helpers, "calculate_source_size", Mock(return_value=None))
    monkeypatch.setattr(prep_module.prep_helpers, "validate_media", AsyncMock(return_value=None))
    monkeypatch.setattr(prep_module.prep_helpers, "process_trackers_and_torrent", AsyncMock(return_value=None))
    monkeypatch.setattr(prep_module, "restart_early_artifact_tasks", AsyncMock(return_value=None))
    monkeypatch.setattr(prep_module.prep_helpers, "search_metadata", observe_search)
    monkeypatch.setattr(prep_module.prep_helpers, "finalize_metadata", AsyncMock(return_value=None))
    monkeypatch.setattr(prep_module.languages_manager, "apply_confirmed_single_audio_language", AsyncMock(return_value=None))
    monkeypatch.setattr(prep_module.languages_manager, "process_desc_language", AsyncMock(return_value=None))

    result = await prep.gather_prep(meta, "cli")

    assert result.episode == "E05"
    assert observed == [("E05", 5, None)]
