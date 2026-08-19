from __future__ import annotations

import asyncio
import json
from typing import ClassVar, Self

import pytest

from src.integrations.external_apis import myanonamouse as mam
from src.integrations.external_apis.myanonamouse import MyAnonamouseManager


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
    requests: ClassVar[list[dict[str, object]]] = []

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def post(self, _url: str, **kwargs: object) -> _Response:
        type(self).requests.append(kwargs)
        value = type(self).queue.pop(0)
        if isinstance(value, BaseException):
            raise value
        assert isinstance(value, _Response)
        return value


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch) -> None:
    _Client.queue = []
    _Client.requests = []
    monkeypatch.setattr(mam.httpx, "AsyncClient", _Client)


def test_normalize_isbn_title_and_metadata_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mam, "validate_isbn_checksum", lambda value: value if value in {"0123456789", "9780306406157"} else None)
    assert mam._normalize_mam_isbn("123456789") == "0123456789"
    assert mam._normalize_mam_isbn("978-0-306-40615-7") == "9780306406157"
    assert mam._normalize_mam_isbn("bad") is None

    assert mam._clean_mam_title("Author - Book.epub", "Author") == "Book"
    assert mam._clean_mam_title("Book (PDF)") == "Book"
    assert mam._clean_mam_title("Book - 9780306406157") == "Book"
    assert mam._clean_mam_title(None) == ""

    assert mam._metadata_values("  value  ") == ["value"]
    assert mam._metadata_values('["one", {"name": "two"}]') == ["one", "two"]
    assert mam._metadata_values("{bad json") == ["{bad json"]
    assert mam._metadata_values({"publisher": "Pub"}) == ["Pub"]
    assert sorted(mam._metadata_values({"one": "A", "two": "B"})) == ["A", "B"]
    assert sorted(mam._metadata_values(("A", {"value": "B"}))) == ["A", "B"]
    assert mam._metadata_values(None) == []
    assert mam._metadata_values(123) == ["123"]


def test_parse_torrent_full_fields_and_categories(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mam, "validate_isbn_checksum", lambda value: value if value == "9780306406157" else None)
    monkeypatch.setattr(mam, "resolve_book_language", lambda _value: ("English", "eng"))
    monkeypatch.setattr(mam, "is_valid_book_language", lambda *_args: True)
    item = {
        "title": "Alice Writer - Example Book.epub",
        "author_info": json.dumps({"1": "Alice &amp; Writer", "2": "Bob"}),
        "narrator_info": {"1": "Narrator &amp; One"},
        "description": "A &amp; B",
        "publisher_info": '[{"name": "Publisher"}, {"name": "Publisher"}, {"name": "Second"}]',
        "isbn": "978-0-306-40615-7",
        "asin": "ASIN: b012345678",
        "publication_date": "Published 2024-01-01",
        "lang_code": "en",
        "id": 123,
        "poster_type": "image/png",
        "catname": "Comics",
        "tags": "Manga Magazine",
        "categories": "Newspaper",
    }
    result = MyAnonamouseManager()._parse_torrent_info(item)
    assert result == {
        "author": "Alice & Writer, Bob",
        "narrator": "Narrator & One",
        "overview": "A & B",
        "publisher": "Publisher, Second",
        "title": "Alice Writer - Example Book",
        "isbn": "9780306406157",
        "asin": "B012345678",
        "year": 2024,
        "book_language": "English",
        "book_language_iso": "eng",
        "artwork_url": "https://cdn.myanonamouse.net/t/p/large/123.png",
        "comic": True,
        "manga": True,
        "magazine": True,
        "newspaper": True,
    }


