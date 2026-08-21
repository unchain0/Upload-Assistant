from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from pathlib import Path
from types import TracebackType
from typing import Any, ClassVar, Self
from unittest.mock import AsyncMock

import httpx
import pytest

from src.domain_models.release import Meta
from src.services import metadata_service


class _Tmdb:
    def __init__(self, **values: object) -> None:
        self.config: dict[str, Any] = {}
        self.values = values
        self.calls: list[str] = []

    async def _result(self, name: str) -> Any:
        self.calls.append(name)
        value = self.values.get(name)
        if isinstance(value, BaseException):
            raise value
        return value

    async def tmdb_other_meta(self, **_kwargs: object) -> Any:
        return await self._result("tmdb_other_meta")

    async def get_episode_details(
        self, *_args: object, **_kwargs: object
    ) -> Any:
        return await self._result("get_episode_details")

    async def get_season_details(
        self, *_args: object, **_kwargs: object
    ) -> Any:
        return await self._result("get_season_details")

    async def get_tmdb_from_imdb(
        self, *_args: object, **_kwargs: object
    ) -> Any:
        return await self._result("get_tmdb_from_imdb")


class _Tvdb:
    def __init__(self, **values: object) -> None:
        self.values = values
        self.calls: list[str] = []

    async def _result(self, name: str) -> Any:
        self.calls.append(name)
        value = self.values.get(name)
        if isinstance(value, BaseException):
            raise value
        return value

    async def get_tvdb_episodes(
        self, *_args: object, **_kwargs: object
    ) -> Any:
        return await self._result("get_tvdb_episodes")

    async def get_tvdb_by_external_id(
        self, *_args: object, **_kwargs: object
    ) -> Any:
        return await self._result("get_tvdb_by_external_id")

    async def search_tvdb_series(
        self, *_args: object, **_kwargs: object
    ) -> Any:
        return await self._result("search_tvdb_series")

    async def get_specific_episode_data(
        self, *_args: object, **_kwargs: object
    ) -> Any:
        return await self._result("get_specific_episode_data")

    async def get_imdb_id_from_tvdb_episode_id(
        self, *_args: object, **_kwargs: object
    ) -> Any:
        return await self._result("get_imdb_id_from_tvdb_episode_id")


def _meta(tmp_path: Path, **values: object) -> Meta:
    media = tmp_path / "Show.S01E02.mkv"
    media.write_bytes(b"media")
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "path": str(media),
        "filename": media.name,
        "category": "TV",
        "tmdb_id": 101,
        "imdb_id": 202,
        "tvdb_id": 303,
        "tvmaze_id": 404,
        "season_int": 1,
        "episode_int": 2,
        "season": "S01",
        "episode": "E02",
        "tv_pack": False,
        "daily_episode_title": "",
        "manual_date": None,
        "manual_language": "",
        "tvmaze_manual": 0,
        "search_year": "2024",
        "anime": False,
        "mal_manual": 0,
        "aka": "",
        "original_language": "fr",
        "artwork_url": "",
        "debug": True,
        "mode": "cli",
        "imdb_info": {"existing": True},
        "tvdb_episode_data": {},
        "tvmaze_episode_data": {},
        "tmdb_episode_data": None,
        "we_checked_tvdb": False,
        "we_asked_tvmaze": False,
        "we_checked_tmdb": False,
        "episode_overview": True,
        "auto_episode_title": None,
        "overview_meta": None,
        "tvdb_episode_name": None,
        "tvdb_overview": None,
        "tvdb_season": None,
        "tvdb_episode": None,
        "tvdb_episode_id": None,
        "no_season": False,
    }
    state.update(values)
    return Meta(state)


def _patch_imdb_tvmaze(
    monkeypatch: pytest.MonkeyPatch,
    *,
    imdb: object = None,
    tvmaze_search: object = None,
    tvmaze_episode: object = None,
) -> None:
    async def imdb_info(*_args: object, **_kwargs: object) -> Any:
        if isinstance(imdb, BaseException):
            raise imdb
        return imdb

    async def search(*_args: object, **_kwargs: object) -> Any:
        if isinstance(tvmaze_search, BaseException):
            raise tvmaze_search
        return tvmaze_search

    async def episode(*_args: object, **_kwargs: object) -> Any:
        if isinstance(tvmaze_episode, BaseException):
            raise tvmaze_episode
        return tvmaze_episode

    monkeypatch.setattr(
        metadata_service.imdb_manager, "get_imdb_info_api", imdb_info
    )
    monkeypatch.setattr(
        metadata_service.tvmaze_manager, "search_tvmaze", search
    )
    monkeypatch.setattr(
        metadata_service.tvmaze_manager, "get_tvmaze_episode_data", episode
    )


