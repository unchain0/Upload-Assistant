"""Compatibility package for user-owned runtime configuration."""

from __future__ import annotations

import warnings

from src.bootstrap import prepare_runtime_configuration
from src.integrations.filesystem.paths import DATA_DIR

_migration = prepare_runtime_configuration()
if _migration.changed:
    warnings.warn(
        "Reconciled the runtime configuration using configured legacy values "
        f"({len(_migration.migrated_paths)} recovered, "
        f"{len(_migration.added_default_paths)} schema defaults added). "
        "The previous runtime file was preserved as config.py.pre-masa.bak.",
        stacklevel=2,
    )

# Resolve the user-owned config first; bundled static resources remain available
# from this package directory.
__path__.insert(0, str(DATA_DIR))
