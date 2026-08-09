# ruff: noqa: ARG001, ARG005, S101
from __future__ import annotations

import signal
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

import src.configvalidator as configvalidator
import upload
from src.exceptions import ItemProcessingError
from src.meta import Meta

_DEFAULT_META_QUEUE = object()


def _configure_do_the_thing_stubs(
    monkeypatch: pytest.MonkeyPatch,
    queue: list[Any],
    process_meta: Any,
    meta_queue: list[str] | object | None = _DEFAULT_META_QUEUE,
    meta_overrides: dict[str, Any] | None = None,
) -> tuple[list[str], AsyncMock]:
    queue_items = list(queue)
    queue_paths = [str(item.get("path", item)) if isinstance(item, dict) else str(item) for item in queue_items]
    for path in queue_paths:
        Path(path).touch()

    info_messages: list[str] = []

    def fake_parse(argv: list[str], meta: Meta) -> tuple[Meta, Any, list[str]]:
        first = argv[0] if argv else queue_paths[0]
        parsed_queue = [] if meta_queue is _DEFAULT_META_QUEUE else meta_queue
        values: dict[str, Any] = {"path": first, "queue": parsed_queue, "base_dir": upload.base_dir, "trackers": [], "site_check": False}
        values.update(meta_overrides or {})
        parsed = Meta(**values)
        return parsed, None, []

    async def fake_handle_queue(_path: str, _meta: Meta, _paths: list[str], _base_dir: str) -> tuple[list[str], str | None]:
        return list(queue_items), None

    class _NoopCleanup:
        async def cleanup(self) -> None:
            return None

        def reset_terminal(self) -> None:
            return None

    monkeypatch.setattr(upload, "parser", type("Parser", (), {"parse": staticmethod(fake_parse)})())
    handle_queue = AsyncMock(side_effect=fake_handle_queue)
    process_meta_mock = AsyncMock(side_effect=process_meta)
    monkeypatch.setattr(upload.QueueManager, "handle_queue", handle_queue)
    monkeypatch.setattr(upload, "process_meta", process_meta_mock)
    monkeypatch.setattr(upload, "update_notification", AsyncMock(return_value=""))
    monkeypatch.setattr(upload, "get_mkbrr_path", AsyncMock(return_value=False))
    monkeypatch.setattr(upload, "cleanup_manager", _NoopCleanup())
    monkeypatch.setattr(upload, "save_processed_file", AsyncMock(return_value=None))
    monkeypatch.setattr(upload, "cancel_and_drain_early_artifact_tasks", AsyncMock(return_value=None))
    monkeypatch.setattr(upload, "_publish_webui_preview_target", lambda *args, **kwargs: None)
    monkeypatch.setattr(upload.logger, "info", lambda message, *args, **kwargs: info_messages.append(str(message)))
    default_config = dict(upload.config.get("DEFAULT", {}))
    if "cross_seeding" not in default_config:
        default_config["cross_seeding"] = False
    if "sanitize_meta" not in default_config:
        default_config["sanitize_meta"] = False
    if "mkbrr" not in default_config:
        default_config["mkbrr"] = False
    if "debug" not in default_config:
        default_config["debug"] = False
    default_config["cross_seeding"] = False
    default_config["sanitize_meta"] = False
    default_config["mkbrr"] = False
    default_config["debug"] = False
    monkeypatch.setitem(upload.config, "DEFAULT", default_config)
    monkeypatch.setattr(configvalidator, "validate_config", lambda *_args, **_kwargs: (True, [], []))
    monkeypatch.setattr(configvalidator, "group_warnings", lambda warnings: warnings)

    return info_messages, process_meta_mock


@pytest.mark.asyncio
async def test_batch_continues_when_first_item_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    queue = [str(tmp_path / "first_fail.mkv"), str(tmp_path / "second_ok.mkv")]

    def fake_process_meta(meta: Meta, _base_dir: str) -> Any:
        if "first_fail" in (meta.path or ""):
            raise ItemProcessingError("No Video files found", meta.path)
        return True

    info_messages, process_meta_mock = _configure_do_the_thing_stubs(monkeypatch, queue, fake_process_meta)
    monkeypatch.setattr(sys, "argv", ["upload.py", *queue])

    await upload.do_the_thing(upload.base_dir)

    assert process_meta_mock.call_count == 2
    assert any(
        "Batch summary: total queued 2, fully successful 0, partial 0, skipped/failed 2" in message
        for message in info_messages
    )
    assert any("first_fail.mkv" in message and "No Video files found" in message for message in info_messages)