def test_coercion_and_tvdb_series_metadata() -> None:
    assert metadata_service._coerce_int("7") == 7
    assert metadata_service._coerce_int("bad") is None

    english = Meta(original_language="en", search_year="old")
    metadata_service._apply_tvdb_series_metadata(
        english, {"series_title": "Ignored", "series_year": 2024}, "Ignored"
    )
    assert not english.tvdb_series_name and english.search_year == "old"

    meta = Meta(original_language="fr", search_year="old")
    metadata_service._apply_tvdb_series_metadata(
        meta, {"series_title": "Fallback", "series_year": "2024"}
    )
    assert meta.tvdb_series_name == "Fallback"
    assert meta.tvdb_series_year == 2024 and meta.search_year == "2024"

    metadata_service._apply_tvdb_series_metadata(
        meta, {"series_year": "2099"}, "Explicit"
    )
    assert meta.tvdb_series_name == "Explicit"
    assert meta.tvdb_series_year == 2024
    metadata_service._apply_tvdb_series_metadata(
        meta, ["invalid"], "List Name"
    )
    assert meta.tvdb_series_name == "List Name"


def test_all_ids_tv_episode_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmdb = _Tmdb(
        tmdb_other_meta={"title": "TMDb Title"},
        get_episode_details={"name": "TMDb Episode"},
    )
    tvdb = _Tvdb(
        get_tvdb_episodes=(
            {"series_title": "TVDb Series", "series_year": 2024},
            "Explicit Series",
        ),
    )
    _patch_imdb_tvmaze(
        monkeypatch,
        imdb={"title": "IMDb Title"},
        tvmaze_episode={"name": "TVMaze Episode"},
    )
    meta = _meta(tmp_path)

    result = asyncio.run(metadata_service.all_ids(meta, tvdb, tmdb))

    assert result.title == "TMDb Title"
    assert result.imdb_info == {"title": "IMDb Title"}
    assert result.tvdb_episode_data["series_title"] == "TVDb Series"
    assert result.tvdb_series_name == "Explicit Series"
    assert result.tvdb_series_year == 2024
    assert result.tvmaze_episode_data == {"name": "TVMaze Episode"}
    assert result.tmdb_episode_data == {"name": "TMDb Episode"}
    assert (
        result.we_checked_tvdb
        and result.we_asked_tvmaze
        and result.we_checked_tmdb
    )


def test_all_ids_errors_bad_tvdb_formats_and_pack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmdb = _Tmdb(
        tmdb_other_meta=RuntimeError("tmdb failed"),
        get_episode_details=RuntimeError("episode failed"),
    )
    tvdb = _Tvdb(get_tvdb_episodes=RuntimeError("tvdb failed"))
    _patch_imdb_tvmaze(
        monkeypatch,
        imdb=RuntimeError("imdb failed"),
        tvmaze_episode=RuntimeError("tvmaze failed"),
    )
    meta = _meta(tmp_path, imdb_info={"kept": True})
    result = asyncio.run(metadata_service.all_ids(meta, tvdb, tmdb))
    assert result.imdb_info == {"kept": True}
    assert (
        not result.we_checked_tvdb
        and not result.we_asked_tvmaze
        and not result.we_checked_tmdb
    )

    for bad in (({"data": True},), {"unexpected": True}):
        tmdb = _Tmdb(tmdb_other_meta={}, get_episode_details={})
        tvdb = _Tvdb(get_tvdb_episodes=bad)
        _patch_imdb_tvmaze(monkeypatch, imdb=None, tvmaze_episode=None)
        result = asyncio.run(
            metadata_service.all_ids(_meta(tmp_path), tvdb, tmdb)
        )
        assert not result.we_checked_tvdb
        assert result.imdb_info == {}

    pack_tmdb = _Tmdb(tmdb_other_meta={}, get_season_details={"episodes": []})
    pack_tvdb = _Tvdb(get_tvdb_episodes=([], "Series"))
    _patch_imdb_tvmaze(monkeypatch, imdb={})
    pack = _meta(tmp_path, tv_pack=True)
    result = asyncio.run(metadata_service.all_ids(pack, pack_tvdb, pack_tmdb))
    assert (
        result.tmdb_season_data == {"episodes": []} and result.we_checked_tmdb
    )

    pack_tmdb = _Tmdb(
        tmdb_other_meta={}, get_season_details=RuntimeError("season failed")
    )
    result = asyncio.run(
        metadata_service.all_ids(
            _meta(tmp_path, tv_pack=True), pack_tvdb, pack_tmdb
        )
    )
    assert not result.we_checked_tmdb


def test_all_ids_gather_and_core_result_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmdb = _Tmdb(tmdb_other_meta={})
    tvdb = _Tvdb()
    _patch_imdb_tvmaze(monkeypatch, imdb={})

    async def raising_gather(
        *aws: Awaitable[object], **_kwargs: object
    ) -> list[object]:
        for awaitable in aws:
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
        raise RuntimeError("gather failed")

    monkeypatch.setattr(metadata_service.asyncio, "gather", raising_gather)
    meta = _meta(tmp_path, category="MOVIE")
    assert asyncio.run(metadata_service.all_ids(meta, tvdb, tmdb)) is meta

    class BrokenResults:
        def __getitem__(self, _key: object) -> object:
            raise RuntimeError("slice failed")

    async def broken_results(
        *aws: Awaitable[object], **_kwargs: object
    ) -> BrokenResults:
        for awaitable in aws:
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
        return BrokenResults()

    monkeypatch.setattr(metadata_service.asyncio, "gather", broken_results)
    meta = _meta(tmp_path, category="MOVIE")
    result = asyncio.run(metadata_service.all_ids(meta, tvdb, tmdb))
    assert result.imdb_info == {}


def test_imdb_tmdb_tvdb_episode_and_pack_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_imdb_tvmaze(monkeypatch, imdb={"title": "IMDb"}, tvmaze_search=777)
    tmdb = _Tmdb(
        tmdb_other_meta={"title": "TMDb"},
        get_episode_details={"name": "Episode"},
        get_season_details={"episodes": [1]},
    )
    tvdb = _Tvdb(get_tvdb_episodes=({"series_year": 2023}, "Series"))

    episode = asyncio.run(
        metadata_service.imdb_tmdb_tvdb(_meta(tmp_path), "file", tvdb, tmdb)
    )
    assert episode.title == "TMDb" and episode.tvmaze_id == 777
    assert episode.tvdb_series_year == 2023
    assert episode.tmdb_episode_data == {"name": "Episode"}

    pack = asyncio.run(
        metadata_service.imdb_tmdb_tvdb(
            _meta(tmp_path, tv_pack=True), "file", tvdb, tmdb
        )
    )
    assert pack.tmdb_season_data == {"episodes": [1]}


def test_imdb_tmdb_tvdb_exception_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_imdb_tvmaze(
        monkeypatch,
        imdb=RuntimeError("imdb failed"),
        tvmaze_search=RuntimeError("tvmaze failed"),
    )
    tmdb = _Tmdb(
        tmdb_other_meta=RuntimeError("tmdb failed"),
        get_episode_details=RuntimeError("episode failed"),
        get_season_details=RuntimeError("season failed"),
    )
    tvdb = _Tvdb(get_tvdb_episodes=RuntimeError("tvdb failed"))

    episode = asyncio.run(
        metadata_service.imdb_tmdb_tvdb(
            _meta(tmp_path, imdb_info={"kept": True}), "file", tvdb, tmdb
        )
    )
    assert episode.imdb_info == {"kept": True} and episode.tvmaze_id == 0
    assert not episode.we_checked_tvdb and not episode.we_checked_tmdb

    pack = asyncio.run(
        metadata_service.imdb_tmdb_tvdb(
            _meta(tmp_path, tv_pack=True), "file", tvdb, tmdb
        )
    )
    assert not pack.we_checked_tmdb


def test_imdb_tvdb_success_unexpected_and_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmdb = _Tmdb(get_tmdb_from_imdb=("TV", 909, "ja", True))
    tvdb = _Tvdb(
        get_tvdb_episodes=(
            {"series_title": "Series", "series_year": 2022},
            "Series Name",
        )
    )
    _patch_imdb_tvmaze(monkeypatch, imdb={"title": "IMDb"}, tvmaze_search=808)
    result = asyncio.run(
        metadata_service.imdb_tvdb(_meta(tmp_path), "file", tvdb, tmdb)
    )
    assert result.tmdb_id == 909 and result.tvmaze_id == 808
    assert result.original_language == "ja" and result.no_ids is True
    assert result.tvdb_series_year == 2022 and result.we_checked_tvdb

    tmdb = _Tmdb(get_tmdb_from_imdb="bad")
    tvdb = _Tvdb(get_tvdb_episodes={"bad": True})
    _patch_imdb_tvmaze(monkeypatch, imdb=None, tvmaze_search="bad")
    result = asyncio.run(
        metadata_service.imdb_tvdb(_meta(tmp_path), "file", tvdb, tmdb)
    )
    assert (
        result.imdb_info == {}
        and result.tvmaze_id == 0
        and not result.we_checked_tvdb
    )

    tmdb = _Tmdb(get_tmdb_from_imdb=RuntimeError("tmdb failed"))
    tvdb = _Tvdb(get_tvdb_episodes=RuntimeError("tvdb failed"))
    _patch_imdb_tvmaze(
        monkeypatch,
        imdb=RuntimeError("imdb failed"),
        tvmaze_search=RuntimeError("tvmaze failed"),
    )
    result = asyncio.run(
        metadata_service.imdb_tvdb(
            _meta(tmp_path, imdb_info={"kept": True}), "file", tvdb, tmdb
        )
    )
    assert result.imdb_info == {"kept": True} and not result.we_checked_tvdb


