"""Background artifact preparation shared by prep and upload stages."""

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, cast

from src.domain_models.release import Meta
from src.integrations.observability.runtime_support import (
    logger,
    suppress_cli_progress,
)
from src.integrations.torrent.torrent_creator import TorrentCreator
from src.integrations.torrent_clients.client_manager import Clients
from src.integrations.trackers.registry import tracker_class_map

_early_artifact_tasks: dict[
    str, tuple[asyncio.Task[None], asyncio.Task[None]]
] = {}


async def _run_early_artifact_task(
    factory: Callable[[], Awaitable[None]],
) -> None:
    """Create and run preparatory work without rendering CLI progress bars.

    Creating the inner coroutine lazily prevents an un-awaited coroutine when a
    retained task is cancelled before the event loop schedules its first step.
    """
    with suppress_cli_progress():
        await factory()


def start_early_artifact_tasks(
    meta: Meta, client: Clients, config: Mapping[str, Any]
) -> tuple[asyncio.Task[None], asyncio.Task[None]]:
    """Start, and retain outside ``Meta``, the local-artifact preparation tasks."""
    release_id = str(meta.uuid)
    tasks = _early_artifact_tasks.get(release_id)
    if tasks is None:
        tasks = (
            asyncio.create_task(
                _run_early_artifact_task(
                    lambda: create_base_torrents_early(meta, client)
                )
            ),
            asyncio.create_task(
                _run_early_artifact_task(
                    lambda: prepare_usenet_archive_early(meta, config)
                )
            ),
        )
        _early_artifact_tasks[release_id] = tasks
    return tasks


def get_early_artifact_tasks(
    release_id: str,
) -> tuple[asyncio.Task[None], asyncio.Task[None]] | None:
    """Return the retained tasks for a release, if prep already started them."""
    return _early_artifact_tasks.get(str(release_id))


async def cancel_and_drain_early_artifact_tasks(release_id: str) -> None:
    """Cancel unfinished preparation tasks and wait for both before forgetting them."""
    tasks = _early_artifact_tasks.pop(str(release_id), None)
    if tasks is None:
        return
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def restart_early_artifact_tasks(
    meta: Meta, client: Clients, config: Mapping[str, Any]
) -> tuple[asyncio.Task[None], asyncio.Task[None]]:
    """Replace stale tasks after metadata edits with tasks based on current metadata."""
    await cancel_and_drain_early_artifact_tasks(str(meta.uuid))
    return start_early_artifact_tasks(meta, client, config)


def _normalized_trackers(meta: Meta) -> list[str]:
    raw_trackers = meta.trackers
    trackers = (
        raw_trackers.split(",")
        if isinstance(raw_trackers, str)
        else raw_trackers
    )
    return [
        str(tracker).strip().upper()
        for tracker in trackers
        if str(tracker).strip()
    ]


def _tracker_is_usenet(tracker: str) -> bool:
    return bool(
        tracker in {"USENET", "MANUAL"}
        or getattr(tracker_class_map.get(tracker), "is_usenet", False)
    )


def is_usenet_only(meta: Meta) -> bool:
    normalized = _normalized_trackers(meta)
    return bool(normalized) and all(
        _tracker_is_usenet(tracker) for tracker in normalized
    )


def _skip_early_torrent_creation(meta: Meta) -> bool:
    return bool(
        meta.nohash
        or meta.rehash
        or meta.force_recheck
        or is_usenet_only(meta)
    )


def _early_torrent_paths(meta: Meta) -> tuple[str, Path, Path]:
    release_id = str(meta.uuid)
    release_root = Path(str(meta.base_dir)) / "tmp" / release_id
    return (
        release_id,
        release_root / "BASE.torrent",
        release_root / "BASE_SUBS.torrent",
    )


def _reusable_torrent_exists(value: object) -> bool:
    return bool(value and Path(str(value)).exists())


async def _find_reusable_torrent(meta: Meta, client: Clients) -> str | None:
    reuse_torrent = meta.reuse_torrent_path
    if _reusable_torrent_exists(reuse_torrent):
        return str(reuse_torrent)
    logger.debug(
        "[cyan]Early torrent creation has no cached reusable torrent; "
        "searching client.[/cyan]"
    )
    search_started = time.perf_counter()
    reuse_torrent = await client.find_existing_torrent(meta)
    logger.debug(
        "[cyan]Early client torrent search completed in "
        f"{time.perf_counter() - search_started:.2f}s[/cyan]"
    )
    return str(reuse_torrent) if reuse_torrent else None


