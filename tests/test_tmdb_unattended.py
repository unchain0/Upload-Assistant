from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import src.integrations.external_apis.tmdb as tmdb
from src.domain_models.external_api import TmdbCredential
from src.services import metadata_service as metadata_searching
from src.services.preparation_helpers import _distinct_aka


@pytest.fixture(autouse=True)
def _configured_tmdb_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tmdb, "_tmdb_credential", TmdbCredential.parse("test-api-key"))


class _EmptyResponse:
    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, list[object]]:
        return {"movie_results": [], "tv_results": []}


class _EmptyClient:
    async def __aenter__(self) -> _EmptyClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, *_args: object, **_kwargs: object) -> _EmptyResponse:
        return _EmptyResponse()


class _MultipleResultsResponse:
    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, list[dict[str, object]]]:
        return {
            "results": [
                {"id": 651, "name": "60 Minutes", "original_name": "60 Minutes", "first_air_date": "1968-09-24"},
                {"id": 133713, "name": "60 Minutes", "original_name": "60 Minutes", "first_air_date": "2021-09-16"},
            ]
        }


class _MultipleResultsClient:
    async def __aenter__(self) -> _MultipleResultsClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, *_args: object, **_kwargs: object) -> _MultipleResultsResponse:
        return _MultipleResultsResponse()


@pytest.mark.asyncio
async def test_get_tmdb_from_imdb_never_prompts_when_unattended() -> None:
    prompt = AsyncMock(return_value="movie/123")
    with (
        patch.object(tmdb.httpx, "AsyncClient", return_value=_EmptyClient()),
        patch.object(tmdb.imdb_manager, "get_imdb_info_api", new=AsyncMock(return_value={"title": "Unknown", "year": 2026})),
        patch.object(tmdb, "get_tmdb_id", new=AsyncMock(return_value=(0, "MOVIE"))),
        patch.object(tmdb, "prompt_in_thread", new=prompt),
    ):
        category, tmdb_id, _language, _filename_search = await tmdb.get_tmdb_from_imdb(1234567, filename="Unknown", mode="cli", unattended=True)

    assert category == "MOVIE"
    assert tmdb_id == 0
    prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_tmdb_id_never_prompts_in_unattended_debug_mode() -> None:
    prompt = AsyncMock(return_value="2")
    with (
        patch.object(tmdb.httpx, "AsyncClient", return_value=_MultipleResultsClient()),
        patch.object(tmdb, "prompt_in_thread", new=prompt),
    ):
        tmdb_id, category = await tmdb.get_tmdb_id("60 Minutes", 2026, "TV", debug=True, unattended=True)

    assert tmdb_id == 651
    assert category == "TV"
    prompt.assert_not_awaited()


def test_quickie_imdb_id_requires_tmdb_confirmation() -> None:
    assert tmdb._reconcile_tmdb_imdb_id(8484866, None, True) == (0, False, 0)


def test_quickie_imdb_id_uses_tmdb_mismatch_as_authoritative() -> None:
    assert tmdb._reconcile_tmdb_imdb_id(8484866, "tt27497198", True) == (8484866, True, 27497198)


def test_manual_imdb_id_is_preserved_without_tmdb_confirmation() -> None:
    assert tmdb._reconcile_tmdb_imdb_id(8484866, None, False) == (8484866, False, 0)


def test_manual_imdb_id_reports_but_does_not_apply_tmdb_mismatch() -> None:
    assert tmdb._reconcile_tmdb_imdb_id(8484866, "tt27497198", False) == (8484866, False, 27497198)


@pytest.mark.asyncio
async def test_tmdb_only_identity_does_not_inherit_tvmaze_external_ids() -> None:
    tvdb_handler = SimpleNamespace(get_tvdb_by_external_id=AsyncMock(return_value=(None, None)))
    with patch.object(metadata_searching.tvmaze_manager, "search_tvmaze", new=AsyncMock(return_value=(123, 8484866, 333734))):
        tvmaze_id, tvdb_id, _data, _name = await metadata_searching.get_tvmaze_tvdb(
            "The Rap of China",
            "2026",
            0,
            326694,
            tvdb_handler,
        )

    assert tvmaze_id == 0
    assert tvdb_id == 0
    tvdb_handler.get_tvdb_by_external_id.assert_awaited_once_with(imdb=0, tmdb=326694, tv_movie=False)


def test_equivalent_aka_is_removed_after_metadata_reconciliation() -> None:
    assert _distinct_aka("The Rap of China", "AKA The Rap of China", 2026) == ""
    assert _distinct_aka("The Rap of China", "AKA The Rap of China (2026)", 2026) == ""


def test_distinct_transliterated_aka_is_preserved() -> None:
    assert _distinct_aka("The Great Ruler", "AKA Da Zhu Zai Nian Fan", 2023) == "AKA Da Zhu Zai Nian Fan"
