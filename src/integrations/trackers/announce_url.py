# Upload Assistant © 2026 Audionut & wastaken7 — Licensed under UAPL v1.0
"""Tracker announce-URL configuration validation."""

from typing import cast


def _string_list(value: object) -> list[str] | None:
    if not isinstance(value, list) or not value:
        return None
    values = cast(list[object], value)
    strings: list[str] = []
    for item in values:
        if not isinstance(item, str):
            return None
        strings.append(item)
    return strings


def required_announce_url(value: object, tracker: str) -> str | list[str]:
    """Return a configured announce URL or reject malformed configuration."""
    if isinstance(value, str):
        return value
    strings = _string_list(value)
    if strings is not None:
        return strings
    raise ValueError(f"{tracker}: announce URL is not configured")
