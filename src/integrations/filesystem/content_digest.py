# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
"""Content digests shared by image upload and description assembly."""

from __future__ import annotations

from collections.abc import Sequence
from hashlib import sha256
from pathlib import Path

_CHUNK_SIZE = 1024 * 1024


def file_content_digest(path: str | Path) -> str:
    digest = sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def unique_by_content(paths: Sequence[str]) -> list[str]:
    """Keep the first path for each distinct byte sequence."""
    seen: set[str] = set()
    unique: list[str] = []
    for path in paths:
        try:
            key = file_content_digest(path)
        except OSError:
            key = f"path:{path}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique
