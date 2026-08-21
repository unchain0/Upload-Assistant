"""Domain vocabulary for external metadata-provider authentication."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from src.domain_models.errors import TmdbCredentialMissingError


class TmdbCredentialMode(StrEnum):
    """Authentication modes accepted by TMDb's API."""

    V3_API_KEY = "v3_key"
    V4_READ_ACCESS_TOKEN = "v4_read_token"  # noqa: S105 -- enum label, never a credential value


@dataclass(frozen=True, slots=True)
class TmdbCredential:
    """Normalized TMDb credential supporting v3 keys and v4 read tokens."""

    value: str
    mode: TmdbCredentialMode

    @classmethod
    def parse(cls, raw_value: object) -> TmdbCredential:
        normalized = cls._normalized_value(raw_value)
        return cls(value=normalized, mode=cls._credential_mode(normalized))

    @staticmethod
    def _normalized_value(raw_value: object) -> str:
        if not isinstance(raw_value, str):
            raise TmdbCredentialMissingError(
                "TMDb credential must be a string"
            )
        normalized = raw_value.strip()
        if not normalized:
            raise TmdbCredentialMissingError("TMDb credential is empty")
        return normalized

    @staticmethod
    def _credential_mode(normalized: str) -> TmdbCredentialMode:
        looks_like_jwt = (
            normalized.startswith("eyJ") and normalized.count(".") >= 2
        )
        if looks_like_jwt or len(normalized) > 64:
            return TmdbCredentialMode.V4_READ_ACCESS_TOKEN
        return TmdbCredentialMode.V3_API_KEY