@pytest.mark.asyncio
async def test_batch_continues_when_intermediate_item_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    queue = [str(tmp_path / "first_ok.mkv"), str(tmp_path / "middle_fail.mkv"), str(tmp_path / "last_ok.mkv")]

    def fake_process_meta(meta: Meta, _base_dir: str) -> Any:
        if "middle_fail" in (meta.path or ""):
            raise ItemProcessingError("No Video files found", meta.path)
        return True

    info_messages, process_meta_mock = _configure_do_the_thing_stubs(monkeypatch, queue, fake_process_meta)
    monkeypatch.setattr(sys, "argv", ["upload.py", *queue])

    await upload.do_the_thing(upload.base_dir)

    assert process_meta_mock.call_count == 3
    assert any(
        "Batch summary: total queued 3, fully successful 0, partial 0, skipped/failed 3" in message
        for message in info_messages
    )
    assert any("middle_fail.mkv" in message and "No Video files found" in message for message in info_messages)


@pytest.mark.asyncio
async def test_batch_fails_all_items_without_aborting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    queue = [str(tmp_path / "bad_one.mkv"), str(tmp_path / "bad_two.mkv")]

    def fake_process_meta(meta: Meta, _base_dir: str) -> Any:
        raise ItemProcessingError("No Video files found", meta.path)

    info_messages, process_meta_mock = _configure_do_the_thing_stubs(monkeypatch, queue, fake_process_meta)
    monkeypatch.setattr(sys, "argv", ["upload.py", *queue])

    await upload.do_the_thing(upload.base_dir)

    assert process_meta_mock.call_count == 2
    assert any(
        "Batch summary: total queued 2, fully successful 0, partial 0, skipped/failed 2" in message
        for message in info_messages
    )
    assert any("bad_one.mkv" in message and "No Video files found" in message for message in info_messages)
    assert any("bad_two.mkv" in message and "No Video files found" in message for message in info_messages)


@pytest.mark.asyncio
async def test_single_item_failure_still_aborts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    queue = [str(tmp_path / "single_fail.mkv")]

    def fake_process_meta(_meta: Meta, _base_dir: str) -> Any:
        raise ItemProcessingError("No Video files found", None)

    info_messages, process_meta_mock = _configure_do_the_thing_stubs(monkeypatch, queue, fake_process_meta)
    monkeypatch.setattr(sys, "argv", ["upload.py", *queue])

    await upload.do_the_thing(upload.base_dir)

    assert not any("Batch summary:" in message for message in info_messages)
    assert process_meta_mock.call_count == 1


@pytest.mark.asyncio
async def test_site_check_batch_reports_completed_checks_instead_of_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    queue = [str(tmp_path / "first.mkv"), str(tmp_path / "second.mkv")]

    info_messages, process_meta_mock = _configure_do_the_thing_stubs(
        monkeypatch,
        queue,
        lambda _meta, _base_dir: True,
        meta_overrides={"site_check": True, "debug": True},
    )
    monkeypatch.setattr(sys, "argv", ["upload.py", *queue])

    await upload.do_the_thing(upload.base_dir)

    assert process_meta_mock.call_count == 2
    assert any("skipped/failed 0, site checks completed 2" in message for message in info_messages)
    assert not any("Failed items:" in message for message in info_messages)


@pytest.mark.asyncio
async def test_ctrl_c_stops_batch_processing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    queue = [str(tmp_path / "first_ok.mkv"), str(tmp_path / "second_ok.mkv")]

    def fake_process_meta(_meta: Meta, _base_dir: str) -> Any:
        raise KeyboardInterrupt

    _configure_do_the_thing_stubs(monkeypatch, queue, fake_process_meta)
    monkeypatch.setattr(sys, "argv", ["upload.py", *queue])

    with pytest.raises(KeyboardInterrupt):
        await upload.do_the_thing(upload.base_dir)


