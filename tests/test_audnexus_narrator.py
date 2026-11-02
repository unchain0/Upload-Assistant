from __future__ import annotations

import asyncio

from src.integrations.external_apis.audnexus import AudnexusManager


class _Response:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _Client:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.calls: list[str] = []

    async def get(self, url: str, params: dict | None = None) -> _Response:
        self.calls.append(url)
        return self.response


def test_narrator_comes_from_audnexus() -> None:
    client = _Client(
        _Response(200, {"narrators": [{"name": "Jane Doe"}, {"name": "John Roe"}]})
    )
    narrator = asyncio.run(
        AudnexusManager().narrator_by_asin("B000000000", client=client)  # type: ignore[arg-type]
    )
    assert narrator == "Jane Doe, John Roe"
    assert client.calls == ["https://api.audnex.us/books/B000000000"]


def test_missing_asin_skips_the_lookup() -> None:
    client = _Client(_Response(200, {"narrators": [{"name": "Jane Doe"}]}))
    assert (
        asyncio.run(
            AudnexusManager().narrator_by_asin("", client=client)  # type: ignore[arg-type]
        )
        == ""
    )
    assert client.calls == []


def test_not_found_or_error_returns_empty() -> None:
    client = _Client(_Response(404, {}))
    assert (
        asyncio.run(
            AudnexusManager().narrator_by_asin("B000000000", client=client)  # type: ignore[arg-type]
        )
        == ""
    )


def test_book_prep_applies_the_narrator(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from src.domain_models.release import Meta
    from src.services import book_preparation

    async def fake(self, asin: str, client=None):  # type: ignore[no-untyped-def]
        return "Jane Doe"

    monkeypatch.setattr(
        "src.integrations.external_apis.audnexus.AudnexusManager.narrator_by_asin",
        fake,
    )
    meta = Meta(audiobook=True, asin="B000000000")
    asyncio.run(book_preparation._apply_audnexus_narrator(meta))
    assert meta.narrator == "Jane Doe"
