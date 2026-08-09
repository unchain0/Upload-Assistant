# ruff: noqa: S101

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import src.tmdb as tmdb


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
