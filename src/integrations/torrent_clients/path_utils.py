# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import ast
import os
from pathlib import Path, PureWindowsPath
from typing import cast


def _non_empty_strings(
    values: list[object] | tuple[object, ...],
) -> list[str]:
    return [str(item) for item in values if item is not None and str(item)]


def _literal_sequence(value: str) -> list[str] | None:
    if not value.startswith("[") or not value.endswith("]"):
        return None
    try:
        parsed = ast.literal_eval(value)
    except SyntaxError, ValueError:
        return None
    parsed_values = cast(list[object], parsed)
    return _non_empty_strings(parsed_values)


def _coerce_string_list(value: str) -> list[str]:
    normalized = value.strip()
    parsed = _literal_sequence(normalized)
    if parsed is not None:
        return parsed
    return [normalized] if normalized else []


def coerce_str_list(value: object) -> list[str]:
    """Coerce a configured path value into a list of non-empty strings."""
    if isinstance(value, (list, tuple)):
        values = cast(list[object] | tuple[object, ...], value)
        return _non_empty_strings(values)
    if isinstance(value, str):
        return _coerce_string_list(value)
    return [str(value)] if value is not None else []


def is_path_under(path: str | Path, root: str | Path) -> bool:
    """Return whether path is within root using case-insensitive boundaries."""
    return _relative_path_parts(path, root) is not None


def _relative_path_parts(
    path: str | Path, root: str | Path
) -> tuple[str, ...] | None:
    """Return path components below root, or None when root is not a prefix."""
    path_parts = Path(os.path.normpath(str(path))).parts
    root_parts = Path(os.path.normpath(str(root))).parts
    if len(path_parts) < len(root_parts):
        return None
    if not all(
        os.path.normcase(path_part).casefold()
        == os.path.normcase(root_part).casefold()
        for path_part, root_part in zip(
            path_parts[: len(root_parts)], root_parts, strict=True
        )
    ):
        return None
    return path_parts[len(root_parts) :]


def _mapping_relative_parts(
    save_path: str,
    local_path: str,
    remote_path: str,
) -> tuple[str, ...] | None:
    if not local_path or not remote_path:
        return None
    if os.path.normcase(local_path) == os.path.normcase(remote_path):
        return None
    return _relative_path_parts(save_path, local_path)


def _mapped_client_path(
    save_path: str,
    local_path: str,
    remote_path: str,
) -> str:
    relative_parts = _mapping_relative_parts(
        save_path, local_path, remote_path
    )
    if relative_parts is None:
        return save_path
    mapped = Path(remote_path)
    if relative_parts:
        mapped /= Path(*relative_parts)
    return str(mapped)


def _format_client_path(path: str, trailing_slash: bool) -> str:
    normalized = path.replace("\\", "/").replace(os.sep, "/")
    if not trailing_slash or normalized.endswith("/"):
        return normalized
    return f"{normalized}/"


def map_save_path(
    save_path: str | Path,
    local_path: str | Path | None,
    remote_path: str | Path | None,
    *,
    trailing_slash: bool = True,
) -> str:
    """Map a local path to a client path and format it with a trailing slash."""
    local_path_str = str(local_path) if local_path is not None else ""
    remote_path_str = str(remote_path) if remote_path is not None else ""
    mapped_path = _mapped_client_path(
        str(save_path), local_path_str, remote_path_str
    )
    return _format_client_path(mapped_path, trailing_slash)


def _reserved_windows_device_names() -> set[str]:
    return {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }


def _windows_device_name(directory_name: str) -> str:
    return directory_name.split(".", 1)[0].rstrip(" .").casefold()


def _has_path_separator(directory_name: str) -> bool:
    return "/" in directory_name or "\\" in directory_name


def _windows_path_is_anchored(directory_name: str) -> bool:
    windows_path = PureWindowsPath(directory_name)
    return bool(windows_path.drive or windows_path.anchor)


def _unsafe_tracker_directory_name(directory_name: str) -> bool:
    checks = (
        not directory_name,
        directory_name in {".", ".."},
        _has_path_separator(directory_name),
        Path(directory_name).is_absolute(),
        _windows_path_is_anchored(directory_name),
        _windows_device_name(directory_name)
        in _reserved_windows_device_names(),
    )
    return any(checks)


def tracker_directory(
    link_target: str | Path, link_dir_name: str, tracker: str
) -> Path:
    """Build a safe tracker link directory and reject unsafe Windows names."""
    directory_name = link_dir_name.strip() or tracker
    if _unsafe_tracker_directory_name(directory_name):
        raise ValueError(
            f"Invalid tracker link directory name: {directory_name!r}"
        )
    return Path(link_target) / directory_name