def test_parse_torrent_author_narrator_errors_optional_fields_and_cover_types(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = MyAnonamouseManager()
    bad = manager._parse_torrent_info(
        {
            "name": "Name",
            "author_info": "bad json",
            "narrator_info": object(),
            "asin": "invalid",
            "published": "no year",
            "lang_code": "xx",
            "id": 1,
            "poster_type": "image/gif",
        }
    )
    assert bad["title"] == "Name" and bad["artwork_url"].endswith("1.gif")
    assert "author" not in bad and "narrator" not in bad and "asin" not in bad and "year" not in bad

    monkeypatch.setattr(mam, "resolve_book_language", lambda _value: ("Unknown", ""))
    monkeypatch.setattr(mam, "is_valid_book_language", lambda *_args: False)
    assert "book_language" not in manager._parse_torrent_info({"title": "Name", "lang_code": "xx"})
    monkeypatch.setattr(mam, "resolve_book_language", lambda _value: (_ for _ in ()).throw(LookupError("bad")))
    assert "book_language" not in manager._parse_torrent_info({"title": "Name", "lang_code": "xx"})

    jpeg = manager._parse_torrent_info({"title": "Name", "id": 2, "poster_type": "image/jpeg"})
    assert jpeg["artwork_url"].endswith("2.jpeg")
    assert "artwork_url" not in manager._parse_torrent_info({"title": "Name", "id": 2})


def test_search_invalid_cache_api_key_success_not_found_status_and_error(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = MyAnonamouseManager()
    assert asyncio.run(manager.search_by_id("")) is None
    assert asyncio.run(manager.search_by_id("abc")) is None

    cache = _Cache({"not_found": True})
    monkeypatch.setattr(mam, "cache_for", lambda _base: cache)
    monkeypatch.setattr(mam, "is_cache_miss", lambda _value: False)
    assert asyncio.run(manager.search_by_id("123", api_key="cookie")) is None
    cache.value = {"title": "Cached"}
    assert asyncio.run(manager.search_by_id("123", api_key="cookie")) == {"title": "Cached"}

    cache.value = object()
    monkeypatch.setattr(mam, "is_cache_miss", lambda _value: True)
    assert asyncio.run(manager.search_by_id("123")) is None

    monkeypatch.setattr(mam, "validate_isbn_checksum", lambda value: value if value == "9780306406157" else None)
    _Client.queue = [_Response(200, {"data": [{"title": "Book", "isbn": "9780306406157"}]})]
    result = asyncio.run(manager.search_by_id("123", api_key="cookie"))
    assert result and result["title"] == "Book"
    assert _Client.requests[-1]["cookies"] == {"mam_id": "cookie"}
    assert cache.set_calls[-1][0][:3] == ("myanonamouse", "torrent", "123")

    _Client.queue = [_Response(200, {"data": []})]
    assert asyncio.run(manager.search_by_id("123", api_key="cookie")) is None
    assert cache.set_calls[-1][1]["negative"] is True

    _Client.queue = [_Response(200, {"data": [{}]})]
    assert asyncio.run(manager.search_by_id("123", api_key="cookie")) is None

    for status in (401, 403, 500):
        _Client.queue = [_Response(status, {})]
        assert asyncio.run(manager.search_by_id("123", api_key="cookie")) is None
    _Client.queue = [_Response(200, RuntimeError("bad json")), RuntimeError("network")]
    assert asyncio.run(manager.search_by_id("123", api_key="cookie")) is None
    assert asyncio.run(manager.search_by_id("123", api_key="cookie")) is None


def test_parse_author_dict_unknown_type_narrator_string_and_error() -> None:
    manager = MyAnonamouseManager()
    result = manager._parse_torrent_info(
        {
            "title": "Book",
            "author_info": {"1": "Author"},
            "narrator_info": json.dumps({"1": "Narrator"}),
        }
    )
    assert result["author"] == "Author" and result["narrator"] == "Narrator"

    result = manager._parse_torrent_info({"title": "Book", "author_info": ["ignored"]})
    assert "author" not in result

    result = manager._parse_torrent_info({"title": "Book", "narrator_info": "bad json"})
    assert "narrator" not in result
