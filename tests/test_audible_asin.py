from __future__ import annotations

import asyncio

from src.integrations.external_apis.audible import AudibleManager


class _Response:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _Client:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.calls = 0

    async def get(self, url: str, params: dict | None = None) -> _Response:
        self.calls += 1
        return self.response


def _product(asin: str, title: str, author: str) -> dict:
    return {"asin": asin, "title": title, "authors": [{"name": author}]}


def test_exact_title_and_author_resolve_the_asin() -> None:
    AudibleManager._cache.clear()
    client = _Client(
        _Response(
            200,
            {
                "products": [
                    _product("B000000001", "Database Internals", "Alex Petrov"),
                    _product("B000000002", "Other Book", "Other Author"),
                ]
            },
        )
    )
    asin = asyncio.run(
        AudibleManager().asin_by_title_author(
            "Database Internals", "Alex Petrov", client=client  # type: ignore[arg-type]
        )
    )
    assert asin == "B000000001"


def test_same_title_editions_resolve_to_the_first() -> None:
    AudibleManager._cache.clear()
    client = _Client(
        _Response(
            200,
            {
                "products": [
                    _product("B000000001", "Daring Greatly", "Brené Brown"),
                    _product("B000000003", "Daring Greatly", "Brené Brown"),
                ]
            },
        )
    )
    assert (
        asyncio.run(
            AudibleManager().asin_by_title_author(
                "Daring Greatly", "Brené Brown", client=client  # type: ignore[arg-type]
            )
        )
        == "B000000001"
    )


def test_divergent_titles_do_not_resolve(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    AudibleManager._cache.clear()
    monkeypatch.setattr(
        AudibleManager, "_title_similarity", classmethod(lambda cls, a, b: 1.0)
    )
    client = _Client(
        _Response(
            200,
            {
                "products": [
                    _product("B000000001", "Database Internals", "Alex Petrov"),
                    _product("B000000003", "Internals Database", "Alex Petrov"),
                ]
            },
        )
    )
    assert (
        asyncio.run(
            AudibleManager().asin_by_title_author(
                "Database Internals", "Alex Petrov", client=client  # type: ignore[arg-type]
            )
        )
        == ""
    )


def test_no_result_does_not_resolve() -> None:
    AudibleManager._cache.clear()
    client = _Client(_Response(200, {"products": []}))
    assert (
        asyncio.run(
            AudibleManager().asin_by_title_author(
                "Database Internals", "Alex Petrov", client=client  # type: ignore[arg-type]
            )
        )
        == ""
    )


def test_results_are_cached() -> None:
    AudibleManager._cache.clear()
    client = _Client(
        _Response(200, {"products": [_product("B000000001", "Book", "Author")]})
    )
    manager = AudibleManager()
    first = asyncio.run(manager.asin_by_title_author("Book", "Author", client=client))  # type: ignore[arg-type]
    second = asyncio.run(manager.asin_by_title_author("Book", "Author", client=client))  # type: ignore[arg-type]
    assert first == second == "B000000001"
    assert client.calls == 1


def test_book_prep_resolves_asin_and_narrator(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from src.domain_models.release import Meta
    from src.services import book_preparation

    async def fake_asin(self, title: str, author: str, client=None):  # type: ignore[no-untyped-def]
        return "B000000001"

    async def fake_metadata(self, asin: str, client=None):  # type: ignore[no-untyped-def]
        return {"narrator": "Jane Doe", "language": "english", "publisher": "Penguin", "year": "2018"}

    monkeypatch.setattr(
        "src.integrations.external_apis.audible.AudibleManager.asin_by_title_author",
        fake_asin,
    )
    monkeypatch.setattr(
        "src.integrations.external_apis.audnexus.AudnexusManager.metadata_by_asin",
        fake_metadata,
    )
    meta = Meta(audiobook=True, title="Book", author="Author")
    asyncio.run(book_preparation._apply_audnexus_narrator(meta))
    assert meta.asin == "B000000001"
    assert meta.narrator == "Jane Doe"
