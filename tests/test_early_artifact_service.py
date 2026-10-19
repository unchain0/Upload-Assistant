from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.domain_models.release import Meta
from src.integrations.torrent_clients.client_manager import Clients
from src.services import early_artifact_service


class _Client:
    def __init__(self, result: str | None = None) -> None:
        self.result = result
        self.calls = 0

    async def find_existing_torrent(self, _meta: Meta) -> str | None:
        self.calls += 1
        return self.result


def _meta(tmp_path: Path, **values: object) -> Meta:
    path = tmp_path / "media.mkv"
    path.write_bytes(b"media")
    state = {
        "base_dir": str(tmp_path),
        "uuid": "early",
        "path": str(path),
        "trackers": ["TRACKER"],
        **values,
    }
    return Meta(state)


def test_task_registry_start_reuse_restart_and_missing_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        early_artifact_service._early_artifact_tasks.clear()
        gate = asyncio.Event()

        async def blocked(*_args: object, **_kwargs: object) -> None:
            await gate.wait()

        monkeypatch.setattr(
            early_artifact_service, "create_base_torrents_early", blocked
        )
        monkeypatch.setattr(
            early_artifact_service, "prepare_usenet_archive_early", blocked
        )
        meta = _meta(tmp_path)
        client = _Client()
        first = early_artifact_service.start_early_artifact_tasks(
            meta, client, {}
        )
        await asyncio.sleep(0)
        second = early_artifact_service.start_early_artifact_tasks(
            meta, client, {}
        )
        assert first is second
        assert (
            early_artifact_service.get_early_artifact_tasks("early") is first
        )

        replacement = (
            await early_artifact_service.restart_early_artifact_tasks(
                meta, client, {}
            )
        )
        await asyncio.sleep(0)
        assert replacement is not first
        await early_artifact_service.cancel_and_drain_early_artifact_tasks(
            "early"
        )
        await early_artifact_service.cancel_and_drain_early_artifact_tasks(
            "missing"
        )

    asyncio.run(exercise())


def test_tracker_predicates_cover_strings_lists_and_usenet_adapters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        early_artifact_service.tracker_class_map,
        "NZB",
        SimpleNamespace(is_usenet=True),
    )
    assert (
        early_artifact_service.is_usenet_only(Meta(trackers="USENET, MANUAL"))
        is True
    )
    assert (
        early_artifact_service.is_usenet_only(Meta(trackers=["NZB"])) is True
    )
    assert (
        early_artifact_service.is_usenet_only(Meta(trackers=["TRACKER"]))
        is False
    )
    assert early_artifact_service.is_usenet_only(Meta(trackers=[])) is False
    assert (
        early_artifact_service.needs_usenet_archive(Meta(usenet=True)) is True
    )
    assert (
        early_artifact_service.needs_usenet_archive(Meta(trackers="NZB"))
        is True
    )
    assert (
        early_artifact_service.needs_usenet_archive(Meta(trackers=["TRACKER"]))
        is False
    )


def test_create_base_torrents_skip_existing_reuse_hash_and_subtitles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        client = _Client()
        for values in (
            {"nohash": True},
            {"rehash": True},
            {"force_recheck": True},
            {"trackers": ["USENET"]},
        ):
            await early_artifact_service.create_base_torrents_early(
                _meta(tmp_path, **values), client
            )
        assert client.calls == 0

        existing = _meta(tmp_path, uuid="existing")
        target = tmp_path / "tmp" / "existing" / "BASE.torrent"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"torrent")
        await early_artifact_service.create_base_torrents_early(
            existing, client
        )
        assert client.calls == 0

        reusable = tmp_path / "reuse.torrent"
        reusable.write_bytes(b"torrent")
        reused = _meta(
            tmp_path, uuid="reused", reuse_torrent_path=str(reusable)
        )
        create_existing = AsyncMock(
            return_value=str(tmp_path / "tmp" / "reused" / "BASE.torrent")
        )
        monkeypatch.setattr(
            early_artifact_service.TorrentCreator,
            "create_base_from_existing_torrent",
            create_existing,
        )
        await early_artifact_service.create_base_torrents_early(reused, client)
        create_existing.assert_awaited_once()

        found = tmp_path / "found.torrent"
        found.write_bytes(b"torrent")
        client.result = str(found)
        searched = _meta(
            tmp_path, uuid="searched", subtitle_files=["subtitle.srt"]
        )
        create_existing.reset_mock()
        create_torrent = AsyncMock(return_value=str(tmp_path / "torrent"))
        monkeypatch.setattr(
            early_artifact_service.TorrentCreator,
            "create_torrent",
            create_torrent,
        )
        await early_artifact_service.create_base_torrents_early(
            searched, client
        )
        assert searched.reuse_torrent_path == str(found)
        create_existing.assert_awaited_once()
        create_torrent.assert_awaited_once_with(
            searched, Path(searched.path), "BASE_SUBS"
        )

        client.result = None
        hashed = _meta(
            tmp_path, uuid="hashed", subtitle_files=["subtitle.srt"]
        )
        create_torrent.reset_mock()
        await early_artifact_service.create_base_torrents_early(hashed, client)
        assert [call.args[2] for call in create_torrent.await_args_list] == [
            "BASE",
            "BASE_SUBS",
        ]

    asyncio.run(exercise())


