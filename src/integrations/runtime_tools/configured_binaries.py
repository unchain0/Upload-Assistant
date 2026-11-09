"""Configuration-backed resolution for optional external executables."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_active_config: Mapping[str, Any] = {}


def configure_binary_paths(default_config: Mapping[str, Any]) -> None:
    """Bind executable path overrides loaded by the composition root."""

    global _active_config
    _active_config = {"DEFAULT": dict(default_config)}


def _default_binary_config(
    config: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    selected = _active_config if config is None else config
    if not isinstance(selected, Mapping):
        return {}
    default = selected.get("DEFAULT", {})
    return default if isinstance(default, Mapping) else {}


def _managed_binary_path(key: str) -> str:
    if key != "ffmpeg_path":
        return ""
    return os.environ.get("UA_FFMPEG_PATH", "").strip()


def _configured_path_text(
    key: str,
    config: Mapping[str, Any] | None,
) -> str:
    default = _default_binary_config(config)
    explicit = str(default.get(key, "") or "").strip()
    return explicit or _managed_binary_path(key)


def _existing_binary_path(key: str, path_text: str) -> Path | None:
    if not path_text:
        return None
    path = Path(path_text).expanduser()
    if path.is_file():
        return path
    raise FileNotFoundError(
        f"Configured {key} does not exist or is not a file: {path}"
    )


def _executable_binary_path(key: str, path: Path) -> None:
    if os.name == "nt" or os.access(path, os.X_OK):
        return
    raise FileNotFoundError(f"Configured {key} is not executable: {path}")


def _validated_binary_path(key: str, path_text: str) -> str | None:
    path = _existing_binary_path(key, path_text)
    if path is None:
        return None
    _executable_binary_path(key, path)
    return str(path) if path_text.startswith("~") else path_text


def configured_binary(
    key: str, config: Mapping[str, Any] | None = None
) -> str | None:
    """Return an explicitly configured executable, if any.

    A configured path is an override, not a hint: fail clearly when it no
    longer points at a file instead of silently running a different binary.
    """
    return _validated_binary_path(key, _configured_path_text(key, config))
