"""UUID-addressed local screenshot inventory.

Screenshot filenames are implementation details.  Consumers select a capture
group (main video, disc, or playlist) from this manifest instead of deriving
meaning from a filename.
"""

import json
import threading
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from src.integrations.filesystem.temp_paths import screenshots_dir

_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def _lock(base_dir: str | Path, release_id: str) -> threading.RLock:
    key = str(_path(base_dir, release_id).resolve()).casefold()
    with _locks_guard:
        return _locks.setdefault(key, threading.RLock())


def _path(base_dir: str | Path, release_id: str) -> Path:
    return Path(base_dir) / "tmp" / release_id / "screenshot_manifest.json"


def _load(base_dir: str | Path, release_id: str) -> dict[str, Any]:
    try:
        value = json.loads(
            _path(base_dir, release_id).read_text(encoding="utf-8")
        )
    except OSError, json.JSONDecodeError:
        value = {}
    return value if isinstance(value, dict) else {}


def _save(
    base_dir: str | Path, release_id: str, value: dict[str, Any]
) -> None:
    output = _path(base_dir, release_id)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(output)


def _mutable_entries(manifest: dict[str, Any]) -> dict[str, Any]:
    entries = manifest.setdefault("screenshots", {})
    if isinstance(entries, dict):
        return entries
    manifest["screenshots"] = {}
    return manifest["screenshots"]


def _unique_target(source: Path) -> tuple[str, Path]:
    screenshot_id = uuid.uuid4().hex
    suffix = source.suffix.lower() or ".png"
    target = source.with_name(f"{screenshot_id}{suffix}")
    while target.exists():
        screenshot_id = uuid.uuid4().hex
        target = source.with_name(f"{screenshot_id}{suffix}")
    return screenshot_id, target


def _register_file(
    source: Path, group: str, entries: dict[str, Any]
) -> Path | None:
    if not source.is_file():
        return None
    screenshot_id, target = _unique_target(source)
    source.replace(target)
    entries[screenshot_id] = {"file": target.name, "group": group}
    return target


def register(
    base_dir: str | Path,
    release_id: str,
    paths: Iterable[str | Path],
    group: str,
) -> list[Path]:
    """Publish capture files under UUID names and return their new paths."""
    with _lock(base_dir, release_id):
        manifest = _load(base_dir, release_id)
        entries = _mutable_entries(manifest)
        result: list[Path] = []
        for value in paths:
            target = _register_file(Path(value), group, entries)
            if target is not None:
                result.append(target)
        _save(base_dir, release_id, manifest)
        return result


def _loaded_entries(
    base_dir: str | Path, release_id: str
) -> dict[str, Any] | None:
    entries = _load(base_dir, release_id).get("screenshots", {})
    return entries if isinstance(entries, dict) else None


def _entry_matches_group(value: object, group: str | None) -> bool:
    if not isinstance(value, dict):
        return False
    return group is None or value.get("group") == group


def _active_entry_path(
    directory: Path, screenshot_id: str, value: dict[str, Any]
) -> Path | None:
    path = directory / str(value.get("file", f"{screenshot_id}.png"))
    return path if path.is_file() else None


def files(
    base_dir: str | Path, release_id: str, group: str | None = None
) -> list[Path]:
    """Return active UUID screenshots, optionally limited to a capture group."""
    entries = _loaded_entries(base_dir, release_id)
    if entries is None:
        return []
    directory = screenshots_dir(base_dir, release_id)
    result: list[Path] = []
    for screenshot_id, value in entries.items():
        if not _entry_matches_group(value, group):
            continue
        path = _active_entry_path(directory, screenshot_id, value)
        if path is not None:
            result.append(path)
    return sorted(result, key=lambda path: path.name)


def clear_group(base_dir: str | Path, release_id: str, group: str) -> None:
    """Delete the active files and manifest entries for one capture group."""
    with _lock(base_dir, release_id):
        manifest = _load(base_dir, release_id)
        entries = manifest.get("screenshots")
        if not isinstance(entries, dict):
            return
        directory = screenshots_dir(base_dir, release_id)
        for screenshot_id, value in list(entries.items()):
            if not isinstance(value, dict) or value.get("group") != group:
                continue
            path = directory / str(value.get("file", f"{screenshot_id}.png"))
            path.unlink(missing_ok=True)
            entries.pop(screenshot_id)
        _save(base_dir, release_id, manifest)


def _entry_group_for_path(value: object, path: Path) -> str | None:
    if not isinstance(value, dict) or value.get("file") != path.name:
        return None
    group = value.get("group")
    return group if isinstance(group, str) and group else None


def group_for(base_dir: str | Path, release_id: str, path: Path) -> str:
    """Return the logical capture group for a local screenshot."""
    entries = _loaded_entries(base_dir, release_id)
    if entries is None:
        return "main"
    for value in entries.values():
        group = _entry_group_for_path(value, path)
        if group is not None:
            return group
    return "main"


def forget_file(base_dir: str | Path, release_id: str, path: Path) -> None:
    """Remove a transient capture entry after it has been atomically moved."""
    with _lock(base_dir, release_id):
        manifest = _load(base_dir, release_id)
        entries = manifest.get("screenshots")
        if not isinstance(entries, dict):
            return
        for screenshot_id, value in list(entries.items()):
            if isinstance(value, dict) and value.get("file") == path.name:
                entries.pop(screenshot_id)
        _save(base_dir, release_id, manifest)