@pytest.mark.asyncio
async def test_ctrl_c_stops_single_item_processing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    queue = [str(tmp_path / "single_interrupt.mkv")]

    def fake_process_meta(_meta: Meta, _base_dir: str) -> Any:
        raise KeyboardInterrupt

    _configure_do_the_thing_stubs(monkeypatch, queue, fake_process_meta)
    monkeypatch.setattr(sys, "argv", ["upload.py", *queue])

    with pytest.raises(KeyboardInterrupt):
        await upload.do_the_thing(upload.base_dir)


def test_sigterm_signal_maps_to_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    upload._reset_shutdown_state()
    with pytest.raises(KeyboardInterrupt):
        upload._handle_shutdown_signal(signal.SIGTERM, None)
    assert upload._shutdown_requested is True


def test_movie_tv_identity_validation_rejects_invalid_automatic_metadata() -> None:
    assert upload._movie_tv_identity_error(Meta(category="TV", title="", unattended=True)) == "TV metadata has no valid title. Refusing to process the upload."
    assert upload._movie_tv_identity_error(Meta(category="MOVIE", title="Known Title", unattended=True)) == (
        "Unattended MOVIE metadata has no valid TMDb, IMDb, TVDB, TVmaze, or MAL identifier. Refusing to process the upload."
    )
    assert upload._movie_tv_identity_error(Meta(category="TV", title="Known Show", tmdb=123, unattended=True)) is None
    assert upload._movie_tv_identity_error(Meta(category="TV", title="Manual Show", unattended=False)) is None
    assert upload._movie_tv_identity_error(Meta(category="BOOK", title="", unattended=True)) is None


@pytest.mark.parametrize(
    "identity",
    [
        {"tmdb": "unknown"},
        {"tmdb_id": "12x"},
        {"imdb": "N/A"},
        {"imdb_id": "1234567"},
        {"imdb_info": {"imdbID": "tt0"}},
        {"tvdb_id": "none"},
        {"tvdb_id": "-1"},
        {"tvmaze_id": "N/A"},
        {"tvmaze_id": "1.5"},
        {"mal_id": "unknown"},
        {"mal_id": "abc"},
    ],
)
def test_movie_tv_identity_validation_rejects_provider_placeholders_and_malformed_ids(identity: dict[str, Any]) -> None:
    assert upload._movie_tv_identity_error(Meta(category="TV", title="Known Show", unattended=True, **identity)) is not None


@pytest.mark.parametrize(
    "identity",
    [
        {"tmdb_id": "123"},
        {"imdb_info": {"imdbID": "tt1234567"}},
        {"tvdb_id": 123},
        {"tvmaze_id": "123"},
        {"mal_id": 123},
    ],
)
def test_movie_tv_identity_validation_accepts_provider_specific_ids(identity: dict[str, Any]) -> None:
    assert upload._movie_tv_identity_error(Meta(category="TV", title="Known Show", unattended=True, **identity)) is None


def test_movie_tv_identity_validation_rejects_unmapped_anime_episode() -> None:
    meta = Meta(category="TV", title="Rilakkuma and Kaoru", unattended=True, anime=True, episode_int=19, tvdb_id=347929)

    assert upload._movie_tv_identity_error(meta) == "Unattended anime episode could not be mapped to a TVDB episode. Refusing to process the upload."


def test_movie_tv_identity_validation_accepts_mapped_anime_episode() -> None:
    meta = Meta(category="TV", title="Known Anime", unattended=True, anime=True, episode_int=1, tvdb_id=123, tvdb_episode_id=456)

    assert upload._movie_tv_identity_error(meta) is None


def test_available_screens_preserves_configured_minimum(tmp_path: Path) -> None:
    screenshot_dir = tmp_path / "tmp" / "release" / "screenshots"
    screenshot_dir.mkdir(parents=True)
    (screenshot_dir / "one.png").write_bytes(b"image")
    (screenshot_dir / "two.png").write_bytes(b"image")

    assert upload.available_screens(Meta(base_dir=str(tmp_path), uuid="release"), 4) == (2, 4)


