from __future__ import annotations

import asyncio
import json
import ssl
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar
from urllib.error import URLError

import pytest

from src.integrations.external_apis import tvdb as tvdb_module
from src.integrations.external_apis.tvdb import TvdbData


class FakeTVDB:
    search_result: ClassVar[object] = []
    episode_pages: ClassVar[dict[int, object]] = {}
    series_extended: ClassVar[object] = {}
    translation: ClassVar[object] = {}
    remote_results: ClassVar[dict[str, object]] = {}
    episode_extended: ClassVar[object] = {}
    calls: ClassVar[
        list[tuple[str, tuple[object, ...], dict[str, object]]]
    ] = []

    def __init__(self, api_key: str = "key") -> None:
        self.api_key = api_key

    @classmethod
    def reset(cls) -> None:
        cls.search_result = []
        cls.episode_pages = {}
        cls.series_extended = {}
        cls.translation = {}
        cls.remote_results = {}
        cls.episode_extended = {}
        cls.calls = []

    @staticmethod
    def _resolve(value: object) -> object:
        if isinstance(value, BaseException):
            raise value
        return value

    def search(self, *args: object, **kwargs: object) -> object:
        type(self).calls.append(("search", args, dict(kwargs)))
        return self._resolve(type(self).search_result)

    def get_series_episodes(self, *args: object, **kwargs: object) -> object:
        type(self).calls.append(("get_series_episodes", args, dict(kwargs)))
        page = int(kwargs.get("page", 0))
        return self._resolve(type(self).episode_pages.get(page, []))

    def get_series_extended(self, *args: object, **kwargs: object) -> object:
        type(self).calls.append(("get_series_extended", args, dict(kwargs)))
        return self._resolve(type(self).series_extended)

    def get_series_translation(
        self, *args: object, **kwargs: object
    ) -> object:
        type(self).calls.append(("get_series_translation", args, dict(kwargs)))
        return self._resolve(type(self).translation)

    def search_by_remote_id(self, value: object) -> object:
        type(self).calls.append(("search_by_remote_id", (value,), {}))
        return self._resolve(type(self).remote_results.get(str(value), []))

    def get_episode_extended(self, *args: object, **kwargs: object) -> object:
        type(self).calls.append(("get_episode_extended", args, dict(kwargs)))
        return self._resolve(type(self).episode_extended)


