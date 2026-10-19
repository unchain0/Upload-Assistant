from __future__ import annotations

import asyncio
import copy
from unittest.mock import AsyncMock

import cli_ui
import pytest

from data.example_config import config as example_config
from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.skipthecommercials import (
    SkipTheCommercials,
)


def _config() -> dict:
    config = copy.deepcopy(example_config)
    config.setdefault("DEFAULT", {}).setdefault("tmdb_api", "test-key")
    tracker = config.setdefault("TRACKERS", {}).setdefault(
        "SKIPTHECOMMERCIALS", {}
    )
    tracker.setdefault("api_key", "test-key")
    tracker.setdefault("announce_url", "https://tracker.invalid/announce")
    return config


def test_skipthecommercials_type_mapping_edges() -> None:
    tracker = SkipTheCommercials(_config())
    assert asyncio.run(tracker.get_type_id(Meta(type="REMUX"))) == {
        "type_id": "2"
    }
    assert asyncio.run(
        tracker.get_type_id(Meta(type="WEBDL", tv_pack=True, sd=0))
    ) == {"type_id": "13"}
    assert asyncio.run(
        tracker.get_type_id(Meta(type="WEBDL", tv_pack=True, sd=1))
    ) == {"type_id": "14"}
    assert asyncio.run(
        tracker.get_type_id(Meta(type="REMUX", tv_pack=True, sd=0))
    ) == {"type_id": "18"}
    assert asyncio.run(
        tracker.get_type_id(Meta(type="REMUX", tv_pack=True, sd=1))
    ) == {"type_id": "17"}


def test_skipthecommercials_non_tv_and_normal_tv_edges() -> None:
    tracker = SkipTheCommercials(_config())
    assert not asyncio.run(
        tracker.get_additional_checks(Meta(category="MOVIE", unattended=False))
    )
    assert not asyncio.run(
        tracker.get_additional_checks(Meta(category="MOVIE", unattended=True))
    )
    assert asyncio.run(
        tracker.get_additional_checks(
            Meta(category="TV", keywords=["drama"], combined_genres="Comedy")
        )
    )


def test_skipthecommercials_unattended_confirm_allows_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = SkipTheCommercials(_config())
    ask = AsyncMock()
    _ = ask
    monkeypatch.setattr(cli_ui, "ask_yes_no", lambda *_args, **_kwargs: True)
    meta = Meta(
        category="TV",
        keywords=["porn"],
        combined_genres="",
        unattended=True,
        unattended_confirm=True,
    )
    assert tracker._adult_confirmation_allowed(meta)
    assert asyncio.run(tracker.get_additional_checks(meta))
