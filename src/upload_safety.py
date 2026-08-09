# Upload Assistant © 2026 Audionut & wastaken7 — Licensed under UAPL v1.0
from pathlib import Path
from typing import Any

from src.meta import Meta


def _has_whitespace(value: str) -> bool:
    return any(character.isspace() for character in value)


def content_paths_with_spaces(meta: Meta) -> list[str]:
    suspicious: list[str] = []
    root_text = str(meta.path or "").strip()
    root = Path(root_text.replace("\\", "/")) if root_text else None

    def remember(parts: tuple[str, ...]) -> None:
        for part in parts:
            if part not in {"", ".", ".."} and _has_whitespace(part) and part not in suspicious:
                suspicious.append(part)

    if root is not None:
        remember((root.name,))

    raw_filelist: Any = meta.filelist
    if not isinstance(raw_filelist, (list, tuple, set)):
        return suspicious
    items: list[Any] = list(raw_filelist)

    for item in items:
        item_text = str(item or "").strip()
        if not item_text:
            continue
        item_path = Path(item_text.replace("\\", "/"))
        if not item_path.is_absolute():
            remember(item_path.parts)
            continue
        if root is not None:
            try:
                relative = item_path.relative_to(root)
            except ValueError:
                remember((item_path.name,))
            else:
                remember(relative.parts)
        else:
            remember((item_path.name,))

    return suspicious


def blocks_automatic_upload(meta: Meta) -> bool:
    return not meta.allow_spaces and bool(content_paths_with_spaces(meta))
