# Upload Assistant © 2026 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from pathlib import PurePath, PurePosixPath, PureWindowsPath
from typing import Any

from src.domain_models.release import Meta

_CJK_PATTERN = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def _has_whitespace(value: str) -> bool:
    return any(character.isspace() for character in value)


def _content_path(value: str) -> PurePath:
    if re.match(r"^[A-Za-z]:[\\/]", value) or value.startswith(("\\\\", "//")):
        return PureWindowsPath(value)
    return PurePosixPath(value.replace("\\", "/"))


def content_paths_with_spaces(meta: Meta) -> list[str]:
    suspicious: list[str] = []
    root_text = str(meta.path or "").strip()
    root = _content_path(root_text) if root_text else None

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
        item_path = _content_path(item_text)
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


def book_metadata_cjk_fields(meta: Meta) -> list[str]:
    if str(meta.category or "").upper() != "BOOK":
        return []
    values: dict[str, str] = {
        "release name": str(meta.name or ""),
        "author": str(meta.author or meta.book_author or ""),
        "title": str(meta.title or meta.book_title or ""),
        "description": str(meta.book_overview or meta.overview or ""),
    }
    return [field for field, value in values.items() if _CJK_PATTERN.search(value)]