async def _create_early_base(
    meta: Meta, release_id: str, reuse_torrent: str | None
) -> None:
    if not _reusable_torrent_exists(reuse_torrent):
        logger.debug(
            "[cyan]No reusable client torrent found; creating BASE torrent "
            "while metadata and screenshots are processed.[/cyan]"
        )
        await TorrentCreator.create_torrent(
            meta, Path(cast(str, meta.path)), "BASE"
        )
        return
    meta.reuse_torrent_path = reuse_torrent
    logger.debug(
        "[cyan]Creating torrent from the client copy while metadata and "
        "screenshots are processed.[/cyan]"
    )
    base_creation_started = time.perf_counter()
    created_path = await TorrentCreator.create_base_from_existing_torrent(
        cast(str, reuse_torrent), str(meta.base_dir), release_id
    )
    logger.debug(
        "[cyan]Early base torrent creation completed in "
        f"{time.perf_counter() - base_creation_started:.2f}s: "
        f"{created_path or 'no file created'}[/cyan]"
    )


async def _create_early_subtitle_torrent(
    meta: Meta, subs_torrent_path: Path
) -> None:
    if not meta.subtitle_files or subs_torrent_path.exists():
        return
    await TorrentCreator.create_torrent(
        meta, Path(cast(str, meta.path)), "BASE_SUBS"
    )


async def _run_early_torrent_creation(
    meta: Meta, client: Clients, task_started: float
) -> None:
    release_id, torrent_path, subs_torrent_path = _early_torrent_paths(meta)
    if torrent_path.exists():
        logger.debug(
            "[cyan]Skipping early torrent creation; BASE already exists at "
            f"{torrent_path}[/cyan]"
        )
        return
    reuse_torrent = await _find_reusable_torrent(meta, client)
    await _create_early_base(meta, release_id, reuse_torrent)
    await _create_early_subtitle_torrent(meta, subs_torrent_path)
    logger.debug(
        "[cyan]Early torrent task completed in "
        f"{time.perf_counter() - task_started:.2f}s[/cyan]"
    )


async def create_base_torrents_early(meta: Meta, client: Clients) -> None:
    """Reuse or hash BASE torrents while metadata and screenshots are processed."""
    task_started = time.perf_counter()
    if _skip_early_torrent_creation(meta):
        logger.debug(
            "[cyan]Skipping early torrent creation due to hashing or tracker "
            "settings.[/cyan]"
        )
        return
    try:
        await _run_early_torrent_creation(meta, client, task_started)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        logger.warning(
            "[yellow]Early torrent creation failed; upload stage will retry: "
            f"{error}[/yellow]"
        )


def needs_usenet_archive(meta: Meta) -> bool:
    if meta.usenet:
        return True
    return any(
        _tracker_is_usenet(tracker)
        for tracker in _normalized_trackers(meta)
        if tracker != "MANUAL"
    )


def _usenet_archive_allowed(meta: Meta, config: Mapping[str, Any]) -> bool:
    usenet_cfg = config.get("USENET", {})
    if not needs_usenet_archive(meta):
        return False
    if not isinstance(usenet_cfg, Mapping):
        return False
    return not bool(usenet_cfg.get("skip_archive", False))


async def _prepare_usenet_archive(
    meta: Meta, config: Mapping[str, Any]
) -> None:
    from src.integrations.usenet.creator import prepare_and_upload_usenet

    logger.debug(
        "[cyan]Preparing Usenet archive and PAR2 files while metadata and "
        "screenshots are processed.[/cyan]"
    )
    with suppress_cli_progress():
        prepared_path = await prepare_and_upload_usenet(
            meta, dict(config), prepare_only=True
        )
    if not prepared_path:
        logger.warning(
            "[yellow]Early Usenet preparation did not complete; posting stage "
            "will retry.[/yellow]"
        )


async def prepare_usenet_archive_early(
    meta: Meta, config: Mapping[str, Any]
) -> None:
    """Create archive/PAR2 files before duplicate confirmation, never post them."""
    if not _usenet_archive_allowed(meta, config):
        return
    try:
        await _prepare_usenet_archive(meta, config)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        logger.warning(
            "[yellow]Early Usenet preparation failed; posting stage will retry: "
            f"{error}[/yellow]"
        )
