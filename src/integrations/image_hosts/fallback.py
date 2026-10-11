"""Pure image-host fallback planning."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping


def _normalized_host(value: object) -> str:
    return str(value or "").strip().lower()


def _indexed_host(key: object, value: object) -> tuple[int, str] | None:
    match = re.fullmatch(r"img_host_(\d+)", str(key))
    host = _normalized_host(value)
    if match is None or not host:
        return None
    return int(match.group(1)), host


def configured_image_hosts(
    default_config: Mapping[str, object],
) -> tuple[str, ...]:
    indexed: list[tuple[int, str]] = []
    for key, value in default_config.items():
        item = _indexed_host(key, value)
        if item is not None:
            indexed.append(item)
    indexed.sort(key=lambda item: item[0])
    return tuple(dict.fromkeys(host for _, host in indexed))


def _normalized_host_set(hosts: Iterable[str]) -> set[str]:
    normalized = {_normalized_host(host) for host in hosts}
    normalized.discard("")
    return normalized


def _preferred_host_order(hosts: tuple[str, ...], preferred: str) -> list[str]:
    if not preferred:
        return list(hosts)
    return [preferred, *(host for host in hosts if host != preferred)]


def _host_is_eligible(
    host: str, unavailable: set[str], allowed: set[str]
) -> bool:
    if host in unavailable:
        return False
    return not allowed or host in allowed


def image_host_fallback_plan(
    default_config: Mapping[str, object],
    *,
    preferred_host: str | None,
    allowed_hosts: Iterable[str] | None = None,
    unavailable_hosts: Iterable[str] = (),
) -> tuple[str, ...]:
    """Return a complete, non-cyclic host plan.

    The preferred host is attempted first regardless of its numbered slot;
    every other configured eligible host remains available as a fallback,
    including hosts in earlier slots.
    """

    allowed = _normalized_host_set(allowed_hosts or ())
    unavailable = _normalized_host_set(unavailable_hosts)
    preferred = _normalized_host(preferred_host)
    ordered = _preferred_host_order(
        configured_image_hosts(default_config), preferred
    )
    return tuple(
        host
        for host in ordered
        if _host_is_eligible(host, unavailable, allowed)
    )