def test_failed_tracker_names_include_attempted_failure_after_upload_flag_is_cleared() -> None:
    statuses = {
        "LUMINARR": {"upload": False, "upload_success": False},
        "SKIPPED": {"upload": False, "skipped": True},
        "GOOD": {"upload": True, "upload_success": True},
    }

    assert upload._failed_tracker_names(statuses) == ["LUMINARR"]


def test_failed_tracker_names_excludes_late_duplicate() -> None:
    statuses = {
        "ZENITH": {
            "dupe": True,
            "upload": False,
            "status_message": "Duplicate detected during upload: this release name already exists on the tracker.",
        },
        "SAMARITANO": {"upload": True, "upload_success": False},
    }

    assert upload._failed_tracker_names(statuses) == ["SAMARITANO"]


@pytest.mark.asyncio
async def test_batch_failed_items_lists_skipped_terminal_outcomes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    queue = [str(tmp_path / "missing-year.pdf"), str(tmp_path / "duplicate.mp3")]

    def fake_process_meta(meta: Meta, _base_dir: str) -> bool:
        meta.we_are_uploading = False
        return True

    info_messages, _ = _configure_do_the_thing_stubs(monkeypatch, queue, fake_process_meta)
    monkeypatch.setattr(sys, "argv", ["upload.py", *queue])

    await upload.do_the_thing(upload.base_dir)

    assert any("missing-year.pdf" in message and "Uploading is disabled" in message for message in info_messages)
    assert any("duplicate.mp3" in message and "Uploading is disabled" in message for message in info_messages)


@pytest.mark.asyncio
async def test_process_meta_stops_before_trackers_for_invalid_automatic_tv_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakePrep:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def gather_prep(self, meta: Meta, mode: str) -> Meta:
            del mode
            meta.category = "TV"
            meta.title = ""
            meta.tmdb = None
            meta.imdb = ""
            return meta

    cancel_tasks = AsyncMock(return_value=None)
    monkeypatch.setattr(upload, "Prep", FakePrep)
    monkeypatch.setattr(upload, "cancel_and_drain_early_artifact_tasks", cancel_tasks)
    meta = Meta(base_dir=str(tmp_path), uuid="invalid-tv", imghost="imgbb", unattended=True, trackers=["PEERGARDEN"])

    assert await upload.process_meta(meta, str(tmp_path)) is False
    cancel_tasks.assert_awaited_once_with("invalid-tv")


@pytest.mark.asyncio
async def test_batch_summary_reports_partial_tracker_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    queue = [str(tmp_path / "partial.epub"), str(tmp_path / "complete.epub")]

    def fake_process_meta(meta: Meta, _base_dir: str) -> Any:
        meta.we_are_uploading = True
        meta.trackers = ["GOOD", "BAD"] if "partial" in str(meta.path) else ["GOOD"]
        meta.tracker_status = {tracker: {"upload": True} for tracker in meta.trackers}
        return True

    async def fake_process_trackers(meta: Meta, *_args: Any, **_kwargs: Any) -> None:
        for tracker in meta.trackers:
            meta.tracker_status[tracker]["upload_success"] = tracker == "GOOD"

    info_messages, _ = _configure_do_the_thing_stubs(monkeypatch, queue, fake_process_meta)
    monkeypatch.setattr(upload, "process_trackers", fake_process_trackers)
    monkeypatch.setattr(sys, "argv", ["upload.py", *queue])

    await upload.do_the_thing(upload.base_dir)

    assert any("total queued 2, fully successful 1, partial 1, skipped/failed 0" in message for message in info_messages)


