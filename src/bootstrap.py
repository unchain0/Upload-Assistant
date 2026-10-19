"""Composition root for application-wide infrastructure and services."""

from __future__ import annotations

import os
from pathlib import Path

from src.domain_models.configuration import (
    ApplicationConfiguration,
    ConfigurationMigration,
)
from src.integrations.configuration import PythonConfigurationRepository
from src.integrations.filesystem.paths import (
    CONFIG_PATH,
    EXAMPLE_CONFIG_PATH,
    LEGACY_CONFIG_PATH,
)
from src.services.configuration_service import ConfigurationService


def build_configuration_service() -> ConfigurationService:
    """Construct the configuration use case with concrete filesystem paths."""

    explicit_value = os.environ.get("UA_CONFIG_PATH", "").strip()
    return ConfigurationService(
        PythonConfigurationRepository(),
        runtime_path=CONFIG_PATH,
        legacy_path=LEGACY_CONFIG_PATH,
        defaults_path=EXAMPLE_CONFIG_PATH,
        explicit_path=Path(explicit_value).expanduser()
        if explicit_value
        else None,
    )


def prepare_runtime_configuration() -> ConfigurationMigration:
    """Reconcile and materialize the single effective runtime configuration."""

    return build_configuration_service().prepare_runtime_configuration()


def load_runtime_configuration() -> ApplicationConfiguration:
    """Load and, when necessary, materialize the effective runtime config."""

    return prepare_runtime_configuration().configuration
