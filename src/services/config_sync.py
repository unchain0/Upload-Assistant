"""Add missing example-config keys without changing values the user already set."""

import ast
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ConfigSyncError(RuntimeError):
    """The config file could not be synchronized safely."""


@dataclass(frozen=True)
class ConfigSyncResult:
    updated: bool
    added: tuple[str, ...]


def _config_target(target: ast.AST) -> bool:
    return isinstance(target, ast.Name) and target.id == "config"


def _assigns_config(node: ast.AST) -> bool:
    if not isinstance(node, ast.Assign):
        return False
    return any(_config_target(target) for target in node.targets)


def _literal_mapping(node: ast.Assign) -> dict[str, Any] | None:
    value = ast.literal_eval(node.value)
    return value if isinstance(value, dict) else None


def _config_mapping(source: str) -> dict[str, Any]:
    tree = ast.parse(source)
    for node in tree.body:
        if not _assigns_config(node):
            continue
        mapping = _literal_mapping(node)
        if mapping is not None:
            return mapping
    raise ConfigSyncError("config assignment was not found")


def _missing_paths(
    example: Mapping[str, Any],
    user: Mapping[str, Any],
    prefix: tuple[str, ...] = (),
) -> list[tuple[tuple[str, ...], Any]]:
    missing: list[tuple[tuple[str, ...], Any]] = []
    for key, value in example.items():
        name = str(key)
        if name not in user:
            missing.append((prefix + (name,), value))
            continue
        if isinstance(value, dict) and isinstance(user.get(name), dict):
            missing.extend(_missing_paths(value, user[name], prefix + (name,)))
    return missing


def _merged_value(current: Any, value: Any) -> Any:
    if isinstance(value, dict) and isinstance(current, dict):
        return _merge_missing(current, value)
    return current


def _copy_mapping(user: Any) -> dict[str, Any]:
    if isinstance(user, dict):
        return dict(user)
    return {}


def _merge_missing(user: Any, example: Any) -> Any:
    if not isinstance(example, dict):
        return user
    merged = _copy_mapping(user)
    for key, value in example.items():
        if key not in merged:
            merged[key] = value
            continue
        merged[key] = _merged_value(merged[key], value)
    return merged


def _mapping_preserved(before: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
    for key, value in before.items():
        if key not in after:
            return False
        if not _values_preserved(value, after[key]):
            return False
    return True


def _values_preserved(before: Any, after: Any) -> bool:
    if isinstance(before, dict) and isinstance(after, dict):
        return _mapping_preserved(before, after)
    return before == after


def _render_config(mapping: dict[str, Any]) -> str:
    return f"config = {mapping!r}\n"


def sync_user_config(config_path: Path, example_path: Path) -> ConfigSyncResult:
    """Copy keys that exist only in the example config into the user file."""
    example = _config_mapping(example_path.read_text(encoding="utf-8"))
    user_source = config_path.read_text(encoding="utf-8")
    user = _config_mapping(user_source)
    missing = _missing_paths(example, user)
    if not missing:
        return ConfigSyncResult(False, ())
    merged = _merge_missing(user, example)
    if not isinstance(merged, dict) or not _values_preserved(user, merged):
        raise ConfigSyncError("user configuration values would change")
    backup = config_path.with_name(f"{config_path.name}.bak")
    backup.write_text(user_source, encoding="utf-8")
    config_path.write_text(_render_config(merged), encoding="utf-8")
    added = tuple(".".join(path) for path, _value in missing)
    return ConfigSyncResult(True, added)