@pytest.mark.asyncio
async def test_batch_records_tracker_outcomes_without_meta_queue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    queue = [str(tmp_path / "partial.epub"), str(tmp_path / "complete.epub")]

    def fake_process_meta(meta: Meta, _base_dir: str) -> Any:
        meta.we_are_uploading = True
        meta.trackers = ["GOOD", "BAD"] if "partial" in str(meta.path) else ["GOOD"]
        meta.tracker_status = {tracker: {"upload": True} for tracker in meta.trackers}
        return True

    async def fake_process_trackers(meta: Meta, *_args: Any, **_kwargs: Any) -> None:
        for tracker in meta.trackers:
            meta.tracker_status[tracker]["upload_success"] = tracker == "GOOD"

    info_messages, _ = _configure_do_the_thing_stubs(monkeypatch, queue, fake_process_meta, meta_queue=None)
    monkeypatch.setattr(upload, "process_trackers", fake_process_trackers)
    monkeypatch.setattr(sys, "argv", ["upload.py", *queue])

    await upload.do_the_thing(upload.base_dir)

    assert any("total queued 2, fully successful 1, partial 1, skipped/failed 0" in message for message in info_messages)
    assert any("partial.epub" in message and "BAD" in message for message in info_messages)


@pytest.mark.asyncio
async def test_site_upload_queue_failure_uses_original_item_identifier(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = str(tmp_path / "site-item.mkv")
    queue = [{"path": path, "tracker": "SITE", "imdb_id": 1}]
    info_messages, _ = _configure_do_the_thing_stubs(
        monkeypatch, queue, lambda *_args: True, meta_overrides={"site_upload_queue": True}
    )
    monkeypatch.setattr(upload.QueueManager, "process_site_upload_item", AsyncMock(side_effect=RuntimeError("site queue failure")))
    monkeypatch.setattr(sys, "argv", ["upload.py", path])

    await upload.do_the_thing(upload.base_dir)

    assert any(path in message and "site queue failure" in message for message in info_messages)


@pytest.mark.asyncio
async def test_args_line_queue_failure_uses_original_item_identifier(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = str(tmp_path / "args-item.mkv")
    line = f'"{path}" -tk SITE'
    queue = [{"path": path, "line": line, "args": [path, "-tk", "SITE"]}]
    info_messages, _ = _configure_do_the_thing_stubs(
        monkeypatch, queue, lambda *_args: True, meta_overrides={"args_line_queue": True}
    )

    def fail_item_parse(argv: list[str], meta: Meta) -> tuple[Meta, Any, list[str]]:
        if meta.args_line_queue:
            raise RuntimeError("args queue failure")
        return Meta(path=argv[0], args_line_queue=True, base_dir=upload.base_dir, trackers=[]), None, []

    monkeypatch.setattr(upload, "parser", type("Parser", (), {"parse": staticmethod(fail_item_parse)})())
    monkeypatch.setattr(sys, "argv", ["upload.py", path])

    await upload.do_the_thing(upload.base_dir)

    assert any(line in message and "args queue failure" in message for message in info_messages)


@pytest.mark.asyncio
async def test_limit_queue_records_unprocessed_items(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    queue = [str(tmp_path / f"item-{index}.mkv") for index in range(5)]

    def fake_process_meta(meta: Meta, _base_dir: str) -> bool:
        meta.we_are_uploading = True
        meta.trackers = ["GOOD"]
        meta.tracker_status = {"GOOD": {"upload": True}}
        return True

    async def fake_process_trackers(meta: Meta, *_args: Any, **_kwargs: Any) -> None:
        meta.tracker_status["GOOD"]["upload_success"] = True

    info_messages, process_meta_mock = _configure_do_the_thing_stubs(
        monkeypatch, queue, fake_process_meta, meta_overrides={"limit_queue": 1}
    )
    monkeypatch.setattr(upload, "process_trackers", fake_process_trackers)
    monkeypatch.setattr(sys, "argv", ["upload.py", *queue])

    await upload.do_the_thing(upload.base_dir)

    assert process_meta_mock.call_count == 1
    assert any("total queued 5, fully successful 1, partial 0, skipped/failed 4" in message for message in info_messages)
    assert any("item-4.mkv" in message and "Queue limit of 1" in message for message in info_messages)


@pytest.mark.asyncio
async def test_debug_items_do_not_consume_success_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    queue = [str(tmp_path / f"debug-{index}.mkv") for index in range(2)]

    def fake_process_meta(meta: Meta, _base_dir: str) -> bool:
        meta.we_are_uploading = True
        meta.trackers = ["DEBUG"]
        meta.tracker_status = {"DEBUG": {"upload": True}}
        return True

    info_messages, process_meta_mock = _configure_do_the_thing_stubs(
        monkeypatch,
        queue,
        fake_process_meta,
        meta_overrides={"debug": True, "limit_queue": 1},
    )
    monkeypatch.setattr(upload, "process_trackers", AsyncMock(return_value=None))
    monkeypatch.setattr(sys, "argv", ["upload.py", *queue])

    await upload.do_the_thing(upload.base_dir)

    assert process_meta_mock.call_count == 2
    assert any("total queued 2, fully successful 0, partial 0, skipped/failed 2" in message for message in info_messages)


@pytest.mark.asyncio
async def test_batch_metadata_failure_without_meta_queue_updates_progress_and_persists_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = str(tmp_path / "failed.mkv")
    second_path = str(tmp_path / "second-failed.mkv")
    queue = [path, second_path]
    log_file = str(tmp_path / "processed.log")
    info_messages, _ = _configure_do_the_thing_stubs(
        monkeypatch,
        queue,
        lambda *_args: False,
        meta_queue=None,
    )
    monkeypatch.setattr(upload.QueueManager, "handle_queue", AsyncMock(return_value=(queue, log_file)))
    save_processed = AsyncMock(return_value=None)
    monkeypatch.setattr(upload, "save_processed_file", save_processed)
    monkeypatch.setattr(sys, "argv", ["upload.py", *queue])

    await upload.do_the_thing(upload.base_dir)

    assert any("Processed 2/2 files with 2 skipped uploading" in message for message in info_messages)
    assert save_processed.await_args_list[0].args == (log_file, path)
    assert save_processed.await_args_list[1].args == (log_file, second_path)


@pytest.mark.asyncio
async def test_batch_reports_torrent_success_with_usenet_preparation_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    queue = [str(tmp_path / "mixed.epub"), str(tmp_path / "torrent-only.epub")]

    def fake_process_meta(meta: Meta, _base_dir: str) -> bool:
        meta.we_are_uploading = True
        meta.trackers = ["GOOD", "NZB"] if "mixed" in str(meta.path) else ["GOOD"]
        meta.tracker_status = {tracker: {"upload": True} for tracker in meta.trackers}
        return True

    async def fake_process_trackers(meta: Meta, *_args: Any, **_kwargs: Any) -> None:
        for tracker in meta.trackers:
            meta.tracker_status[tracker]["upload_success"] = True

    class UsenetTracker:
        is_usenet = True

    info_messages, _ = _configure_do_the_thing_stubs(monkeypatch, queue, fake_process_meta)
    monkeypatch.setitem(upload.tracker_class_map, "NZB", UsenetTracker)
    monkeypatch.setattr(upload, "process_trackers", fake_process_trackers)
    monkeypatch.setattr("src.usenetcreate.prepare_and_upload_usenet", AsyncMock(side_effect=RuntimeError("Usenet preparation failed")))
    monkeypatch.setattr(sys, "argv", ["upload.py", *queue])

    await upload.do_the_thing(upload.base_dir)

    assert any("total queued 2, fully successful 1, partial 1, skipped/failed 0" in message for message in info_messages)
    assert any("mixed.epub" in message and "NZB" in message for message in info_messages)


@pytest.mark.asyncio
async def test_batch_summary_preserves_required_game_field_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    queue = [str(tmp_path / "Native_Instruments_SuperStarSaw_1.0.0_[HCiSO].dmg"), str(tmp_path / "another.dmg")]

    def fake_process_meta(meta: Meta, _base_dir: str) -> bool:
        meta.we_are_uploading = False
        meta.tracker_status = {
            "ZENITH": {
                "upload": False,
                "skipped": True,
                "skip_reason": "Required GAME fields missing: year",
            }
        }
        return True

    info_messages, _ = _configure_do_the_thing_stubs(monkeypatch, queue, fake_process_meta)
    monkeypatch.setattr(sys, "argv", ["upload.py", *queue])

    await upload.do_the_thing(upload.base_dir)

    assert any("Native_Instruments_SuperStarSaw" in message and "Required GAME fields missing: year" in message for message in info_messages)
