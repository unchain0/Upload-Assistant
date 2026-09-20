"""Filesystem adapter for Upload Assistant runtime locations.

The source checkout is intentionally treated as read-only. ``UA_DATA_DIR`` is
the supported override for containers, portable installs, and test runs.
"""

import os
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parents[3]


def _configured_data_dir() -> Path | None:
    override = os.environ.get("UA_DATA_DIR", "").strip()
    return Path(override).expanduser() if override else None


def _windows_data_dir() -> Path:
    local_app_data = os.environ.get(
        "LOCALAPPDATA", Path.home() / "AppData" / "Local"
    )
    return Path(local_app_data) / "Upload-Assistant"


def _unix_data_dir() -> Path:
    xdg_data_home = os.environ.get("XDG_DATA_HOME", "").strip()
    base = (
        Path(xdg_data_home).expanduser()
        if xdg_data_home
        else Path.home() / ".local" / "share"
    )
    primary = base / "Upload-Assistant"
    legacy = base / "upload-assistant"
    return legacy if not primary.exists() and legacy.exists() else primary


def _default_data_dir() -> Path:
    override = _configured_data_dir()
    if override is not None:
        return override
    return _windows_data_dir() if os.name == "nt" else _unix_data_dir()


STATE_DIR = _default_data_dir()
# Keep the historic data/ and tmp/ layout, but place the whole tree below a
# user-owned state root. This avoids a broad, fragile rewrite of path consumers.
DATA_DIR = STATE_DIR / "data"
TMP_DIR = STATE_DIR / "tmp"
CONFIG_PATH = DATA_DIR / "config.py"
LEGACY_CONFIG_PATH = CODE_DIR / "data" / "config.py"
EXAMPLE_CONFIG_PATH = CODE_DIR / "data" / "example_config.py"


def ensure_data_dir() -> Path:
    """Create and return the user-owned runtime directory."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return STATE_DIR