def test_imdb_tmdb_tvmaze_episode_success_and_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmdb = _Tmdb(
        tmdb_other_meta={"title": "TMDb"},
        get_episode_details={"name": "Episode"},
    )
    _patch_imdb_tvmaze(
        monkeypatch, imdb={"title": "IMDb"}, tvmaze_search=(707, 202, 606)
    )
    meta = _meta(tmp_path, tvdb_id=0)
    result = asyncio.run(metadata_service.imdb_tmdb(meta, "file", None, tmdb))
    assert (
        result.title == "TMDb"
        and result.tvmaze_id == 707
        and result.tvdb_id == 606
    )
    assert (
        result.tmdb_episode_data == {"name": "Episode"}
        and result.we_checked_tmdb
    )

    for tvmaze_result in ((1, 2), 505, RuntimeError("tvmaze failed"), "bad"):
        tmdb = _Tmdb(
            tmdb_other_meta={},
            get_episode_details=RuntimeError("episode failed"),
        )
        _patch_imdb_tvmaze(
            monkeypatch,
            imdb=RuntimeError("imdb failed"),
            tvmaze_search=tvmaze_result,
        )
        result = asyncio.run(
            metadata_service.imdb_tmdb(
                _meta(tmp_path, imdb_info={"kept": True}), "file", None, tmdb
            )
        )
        assert result.imdb_info == {"kept": True}
        assert not result.we_checked_tmdb


def test_imdb_tmdb_pack_and_tmdb_core_variants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_imdb_tvmaze(monkeypatch, imdb=None, tvmaze_search=0)
    tmdb = _Tmdb(tmdb_other_meta=None, get_season_details={"episodes": []})
    result = asyncio.run(
        metadata_service.imdb_tmdb(
            _meta(tmp_path, tv_pack=True), "file", None, tmdb
        )
    )
    assert result.imdb_info == {} and result.tmdb_season_data == {
        "episodes": []
    }

    tmdb = _Tmdb(
        tmdb_other_meta=RuntimeError("tmdb failed"),
        get_season_details=RuntimeError("season failed"),
    )
    result = asyncio.run(
        metadata_service.imdb_tmdb(
            _meta(tmp_path, tv_pack=True), "file", None, tmdb
        )
    )
    assert not result.we_checked_tmdb


def test_get_tvmaze_tvdb_external_lookup_variants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tvdb = _Tvdb(get_tvdb_by_external_id=(303, "Series"))
    _patch_imdb_tvmaze(monkeypatch, tvmaze_search=(404, 202, 303))
    result = asyncio.run(
        metadata_service.get_tvmaze_tvdb(
            "Show", "2024", 202, 101, tvdb, base_dir=str(tmp_path)
        )
    )
    assert result == (404, 303, None, "Series")

    tvdb = _Tvdb(get_tvdb_by_external_id=None)
    _patch_imdb_tvmaze(monkeypatch, tvmaze_search=(404, 202, 505))
    assert asyncio.run(
        metadata_service.get_tvmaze_tvdb("Show", "2024", 202, 0, tvdb)
    )[:2] == (404, 505)

    for tvmaze_value, tvdb_value, expected in (
        (707, 808, (707, 808)),
        (RuntimeError("tvmaze"), RuntimeError("tvdb"), (0, 0)),
        ("unexpected", "unexpected", (0, 0)),
    ):
        tvdb = _Tvdb(get_tvdb_by_external_id=tvdb_value)
        _patch_imdb_tvmaze(monkeypatch, tvmaze_search=tvmaze_value)
        assert (
            asyncio.run(
                metadata_service.get_tvmaze_tvdb("Show", "2024", 202, 0, tvdb)
            )[:2]
            == expected
        )

    tvdb = _Tvdb(get_tvdb_by_external_id=(909, 123))
    _patch_imdb_tvmaze(monkeypatch, tvmaze_search=(404, 0, 0))
    result = asyncio.run(
        metadata_service.get_tvmaze_tvdb("Show", "2024", 0, 101, tvdb)
    )
    assert result[0] == 0 and result[1] == 909 and result[3] == ""


