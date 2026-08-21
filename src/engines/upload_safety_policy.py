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
    root = _root_content_path(meta.path)
    _remember_path_parts(suspicious, (root.name,) if root is not None else ())
    for item in _filelist_items(meta.filelist):
        _inspect_content_item(suspicious, root, item)
    return suspicious


def _root_content_path(value: Any) -> PurePath | None:
    text = str(value or "").strip()
    return _content_path(text) if text else None


def _filelist_items(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple, set)) else []


def _inspect_content_item(suspicious: list[str], root: PurePath | None, value: Any) -> None:
    text = str(value or "").strip()
    if not text:
        return
    path = _content_path(text)
    parts = _relative_content_parts(root, path)
    _remember_path_parts(suspicious, parts)


def _relative_content_parts(root: PurePath | None, path: PurePath) -> tuple[str, ...]:
    if not path.is_absolute():
        return path.parts
    if root is None:
        return (path.name,)
    try:
        return path.relative_to(root).parts
    except ValueError:
        return (path.name,)


def _remember_path_parts(suspicious: list[str], parts: tuple[str, ...]) -> None:
    for part in parts:
        if _suspicious_path_part(part, suspicious):
            suspicious.append(part)


def _suspicious_path_part(part: str, suspicious: list[str]) -> bool:
    return part not in {"", ".", ".."} and _has_whitespace(part) and part not in suspicious


def blocks_automatic_upload(meta: Meta) -> bool:
    return not meta.allow_spaces and bool(content_paths_with_spaces(meta))


def book_metadata_cjk_fields(meta: Meta) -> list[str]:
    if str(meta.category or "").upper() != "BOOK":
        return []
    return [field for field, value in _book_metadata_values(meta).items() if _CJK_PATTERN.search(value)]


def _book_metadata_values(meta: Meta) -> dict[str, str]:
    return {
        "release name": str(meta.name or ""),
        "author": _first_text(meta.author, meta.book_author),
        "title": _first_text(meta.title, meta.book_title),
        "description": _first_text(meta.book_overview, meta.overview),
    }


def _first_text(primary: Any, fallback: Any) -> str:
    return str(primary if primary else fallback or "")
