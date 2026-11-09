from __future__ import annotations

import asyncio
from typing import Any, ClassVar, Self

import pytest

from src.integrations.external_apis import openlibrary
from src.integrations.external_apis.openlibrary import OpenLibraryManager


class _Cache:
    def __init__(self, value: object = None) -> None:
        self.value = value
        self.values: dict[tuple[object, ...], object] = {}
        self.set_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def get(self, *args: object) -> object:
        return self.values.get(args, self.value)

    async def set(self, *args: object, **kwargs: object) -> None:
        self.set_calls.append((args, kwargs))
        if len(args) >= 4:
            self.values[tuple(args[:3])] = args[3]


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


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch) -> None:
    _Client.queue = []
    _Client.urls = []
    monkeypatch.setattr(openlibrary.httpx, "AsyncClient", _Client)


def test_author_name_cache_success_personal_empty_404_and_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = OpenLibraryManager()
    client = _Client()
    cache = _Cache({"name": "Cached Author"})
    monkeypatch.setattr(openlibrary, "is_cache_miss", lambda _value: False)
    assert (
        asyncio.run(manager.get_author_name("/authors/OL1A", client, cache))
        == "Cached Author"
    )

    monkeypatch.setattr(openlibrary, "is_cache_miss", lambda _value: True)
    cache.value = object()
    _Client.queue = [_Response(200, {"name": "Named"})]
    assert (
        asyncio.run(manager.get_author_name("/authors/OL2A", client, cache))
        == "Named"
    )
    _Client.queue = [_Response(200, {"personal_name": "Personal"})]
    assert (
        asyncio.run(manager.get_author_name("OL3A", client, cache))
        == "Personal"
    )
    _Client.queue = [_Response(200, {})]
    assert asyncio.run(manager.get_author_name("OL4A", client, cache)) == ""
    assert cache.set_calls[-1][1]["negative"] is True
    _Client.queue = [_Response(404, {})]
    assert asyncio.run(manager.get_author_name("OL5A", client, cache)) == ""
    _Client.queue = [RuntimeError("network")]
    assert asyncio.run(manager.get_author_name("OL6A", client, cache)) == ""


def test_work_empty_cache_success_full_metadata_and_author_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = OpenLibraryManager()
    assert asyncio.run(manager.search_by_work_id("  ")) is None

    cache = _Cache({"not_found": True})
    monkeypatch.setattr(openlibrary, "cache_for", lambda _base: cache)
    monkeypatch.setattr(openlibrary, "is_cache_miss", lambda _value: False)
    assert asyncio.run(manager.search_by_work_id("OL1W")) is None
    cache.value = {"title": "Cached"}
    assert asyncio.run(manager.search_by_work_id("OL1W")) == {
        "title": "Cached"
    }

    cache.value = object()
    monkeypatch.setattr(openlibrary, "is_cache_miss", lambda _value: True)
    _Client.queue = [
        _Response(
            200,
            {
                "title": "Work",
                "subtitle": "Subtitle",
                "description": {"value": "<p>Overview</p>"},
                "covers": [123],
                "authors": [
                    {"author": {"key": "/authors/OL1A"}},
                    {"author": {}},
                    {},
                ],
                "subjects": ["One", "Two", "", *[f"S{i}" for i in range(20)]],
            },
        ),
        _Response(200, {"name": "Author"}),
    ]
    result = asyncio.run(manager.search_by_work_id("OL1W"))
    assert result and result["title"] == "Work: Subtitle"
    assert result["overview"] == "Overview" and result["author"] == "Author"
    assert result["artwork_url"].endswith("/123-L.jpg")
    assert (
        len(result["subjects"] if "subjects" in result else result["keywords"])
        == 9
    )
    assert result["genres"] == result["keywords"]
    assert result["openlibrary"] == "OL1W"


def test_work_string_description_optional_fields_not_found_status_and_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = OpenLibraryManager()
    cache = _Cache(object())
    monkeypatch.setattr(openlibrary, "cache_for", lambda _base: cache)
    monkeypatch.setattr(openlibrary, "is_cache_miss", lambda _value: True)

    _Client.queue = [
        _Response(
            200,
            {
                "title": "Work",
                "description": "<b>Text</b>",
                "covers": [-1],
                "authors": [],
                "subjects": [],
            },
        )
    ]
    result = asyncio.run(manager.search_by_work_id("OL2W"))
    assert (
        result and result["title"] == "Work" and result["overview"] == "Text"
    )
    assert (
        "artwork_url" not in result
        and "author" not in result
        and "keywords" not in result
    )

    _Client.queue = [_Response(200, {})]
    assert asyncio.run(manager.search_by_work_id("OL3W")) is None
    assert cache.set_calls[-1][1]["negative"] is True
    for status in (404, 500):
        _Client.queue = [_Response(status, {})]
        assert asyncio.run(manager.search_by_work_id("OL4W")) is None
    _Client.queue = [RuntimeError("network")]
    assert asyncio.run(manager.search_by_work_id("OL5W")) is None