def test_get_tvmaze_tvdb_title_search_variants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_imdb_tvmaze(monkeypatch, tvmaze_search=(404, 0, 505))
    tvdb = _Tvdb(search_tvdb_series=([{"name": "Series"}], "303"))
    result = asyncio.run(
        metadata_service.get_tvmaze_tvdb(
            "Show", "2024", 0, 0, tvdb, year="2024"
        )
    )
    assert result == (404, 303, [{"name": "Series"}], "")

    variants = [
        (([{"name": "Series"}], "bad"), (404, 505, [{"name": "Series"}], "")),
        (({"only": "one"},), (404, 505, None, "")),
        ({"unexpected": True}, (404, 505, None, "")),
        (RuntimeError("tvdb failed"), (404, 505, None, "")),
    ]
    for value, expected in variants:
        tvdb = _Tvdb(search_tvdb_series=value)
        assert (
            asyncio.run(
                metadata_service.get_tvmaze_tvdb("Show", "2024", 0, 0, tvdb)
            )
            == expected
        )


def test_get_tv_data_direct_sources_and_corrections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tvdb = _Tvdb(
        get_tvdb_episodes=({"series_year": 2020}, "Series"),
        get_specific_episode_data=(
            "Season",
            "Real Episode",
            "TVDb overview",
            3,
            4,
            "2020",
            999,
        ),
        get_imdb_id_from_tvdb_episode_id="tt999",
    )
    tmdb = _Tmdb(
        get_episode_details={
            "name": "TMDb Episode",
            "overview": "TMDb overview",
        }
    )
    _patch_imdb_tvmaze(
        monkeypatch,
        tvmaze_episode={
            "name": "TVMaze Episode",
            "overview": "TVMaze overview",
        },
    )
    meta = _meta(
        tmp_path,
        tvmaze_id=0,
        tmdb_id=101,
        season="",
        episode="",
        tvdb_episode_data={},
        tvmaze_episode_data=None,
    )
    meta.pop("tvdb_series_name", None)
    result = asyncio.run(metadata_service.get_tv_data(meta, tvdb, tmdb))
    assert (
        result.tvdb_series_name == "Series" and result.tvdb_series_year == 2020
    )
    assert (
        result.auto_episode_title == "Real Episode"
        and result.overview_meta == "TVDb overview"
    )
    assert result.season == "S03" and result.episode == "E04"
    assert result.tvdb_imdb_id == "tt999"

    failing = _Tvdb(
        get_tvdb_episodes=({}, ""),
        get_specific_episode_data=RuntimeError("specific failed"),
    )
    result = asyncio.run(
        metadata_service.get_tv_data(
            _meta(tmp_path, tvmaze_id=0, tvdb_episode_data={"x": 1}),
            failing,
            tmdb,
        )
    )
    assert result.tvdb_episode_name is None


def test_get_tv_data_tvmaze_and_tmdb_fallbacks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tvdb = _Tvdb(get_tvdb_episodes=({}, ""))
    tmdb = _Tmdb(
        get_episode_details={
            "name": "TMDb Episode",
            "overview": "TMDb overview",
        }
    )
    _patch_imdb_tvmaze(
        monkeypatch,
        tvmaze_episode={
            "episode_name": "TVMaze Episode",
            "overview": "TVMaze overview",
        },
    )
    meta = _meta(
        tmp_path,
        tvdb_id=0,
        tvmaze_episode_data=None,
        tvdb_episode_data={},
        auto_episode_title=None,
        overview_meta=None,
    )
    result = asyncio.run(metadata_service.get_tv_data(meta, tvdb, tmdb))
    assert (
        result.auto_episode_title == "TVMaze Episode"
        and result.overview_meta == "TVMaze overview"
    )

    _patch_imdb_tvmaze(
        monkeypatch, tvmaze_episode={"name": "Episode TBA", "overview": None}
    )
    meta = _meta(
        tmp_path,
        tvdb_id=0,
        tvmaze_episode_data=None,
        tmdb_episode_data=None,
        tvdb_episode_int=9,
        tvdb_season_int=2,
        auto_episode_title=None,
        overview_meta=None,
    )
    result = asyncio.run(metadata_service.get_tv_data(meta, tvdb, tmdb))
    assert (
        result.auto_episode_title == "TMDb Episode"
        and result.overview_meta == "TMDb overview"
    )

    meta = _meta(
        tmp_path,
        tvdb_id=0,
        tvmaze_episode_data={},
        tmdb_episode_data={
            "name": "Episode TBA",
            "overview": "Existing overview",
        },
        auto_episode_title=None,
        overview_meta=None,
    )
    result = asyncio.run(metadata_service.get_tv_data(meta, tvdb, tmdb))
    assert (
        result.auto_episode_title is None
        and result.overview_meta == "Existing overview"
    )


