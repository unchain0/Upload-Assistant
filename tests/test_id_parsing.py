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
