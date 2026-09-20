import asyncio

from src.integrations.external_apis.google_books import google_books_manager


class _Response:
    status_code = 200

    def json(self):
        return {"totalItems": 0}


class _Client:
    requests = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, *_args, **_kwargs):
        type(self).requests += 1
        return _Response()


class _NonMatchingClient(_Client):
    async def get(self, *_args, **_kwargs):
        type(self).requests += 1
        response = _Response()
        response.json = lambda: {
            "totalItems": 1,
            "items": [
                {
                    "volumeInfo": {
                        "industryIdentifiers": [
                            {"type": "ISBN_13", "identifier": "9780000000003"}
                        ]
                    }
                }
            ],
        }
        return response


def test_google_books_caches_negative_exact_isbn_lookup(tmp_path, monkeypatch):
    async def run():
        _Client.requests = 0
        monkeypatch.setattr(
            "src.integrations.external_apis.google_books.httpx.AsyncClient",
            lambda **_kwargs: _Client(),
        )

        assert (
            await google_books_manager.search_by_isbn(
                "978-0000000002", tmp_path
            )
            is None
        )
        assert (
            await google_books_manager.search_by_isbn(
                "9780000000002", tmp_path
            )
            is None
        )
        assert _Client.requests == 1

    asyncio.run(run())


def test_google_books_caches_nonmatching_results_as_negative(
    tmp_path, monkeypatch
):
    async def run():
        _NonMatchingClient.requests = 0
        monkeypatch.setattr(
            "src.integrations.external_apis.google_books.httpx.AsyncClient",
            lambda **_kwargs: _NonMatchingClient(),
        )

        assert (
            await google_books_manager.search_by_isbn(
                "9780000000002", tmp_path
            )
            is None
        )
        assert (
            await google_books_manager.search_by_isbn(
                "9780000000002", tmp_path
            )
            is None
        )
        assert _NonMatchingClient.requests == 1

    asyncio.run(run())