def test_isbn_empty_cache_work_success_and_publisher_year_merge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = OpenLibraryManager()
    assert asyncio.run(manager.search_by_isbn("  ")) is None

    cache = _Cache({"not_found": True})
    monkeypatch.setattr(openlibrary, "cache_for", lambda _base: cache)
    monkeypatch.setattr(openlibrary, "is_cache_miss", lambda _value: False)
    assert asyncio.run(manager.search_by_isbn("978-0-306-40615-7")) is None
    cache.value = {"title": "Cached"}
    assert asyncio.run(manager.search_by_isbn("9780306406157")) == {
        "title": "Cached"
    }

    cache.value = object()
    monkeypatch.setattr(openlibrary, "is_cache_miss", lambda _value: True)
    monkeypatch.setattr(
        manager,
        "search_by_work_id",
        lambda *_args, **_kwargs: _async_value({"title": "Work"}),
    )
    _Client.queue = [
        _Response(
            200,
            {
                "ISBN:9780306406157": {
                    "details": {
                        "works": [{"key": "/works/OL1W"}],
                        "publishers": ["Publisher", "Second"],
                        "publish_date": "Published 2024",
                    }
                }
            },
        )
    ]
    result = asyncio.run(manager.search_by_isbn("9780306406157"))
    assert result == {
        "title": "Work",
        "publisher": "Publisher, Second",
        "year": "2024",
        "search_year": 2024,
        "isbn": "9780306406157",
    }


def _async_value(value: object):
    async def result():
        return value

    return result()


def test_isbn_work_existing_publisher_fallback_details_and_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = OpenLibraryManager()
    cache = _Cache(object())
    monkeypatch.setattr(openlibrary, "cache_for", lambda _base: cache)
    monkeypatch.setattr(openlibrary, "is_cache_miss", lambda _value: True)
    monkeypatch.setattr(
        manager,
        "search_by_work_id",
        lambda *_args, **_kwargs: _async_value(
            {"title": "Work", "publisher": "Existing", "year": "2020"}
        ),
    )
    _Client.queue = [
        _Response(
            200,
            {
                "ISBN:9780306406157": {
                    "details": {
                        "works": [{"key": "/works/OL1W"}],
                        "publishers": ["Ignored"],
                        "publish_date": "2024",
                    }
                }
            },
        )
    ]
    result = asyncio.run(manager.search_by_isbn("9780306406157"))
    assert (
        result
        and result["publisher"] == "Existing"
        and result["year"] == "2020"
    )

    monkeypatch.setattr(
        manager,
        "search_by_work_id",
        lambda *_args, **_kwargs: _async_value(None),
    )
    details = {
        "title": "Fallback",
        "subtitle": "Subtitle",
        "authors": [{"name": "Author"}, {}, {"name": "Second"}],
        "publishers": ["Publisher"],
        "publish_date": "March 2023",
        "works": [{"key": "/works/OL2W"}],
    }
    _Client.queue = [
        _Response(
            200,
            {
                "ISBN:9780306406157": {
                    "details": details,
                    "thumbnail_url": "https://covers.invalid/id-S.jpg",
                }
            },
        )
    ]
    result = asyncio.run(manager.search_by_isbn("9780306406157"))
    assert result and result["title"] == "Fallback: Subtitle"
    assert (
        result["author"] == "Author, Second"
        and result["publisher"] == "Publisher"
    )
    assert result["year"] == "2023" and result["artwork_url"].endswith(
        "-L.jpg"
    )

    _Client.queue = [
        _Response(200, {"ISBN:9780306406157": {"details": {"works": []}}})
    ]
    assert asyncio.run(manager.search_by_isbn("9780306406157")) is None
    assert cache.set_calls[-1][1]["negative"] is True


def test_isbn_empty_payload_status_json_and_network_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = OpenLibraryManager()
    cache = _Cache(object())
    monkeypatch.setattr(openlibrary, "cache_for", lambda _base: cache)
    monkeypatch.setattr(openlibrary, "is_cache_miss", lambda _value: True)

    _Client.queue = [_Response(200, {})]
    assert asyncio.run(manager.search_by_isbn("9780306406157")) is None
    assert cache.set_calls[-1][1]["negative"] is True

    for status in (404, 500):
        _Client.queue = [_Response(status, {})]
        assert asyncio.run(manager.search_by_isbn("9780306406157")) is None
    _Client.queue = [
        _Response(200, RuntimeError("bad json")),
        RuntimeError("network"),
    ]
    assert asyncio.run(manager.search_by_isbn("9780306406157")) is None
    assert asyncio.run(manager.search_by_isbn("9780306406157")) is None


def test_add_year_and_metadata_details_all_branches() -> None:
    manager = OpenLibraryManager()
    metadata: dict[str, Any] = {}
    manager._add_year(metadata, None)
    assert metadata == {}
    manager._add_year(metadata, "no year")
    assert metadata == {}
    manager._add_year(metadata, "Published 2024")
    assert metadata == {"year": "2024", "search_year": 2024}
    manager._add_year(metadata, "2025")
    assert metadata["year"] == "2024"

    assert manager._metadata_from_book_details({}, {}, "isbn") == {}
    minimal = manager._metadata_from_book_details(
        {}, {"title": "Book"}, "isbn"
    )
    assert minimal == {"title": "Book", "isbn": "isbn"}
    full = manager._metadata_from_book_details(
        {"thumbnail_url": "https://covers.invalid/id-S.jpg"},
        {
            "title": "Book",
            "subtitle": "Sub",
            "authors": [{"name": "A"}, {}],
            "publishers": ["P"],
            "publish_date": "2022",
        },
        "isbn",
    )
    assert (
        full["title"] == "Book: Sub"
        and full["author"] == "A"
        and full["publisher"] == "P"
    )
    assert (
        full["artwork_url"].endswith("-L.jpg") and full["search_year"] == 2022
    )