def test_get_tv_data_combined_fetch_and_pack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    combined = AsyncMock(side_effect=lambda meta, *_args: meta)
    monkeypatch.setattr(
        metadata_service, "get_tvdb_tvmaze_tmdb_episode_data", combined
    )
    meta = _meta(tmp_path, tvdb_episode_data={})
    result = asyncio.run(metadata_service.get_tv_data(meta, _Tvdb(), _Tmdb()))
    assert result is meta and combined.await_count == 1

    tvdb = _Tvdb(
        get_tvdb_episodes=([{"episode": 1}], "Series"),
        get_specific_episode_data=(
            "Season",
            "Episode",
            "Overview",
            1,
            1,
            "2024",
            88,
        ),
        get_imdb_id_from_tvdb_episode_id="tt88",
    )
    pack = _meta(tmp_path, tv_pack=True, episode_int=0)
    result = asyncio.run(metadata_service.get_tv_data(pack, tvdb, _Tmdb()))
    assert result.tvdb_episode_data and result.tvdb_imdb_id == "tt88"

    failing = _Tvdb(
        get_tvdb_episodes=([{"episode": 1}], "Series"),
        get_specific_episode_data=RuntimeError("specific failed"),
    )
    result = asyncio.run(
        metadata_service.get_tv_data(
            _meta(tmp_path, tv_pack=True, episode_int=0), failing, _Tmdb()
        )
    )
    assert result.tvdb_episode_name is None


def test_combined_episode_data_success_bad_formats_and_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_imdb_tvmaze(monkeypatch, tvmaze_episode={"name": "TVMaze"})
    tvdb = _Tvdb(get_tvdb_episodes=([{"name": "TVDb"}], "Series"))
    tmdb = _Tmdb(get_episode_details={"name": "TMDb"})
    result = asyncio.run(
        metadata_service.get_tvdb_tvmaze_tmdb_episode_data(
            _meta(tmp_path), tvdb, tmdb
        )
    )
    assert (
        result.we_asked_tvmaze
        and result.we_checked_tvdb
        and result.we_checked_tmdb
    )

    _patch_imdb_tvmaze(
        monkeypatch, tvmaze_episode=RuntimeError("tvmaze failed")
    )
    tvdb = _Tvdb(get_tvdb_episodes=RuntimeError("tvdb failed"))
    tmdb = _Tmdb(get_episode_details=RuntimeError("tmdb failed"))
    result = asyncio.run(
        metadata_service.get_tvdb_tvmaze_tmdb_episode_data(
            _meta(tmp_path), tvdb, tmdb
        )
    )
    assert (
        not result.we_asked_tvmaze
        and not result.we_checked_tvdb
        and not result.we_checked_tmdb
    )

    _patch_imdb_tvmaze(monkeypatch, tvmaze_episode=None)
    for bad in (([{"name": "TVDb"}],), {"unexpected": True}):
        result = asyncio.run(
            metadata_service.get_tvdb_tvmaze_tmdb_episode_data(
                _meta(tmp_path, tvmaze_id=0, tmdb_id=0),
                _Tvdb(get_tvdb_episodes=bad),
                _Tmdb(),
            )
        )
        assert not result.we_checked_tvdb

    no_ids = _meta(tmp_path, tvmaze_id=0, tvdb_id=0, tmdb_id=0)
    assert (
        asyncio.run(
            metadata_service.get_tvdb_tvmaze_tmdb_episode_data(
                no_ids, _Tvdb(), _Tmdb()
            )
        )
        is no_ids
    )


class _Cache:
    def __init__(self, value: object) -> None:
        self.value = value
        self.set_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def get(self, *_args: object, **_kwargs: object) -> object:
        return self.value

    async def set(self, *args: object, **kwargs: object) -> None:
        self.set_calls.append((args, kwargs))


class _WebResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code
        self.request = httpx.Request("GET", "https://m.douban.com/search/")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=self.request,
                response=httpx.Response(
                    self.status_code, request=self.request
                ),
            )


class _QueuedClient:
    queue: ClassVar[list[object]] = []

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        return None

    async def get(self, *_args: object, **_kwargs: object) -> _WebResponse:
        value = self.queue.pop(0)
        if isinstance(value, BaseException):
            raise value
        assert isinstance(value, _WebResponse)
        return value


