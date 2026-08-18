# ruff: noqa: S101
import pytest

from src.id_parsing import parse_tmdb_id


@pytest.mark.parametrize("value", ["tv", "tv123", "movie", "movie123"])
def test_parse_tmdb_id_rejects_malformed_category_prefixes_without_crashing(value: str) -> None:
    assert parse_tmdb_id(value, "MOVIE") == ("MOVIE", 0)


def test_parse_tmdb_id_accepts_category_prefixed_id() -> None:
    assert parse_tmdb_id("tv/12345", None) == ("TV", 12345)
    assert parse_tmdb_id("movie/67890", None) == ("MOVIE", 67890)


def test_parse_tmdb_id_accepts_tmdb_url() -> None:
    assert parse_tmdb_id("https://www.themoviedb.org/tv/12345", None) == ("TV", 12345)
    assert parse_tmdb_id("https://www.themoviedb.org/tv/12345/season/1", None) == ("TV", 12345)
    assert parse_tmdb_id("https://www.themoviedb.org/movie/67890/cast", None) == ("MOVIE", 67890)


def test_parse_tmdb_id_does_not_use_unrelated_numeric_url_segment() -> None:
    assert parse_tmdb_id("https://www.themoviedb.org/person/12345", "TV") == ("TV", 0)


def test_parse_tmdb_id_rejects_non_tmdb_url_hosts() -> None:
    assert parse_tmdb_id("https://example.com/tv/12345", "MOVIE") == ("MOVIE", 0)
    assert parse_tmdb_id("https://themoviedb.org.evil.example/movie/67890", "TV") == ("TV", 0)


def test_parse_tmdb_id_rejects_malformed_http_prefix_and_non_http_scheme() -> None:
    assert parse_tmdb_id("httpjunk/tv/12345", "MOVIE") == ("MOVIE", 0)
    assert parse_tmdb_id("ftp://www.themoviedb.org/tv/12345", "MOVIE") == ("MOVIE", 0)
