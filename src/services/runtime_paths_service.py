"""Application policy for immutable code and user-owned runtime locations."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    code_dir: Path
    state_dir: Path
    data_dir: Path
    tmp_dir: Path
    config_path: Path
    legacy_config_path: Path
    example_config_path: Path


def _code_dir() -> Path:
    return Path(__file__).resolve().parents[2]


def _state_dir() -> Path:
    override = os.environ.get("UA_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Upload-Assistant"
    xdg_data_home = os.environ.get("XDG_DATA_HOME", "").strip()
    base = Path(xdg_data_home).expanduser() if xdg_data_home else Path.home() / ".local" / "share"
    primary = base / "Upload-Assistant"
    legacy = base / "upload-assistant"
    return legacy if not primary.exists() and legacy.exists() else primary


def resolve_runtime_paths() -> RuntimePaths:
    code_dir = _code_dir()
    state_dir = _state_dir()
    data_dir = state_dir / "data"
    return RuntimePaths(
        code_dir=code_dir,
        state_dir=state_dir,
        data_dir=data_dir,
        tmp_dir=state_dir / "tmp",
        config_path=data_dir / "config.py",
        legacy_config_path=code_dir / "data" / "config.py",
        example_config_path=code_dir / "data" / "example_config.py",
    )


RUNTIME_PATHS = resolve_runtime_paths()