def _patch_douban(
    monkeypatch: pytest.MonkeyPatch,
    cache: _Cache,
    *responses: object,
) -> None:
    monkeypatch.setattr(metadata_service, "cache_for", lambda _base_dir: cache)
    monkeypatch.setattr(
        metadata_service, "is_cache_miss", lambda value: value == "MISS"
    )
    _QueuedClient.queue = list(responses)
    monkeypatch.setattr(metadata_service.httpx, "AsyncClient", _QueuedClient)

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(metadata_service.asyncio, "sleep", no_sleep)


def test_douban_manual_cache_success_and_negative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (
        asyncio.run(
            metadata_service.get_douban_id(_meta(tmp_path, douban_manual=123))
        )
        == 123
    )
    assert (
        asyncio.run(
            metadata_service.get_douban_id(
                _meta(tmp_path, douban_manual="bad", imdb_tt="")
            )
        )
        == 0
    )

    cache = _Cache({"id": 456})
    _patch_douban(monkeypatch, cache)
    assert (
        asyncio.run(
            metadata_service.get_douban_id(
                _meta(tmp_path, douban_manual=0, imdb_tt="tt123")
            )
        )
        == 456
    )

    cache = _Cache("MISS")
    _patch_douban(
        monkeypatch,
        cache,
        _WebResponse(
            '<ul class="search_results_subjects"><li><a href="https://m.douban.com/subject/789/">Movie</a></li></ul>'
        ),
    )
    assert (
        asyncio.run(
            metadata_service.get_douban_id(
                _meta(tmp_path, douban_manual=0, imdb_tt="tt123")
            )
        )
        == 789
    )
    assert cache.set_calls[-1][0][-1] == {"id": 789}

    for html in (
        "<html></html>",
        '<ul class="search_results_subjects"><li>No link</li></ul>',
        '<ul class="search_results_subjects"><li><a>Missing href</a></li></ul>',
        '<ul class="search_results_subjects"><li><a href="https://m.douban.com/movie/no-id/">No ID</a></li></ul>',
    ):
        cache = _Cache("MISS")
        _patch_douban(monkeypatch, cache, _WebResponse(html))
        assert (
            asyncio.run(
                metadata_service.get_douban_id(
                    _meta(tmp_path, douban_manual=0, imdb_tt="tt123")
                )
            )
            == 0
        )
        assert cache.set_calls[-1][1] == {"negative": True}


def test_douban_retries_statuses_and_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request_error = httpx.RequestError(
        "network", request=httpx.Request("GET", "https://m.douban.com/")
    )
    cache = _Cache("MISS")
    _patch_douban(
        monkeypatch,
        cache,
        request_error,
        _WebResponse(
            '<ul class="search_results_subjects"><a href="/subject/321/">Movie</a></ul>'
        ),
    )
    assert (
        asyncio.run(
            metadata_service.get_douban_id(
                _meta(tmp_path, douban_manual=0, imdb_tt="tt123")
            )
        )
        == 321
    )

    for status in (429, 500):
        cache = _Cache("MISS")
        _patch_douban(
            monkeypatch,
            cache,
            _WebResponse("", status),
            _WebResponse("", status),
            _WebResponse("", status),
        )
        assert (
            asyncio.run(
                metadata_service.get_douban_id(
                    _meta(tmp_path, douban_manual=0, imdb_tt="tt123")
                )
            )
            == 0
        )

    cache = _Cache("MISS")
    _patch_douban(monkeypatch, cache, _WebResponse("", 404))
    assert (
        asyncio.run(
            metadata_service.get_douban_id(
                _meta(tmp_path, douban_manual=0, imdb_tt="tt123")
            )
        )
        == 0
    )

    cache = _Cache("MISS")
    _patch_douban(
        monkeypatch, cache, request_error, request_error, request_error
    )
    assert (
        asyncio.run(
            metadata_service.get_douban_id(
                _meta(tmp_path, douban_manual=0, imdb_tt="tt123")
            )
        )
        == 0
    )


