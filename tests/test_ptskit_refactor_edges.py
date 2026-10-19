from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers import ptskit as ptskit_module
from src.integrations.trackers.ptskit import Ptskit


class _Session:
    def __init__(self) -> None:
        self.cookies: Any = {}

    async def aclose(self) -> None:
        return None


def _tracker(monkeypatch: pytest.MonkeyPatch) -> Ptskit:
    monkeypatch.setattr(
        ptskit_module.httpx,
        "AsyncClient",
        lambda *_args, **_kwargs: _Session(),
    )
    return Ptskit(
        {
            "DEFAULT": {},
            "TRACKERS": {
                "PTSKIT": {"announce_url": "https://tracker.invalid/announce"}
            },
        }
    )


@pytest.mark.asyncio
async def test_ptskit_credentials_and_type_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker(monkeypatch)
    tracker.cookie_validator.load_session_cookies = AsyncMock(
        return_value={"sid": "1"}
    )  # type: ignore[method-assign]

    meta = Meta(category="MOVIE")
    assert await tracker.validate_credentials(meta)
    assert tracker.session.cookies == {"sid": "1"}
    assert await tracker.get_type(Meta(anime=True)) == "407"
    assert await tracker.get_type(Meta(category="TV")) == "405"
    assert await tracker.get_type(Meta(category="MOVIE")) == "404"
    assert await tracker.get_type(Meta(category="OTHER")) is None


@pytest.mark.asyncio
async def test_ptskit_description_and_language_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker(monkeypatch)

    class _Builder:
        def __init__(self, tracker_name: str, config: dict[str, Any]) -> None:
            assert tracker_name == "PTSKIT"
            assert config is tracker.config

        async def general_description_generator(
            self, meta: Meta, **kwargs: Any
        ) -> str:
            assert meta.ua_signature == "UA"
            assert kwargs["book"] is False
            assert kwargs["game"] is False
            assert kwargs["nfo"] is False
            assert "Upload-Assistant" in kwargs["signature"]
            return "description"

    monkeypatch.setattr(ptskit_module, "DescriptionBuilder", _Builder)
    check = AsyncMock(return_value=True)
    monkeypatch.setattr(tracker.common, "check_language_requirements", check)
    meta = Meta(ua_signature="UA")

    assert await tracker.generate_description(meta) == "description"
    assert await tracker.get_additional_checks(meta)
    check.assert_awaited_once_with(
        meta,
        "PTSKIT",
        languages_to_check=["mandarin", "chinese"],
        check_audio=True,
        check_subtitle=True,
    )


def test_ptskit_search_helpers_empty_table() -> None:
    meta = Meta(imdb_info={"imdbID": "tt123"})
    assert Ptskit._search_params(meta) == {
        "incldead": 1,
        "search": "tt123",
        "search_area": 4,
    }
    assert Ptskit._torrent_names("<html></html>") == []


@pytest.mark.asyncio
async def test_ptskit_data_upload_and_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker(monkeypatch)
    meta = Meta(
        name="Release.Name", imdb_info={"imdb_url": "https://imdb.test/title"}
    )
    monkeypatch.setattr(
        tracker, "generate_description", AsyncMock(return_value="desc")
    )
    monkeypatch.setattr(tracker, "get_type", AsyncMock(return_value="404"))

    assert await tracker.get_name(meta) == "Release.Name"
    assert await tracker.get_data(meta) == {
        "name": "Release.Name",
        "url": "https://imdb.test/title",
        "descr": "desc",
        "type": "404",
    }

    tracker.cookie_validator.load_session_cookies = AsyncMock(
        return_value={"sid": "1"}
    )  # type: ignore[method-assign]
    monkeypatch.setattr(
        tracker, "get_data", AsyncMock(return_value={"name": "Release.Name"})
    )
    upload = AsyncMock(return_value=True)
    monkeypatch.setattr(tracker.cookie_auth_uploader, "handle_upload", upload)

    assert await tracker.upload(meta)
    upload.assert_awaited_once()
    kwargs = upload.await_args.kwargs
    assert kwargs["tracker"] == "PTSKIT"
    assert kwargs["torrent_field_name"] == "file"
    assert kwargs["upload_cookies"] == {"sid": "1"}
