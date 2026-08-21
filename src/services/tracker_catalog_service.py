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


def get_tracker_comment_hosts(
    config: Mapping[str, Any],
) -> dict[str, tuple[str, ...]]:
    """Return catalog aliases merged with runtime URL overrides."""

    trackers_value = config.get("TRACKERS", {})
    tracker_config = (
        trackers_value if isinstance(trackers_value, Mapping) else {}
    )
    result: dict[str, tuple[str, ...]] = {}

    for tracker_name, definition in TRACKER_DEFINITIONS.items():
        domains = list(definition.comment_hosts)
        configured = tracker_config.get(tracker_name, {})
        if isinstance(configured, Mapping):
            for key in ("base_url", "announce_url"):
                hostname = _hostname(configured.get(key))
                if hostname:
                    domains.append(hostname)
        if domains:
            result[tracker_name] = tuple(dict.fromkeys(domains))
    return result


def is_known_tracker(tracker_name: str) -> bool:
    """Return whether a tracker identifier exists in the domain catalog."""

    return tracker_name.strip().upper() in KNOWN_TRACKERS | {
        "MANUAL",
        "USENET",
    }
