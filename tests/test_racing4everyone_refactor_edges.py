from __future__ import annotations

import asyncio
import copy
from typing import Any

import pytest

from data.example_config import config as example_config
from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D import racing4everyone as r4e_module
from src.integrations.trackers.UNIT3D.racing4everyone import Racing4Everyone


def _config() -> dict[str, Any]:
    config = copy.deepcopy(example_config)
    config.setdefault("DEFAULT", {}).setdefault("tmdb_api", "test-key")
    values = config.setdefault("TRACKERS", {}).setdefault(
        "RACING4EVERYONE", {}
    )
    values["api_key"] = " test-key "
    values.setdefault("announce_url", "https://tracker.invalid/announce")
    return config


def _tracker() -> Racing4Everyone:
    return Racing4Everyone(_config())


def test_racing4everyone_category_mapping_edges() -> None:
    tracker = _tracker()
    assert asyncio.run(
        tracker.get_category_id(Meta(category="MOVIE", genre_ids="12"))
    ) == {"category_id": "70"}
    assert asyncio.run(
        tracker.get_category_id(Meta(category="MOVIE", genre_ids=" 99, 12 "))
    ) == {"category_id": "66"}
    assert asyncio.run(
        tracker.get_category_id(Meta(category="TV", genre_ids="12"))
    ) == {"category_id": "79"}
    assert asyncio.run(
        tracker.get_category_id(Meta(category="TV", genre_ids="99"))
    ) == {"category_id": "2"}
    assert asyncio.run(
        tracker.get_category_id(Meta(category="OTHER", genre_ids=""))
    ) == {"category_id": "24"}
    assert tracker._genre_ids(Meta(genre_ids=None)) == set()


def test_racing4everyone_type_and_resolution_mapping_edges() -> None:
    tracker = _tracker()
    meta = Meta(type="REMUX", resolution="1080p")

    type_mapping = asyncio.run(tracker.get_type_id(meta, mapping_only=True))
    assert type_mapping["DISC"] == "1"
    type_reverse = asyncio.run(tracker.get_type_id(meta, reverse=True))
    assert type_reverse["2"] == "REMUX"
    assert asyncio.run(tracker.get_type_id(meta)) == {"type_id": "2"}
    assert asyncio.run(tracker.get_type_id(meta, type="WEBDL")) == {
        "type_id": "4"
    }
    assert asyncio.run(tracker.get_type_id(meta, type="UNKNOWN")) == {
        "type_id": "0"
    }

    resolution_mapping = asyncio.run(
        tracker.get_resolution_id(meta, mapping_only=True)
    )
    assert resolution_mapping["4320p"] == "2160p"
    resolution_reverse = asyncio.run(
        tracker.get_resolution_id(meta, reverse=True)
    )
    assert resolution_reverse["1080i"] == "1080i"
    assert asyncio.run(tracker.get_resolution_id(meta)) == {
        "resolution_id": "1080p"
    }
    assert asyncio.run(
        tracker.get_resolution_id(meta, resolution="2160p")
    ) == {"resolution_id": "2160p"}
    assert asyncio.run(
        tracker.get_resolution_id(meta, resolution="UNKNOWN")
    ) == {"resolution_id": "SD"}


def test_racing4everyone_empty_flag_methods() -> None:
    tracker = _tracker()
    meta = Meta()
    assert asyncio.run(tracker.get_personal_release(meta)) == {}
    assert asyncio.run(tracker.get_internal(meta)) == {}
    assert asyncio.run(tracker.get_featured(meta)) == {}
    assert asyncio.run(tracker.get_free(meta)) == {}
    assert asyncio.run(tracker.get_doubleup(meta)) == {}
    assert asyncio.run(tracker.get_sticky(meta)) == {}


@pytest.mark.asyncio
async def test_racing4everyone_search_existing_builds_params_and_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            captured["raised"] = True

        def json(self) -> dict[str, Any]:
            return {
                "data": [
                    {"attributes": {"name": "Race S01 Special"}},
                    {"attributes": {}},
                ]
            }

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            captured["client_kwargs"] = kwargs

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(
            self, *, url: str, params: dict[str, Any]
        ) -> FakeResponse:
            captured["url"] = url
            captured["params"] = params
            return FakeResponse()

    monkeypatch.setattr(r4e_module.httpx, "AsyncClient", FakeClient)
    tracker = _tracker()
    meta = Meta(
        category="TV",
        genre_ids="12",
        type="WEBDL",
        tmdb=123,
        season="S01",
        edition=" Special",
    )

    result = await tracker.search_existing(meta)

    assert captured["url"] == tracker.search_url
    assert captured["client_kwargs"] == {"timeout": 5.0}
    assert captured["params"] == {
        "api_token": "test-key",
        "tmdb": 123,
        "categories[]": "79",
        "types[]": "4",
        "name": "S01 Special",
    }
    assert captured["raised"] is True
    assert result == [
        {
            "name": "Race S01 Special",
            "files": "Race S01 Special",
            "size": 0,
            "link": "",
            "file_count": 0,
            "download": "",
        },
        {
            "name": "",
            "files": "",
            "size": 0,
            "link": "",
            "file_count": 0,
            "download": "",
        },
    ]
