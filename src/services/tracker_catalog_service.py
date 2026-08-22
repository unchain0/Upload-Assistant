"""Read-only tracker catalog exposed to delivery adapters."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

from src.domain_models.tracker_catalog import (
    KNOWN_TRACKERS,
    TRACKER_DEFINITIONS,
)


def _hostname(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = urlparse(value if "://" in value else f"//{value}")
    return parsed.hostname.lower() if parsed.hostname else None


def _tracker_runtime_config(config: Mapping[str, Any]) -> Mapping[str, Any]:
    trackers_value = config.get("TRACKERS", {})
    return trackers_value if isinstance(trackers_value, Mapping) else {}


def _configured_comment_hosts(configured: object) -> list[str]:
    if not isinstance(configured, Mapping):
        return []
    hosts: list[str] = []
    for key in ("base_url", "announce_url"):
        hostname = _hostname(configured.get(key))
        if hostname:
            hosts.append(hostname)
    return hosts


def _merged_comment_hosts(
    catalog_hosts: tuple[str, ...], configured: object
) -> tuple[str, ...]:
    domains = [*catalog_hosts, *_configured_comment_hosts(configured)]
    return tuple(dict.fromkeys(domains))


def get_tracker_comment_hosts(
    config: Mapping[str, Any],
) -> dict[str, tuple[str, ...]]:
    """Return catalog aliases merged with runtime URL overrides."""

    tracker_config = _tracker_runtime_config(config)
    result: dict[str, tuple[str, ...]] = {}
    for tracker_name, definition in TRACKER_DEFINITIONS.items():
        hosts = _merged_comment_hosts(
            definition.comment_hosts,
            tracker_config.get(tracker_name, {}),
        )
        if hosts:
            result[tracker_name] = hosts
    return result


def is_known_tracker(tracker_name: str) -> bool:
    """Return whether a tracker identifier exists in the domain catalog."""

    return tracker_name.strip().upper() in KNOWN_TRACKERS | {
        "MANUAL",
        "USENET",
    }
