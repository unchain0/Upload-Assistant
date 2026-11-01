"""Fail-closed origin gate for unattended uploads.

The finished-download hook runs Upload-Assistant with ``--unattended`` for
every torrent qBittorrent completes. A torrent fetched from a public indexer
must never reach a private tracker automatically, so unattended runs are only
allowed when the torrent announces to a tracker configured in this instance.

Unknown hosts, torrents without trackers (DHT only) and any qBittorrent
lookup failure all skip the upload. Manual runs (no ``--unattended``) are not
affected.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any, NamedTuple
from urllib.parse import urlsplit

_ANNOUNCE_KEYS = ("announce_url", "tracker_urls", "base_url", "tracker_url")


class OriginGateResult(NamedTuple):
    allowed: bool
    reason: str


def _string_urls(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return []


def announce_host(url: str) -> str | None:
    """Return the lowercase host of an announce URL, or None when unusable."""
    raw = url.strip()
    if not raw:
        return None
    try:
        parsed = urlsplit(raw if "://" in raw else f"http://{raw}")
        host = parsed.hostname
    except ValueError:
        return None
    return host.lower() if host else None


def configured_origin_hosts(config: Mapping[str, Any]) -> set[str]:
    """Hosts this instance accepts as torrent origins, taken from config."""
    hosts: set[str] = set()
    trackers = config.get("TRACKERS")
    if not isinstance(trackers, Mapping):
        return hosts
    for tracker_config in trackers.values():
        if not isinstance(tracker_config, Mapping):
            continue
        for key in _ANNOUNCE_KEYS:
            for url in _string_urls(tracker_config.get(key)):
                host = announce_host(url)
                if host:
                    hosts.add(host)
    return hosts


def evaluate_origin(
    *,
    unattended: bool,
    tracker_urls: Iterable[str],
    allowed_hosts: set[str],
    lookup_error: str | None = None,
) -> OriginGateResult:
    """Decide whether an upload may proceed, failing closed when unsure."""
    if not unattended:
        return OriginGateResult(True, "manual run")
    if lookup_error:
        return OriginGateResult(
            False, f"SKIP: qBittorrent tracker lookup failed: {lookup_error}"
        )
    urls = [url for url in tracker_urls if str(url).strip()]
    if not urls:
        return OriginGateResult(
            False, "SKIP: torrent has no trackers (DHT only)"
        )
    hosts = {
        host
        for host in (announce_host(str(url)) for url in urls)
        if host is not None
    }
    if not hosts:
        return OriginGateResult(
            False, "SKIP: torrent has no usable announce URL"
        )
    unknown = sorted(host for host in hosts if host not in allowed_hosts)
    if unknown:
        return OriginGateResult(
            False,
            "SKIP: origin tracker is not a configured private tracker "
            f"({', '.join(unknown)})",
        )
    return OriginGateResult(True, "origin tracker is configured")


async def check_origin(
    *,
    unattended: bool,
    tracker_hash: str | None,
    config: Mapping[str, Any],
    fetch_tracker_urls: Callable[[str], Awaitable[list[str]]],
) -> OriginGateResult:
    """Evaluate the gate, calling qBittorrent only for unattended runs."""
    if not unattended:
        return OriginGateResult(True, "manual run")
    if not tracker_hash:
        return OriginGateResult(
            False, "SKIP: unattended run without a torrent hash"
        )
    try:
        urls = await asyncio.wait_for(
            fetch_tracker_urls(tracker_hash), timeout=20
        )
        error = None
    except Exception as exc:  # any failure must fail closed
        urls = []
        error = str(exc) or type(exc).__name__
    return evaluate_origin(
        unattended=True,
        tracker_urls=urls,
        allowed_hosts=configured_origin_hosts(config),
        lookup_error=error,
    )
