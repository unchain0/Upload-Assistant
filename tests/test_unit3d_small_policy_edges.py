from __future__ import annotations

import asyncio
import copy
from unittest.mock import AsyncMock

import cli_ui
import pytest

from data.example_config import config as example_config
from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.asiancinema import AsianCinema
from src.integrations.trackers.UNIT3D.locadora import Locadora
from src.integrations.trackers.UNIT3D.racing4everyone import Racing4Everyone
from src.integrations.trackers.UNIT3D.skipthecommercials import SkipTheCommercials
from src.integrations.trackers.UNIT3D.theoldschool import TheOldSchool
from src.integrations.trackers.UNIT3D.torrentdesi import DesiTorrents


def _config() -> dict:
    config = copy.deepcopy(example_config)
    default = config.setdefault("DEFAULT", {})
    default.setdefault("tmdb_api", "test-key")
    for tracker in ("LOCADORA", "RACING4EVERYONE", "ASIANCINEMA", "DESITORRENTS", "SKIPTHECOMMERCIALS", "THEOLDSCHOOL"):
        values = config.setdefault("TRACKERS", {}).setdefault(tracker, {})
        values.setdefault("api_key", "test-key")
        values.setdefault("announce_url", "https://tracker.invalid/announce")
    return config


def test_locadora_region_id_returns_resolved_region() -> None:
    tracker = Locadora(_config())
    tracker.common.unit3d_region_ids = AsyncMock(return_value="14")  # type: ignore[method-assign]

    assert asyncio.run(tracker.get_region_id(Meta(region="USA"))) == {"region_id": "14"}


def test_racing4everyone_documentary_categories() -> None:
    tracker = Racing4Everyone(_config())

    assert asyncio.run(tracker.get_category_id(Meta(category="MOVIE", genre_ids="99"))) == {"category_id": "66"}
    assert asyncio.run(tracker.get_category_id(Meta(category="TV", genre_ids="99"))) == {"category_id": "2"}


def test_asiancinema_rejects_non_asian_origin_and_formats_single_subtitle() -> None:
    tracker = AsianCinema(_config())

    assert not asyncio.run(tracker.get_additional_checks(Meta(origin_country=["US"])))
    assert tracker.get_subs_tag(Meta(subtitle_languages=["French"])) == " [Fre subs only]"


def test_asiancinema_dvd_name_marks_mpeg_audio() -> None:
    tracker = AsianCinema(_config())
    meta = Meta(
        name="Example 1080p DVD PAL 2.0",
        title="Example",
        original_title="Example",
        aka="",
        audio="2.0",
        channels="2.0",
        source="PAL",
        is_disc="DVD",
        resolution="1080p",
        subtitle_languages=["English"],
    )

    assert "MPEG 2.0" in asyncio.run(tracker.get_name(meta))["name"]


def test_torrentdesi_bd25_uses_bd25_type() -> None:
    tracker = DesiTorrents(_config())
    meta = Meta(type="DISC", disctype="BD25", uhd=False)

    assert asyncio.run(tracker.get_type_id(meta)) == {"type_id": "4"}


def _adult_tv_meta(*, unattended: bool, unattended_confirm: bool = False) -> Meta:
    return Meta(
        category="TV",
        keywords=["porn", "drama"],
        combined_genres="",
        unattended=unattended,
        unattended_confirm=unattended_confirm,
    )


def test_skipthecommercials_adult_confirmation_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = SkipTheCommercials(_config())
    monkeypatch.setattr(cli_ui, "ask_yes_no", lambda *_args, **_kwargs: True)
    assert asyncio.run(tracker.get_additional_checks(_adult_tv_meta(unattended=False)))

    monkeypatch.setattr(cli_ui, "ask_yes_no", lambda *_args, **_kwargs: False)
    assert not asyncio.run(tracker.get_additional_checks(_adult_tv_meta(unattended=False)))
    assert not asyncio.run(tracker.get_additional_checks(_adult_tv_meta(unattended=True)))


def test_theoldschool_vostfr_pack_and_language_rejection() -> None:
    tracker = TheOldSchool(_config())
    meta = Meta(category="TV", tv_pack=True, tag="-VOSTFR")
    assert asyncio.run(tracker.get_category_id(meta)) == {"category_id": "9"}

    tracker.common.check_language_requirements = AsyncMock(return_value=False)  # type: ignore[method-assign]
    assert not asyncio.run(tracker.get_additional_checks(Meta()))


def test_theoldschool_rehash_cooldown_invalid_and_positive(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config()
    tracker = TheOldSchool(config)
    create_torrent = AsyncMock()
    sleep = AsyncMock()
    monkeypatch.setattr("src.integrations.trackers.UNIT3D.theoldschool.TorrentCreator.create_torrent", create_torrent)
    monkeypatch.setattr("src.integrations.trackers.UNIT3D.theoldschool.asyncio.sleep", sleep)
    meta = Meta(
        keep_nfo=True,
        scene=False,
        scene_name="",
        basename_no_ext="Example Release",
        path=str(tmp_path / "example.mkv"),
    )

    config["DEFAULT"]["rehash_cooldown"] = "invalid"
    asyncio.run(tracker.get_name(meta))
    sleep.assert_not_awaited()

    config["DEFAULT"]["rehash_cooldown"] = 2
    asyncio.run(tracker.get_name(meta))
    sleep.assert_awaited_once_with(2)
    assert create_torrent.await_count == 2