@pytest.fixture(autouse=True)
def _reset_tvdb(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeTVDB.reset()

    async def no_sleep(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(tvdb_module.asyncio, "sleep", no_sleep)
    tvdb_module.tvdb = None
    tvdb_module._tvdb_init_error = None
    tvdb_module._tvdb_error_reported = False


def _manager(client: FakeTVDB | None = None) -> TvdbData:
    if client is not None:
        tvdb_module.tvdb = client
    return TvdbData({"DEFAULT": {"tvdb_api": "key"}})


def _episode(
    *,
    season: int = 1,
    number: int = 1,
    absolute: int | None = 1,
    aired: str = "2026-01-01",
    episode_id: int = 101,
) -> dict[str, Any]:
    return {
        "seasonName": f"Season {season}",
        "name": f"Episode {number}",
        "overview": "Overview",
        "seasonNumber": season,
        "number": number,
        "absoluteNumber": absolute,
        "aired": aired,
        "year": 2026,
        "id": episode_id,
    }


def test_small_helpers_and_translation_metadata() -> None:
    assert tvdb_module._coerce_int("7") == 7
    assert tvdb_module._coerce_int("bad") is None
    assert tvdb_module._as_dict_list([{"id": 1}, "bad", 2]) == [{"id": 1}]
    assert tvdb_module._as_dict_list({"id": 1}) == []
    aliases = [
        {"language": "fra", "name": "Nom"},
        {"language": "eng", "name": " English One "},
        {"language": "eng", "name": ""},
        {"language": "eng", "name": "English Two (2025)"},
    ]
    assert tvdb_module._english_alias_names(aliases) == [
        "English One",
        "English Two (2025)",
    ]
    assert tvdb_module._pick_eng_alias([]) is None
    assert (
        tvdb_module._pick_eng_alias([{"language": "fra", "name": "Nom"}])
        is None
    )
    assert tvdb_module._pick_eng_alias(aliases) == "English Two (2025)"
    assert tvdb_module._extract_year_from_text(None) is None
    assert tvdb_module._extract_year_from_text("Show (2026)") == "2026"
    assert tvdb_module._extract_year_from_text(1999) == "1999"
    assert tvdb_module._extract_year_from_text("year 2040") is None
    assert tvdb_module._best_effort_series_year(None) is None
    assert tvdb_module._best_effort_series_year({"year": "2024"}) == "2024"
    assert (
        tvdb_module._best_effort_series_year(
            {"year": "bad", "slug": "show-2023"}
        )
        == "2023"
    )

    client = FakeTVDB()
    FakeTVDB.translation = {
        "name": " Translated ",
        "aliases": ["Alias (2022)", ""],
    }
    result = tvdb_module._series_translation_metadata(
        client, 1, aliases, _series_info={"year": "2020"}
    )
    assert result == {"series_title": "Translated", "series_year": "2022"}

    FakeTVDB.translation = {"aliases": ["Only Alias (2021)"]}
    assert tvdb_module._series_translation_metadata(
        client, 1, [], _series_info={}
    ) == {
        "series_title": "Only Alias (2021)",
        "series_year": "2021",
    }

    FakeTVDB.translation = RuntimeError("translation failed")
    result = tvdb_module._series_translation_metadata(
        client,
        1,
        [{"language": "eng", "name": "Fallback (2020)"}],
        _series_info={"slug": "fallback-2019"},
    )
    assert result == {"series_title": "Fallback (2020)", "series_year": "2020"}

    result = tvdb_module._series_translation_metadata(
        client, 1, [], _series_info={"slug": "fallback-2019"}
    )
    assert result == {"series_title": None, "series_year": "2019"}


def test_get_tvdb_or_warn_missing_existing_success_and_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = FakeTVDB("existing")
    tvdb_module.tvdb = existing
    assert tvdb_module._get_tvdb_or_warn({}) is existing

    tvdb_module.tvdb = None
    assert tvdb_module._get_tvdb_or_warn(None) is None
    assert tvdb_module._tvdb_error_reported is True
    assert tvdb_module._get_tvdb_or_warn({"DEFAULT": {"tvdb_api": ""}}) is None

    tvdb_module._tvdb_error_reported = False
    created: list[str] = []

    class Constructor(FakeTVDB):
        def __init__(self, key: str) -> None:
            created.append(key)
            super().__init__(key)

    monkeypatch.setattr(tvdb_module, "TVDB", Constructor)
    result = tvdb_module._get_tvdb_or_warn({"DEFAULT": {"tvdb_api": " key "}})
    assert isinstance(result, Constructor) and created == ["key"]

    for error in (
        ssl.SSLError("ssl"),
        URLError("url"),
        RuntimeError("generic"),
    ):
        tvdb_module.tvdb = None
        tvdb_module._tvdb_init_error = None
        tvdb_module._tvdb_error_reported = False

        class Broken:
            def __init__(
                self, _key: str, *, _error: BaseException = error
            ) -> None:
                raise _error

        monkeypatch.setattr(tvdb_module, "TVDB", Broken)
        assert (
            tvdb_module._get_tvdb_or_warn({"DEFAULT": {"tvdb_api": "key"}})
            is None
        )
        assert tvdb_module._tvdb_init_error is error


def test_search_series_exact_alias_first_empty_and_errors() -> None:
    manager = _manager(FakeTVDB())
    FakeTVDB.search_result = [
        {"tvdb_id": "1", "year": "2025", "aliases": []},
        {"tvdb_id": "2", "year": "2026", "aliases": []},
    ]
    results, series_id = asyncio.run(
        manager.search_tvdb_series("Show", "2026")
    )
    assert results and series_id == 2

    FakeTVDB.search_result = [
        {"tvdb_id": "3", "year": "2024", "aliases": [{"name": "Show (2026)"}]},
        {"tvdb_id": "4", "year": "2023", "aliases": "bad"},
    ]
    assert asyncio.run(manager.search_tvdb_series("Show", "2026"))[1] == 3

    FakeTVDB.search_result = [
        {"tvdb_id": "5", "aliases": ["Show (2026)"]},
        {"tvdb_id": "6"},
    ]
    assert asyncio.run(manager.search_tvdb_series("Show", "2026"))[1] == 5

    FakeTVDB.search_result = []
    assert asyncio.run(manager.search_tvdb_series("Missing")) == (None, None)

    FakeTVDB.search_result = [{"name": "missing id"}]
    assert asyncio.run(manager.search_tvdb_series("Broken")) == (None, None)

    tvdb_module.tvdb = None
    assert asyncio.run(
        TvdbData({"DEFAULT": {}}).search_tvdb_series("No Client")
    ) == (None, None)


def _write_cache(base: Path, series_id: int, payload: object) -> Path:
    target = base / "data" / "tvdb" / f"{series_id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


def test_cached_episodes_match_all_request_modes_and_language(
    tmp_path: Path,
) -> None:
    manager = _manager(FakeTVDB())
    episodes = [
        _episode(),
        _episode(
            season=2, number=3, absolute=15, aired="2026-02-03", episode_id=203
        ),
    ]
    payload = {
        "episodes": episodes,
        "aliases": [{"language": "eng", "name": "Cached Alias (2026)"}],
        "slug": "cached-2026",
        "series_title": "Cached Series",
        "series_year": "2026",
    }
    _write_cache(tmp_path, 10, payload)

    data, alias = asyncio.run(manager.get_tvdb_episodes(10, str(tmp_path)))
    assert data and data["episodes"] == episodes and alias == "Cached Series"
    assert not FakeTVDB.calls

    for kwargs in (
        {"season": 2, "episode": 3},
        {"absolute_number": 15},
        {"aired_date": "2026.02.03"},
        {"season": "bad", "episode": "bad"},
        {"season": 2, "episode": 0},
    ):
        data, _ = asyncio.run(
            manager.get_tvdb_episodes(10, str(tmp_path), **kwargs)
        )
        assert data and len(data["episodes"]) == 2

    _, alias = asyncio.run(
        manager.get_tvdb_episodes(10, str(tmp_path), original_language="en")
    )
    assert alias is None

    # Old positional ``debug`` form bypasses the cache path and reads from API.
    FakeTVDB.episode_pages = {0: {"episodes": [_episode()]}}
    FakeTVDB.series_extended = {"aliases": []}
    FakeTVDB.translation = {}
    data, _ = asyncio.run(manager.get_tvdb_episodes(10, True))
    assert data and data["episodes"]


def test_cached_episodes_refresh_metadata_stale_and_cache_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(FakeTVDB())
    _write_cache(
        tmp_path, 11, {"episodes": [_episode()], "aliases": "bad", "slug": 7}
    )
    FakeTVDB.series_extended = {
        "aliases": [{"language": "eng", "name": "English Alias (2024)"}],
        "year": "2023",
    }
    FakeTVDB.translation = {"name": "English Series", "aliases": []}
    data, alias = asyncio.run(manager.get_tvdb_episodes(11, str(tmp_path)))
    assert (
        data
        and data["series_title"] == "English Series"
        and data["series_year"] == "2024"
    )
    assert alias == "English Series"

    FakeTVDB.series_extended = RuntimeError("series failed")
    _write_cache(tmp_path, 12, {"episodes": [_episode()]})
    data, alias = asyncio.run(manager.get_tvdb_episodes(12, str(tmp_path)))
    assert data and alias is None

    # Requested episode absent from cache: refresh with API data.
    _write_cache(tmp_path, 13, {"episodes": [_episode()]})
    FakeTVDB.episode_pages = {
        0: {
            "slug": "show",
            "episodes": [_episode(season=3, number=4, absolute=20)],
        }
    }
    FakeTVDB.series_extended = {"aliases": []}
    FakeTVDB.translation = {}
    data, _ = asyncio.run(
        manager.get_tvdb_episodes(13, str(tmp_path), season=3, episode=4)
    )
    assert data and data["episodes"][0]["seasonNumber"] == 3

    # Non-list cached episodes and malformed JSON are safely refreshed.
    _write_cache(tmp_path, 14, {"episodes": {"bad": True}})
    FakeTVDB.episode_pages = {0: [_episode()]}
    data, _ = asyncio.run(
        manager.get_tvdb_episodes(14, str(tmp_path), season=1, episode=1)
    )
    assert data and data["episodes"]

    target = _write_cache(tmp_path, 15, {})
    target.write_text("not-json", encoding="utf-8")
    FakeTVDB.episode_pages = {0: [_episode()]}
    data, _ = asyncio.run(manager.get_tvdb_episodes(15, str(tmp_path)))
    assert data and data["episodes"]

    # Cache stat errors remain non-fatal.
    original_exists = Path.exists

    def fail_exists(path: Path) -> bool:
        if path.name == "16.json":
            raise OSError("stat failed")
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", fail_exists)
    FakeTVDB.episode_pages = {0: [_episode()]}
    data, _ = asyncio.run(manager.get_tvdb_episodes(16, str(tmp_path)))
    assert data and data["episodes"]


def test_fresh_episode_pagination_metadata_cache_and_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(FakeTVDB())
    FakeTVDB.episode_pages = {
        0: {
            "slug": "multi-show",
            "episodes": [
                _episode(number=index + 1, absolute=index + 1)
                for index in range(500)
            ],
        },
        1: {"episodes": [_episode(season=2, number=1, absolute=501)]},
    }
    FakeTVDB.series_extended = {
        "aliases": [{"language": "eng", "name": "Multi Show (2026)"}],
        "slug": "multi-show-2025",
    }
    FakeTVDB.translation = {"aliases": ["Translated Alias (2026)"]}
    data, alias = asyncio.run(manager.get_tvdb_episodes(20, str(tmp_path)))
    assert (
        data and len(data["episodes"]) == 501 and data["slug"] == "multi-show"
    )
    assert (
        data["series_title"] == "Translated Alias (2026)"
        and alias == "Translated Alias (2026)"
    )
    cache = tmp_path / "data" / "tvdb" / "20.json"
    assert (
        cache.is_file()
        and json.loads(cache.read_text())["series_year"] == "2026"
    )

    # A direct-list API shape is supported.
    FakeTVDB.episode_pages = {0: [_episode()]}
    FakeTVDB.series_extended = {"aliases": []}
    FakeTVDB.translation = {}
    data, _ = asyncio.run(manager.get_tvdb_episodes(21))
    assert data and data["slug"] is None

    # Empty first page is a successful empty response.
    FakeTVDB.episode_pages = {0: []}
    data, alias = asyncio.run(manager.get_tvdb_episodes(22))
    assert data == {
        "episodes": [],
        "aliases": [],
        "slug": None,
        "series_title": None,
        "series_year": None,
    }
    assert alias is None

    # First page failure aborts; later page failure preserves already fetched data.
    FakeTVDB.episode_pages = {0: RuntimeError("page zero")}
    assert asyncio.run(manager.get_tvdb_episodes(23)) == (None, None)

    FakeTVDB.episode_pages = {
        0: [
            _episode(number=index + 1, absolute=index + 1)
            for index in range(500)
        ],
        1: RuntimeError("later page"),
    }
    FakeTVDB.series_extended = RuntimeError("aliases failed")
    data, _ = asyncio.run(manager.get_tvdb_episodes(24))
    assert data and len(data["episodes"]) == 500

    # Cache write failures are non-fatal.
    FakeTVDB.episode_pages = {
        0: [
            _episode(number=index + 1, absolute=index + 1)
            for index in range(500)
        ],
        1: [_episode(season=2, number=1, absolute=501)],
    }
    FakeTVDB.series_extended = {"aliases": []}
    original_open = Path.open

    def fail_write(
        path: Path, mode: str = "r", *args: object, **kwargs: object
    ):
        if path.name == "25.json" and "w" in mode:
            raise OSError("read only")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_write)
    data, _ = asyncio.run(manager.get_tvdb_episodes(25, str(tmp_path)))
    assert data and len(data["episodes"]) == 501

    tvdb_module.tvdb = None
    assert asyncio.run(TvdbData({"DEFAULT": {}}).get_tvdb_episodes(26)) == (
        None,
        None,
    )
    assert asyncio.run(manager.get_tvdb_episodes("bad")) == (None, None)


def test_external_id_series_translation_and_all_imdb_forms() -> None:
    manager = _manager(FakeTVDB())
    FakeTVDB.series_extended = {
        "aliases": [{"language": "eng", "name": "Fallback Alias (2024)"}],
        "slug": "fallback-2023",
    }
    FakeTVDB.translation = {"name": "Translated Series", "aliases": []}

    for value, expected_key in (
        ("tt1234567", "tt1234567"),
        ("1234567", "tt1234567"),
        (1234567, "tt1234567"),
    ):
        FakeTVDB.remote_results = {
            expected_key: [{"series": {"id": "77", "name": "Fallback"}}]
        }
        assert asyncio.run(manager.get_tvdb_by_external_id(value, None)) == (
            77,
            "Translated Series",
        )

    # An unrecognized identifier is passed through to the adapter.
    FakeTVDB.remote_results = {
        "custom": [{"series": {"id": "78", "name": "Custom"}}]
    }
    assert asyncio.run(manager.get_tvdb_by_external_id("custom", None)) == (
        78,
        "Translated Series",
    )

    # Invalid translated series ID retains the external fallback name.
    FakeTVDB.remote_results = {
        "tt1234567": [{"series": {"id": "bad", "name": " Fallback Name "}}]
    }
    assert asyncio.run(manager.get_tvdb_by_external_id(1234567, None)) == (
        None,
        "Fallback Name",
    )

    # Translation lookup failure also retains fallback.
    FakeTVDB.remote_results = {
        "tt1234567": [{"series": {"id": 79, "name": "Fallback Name"}}]
    }
    FakeTVDB.series_extended = RuntimeError("series failed")
    assert asyncio.run(manager.get_tvdb_by_external_id(1234567, None)) == (
        79,
        "Fallback Name",
    )


def test_external_id_tv_movie_episode_movie_tmdb_and_no_matches() -> None:
    manager = _manager(FakeTVDB())
    FakeTVDB.series_extended = {"aliases": []}
    FakeTVDB.translation = {}

    FakeTVDB.remote_results = {
        "tt1234567": [
            {"episode": {"seriesId": "80", "seriesName": "Episode Series"}},
            {"movie": {"id": "90", "name": "Movie"}},
        ]
    }
    assert asyncio.run(
        manager.get_tvdb_by_external_id(1234567, None, tv_movie=True)
    ) == (80, "Episode Series")

    FakeTVDB.remote_results = {
        "tt1234567": [
            {"episode": {"seriesId": 0}},
            {"movie": {"id": "90", "name": "Movie"}},
        ]
    }
    assert asyncio.run(
        manager.get_tvdb_by_external_id(1234567, None, tv_movie=True)
    ) == (90, "Movie")

    FakeTVDB.remote_results = {
        "tt1234567": [
            {"movie": {"id": 90, "name": "Ignored without tv_movie"}}
        ],
        "456": [{"series": {"id": "81", "name": "TMDb Series"}}],
    }
    assert asyncio.run(manager.get_tvdb_by_external_id(1234567, 456)) == (
        81,
        "TMDb Series",
    )

    FakeTVDB.remote_results = {
        "456": [
            {
                "episode": {
                    "seriesId": "82",
                    "seriesName": "TMDb Episode Series",
                }
            },
            {"movie": {"id": "91", "name": "TMDb Movie"}},
        ]
    }
    assert asyncio.run(
        manager.get_tvdb_by_external_id(None, 456, tv_movie=True)
    ) == (82, "TMDb Episode Series")

    FakeTVDB.remote_results = {
        "456": [{"movie": {"id": "91", "name": "TMDb Movie"}}]
    }
    assert asyncio.run(
        manager.get_tvdb_by_external_id(None, 456, tv_movie=True)
    ) == (91, "TMDb Movie")

    FakeTVDB.remote_results = {"tt1234567": [], "456": []}
    assert asyncio.run(manager.get_tvdb_by_external_id(1234567, 456)) == (
        None,
        None,
    )
    assert asyncio.run(
        manager.get_tvdb_by_external_id(None, None, tv_movie=True)
    ) == (None, None)


def test_external_id_errors_and_client_absent() -> None:
    manager = _manager(FakeTVDB())
    FakeTVDB.remote_results = {
        "tt1234567": RuntimeError("imdb remote failed"),
        "456": RuntimeError("tmdb remote failed"),
    }
    assert asyncio.run(manager.get_tvdb_by_external_id(1234567, 456)) == (
        None,
        None,
    )

    FakeTVDB.remote_results = {
        "tt1234567": [{"episode": "bad"}, {"movie": "bad"}, {}],
        "456": [{"episode": {}}, {"movie": {}}],
    }
    assert asyncio.run(
        manager.get_tvdb_by_external_id(1234567, 456, tv_movie=True)
    ) == (None, None)

    tvdb_module.tvdb = None
    assert asyncio.run(
        TvdbData({"DEFAULT": {}}).get_tvdb_by_external_id(1, 2)
    ) == (None, None)


def test_episode_imdb_mapping_success_missing_invalid_error_and_client() -> (
    None
):
    manager = _manager(FakeTVDB())
    FakeTVDB.episode_extended = {
        "remoteIds": [
            "bad",
            {"type": 1, "id": "not-imdb"},
            {"type": 2, "id": "tt1234567"},
        ]
    }
    assert (
        asyncio.run(manager.get_imdb_id_from_tvdb_episode_id("100"))
        == "tt1234567"
    )

    FakeTVDB.episode_extended = {
        "remoteIds": [{"sourceName": "IMDB", "id": "tt7654321"}]
    }
    assert (
        asyncio.run(manager.get_imdb_id_from_tvdb_episode_id(101))
        == "tt7654321"
    )

    FakeTVDB.episode_extended = {"remoteIds": []}
    assert asyncio.run(manager.get_imdb_id_from_tvdb_episode_id(102)) is None
    assert asyncio.run(manager.get_imdb_id_from_tvdb_episode_id("bad")) is None

    FakeTVDB.episode_extended = RuntimeError("episode failed")
    assert asyncio.run(manager.get_imdb_id_from_tvdb_episode_id(103)) is None

    tvdb_module.tvdb = None
    assert (
        asyncio.run(
            TvdbData({"DEFAULT": {}}).get_imdb_id_from_tvdb_episode_id(104)
        )
        is None
    )


def test_specific_episode_data_all_input_and_match_paths() -> None:
    manager = TvdbData({})
    empty = (None, None, None, None, None, None, None)
    assert asyncio.run(manager.get_specific_episode_data("bad", 1, 1)) == empty
    assert asyncio.run(manager.get_specific_episode_data({}, 1, 1)) == empty
    assert asyncio.run(manager.get_specific_episode_data([], 1, 1)) == empty

    episodes = [
        _episode(
            season=1, number=1, absolute=5, aired="2026-01-01", episode_id=11
        ),
        _episode(
            season=1, number=2, absolute=6, aired="2026-01-02", episode_id=12
        ),
        _episode(
            season=2, number=3, absolute=15, aired="2026-02-03", episode_id=23
        ),
    ]
    expected_daily = ("Season 2", "Episode 3", "Overview", 2, 3, 2026, 23)
    assert (
        asyncio.run(
            manager.get_specific_episode_data(
                {"episodes": episodes}, 2, 99, "2026.02.03"
            )
        )
        == expected_daily
    )
    assert asyncio.run(manager.get_specific_episode_data(episodes, 1, None))[
        :5
    ] == ("Season 1", "Episode 1", "Overview", 1, 1)
    assert asyncio.run(manager.get_specific_episode_data(episodes, 1, 0))[
        :5
    ] == ("Season 1", "Episode 1", "Overview", 1, 1)
    assert asyncio.run(manager.get_specific_episode_data(episodes, "1", "2"))[
        :5
    ] == ("Season 1", "Episode 2", "Overview", 1, 2)
    assert (
        asyncio.run(manager.get_specific_episode_data(episodes, 1, 15))
        == expected_daily
    )
    assert (
        asyncio.run(manager.get_specific_episode_data(episodes, 1, 999))
        == empty
    )
    assert (
        asyncio.run(manager.get_specific_episode_data(episodes, "bad", 1))
        == empty
    )
    assert (
        asyncio.run(manager.get_specific_episode_data(episodes, None, 1))
        == empty
    )


def test_remaining_tvdb_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(FakeTVDB())
    cached = {"episodes": [_episode()], "series_title": "Alias"}
    _write_cache(tmp_path, 30, cached)

    class BadAbsolute:
        def __int__(self) -> int:
            raise ValueError("bad absolute")

    data, _ = asyncio.run(
        manager.get_tvdb_episodes(
            30,
            str(tmp_path),
            season=1,
            episode=1,
            absolute_number=BadAbsolute(),
        )
    )
    assert data and data["episodes"]

    # Exercise the non-POSIX cache-directory branch and alias suppression on a
    # fresh multi-page response.
    monkeypatch.setattr(tvdb_module, "os", SimpleNamespace(name="nt"))
    FakeTVDB.episode_pages = {
        0: [
            _episode(number=index + 1, absolute=index + 1)
            for index in range(500)
        ],
        1: [_episode(season=2, number=1, absolute=501)],
    }
    FakeTVDB.series_extended = {
        "aliases": [{"language": "eng", "name": "Series (2026)"}]
    }
    FakeTVDB.translation = {"name": "Series", "aliases": []}
    data, alias = asyncio.run(
        manager.get_tvdb_episodes(31, str(tmp_path), original_language="en")
    )
    assert data and len(data["episodes"]) == 501 and alias is None

    # A non-empty TMDb result with no acceptable series/movie reaches the
    # diagnostic result-type path before returning no match.
    FakeTVDB.remote_results = {
        "456": [{"episode": {"seriesId": 0}}, {"other": {"id": 1}}]
    }
    assert asyncio.run(
        manager.get_tvdb_by_external_id(None, 456, tv_movie=False)
    ) == (None, None)

    # Constructor returning no client without raising covers the generic
    # unavailable message path.
    tvdb_module.tvdb = None
    tvdb_module._tvdb_init_error = None
    tvdb_module._tvdb_error_reported = False
    monkeypatch.setattr(tvdb_module, "TVDB", lambda _key: None)
    assert (
        tvdb_module._get_tvdb_or_warn({"DEFAULT": {"tvdb_api": "key"}}) is None
    )
