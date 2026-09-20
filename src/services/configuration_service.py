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
from src.engines.configuration_reconciliation import (
    reconcile_runtime_configuration,
)
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

    def _load_defaults(self) -> ApplicationConfiguration:
        return self._repository.load(
            self._defaults_path, ConfigurationSourceKind.DEFAULT
        )

    def _materialize_explicit_configuration(self) -> None:
        if self._explicit_path is None:
            return
        resolved_explicit = self._explicit_path.expanduser().resolve()
        if not resolved_explicit.is_file():
            raise ConfigurationNotFoundError(
                f"Explicit configuration file not found: {resolved_explicit}"
            )
        runtime = self._runtime_path.expanduser().resolve()
        if resolved_explicit != runtime:
            self._repository.copy_atomically(
                resolved_explicit, self._runtime_path
            )

    def _runtime_seed_source(self) -> Path:
        if self._legacy_path.expanduser().is_file():
            return self._legacy_path
        return self._defaults_path

    def _ensure_runtime_configuration(self) -> None:
        if self._runtime_path.expanduser().is_file():
            return
        self._repository.copy_atomically(
            self._runtime_seed_source(), self._runtime_path
        )

    def _load_legacy_or_defaults(
        self, defaults: ApplicationConfiguration
    ) -> ApplicationConfiguration:
        if not self._legacy_path.expanduser().is_file():
            return defaults
        return self._repository.load(
            self._legacy_path, ConfigurationSourceKind.LEGACY
        )

    def _reconcile_configuration(
        self,
        runtime: ApplicationConfiguration,
        legacy: ApplicationConfiguration,
        defaults: ApplicationConfiguration,
    ) -> ConfigurationMigration:
        return reconcile_runtime_configuration(
            runtime,
            legacy,
            defaults,
            runtime_path=str(self._runtime_path.expanduser().resolve()),
        )

    def _persist_migration(self, migration: ConfigurationMigration) -> None:
        if not migration.changed:
            return
        self._repository.write_atomically(
            migration.configuration, self._runtime_path
        )

    def prepare_runtime_configuration(self) -> ConfigurationMigration:
        defaults = self._load_defaults()
        self._materialize_explicit_configuration()
        self._ensure_runtime_configuration()
        runtime = self._repository.load(
            self._runtime_path, ConfigurationSourceKind.RUNTIME
        )
        legacy = self._load_legacy_or_defaults(defaults)
        migration = self._reconcile_configuration(runtime, legacy, defaults)
        self._persist_migration(migration)
        return migration

    def load(self) -> ApplicationConfiguration:
        """Compatibility surface while legacy call sites migrate to typed inputs."""

        return self.prepare_runtime_configuration().configuration

    def load_mutable(self) -> MutableConfiguration:
        """Return a fresh mutable view for legacy callers during migration."""

        return self.load().mutable_copy()
