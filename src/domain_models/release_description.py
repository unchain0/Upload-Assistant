"""Pure release-description value normalization."""

from __future__ import annotations

from src.domain_models.release import Meta


def base_description(meta: Meta) -> str:
    """Return the CLI-provided release description as text."""

    value = meta.description
    return value if isinstance(value, str) else str(value or "")
