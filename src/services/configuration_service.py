"""Application service for selecting and reconciling runtime configuration."""

from __future__ import annotations

from pathlib import Path

from src.domain_models.configuration import (
    ApplicationConfiguration,
    ConfigurationMigration,
    ConfigurationSourceKind,
    MutableConfiguration,
)
from src.domain_models.errors import ConfigurationNotFoundError
from src.engines.configuration_reconciliation import reconcile_runtime_configuration
from src.services.configuration_ports import ConfigurationRepository


class ConfigurationService:
    """Materialize one effective configuration source for every delivery path."""

    def __init__(
        self,
        repository: ConfigurationRepository,
        *,
        runtime_path: Path,
        legacy_path: Path,
        defaults_path: Path,
        explicit_path: Path | None = None,
    ) -> None:
        self._repository = repository
        self._runtime_path = runtime_path
        self._legacy_path = legacy_path
        self._defaults_path = defaults_path
        self._explicit_path = explicit_path

    def prepare_runtime_configuration(self) -> ConfigurationMigration:
        defaults = self._repository.load(self._defaults_path, ConfigurationSourceKind.DEFAULT)

        if self._explicit_path is not None:
            resolved_explicit = self._explicit_path.expanduser().resolve()
            if not resolved_explicit.is_file():
                raise ConfigurationNotFoundError(f"Explicit configuration file not found: {resolved_explicit}")
            if resolved_explicit != self._runtime_path.expanduser().resolve():
                self._repository.copy_atomically(resolved_explicit, self._runtime_path)

        if not self._runtime_path.expanduser().is_file():
            source = self._legacy_path if self._legacy_path.expanduser().is_file() else self._defaults_path
            self._repository.copy_atomically(source, self._runtime_path)

        runtime = self._repository.load(self._runtime_path, ConfigurationSourceKind.RUNTIME)
        legacy: ApplicationConfiguration = self._repository.load(self._legacy_path, ConfigurationSourceKind.LEGACY) if self._legacy_path.expanduser().is_file() else defaults
        migration = reconcile_runtime_configuration(
            runtime,
            legacy,
            defaults,
            runtime_path=str(self._runtime_path.expanduser().resolve()),
        )
        if migration.changed:
            self._repository.write_atomically(migration.configuration, self._runtime_path)
        return migration

    def load(self) -> ApplicationConfiguration:
        """Compatibility surface while legacy call sites migrate to typed inputs."""

        return self.prepare_runtime_configuration().configuration

    def load_mutable(self) -> MutableConfiguration:
        """Return a fresh mutable view for legacy callers during migration."""

        return self.load().mutable_copy()
