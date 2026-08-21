"""Immutable domain representation of application configuration."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import TypeGuard

type ConfigScalar = str | int | float | bool | None
type ConfigValue = ConfigScalar | tuple[ConfigValue, ...] | Mapping[str, ConfigValue]
type MutableConfigValue = ConfigScalar | list[MutableConfigValue] | dict[str, MutableConfigValue]
type MutableConfiguration = dict[str, MutableConfigValue]
type ConfigPath = tuple[str, ...]


class ConfigurationSourceKind(StrEnum):
    """Supported origins for one effective configuration document."""

    EXPLICIT = "explicit"
    RUNTIME = "runtime"
    LEGACY = "legacy"
    DEFAULT = "default"


@dataclass(frozen=True, slots=True)
class ConfigurationSource:
    """Identity of the file dressed into an application configuration."""

    path: str
    kind: ConfigurationSourceKind


@dataclass(frozen=True, slots=True)
class ApplicationConfiguration:
    """Deeply immutable configuration dressed at the filesystem boundary."""

    sections: Mapping[str, Mapping[str, ConfigValue]]
    source: ConfigurationSource

    @classmethod
    def from_mapping(
        cls,
        data: Mapping[str, object],
        source: ConfigurationSource,
    ) -> ApplicationConfiguration:
        sections: dict[str, Mapping[str, ConfigValue]] = {}
        for section_name, section_value in data.items():
            if not isinstance(section_value, Mapping):
                raise TypeError(f"Configuration section {section_name!r} must be a mapping")
            sections[str(section_name)] = _freeze_mapping(section_value)
        return cls(sections=MappingProxyType(sections), source=source)

    def section(self, name: str) -> Mapping[str, ConfigValue]:
        """Return one immutable section or an empty immutable mapping."""

        return self.sections.get(name, MappingProxyType({}))

    def mutable_copy(self) -> MutableConfiguration:
        """Create a mutable copy for integration adapters and legacy consumers."""

        return {section: _thaw_mapping(values) for section, values in self.sections.items()}


@dataclass(frozen=True, slots=True)
class ConfigurationMigration:
    """Effective configuration plus paths changed during reconciliation."""

    configuration: ApplicationConfiguration
    migrated_paths: tuple[ConfigPath, ...] = ()
    added_default_paths: tuple[ConfigPath, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.migrated_paths or self.added_default_paths)


def _freeze_value(value: object) -> ConfigValue:
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if _is_configuration_sequence(value):
        return tuple(_freeze_value(item) for item in value)
    if _is_configuration_scalar(value):
        return value
    raise TypeError(f"Unsupported configuration value: {type(value).__name__}")


def _is_configuration_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _is_configuration_scalar(value: object) -> TypeGuard[ConfigScalar]:
    return isinstance(value, str | int | float | bool) or value is None


def _freeze_mapping(values: Mapping[object, object]) -> Mapping[str, ConfigValue]:
    return MappingProxyType({str(key): _freeze_value(value) for key, value in values.items()})


def _thaw_value(value: ConfigValue) -> MutableConfigValue:
    if isinstance(value, Mapping):
        return _thaw_mapping(value)
    if isinstance(value, tuple):
        return [_thaw_value(item) for item in value]
    return value


def _thaw_mapping(values: Mapping[str, ConfigValue]) -> dict[str, MutableConfigValue]:
    return {key: _thaw_value(value) for key, value in values.items()}
