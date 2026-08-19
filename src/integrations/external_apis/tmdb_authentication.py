"""Pure construction of TMDb request authentication."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from src.domain_models.external_api import TmdbCredential, TmdbCredentialMode


@dataclass(frozen=True, slots=True)
class TmdbAuthentication:
    headers: Mapping[str, str]
    query: Mapping[str, str]


def build_tmdb_authentication(credential: TmdbCredential) -> TmdbAuthentication:
    if credential.mode is TmdbCredentialMode.V4_READ_ACCESS_TOKEN:
        return TmdbAuthentication(
            headers=MappingProxyType({"Authorization": f"Bearer {credential.value}", "Accept": "application/json"}),
            query=MappingProxyType({}),
        )
    return TmdbAuthentication(
        headers=MappingProxyType({"Accept": "application/json"}),
        query=MappingProxyType({"api_key": credential.value}),
    )
