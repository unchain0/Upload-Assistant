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


def _configure_do_the_thing_stubs(
    monkeypatch: pytest.MonkeyPatch,
    queue: list[str],
    process_meta: Any,
) -> tuple[list[str], AsyncMock]:
    queue_paths = [str(path) for path in queue]
    for path in queue_paths:
        Path(path).touch()

    info_messages: list[str] = []

    def fake_parse(argv: list[str], meta: Meta) -> tuple[Meta, Any, list[str]]:
        first = argv[0] if argv else queue_paths[0]
        parsed = Meta(path=first, queue=[], base_dir=upload.base_dir, trackers=[], site_check=False)
        return parsed, None, []

    async def fake_handle_queue(_path: str, _meta: Meta, _paths: list[str], _base_dir: str) -> tuple[list[str], str | None]:
        return list(queue_paths), None

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
        "Batch summary: total enfileirado 2, processados com sucesso 1, parciais 0, skipped/failed 1" in message
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
        "Batch summary: total enfileirado 3, processados com sucesso 2, parciais 0, skipped/failed 1" in message
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
        "Batch summary: total enfileirado 2, processados com sucesso 0, parciais 0, skipped/failed 2" in message
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

    assert any("processados com sucesso 1, parciais 1, skipped/failed 0" in message for message in info_messages)
    assert any("partial.epub" in message and "BAD" in message for message in info_messages)
