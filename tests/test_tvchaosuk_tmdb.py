from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers import tvchaosuk


class TmdbDouble:
    def __init__(self, config: dict) -> None:
        self.config = config
        self.get_episode_details = AsyncMock(return_value={"air_date": "2026-01-02", "name": "Episode", "overview": "Overview"})
        self.get_season_details = AsyncMock(
            return_value={
                "air_date": "2026-01-01",
                "name": "Season One",
                "episodes": [
                    {
                        "season_number": 1,
                        "episode_number": 2,
                        "name": "Episode",
                        "air_date": "2026-01-02",
                        "overview": "Overview",
                    }
                ],
            }
        )


def _config() -> dict:
    return {
        "DEFAULT": {
            "tmdb_api": " 0123456789abcdef0123456789abcdef ",
            "screens": 2,
            "img_host_1": "imgbb",
        },
        "TRACKERS": {"TVCHAOSUK": {}},
    }


def _meta(tmp_path: Path, **values: object) -> Meta:
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "uuid": "tvchaos",
        "category": "TV",
        "tmdb": 123,
        "tmdb_id": 123,
        "season_int": 1,
        "episode_int": 2,
        "season": "S01",
        "episode": "E02",
        "tv_pack": False,
        "tmdb_episode_data": None,
        "tmdb_season_data": None,
        "origin_country": [],
        "production_countries": [],
        "production_companies": [],
        "networks": [],
        "debug": False,
    }
    state.update(values)
    return Meta(state)


def test_tvchaosuk_uses_shared_tmdb_adapter_for_episode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(tvchaosuk, "TmdbManager", TmdbDouble)
    tracker = tvchaosuk.TVChaosUK(_config())
    meta = _meta(tmp_path)

    result = asyncio.run(tracker.get_tmdb_data(meta))

    assert result == {}
    assert meta.episode_airdate == "2026-01-02"
    assert meta.episode_name == "Episode"
    assert meta.episode_overview == "Overview"
    tracker.tmdb_manager.get_episode_details.assert_awaited_once_with(123, 1, 2)


def test_tvchaosuk_uses_shared_tmdb_adapter_for_season(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(tvchaosuk, "TmdbManager", TmdbDouble)
    tracker = tvchaosuk.TVChaosUK(_config())
    meta = _meta(tmp_path, tv_pack=True)

    asyncio.run(tracker.get_tmdb_data(meta))

    assert meta.season_air_first_date == "2026-01-01"
    assert meta.season_name == "Season One"
    assert meta.episodes == [{"code": "S01E02", "title": "Episode", "airdate": "2026-01-02", "overview": "Overview"}]
    tracker.tmdb_manager.get_season_details.assert_awaited_once_with(123, 1)


def test_tvchaosuk_movie_debug_does_not_make_a_second_tmdb_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(tvchaosuk, "TmdbManager", TmdbDouble)
    tracker = tvchaosuk.TVChaosUK(_config())
    meta = _meta(tmp_path, category="MOVIE", debug=True)

    assert asyncio.run(tracker.get_tmdb_data(meta)) == {}
    tracker.tmdb_manager.get_episode_details.assert_not_awaited()
    tracker.tmdb_manager.get_season_details.assert_not_awaited()
