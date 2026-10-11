from __future__ import annotations

import asyncio
from typing import Any, ClassVar, Self

import pytest

from src.integrations.external_apis import google_books
from src.integrations.external_apis.google_books import GoogleBooksManager


class _Cache:
    def __init__(self, value: object = None) -> None:
        self.value = value
        self.set_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def get(self, *_args: object) -> object:
        return self.value

    async def set(self, *args: object, **kwargs: object) -> None:
        self.set_calls.append((args, kwargs))


class _Response:
    def __init__(self, status_code: int = 200, payload: object = None) -> None:
        self.status_code = status_code
        self.payload = payload

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


def _volume(isbn: str = "9780306406157", **info: object) -> dict[str, Any]:
    volume_info: dict[str, Any] = {
        "industryIdentifiers": [{"type": "ISBN_13", "identifier": isbn}],
        "title": "Book",
    }
    volume_info.update(info)
    return {"id": "volume", "volumeInfo": volume_info}


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch) -> None:
    _Client.queue = []
    _Client.urls = []
    monkeypatch.setattr(google_books.httpx, "AsyncClient", _Client)


def test_canonical_isbn_10_13_and_invalid() -> None:
    assert (
        GoogleBooksManager._canonical_isbn("0-306-40615-2") == "9780306406157"
    )
    assert (
        GoogleBooksManager._canonical_isbn("978-0-306-40615-7")
        == "9780306406157"
    )
    assert GoogleBooksManager._canonical_isbn("bad") == ""


def test_parse_volume_no_results_no_match_and_full_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = GoogleBooksManager()
    assert manager._parse_volume_info({}, "9780306406157") is None
    assert (
        manager._parse_volume_info({"totalItems": 1}, "9780306406157") is None
    )
    assert (
        manager._parse_volume_info(
            {"totalItems": 1, "items": [_volume("9781861972712")]},
            "9780306406157",
        )
        is None
    )

    monkeypatch.setattr(
        google_books, "resolve_book_language", lambda _lang: ("English", "eng")
    )
    monkeypatch.setattr(
        google_books, "is_valid_book_language", lambda *_args: True
    )
    data = {
        "totalItems": 1,
        "items": [
            _volume(
                title="Main",
                subtitle="Subtitle",
                authors=["Author One", "Author Two"],
                publisher="Publisher",
                description="<p>Overview</p>",
                publishedDate="Published 2024-01-01",
                language="en",
                categories=["Comics", "Manga", "Magazine", "Newspaper", ""],
                imageLinks={"thumbnail": "https://images.invalid/cover.jpg"},
            )
        ],
    }
    result = manager._parse_volume_info(data, "9780306406157")
    assert result == {
        "artwork_url": "https://images.invalid/cover.jpg",
        "title": "Main: Subtitle",
        "author": "Author One, Author Two",
        "publisher": "Publisher",
        "overview": "Overview",
        "year": "2024",
        "search_year": 2024,
        "book_language": "English",
        "book_language_iso": "eng",
        "keywords": ["Comics", "Manga", "Magazine", "Newspaper"],
        "genres": ["Comics", "Manga", "Magazine", "Newspaper"],
        "comic": True,
        "manga": True,
        "magazine": True,
        "newspaper": True,
        "isbn": "9780306406157",
    }


def test_parse_volume_optional_title_language_and_invalid_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = GoogleBooksManager()
    result = manager._parse_volume_info(
        {"totalItems": 1, "items": [_volume(title="Only", subtitle="")]},
        "9780306406157",
    )
    assert result == {"title": "Only", "isbn": "9780306406157"}

    monkeypatch.setattr(
        google_books, "resolve_book_language", lambda _lang: ("Unknown", "")
    )
    monkeypatch.setattr(
        google_books, "is_valid_book_language", lambda *_args: False
    )
    result = manager._parse_volume_info(
        {"totalItems": 1, "items": [_volume(language="xx")]}, "9780306406157"
    )
    assert "book_language" not in result

    monkeypatch.setattr(
        google_books,
        "resolve_book_language",
        lambda _lang: (_ for _ in ()).throw(LookupError("bad")),
    )
    result = manager._parse_volume_info(
        {"totalItems": 1, "items": [_volume(language="xx")]}, "9780306406157"
    )
    assert "book_language" not in result


def test_search_empty_cache_hits_and_network_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = GoogleBooksManager()
    assert asyncio.run(manager.search_by_isbn("  ")) is None

    cache = _Cache({"not_found": True})
    monkeypatch.setattr(google_books, "cache_for", lambda _base: cache)
    monkeypatch.setattr(google_books, "is_cache_miss", lambda _value: False)
    assert asyncio.run(manager.search_by_isbn("978-0-306-40615-7")) is None

    cache.value = {"title": "Cached"}
    assert asyncio.run(manager.search_by_isbn("9780306406157")) == {
        "title": "Cached"
    }

    cache.value = object()
    monkeypatch.setattr(google_books, "is_cache_miss", lambda _value: True)
    monkeypatch.setattr(
        google_books, "resolve_book_language", lambda _lang: ("English", "eng")
    )
    monkeypatch.setattr(
        google_books, "is_valid_book_language", lambda *_args: True
    )
    _Client.queue = [_Response(200, {"totalItems": 1, "items": [_volume()]})]
    result = asyncio.run(
        manager.search_by_isbn("9780306406157", api_key="secret")
    )
    assert result and result["title"] == "Book"
    assert "&key=secret" in _Client.urls[-1]
    assert cache.set_calls[-1][1] == {}


def test_search_not_found_statuses_json_and_network_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = GoogleBooksManager()
    cache = _Cache(object())
    monkeypatch.setattr(google_books, "cache_for", lambda _base: cache)
    monkeypatch.setattr(google_books, "is_cache_miss", lambda _value: True)

    _Client.queue = [
        _Response(200, {"totalItems": 1, "items": [_volume("9781861972712")]})
    ]
    assert asyncio.run(manager.search_by_isbn("9780306406157")) is None
    assert cache.set_calls[-1][1]["negative"] is True

    _Client.queue = [_Response(200, {"totalItems": 0, "items": []})]
    assert asyncio.run(manager.search_by_isbn("9780306406157")) is None
    assert cache.set_calls[-1][1]["negative"] is True

    for status in (429, 500, 404):
        _Client.queue = [_Response(status, {})]
        assert asyncio.run(manager.search_by_isbn("9780306406157")) is None
    assert cache.set_calls[-1][1]["negative"] is True

    _Client.queue = [
        _Response(200, RuntimeError("bad json")),
        RuntimeError("network"),
    ]
    assert asyncio.run(manager.search_by_isbn("9780306406157")) is None
    assert asyncio.run(manager.search_by_isbn("9780306406157")) is None
