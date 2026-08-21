"""Filesystem adapter for literal ``config = {...}`` Python files."""

from __future__ import annotations

import ast
import os
import pprint
import shutil
import tempfile
from pathlib import Path
from typing import cast

from src.domain_models.configuration import (
    ApplicationConfiguration,
    ConfigurationSource,
    ConfigurationSourceKind,
)
from src.domain_models.errors import (
    ConfigurationNotFoundError,
    ConfigurationSyntaxError,
)


class PythonConfigurationRepository:
    """Load and persist the exact configuration path selected by the service."""

    def load(
        self, path: Path, kind: ConfigurationSourceKind
    ) -> ApplicationConfiguration:
        resolved = path.expanduser().resolve()
        if not resolved.is_file():
            raise ConfigurationNotFoundError(
                f"Configuration file not found: {resolved}"
            )
        try:
            source = resolved.read_text(encoding="utf-8")
        except OSError as error:
            raise ConfigurationNotFoundError(
                f"Could not read configuration at {resolved}: {error}"
            ) from error
        try:
            tree = ast.parse(source, filename=str(resolved))
        except SyntaxError as error:
            raise ConfigurationSyntaxError(
                f"Could not parse configuration at {resolved}: {error}"
            ) from error

        value_node: ast.expr | None = None
        for statement in tree.body:
            if isinstance(statement, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "config"
                for target in statement.targets
            ):
                value_node = statement.value
                break
            if (
                isinstance(statement, ast.AnnAssign)
                and isinstance(statement.target, ast.Name)
                and statement.target.id == "config"
            ):
                value_node = statement.value
                break
        if value_node is None:
            raise ConfigurationSyntaxError(
                f"Configuration at {resolved} does not define 'config'"
            )
        try:
            raw = ast.literal_eval(value_node)
        except (TypeError, ValueError, SyntaxError) as error:
            raise ConfigurationSyntaxError(
                f"Configuration at {resolved} must be a literal mapping: {error}"
            ) from error
        if not isinstance(raw, dict):
            raise ConfigurationSyntaxError(
                f"Configuration at {resolved} must define a dictionary"
            )
        try:
            return ApplicationConfiguration.from_mapping(
                cast(dict[str, object], raw),
                ConfigurationSource(path=str(resolved), kind=kind),
            )
        except TypeError as error:
            raise ConfigurationSyntaxError(
                f"Configuration at {resolved} has an invalid section: {error}"
            ) from error

    def copy_atomically(self, source: Path, destination: Path) -> Path | None:
        source = source.expanduser().resolve()
        destination = destination.expanduser().resolve()
        if not source.is_file():
            raise ConfigurationNotFoundError(
                f"Configuration file not found: {source}"
            )

        destination.parent.mkdir(parents=True, exist_ok=True)
        backup_path: Path | None = None
        if (
            destination.is_file()
            and destination.read_bytes() != source.read_bytes()
        ):
            candidate = destination.with_name(
                f"{destination.name}.pre-masa.bak"
            )
            if not candidate.exists():
                shutil.copy2(destination, candidate)
            backup_path = candidate

        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
        )
        temporary = Path(temporary_name)
        try:
            with (
                source.open("rb") as source_handle,
                os.fdopen(descriptor, "wb") as destination_handle,
            ):
                shutil.copyfileobj(source_handle, destination_handle)
                destination_handle.flush()
                os.fsync(destination_handle.fileno())
            shutil.copymode(source, temporary)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        return backup_path

    def write_atomically(
        self, configuration: ApplicationConfiguration, destination: Path
    ) -> None:
        rendered = (
            "from typing import Any\n\nconfig: dict[str, Any] = "
            + pprint.pformat(
                configuration.mutable_copy(), width=160, sort_dicts=False
            )
            + "\n"
        )
        destination = destination.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_file():
            backup = destination.with_name(f"{destination.name}.pre-masa.bak")
            if not backup.exists():
                shutil.copy2(destination, backup)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(rendered)
                handle.flush()
                os.fsync(handle.fileno())
            if destination.exists():
                shutil.copymode(destination, temporary)
            else:
                temporary.chmod(0o600)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