def test_early_base_creation_does_not_repeat_cached_negative_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = {
        "DEFAULT": {"default_torrent_client": "none"},
        "TORRENT_CLIENTS": {},
    }
    client = Clients(config)
    search = AsyncMock(return_value=None)
    client._find_existing_torrent_uncached = search  # type: ignore[method-assign]
    meta = _meta(tmp_path, uuid="negative-cache")
    create_torrent = AsyncMock(return_value=str(tmp_path / "BASE.torrent"))
    monkeypatch.setattr(
        early_artifact_service.TorrentCreator,
        "create_torrent",
        create_torrent,
    )

    async def exercise() -> None:
        assert await client.find_existing_torrent(meta) is None
        await early_artifact_service.create_base_torrents_early(meta, client)

    asyncio.run(exercise())

    search.assert_awaited_once_with(meta)
    create_torrent.assert_awaited_once_with(meta, Path(meta.path), "BASE")


def test_create_base_torrents_cancellation_and_failure_are_semantic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def cancelled(*_args: object, **_kwargs: object) -> None:
        raise asyncio.CancelledError

    async def failed(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("failed")

    meta = _meta(tmp_path)
    monkeypatch.setattr(
        early_artifact_service.TorrentCreator, "create_torrent", cancelled
    )
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            early_artifact_service.create_base_torrents_early(meta, _Client())
        )

    monkeypatch.setattr(
        early_artifact_service.TorrentCreator, "create_torrent", failed
    )
    asyncio.run(
        early_artifact_service.create_base_torrents_early(meta, _Client())
    )


def test_prepare_usenet_skip_success_empty_failure_and_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.integrations.usenet import creator

    async def exercise() -> None:
        no_usenet = _meta(tmp_path)
        await early_artifact_service.prepare_usenet_archive_early(
            no_usenet, {"USENET": {}}
        )
        await early_artifact_service.prepare_usenet_archive_early(
            _meta(tmp_path, usenet=True), {"USENET": "bad"}
        )
        await early_artifact_service.prepare_usenet_archive_early(
            _meta(tmp_path, usenet=True), {"USENET": {"skip_archive": True}}
        )

        prepared = AsyncMock(return_value=str(tmp_path / "prepared"))
        monkeypatch.setattr(creator, "prepare_and_upload_usenet", prepared)
        meta = _meta(tmp_path, usenet=True)
        await early_artifact_service.prepare_usenet_archive_early(
            meta, {"USENET": {}}
        )
        prepared.assert_awaited_once_with(
            meta, {"USENET": {}}, prepare_only=True
        )

        monkeypatch.setattr(
            creator, "prepare_and_upload_usenet", AsyncMock(return_value=None)
        )
        await early_artifact_service.prepare_usenet_archive_early(
            meta, {"USENET": {}}
        )

        monkeypatch.setattr(
            creator,
            "prepare_and_upload_usenet",
            AsyncMock(side_effect=RuntimeError("failed")),
        )
        await early_artifact_service.prepare_usenet_archive_early(
            meta, {"USENET": {}}
        )

        monkeypatch.setattr(
            creator,
            "prepare_and_upload_usenet",
            AsyncMock(side_effect=asyncio.CancelledError),
        )
        with pytest.raises(asyncio.CancelledError):
            await early_artifact_service.prepare_usenet_archive_early(
                meta, {"USENET": {}}
            )

    asyncio.run(exercise())


def test_run_early_artifact_task_executes_awaitable_under_suppression() -> (
    None
):
    seen: list[bool] = []

    async def work() -> None:
        from src.integrations.observability.runtime_support import (
            is_cli_progress_suppressed,
        )

        seen.append(is_cli_progress_suppressed())

    asyncio.run(early_artifact_service._run_early_artifact_task(work))
    assert seen == [True]
