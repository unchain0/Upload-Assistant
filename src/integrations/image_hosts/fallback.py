"""Pure image-host fallback planning."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping


def configured_image_hosts(default_config: Mapping[str, object]) -> tuple[str, ...]:
    indexed: list[tuple[int, str]] = []
    for key, value in default_config.items():
        match = re.fullmatch(r"img_host_(\d+)", str(key))
        host = str(value or "").strip().lower()
        if match and host:
            indexed.append((int(match.group(1)), host))
    indexed.sort(key=lambda item: item[0])
    return tuple(dict.fromkeys(host for _, host in indexed))


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

    allowed = {str(host).strip().lower() for host in allowed_hosts or () if str(host).strip()}
    unavailable = {str(host).strip().lower() for host in unavailable_hosts if str(host).strip()}
    preferred = str(preferred_host or "").strip().lower()
    ordered = list(configured_image_hosts(default_config))
    if preferred:
        ordered = [preferred, *(host for host in ordered if host != preferred)]
    return tuple(host for host in ordered if host not in unavailable and (not allowed or host in allowed))
