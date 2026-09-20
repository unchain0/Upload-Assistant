from __future__ import annotations

from unittest.mock import patch

import pytest

import src.integrations.external_apis.tmdb as tmdb
from src.domain_models.errors import TmdbCredentialMissingError


def test_tmdb_manager_trims_v3_api_key_and_client_uses_query_auth() -> None:
    tmdb.TmdbManager({"DEFAULT": {"tmdb_api": "  abc123  "}})

    with patch.object(tmdb.httpx, "AsyncClient") as client_class:
        tmdb._tmdb_client(timeout=5)

    assert client_class.call_args.kwargs["params"] == {"api_key": "abc123"}
    assert "Authorization" not in client_class.call_args.kwargs["headers"]


def test_tmdb_manager_accepts_read_access_token_and_uses_bearer_auth() -> None:
    token = "eyJ" + "x" * 100
    tmdb.TmdbManager({"DEFAULT": {"tmdb_api": token}})

    with patch.object(tmdb.httpx, "AsyncClient") as client_class:
        tmdb._tmdb_client()

    assert (
        client_class.call_args.kwargs["headers"]["Authorization"]
        == f"Bearer {token}"
    )
    assert client_class.call_args.kwargs["params"] == {}


def test_tmdb_manager_accepts_explicit_access_token_setting() -> None:
    token = "eyJ" + "y" * 100
    tmdb.TmdbManager(
        {"DEFAULT": {"tmdb_api": "", "tmdb_access_token": f" {token} "}}
    )

    with patch.object(tmdb.httpx, "AsyncClient") as client_class:
        tmdb._tmdb_client()

    assert (
        client_class.call_args.kwargs["headers"]["Authorization"]
        == f"Bearer {token}"
    )


def test_tmdb_manager_raises_semantic_error_when_active_config_has_no_credential() -> (
    None
):
    with pytest.raises(TmdbCredentialMissingError):
        tmdb.TmdbManager({"DEFAULT": {"tmdb_api": "   "}})