def test_metadata_searching_manager_wires_and_delegates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tvdb = _Tvdb()
    tmdb = _Tmdb()
    monkeypatch.setattr(metadata_service, "TvdbData", lambda _config: tvdb)
    monkeypatch.setattr(metadata_service, "TmdbManager", lambda _config: tmdb)
    expected = _meta(tmp_path)
    calls: list[str] = []

    async def delegated(name: str, *args: object, **_kwargs: object):
        calls.append(name)
        if name == "get_tvmaze_tvdb":
            return 1, 2, None, "Series"
        return args[0]

    monkeypatch.setattr(
        metadata_service,
        "all_ids",
        lambda *args, **kwargs: delegated("all_ids", *args, **kwargs),
    )
    monkeypatch.setattr(
        metadata_service,
        "imdb_tmdb_tvdb",
        lambda *args, **kwargs: delegated("imdb_tmdb_tvdb", *args, **kwargs),
    )
    monkeypatch.setattr(
        metadata_service,
        "imdb_tvdb",
        lambda *args, **kwargs: delegated("imdb_tvdb", *args, **kwargs),
    )
    monkeypatch.setattr(
        metadata_service,
        "imdb_tmdb",
        lambda *args, **kwargs: delegated("imdb_tmdb", *args, **kwargs),
    )
    monkeypatch.setattr(
        metadata_service,
        "get_tvmaze_tvdb",
        lambda *args, **kwargs: delegated("get_tvmaze_tvdb", *args, **kwargs),
    )
    monkeypatch.setattr(
        metadata_service,
        "get_tv_data",
        lambda *args, **kwargs: delegated("get_tv_data", *args, **kwargs),
    )
    monkeypatch.setattr(
        metadata_service,
        "get_tvdb_tvmaze_tmdb_episode_data",
        lambda *args, **kwargs: delegated(
            "get_tvdb_tvmaze_tmdb_episode_data", *args, **kwargs
        ),
    )

    manager = metadata_service.MetadataSearchingManager(
        {"DEFAULT": {"tmdb_api": "key"}}
    )

    async def exercise() -> None:
        assert await manager.all_ids(expected) is expected
        assert await manager.imdb_tmdb_tvdb(expected, "file") is expected
        assert await manager.imdb_tvdb(expected, "file") is expected
        assert await manager.imdb_tmdb(expected, "file") is expected
        assert await manager.get_tvmaze_tvdb("Show", "2024", 1, 2) == (
            1,
            2,
            None,
            "Series",
        )
        assert await manager.get_tv_data(expected) is expected
        assert (
            await manager.get_tvdb_tvmaze_tmdb_episode_data(expected)
            is expected
        )

    asyncio.run(exercise())
    assert calls == [
        "all_ids",
        "imdb_tmdb_tvdb",
        "imdb_tvdb",
        "imdb_tmdb",
        "get_tvmaze_tvdb",
        "get_tv_data",
        "get_tvdb_tvmaze_tmdb_episode_data",
    ]


def test_get_tv_data_generic_tvdb_title_and_absolute_episode_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tvdb = _Tvdb(
        get_tvdb_episodes=([{"episode": 1}], "Series"),
        get_specific_episode_data=(
            "Season",
            "Episode 9",
            None,
            3,
            9,
            "2024",
            None,
        ),
    )
    tmdb = _Tmdb(
        get_episode_details={
            "name": "TMDb Absolute",
            "overview": "Absolute overview",
        }
    )
    _patch_imdb_tvmaze(monkeypatch, tvmaze_episode={})
    meta = _meta(
        tmp_path,
        season="S01",
        episode="E02",
        episode_int=2,
        tvdb_episode_data={},
        tvmaze_episode_data={},
        auto_episode_title=None,
        overview_meta=None,
        episode_overview=True,
        tmdb_episode_data=None,
    )

    result = asyncio.run(metadata_service.get_tv_data(meta, tvdb, tmdb))

    assert result.auto_episode_title == "TMDb Absolute"
    assert result.overview_meta == "Absolute overview"
    assert result.tvdb_episode_int == 9 and result.episode_int == 2


def test_imdb_tmdb_tvdb_unexpected_imdb_and_tvdb_payloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_imdb_tvmaze(monkeypatch, imdb=None, tvmaze_search=0)
    tmdb = _Tmdb(tmdb_other_meta={"title": "TMDb"}, get_episode_details={})
    tvdb = _Tvdb(get_tvdb_episodes={"unexpected": True})

    result = asyncio.run(
        metadata_service.imdb_tmdb_tvdb(_meta(tmp_path), "file", tvdb, tmdb)
    )

    assert result.imdb_info == {}
    assert not result.we_checked_tvdb


def test_imdb_tvdb_does_not_erase_existing_original_language_when_tmdb_omits_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmdb = _Tmdb(get_tmdb_from_imdb=("MOVIE", 909, None, False))
    _patch_imdb_tvmaze(monkeypatch, imdb={"title": "IMDb"}, tvmaze_search=0)
    meta = _meta(tmp_path, category="MOVIE", original_language="en")

    result = asyncio.run(
        metadata_service.imdb_tvdb(meta, "file", _Tvdb(), tmdb)
    )

    assert result.original_language == "en"
