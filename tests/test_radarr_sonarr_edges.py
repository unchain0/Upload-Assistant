from __future__ import annotations

import asyncio
from typing import Any, ClassVar, Self

import httpx
import pytest

from src.integrations.external_apis import radarr, sonarr
from src.integrations.external_apis.radarr import RadarrManager
from src.integrations.external_apis.sonarr import SonarrManager


class _Response:
    def __init__(self, status_code: int = 200, payload: object = None, text: str = "response") -> None:
        self.status_code = status_code
        self.payload = payload
        self.text = text

    def json(self) -> object:
        if isinstance(self.payload, BaseException):
            raise self.payload
        return self.payload


class _Client:
    queue: ClassVar[list[object]] = []
    urls: ClassVar[list[str]] = []

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, url: str, **_kwargs: object) -> _Response:
        type(self).urls.append(url)
        value = type(self).queue.pop(0)
        if isinstance(value, BaseException):
            raise value
        assert isinstance(value, _Response)
        return value


def _movie_payload(**values: object) -> list[dict[str, Any]]:
    movie: dict[str, Any] = {
        "imdbId": "tt1234567",
        "tmdbId": 123,
        "year": 2026,
        "secondaryYear": 2025,
        "title": "Movie",
        "genres": ["Drama"],
        "movieFile": {"originalFilePath": "/media/movie.mkv", "releaseGroup": "GROUP"},
    }
    movie.update(values)
    return [movie]


