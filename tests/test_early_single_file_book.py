from __future__ import annotations

import asyncio

from src.domain_models.release import Meta
from src.services import early_artifact_service


def _meta(**values: object) -> Meta:
    defaults: dict[str, object] = {
        "category": "BOOK",
        "audiobook": False,
        "isdir": False,
        "filelist": ["/tmp/release/Book.epub"],
    }
    defaults.update(values)
    return Meta(**defaults)


def test_prepared_single_file_book_does_not_reuse_a_client_torrent() -> None:
    assert early_artifact_service._single_file_book(_meta()) is True
    assert early_artifact_service._single_file_book(
        _meta(audiobook=True)
    ) is False
    assert early_artifact_service._single_file_book(_meta(isdir=True)) is False


def test_reuse_lookup_returns_none_for_a_single_file_book() -> None:
    async def fail(_meta):  # type: ignore[no-untyped-def]
        raise AssertionError("client must not be searched")

    class Client:
        find_existing_torrent = staticmethod(fail)

    assert (
        asyncio.run(
            early_artifact_service._find_reusable_torrent(
                _meta(), Client()
            )
        )
        is None
    )
