"""Pure reconciliation of runtime, legacy and bundled configuration documents."""

from __future__ import annotations

from collections.abc import Mapping

from src.domain_models.configuration import (
    ApplicationConfiguration,
    ConfigPath,
    ConfigurationMigration,
    ConfigurationSource,
    ConfigurationSourceKind,
    ConfigValue,
    MutableConfiguration,
    MutableConfigValue,
)
from src.engines.configuration_selection import configuration_has_user_settings, is_user_setting


def reconcile_runtime_configuration(
    runtime: ApplicationConfiguration,
    legacy: ApplicationConfiguration,
    defaults: ApplicationConfiguration,
    *,
    runtime_path: str,
) -> ConfigurationMigration:
    """Preserve runtime values, recover empty user settings, then add defaults."""

    effective = runtime.mutable_copy()
    migrated: list[ConfigPath] = []
    added_defaults: list[ConfigPath] = []

    if configuration_has_user_settings(legacy, defaults):
        _fill_empty_user_settings(effective, legacy.sections, (), migrated)
    _add_missing_defaults(effective, defaults.sections, (), added_defaults)

    configuration = ApplicationConfiguration.from_mapping(
        effective,
        ConfigurationSource(path=runtime_path, kind=ConfigurationSourceKind.RUNTIME),
    )
    return ConfigurationMigration(
        configuration=configuration,
        migrated_paths=tuple(migrated),
        added_default_paths=tuple(added_defaults),
    )


def _is_empty(value: MutableConfigValue | None) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if not normalized:
            return True
        placeholders = {
            "api key",
            "api_key",
            "key here",
            "password",
            "token",
            "token here",
        }
        placeholder_prefixes = (
            "<",
            "change me",
            "changeme",
            "example",
            "get it from",
            "get this from",
            "insert ",
            "replace ",
            "your ",
        )
        return normalized in placeholders or normalized.startswith(placeholder_prefixes)
    if isinstance(value, list | dict):
        return len(value) == 0
    return False


def _fill_empty_user_settings(
    target: MutableConfiguration,
    source: Mapping[str, ConfigValue],
    path: ConfigPath,
    migrated: list[ConfigPath],
) -> None:
    for key, source_value in source.items():
        current_path = (*path, str(key))
        if isinstance(source_value, Mapping):
            target_value = target.get(key)
            if not isinstance(target_value, dict):
                target_value = {}
                target[key] = target_value
            _fill_empty_user_settings(target_value, source_value, current_path, migrated)
            continue
        target_value = target.get(key)
        if is_user_setting(current_path, source_value) and (key not in target or _is_empty(target_value)):
            target[key] = _clone(source_value)
            migrated.append(current_path)


def _add_missing_defaults(
    target: MutableConfiguration,
    defaults: Mapping[str, ConfigValue],
    path: ConfigPath,
    added: list[ConfigPath],
) -> None:
    for key, default_value in defaults.items():
        current_path = (*path, str(key))
        if key not in target:
            target[key] = _clone(default_value)
            added.append(current_path)
            continue
        target_value = target[key]
        if isinstance(target_value, dict) and isinstance(default_value, Mapping):
            _add_missing_defaults(target_value, default_value, current_path, added)


def _clone(value: ConfigValue) -> MutableConfigValue:
    if isinstance(value, Mapping):
        return {str(key): _clone(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_clone(item) for item in value]
    return value
