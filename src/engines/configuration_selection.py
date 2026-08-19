"""Pure selection rules for competing configuration sources."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from src.domain_models.configuration import ApplicationConfiguration, ConfigurationSourceKind, ConfigValue

_CREDENTIAL_KEY = re.compile(r"(?:^|_)(?:api(?:_key)?|token|password|cookie|secret|passkey|username)$")
_SELECTION_KEYS = {
    "default_trackers",
    "default_torrent_client",
    "torrent_client",
    "usenet_server",
    "usenet_user",
    "usenet_username",
}


def configuration_has_user_settings(
    configuration: ApplicationConfiguration,
    defaults: ApplicationConfiguration,
) -> bool:
    """Return whether a candidate contains intentional user-owned settings.

    Schema-only values shipped in ``example_config.py`` do not count. This is
    intentionally conservative: an existing runtime configuration wins as soon
    as it contains a credential, a selected image host/client/tracker, or another
    user-selection signal that differs from the bundled defaults.
    """

    default_leaves = dict(_iter_leaves(defaults.sections))
    for path, value in _iter_leaves(configuration.sections):
        if not is_user_setting(path, value):
            continue
        if _normalized(value) != _normalized(default_leaves.get(path)):
            return True
    return False


def select_configuration(
    candidates: Sequence[ApplicationConfiguration],
    defaults: ApplicationConfiguration,
) -> ApplicationConfiguration:
    """Choose the effective source without relying on import path or mtime."""

    by_kind = {candidate.source.kind: candidate for candidate in candidates}
    explicit = by_kind.get(ConfigurationSourceKind.EXPLICIT)
    if explicit is not None:
        return explicit

    runtime = by_kind.get(ConfigurationSourceKind.RUNTIME)
    if runtime is not None and configuration_has_user_settings(runtime, defaults):
        return runtime

    legacy = by_kind.get(ConfigurationSourceKind.LEGACY)
    if legacy is not None and configuration_has_user_settings(legacy, defaults):
        return legacy

    if runtime is not None:
        return runtime
    if legacy is not None:
        return legacy
    return defaults


def _iter_leaves(
    values: Mapping[str, ConfigValue],
    path: tuple[str, ...] = (),
) -> list[tuple[tuple[str, ...], ConfigValue]]:
    leaves: list[tuple[tuple[str, ...], ConfigValue]] = []
    for key, value in values.items():
        current = (*path, str(key))
        if isinstance(value, Mapping):
            leaves.extend(_iter_leaves(value, current))
        else:
            leaves.append((current, value))
    return leaves


def is_user_setting(path: tuple[str, ...], value: ConfigValue) -> bool:
    if not path or _is_empty(value):
        return False
    key = path[-1].lower()
    if re.fullmatch(r"img_host_\d+", key):
        return True
    if key in _SELECTION_KEYS:
        return True
    if key in {"tmdb_api", "tmdb_access_token"}:
        return True
    return bool(_CREDENTIAL_KEY.search(key))


def _is_empty(value: ConfigValue | None) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if not normalized:
            return True
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
        placeholder_values = {"api key", "api_key", "key here", "password", "token", "token here"}
        return normalized in placeholder_values or normalized.startswith(placeholder_prefixes)
    if isinstance(value, tuple | Mapping):
        return len(value) == 0
    return False


def _normalized(value: ConfigValue | None) -> object:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, tuple):
        return tuple(_normalized(item) for item in value)
    if isinstance(value, Mapping):
        return tuple(sorted((str(key), _normalized(item)) for key, item in value.items()))
    return value
