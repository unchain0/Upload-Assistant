"""Consumer-owned ports for configuration use cases."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from src.domain_models.configuration import (
    ApplicationConfiguration,
    ConfigurationSourceKind,
)


class ConfigurationRepository(Protocol):
    """Persistence contract required by :class:`ConfigurationService`."""

    def load(
        self, path: Path, kind: ConfigurationSourceKind
    ) -> ApplicationConfiguration: ...

    def copy_atomically(
        self, source: Path, destination: Path
    ) -> Path | None: ...

    def write_atomically(
        self, configuration: ApplicationConfiguration, destination: Path
    ) -> None: ...