def _series_payload(**values: object) -> list[dict[str, Any]]:
    series: dict[str, Any] = {
        "tvdbId": 456,
        "imdbId": "tt1234567",
        "tvMazeId": 789,
        "tmdbId": 123,
        "genres": ["Drama"],
        "title": "Show",
        "year": 2026,
        "releaseGroup": "GROUP",
    }
    series.update(values)
    return [series]


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch) -> None:
    _Client.queue = []
    _Client.urls = []
    monkeypatch.setattr(radarr.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(sonarr.httpx, "AsyncClient", _Client)


def test_radarr_extract_empty_invalid_list_filename_and_release_group() -> None:
    manager = RadarrManager({"DEFAULT": {}})
    empty = {"imdb_id": None, "tmdb_id": None, "year": None, "secondary_year": None, "genres": [], "release_group": None}
    assert asyncio.run(manager.extract_movie_data(None)) == empty
    assert asyncio.run(manager.extract_movie_data({"bad": True})) == empty
    assert asyncio.run(manager.extract_movie_data([])) == empty
    assert asyncio.run(manager.extract_movie_data(_movie_payload(), "/other.mkv")) is None

    parsed = asyncio.run(manager.extract_movie_data(_movie_payload(), "/media/movie.mkv"))
    assert parsed == {
        "imdb_id": 1234567,
        "tmdb_id": 123,
        "year": 2026,
        "secondary_year": 2025,
        "genres": ["Drama"],
        "release_group": "GROUP",
    }
    parsed = asyncio.run(manager.extract_movie_data(_movie_payload(imdbId="", movieFile={})))
    assert parsed["imdb_id"] is None and parsed["release_group"] is None


def test_radarr_no_config_invalid_slots_no_query_and_success() -> None:
    assert asyncio.run(RadarrManager({"DEFAULT": {}}).get_radarr_data(tmdb_id=1)) is None
    config = {"DEFAULT": {"radarr_api_key": 1, "radarr_api_key_1": " ", "radarr_api_key_2": "key", "radarr_url_2": 1}}
    assert asyncio.run(RadarrManager(config).get_radarr_data(tmdb_id=1)) is None

    config = {"DEFAULT": {"radarr_api_key": "key", "radarr_url": "https://radarr.invalid/"}}
    assert asyncio.run(RadarrManager(config).get_radarr_data()) is None

    _Client.queue = [_Response(payload=_movie_payload())]
    result = asyncio.run(RadarrManager(config).get_radarr_data(tmdb_id=123))
    assert result and result["tmdb_id"] == 123
    assert "movie?tmdbId=123" in _Client.urls[-1]

    _Client.queue = [_Response(payload=_movie_payload())]
    result = asyncio.run(RadarrManager(config).get_radarr_data(filename="/media/movie.mkv"))
    assert result and result["release_group"] == "GROUP"
    assert "/lookup?term=/media/movie.mkv" in _Client.urls[-1]


def test_radarr_response_errors_then_second_instance_success() -> None:
    request = httpx.Request("GET", "https://radarr.invalid")
    config = {
        "DEFAULT": {
            "radarr_api_key": "key",
            "radarr_url": "https://first.invalid",
            "radarr_api_key_1": "key2",
            "radarr_url_1": "https://second.invalid",
            "radarr_api_key_2": "key3",
            "radarr_url_2": "https://third.invalid",
            "radarr_api_key_3": "key4",
            "radarr_url_3": "https://fourth.invalid",
        }
    }
    _Client.queue = [
        _Response(500, {}, "bad"),
        httpx.TimeoutException("timeout", request=request),
        httpx.RequestError("request", request=request),
        _Response(200, RuntimeError("bad json")),
    ]
    assert asyncio.run(RadarrManager(config).get_radarr_data(tmdb_id=123)) is None

    _Client.queue = [_Response(200, []), _Response(200, _movie_payload())]
    result = asyncio.run(RadarrManager(config).get_radarr_data(tmdb_id=123))
    assert result and result["imdb_id"] == 1234567
    assert _Client.urls[-1].startswith("https://second.invalid")


def test_radarr_accepts_strong_title_match_when_filename_year_matches_secondary_year() -> None:
    manager = RadarrManager({"DEFAULT": {}})
    payload = _movie_payload(
        title="Tatami",
        year=2024,
        secondaryYear=2023,
        tmdbId=1084066,
        imdbId="tt26674818",
        movieFile={},
    )

    parsed = asyncio.run(manager.extract_movie_data(payload, "Tatame.2023.1080p.AMZN.WEB-DL.DDP5.1.H.264.DUAL-FLY.mkv"))

    assert parsed is not None
    assert parsed["tmdb_id"] == 1084066
    assert parsed["year"] == 2024
    assert parsed["secondary_year"] == 2023


def test_radarr_rejects_lookup_candidate_when_only_year_matches() -> None:
    manager = RadarrManager({"DEFAULT": {}})
    payload = _movie_payload(title="Completely Different", year=2024, secondaryYear=2023, movieFile={})

    assert asyncio.run(manager.extract_movie_data(payload, "Tatame.2023.1080p.WEB-DL.mkv")) is None


def test_sonarr_extract_empty_parse_list_and_invalid() -> None:
    manager = SonarrManager({"DEFAULT": {}})
    empty = {"tvdb_id": None, "imdb_id": None, "tvmaze_id": None, "tmdb_id": None, "genres": [], "title": "", "year": None, "release_group": None}
    assert asyncio.run(manager.extract_show_data(None)) == empty
    assert asyncio.run(manager.extract_show_data({"bad": True})) == empty
    assert asyncio.run(manager.extract_show_data([])) == empty

    parsed = asyncio.run(
        manager.extract_show_data(
            {
                "series": _series_payload()[0],
                "parsedEpisodeInfo": {"releaseGroup": "PARSE-GROUP"},
            }
        )
    )
    assert parsed["tvdb_id"] == 456 and parsed["imdb_id"] == 1234567 and parsed["release_group"] == "PARSE-GROUP"
    parsed = asyncio.run(manager.extract_show_data({"series": {"imdbId": ""}, "parsedEpisodeInfo": {}}))
    assert parsed["imdb_id"] is None and parsed["release_group"] is None

    listed = asyncio.run(manager.extract_show_data(_series_payload()))
    assert listed["title"] == "Show" and listed["release_group"] == "GROUP"
    listed = asyncio.run(manager.extract_show_data(_series_payload(imdbId="", releaseGroup="")))
    assert listed["imdb_id"] is None and listed["release_group"] is None


def test_sonarr_no_config_invalid_slots_no_query_and_success() -> None:
    assert asyncio.run(SonarrManager({"DEFAULT": {}}).get_sonarr_data(tvdb_id=1)) is None
    config = {"DEFAULT": {"sonarr_api_key": 1, "sonarr_api_key_1": " ", "sonarr_api_key_2": "key", "sonarr_url_2": 1}}
    assert asyncio.run(SonarrManager(config).get_sonarr_data(tvdb_id=1)) is None

    config = {"DEFAULT": {"sonarr_api_key": "key", "sonarr_url": "https://sonarr.invalid/"}}
    assert asyncio.run(SonarrManager(config).get_sonarr_data()) is None

    _Client.queue = [_Response(payload=_series_payload())]
    result = asyncio.run(SonarrManager(config).get_sonarr_data(tvdb_id=456))
    assert result and result["tvdb_id"] == 456
    assert "series?tvdbId=456" in _Client.urls[-1]

    parse_payload = {"series": _series_payload()[0], "parsedEpisodeInfo": {"releaseGroup": "GROUP"}}
    _Client.queue = [_Response(payload=parse_payload)]
    result = asyncio.run(SonarrManager(config).get_sonarr_data(filename="/media/show.mkv", title="Show.S01E01"))
    assert result and result["release_group"] == "GROUP"
    assert "/parse?title=Show.S01E01&path=/media/show.mkv" in _Client.urls[-1]


def test_sonarr_response_errors_then_second_instance_success() -> None:
    request = httpx.Request("GET", "https://sonarr.invalid")
    config = {
        "DEFAULT": {
            "sonarr_api_key": "key",
            "sonarr_url": "https://first.invalid",
            "sonarr_api_key_1": "key2",
            "sonarr_url_1": "https://second.invalid",
            "sonarr_api_key_2": "key3",
            "sonarr_url_2": "https://third.invalid",
            "sonarr_api_key_3": "key4",
            "sonarr_url_3": "https://fourth.invalid",
        }
    }
    _Client.queue = [
        _Response(500, {}, "bad"),
        httpx.TimeoutException("timeout", request=request),
        httpx.RequestError("request", request=request),
        _Response(200, RuntimeError("bad json")),
    ]
    assert asyncio.run(SonarrManager(config).get_sonarr_data(tvdb_id=456)) is None

    _Client.queue = [_Response(200, []), _Response(200, _series_payload())]
    result = asyncio.run(SonarrManager(config).get_sonarr_data(tvdb_id=456))
    assert result and result["imdb_id"] == 1234567
    assert _Client.urls[-1].startswith("https://second.invalid")


def test_radarr_matching_helper_guards_and_invalid_year_values() -> None:
    manager = RadarrManager({"DEFAULT": {}})
    assert not manager._exact_file_match({"movieFile": "bad"}, "/media/movie.mkv")
    assert manager._best_title_similarity("", {"title": "Movie"}) == 0.0
    assert manager._alternate_titles({"alternateTitles": "bad"}) == []
    assert manager._candidate_years({"year": "bad", "secondaryYear": None}) == set()


def test_radarr_alternate_titles_and_release_group_guards() -> None:
    manager = RadarrManager({"DEFAULT": {}})
    assert manager._alternate_titles({"alternateTitles": [{"title": "Alt"}, "bad"]}) == ["Alt"]
    assert manager._release_group("bad") is None
