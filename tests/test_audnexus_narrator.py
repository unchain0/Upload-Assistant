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
        return {"narrator": "Jane Doe", "language": "english", "publisher": "Penguin", "year": "2018"}

    monkeypatch.setattr(
        "src.integrations.external_apis.audnexus.AudnexusManager.metadata_by_asin",
        fake,
    )
    meta = Meta(audiobook=True, asin="B000000000")
    asyncio.run(book_preparation._apply_audnexus_narrator(meta))
    assert meta.narrator == "Jane Doe"
    assert meta.book_language_iso == "eng"
    assert meta.book_language == "English"
    assert meta.publisher == "Penguin"
    assert meta.year == 2018


def test_skip_reasons_are_logged(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from src.domain_models.release import Meta
    from src.services import book_preparation

    messages: list[str] = []
    monkeypatch.setattr(
        book_preparation.logger,
        "info",
        lambda message, *args, **kwargs: messages.append(str(message)),
    )
    asyncio.run(
        book_preparation._apply_audnexus_narrator(Meta(audiobook=False))
    )
    assert any("not an audiobook" in message for message in messages)
    asyncio.run(
        book_preparation._apply_audnexus_narrator(
            Meta(audiobook=True, narrator="Jane Doe")
        )
    )
    assert any("already set" in message for message in messages)
    asyncio.run(
        book_preparation._apply_audnexus_narrator(Meta(audiobook=True, title=""))
    )
    assert any("missing title or author" in message for message in messages)


class _MetaClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def get(self, url: str, params: dict | None = None):
        class _R:
            status_code = 200
            def json(self_inner):  # type: ignore[no-untyped-def]
                return self.payload
        return _R()


def test_metadata_maps_language_publisher_and_year() -> None:
    manager = AudnexusManager()
    english = asyncio.run(
        manager.metadata_by_asin(
            "B07DX9W2CG",
            client=_MetaClient(
                {
                    "narrators": [{"name": "Brené Brown"}],
                    "language": "english",
                    "publisherName": "Penguin Audio",
                    "releaseDate": "2018-06-26T00:00:00.000Z",
                }
            ),  # type: ignore[arg-type]
        )
    )
    assert english == {
        "narrator": "Brené Brown",
        "language": "english",
        "publisher": "Penguin Audio",
        "year": "2018",
    }
    spanish = asyncio.run(
        manager.metadata_by_asin(
            "B000000000",
            client=_MetaClient(
                {"language": "spanish", "publisherName": "Planeta"}
            ),  # type: ignore[arg-type]
        )
    )
    assert spanish["language"] == "spanish"
