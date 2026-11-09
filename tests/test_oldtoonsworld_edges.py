from __future__ import annotations

import asyncio

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D import oldtoonsworld as otw_module
from src.integrations.trackers.UNIT3D.oldtoonsworld import OldToonsWorld


def _tracker() -> OldToonsWorld:
    return OldToonsWorld({"DEFAULT": {}, "TRACKERS": {"OLDTOONSWORLD": {}}})


def _meta(**values: object) -> Meta:
    state: dict[str, object] = {
        "combined_genres": ["Animation"],
        "keywords": [],
        "unattended": True,
        "unattended_confirm": False,
        "type": "WEBDL",
        "is_disc": "",
        "tag": "-GROUP",
        "name": "Example AKA 1080p WEB-DL AAC 2.0",
        "source": "WEB",
        "resolution": "1080p",
        "aka": "AKA",
        "video_codec": "H.264",
        "audio": "AAC 2.0",
        "category": "MOVIE",
        "year": 2025,
        "imdb_info": {},
        "tvdb_episode_data": {},
        "no_year": False,
        "search_year": "",
        "title": "Example",
    }
    state.update(values)
    return Meta(state)


def test_oldtoonsworld_genre_policy_rejects_unattended_mismatch() -> None:
    assert not asyncio.run(
        _tracker().get_additional_checks(_meta(combined_genres=["Drama"]))
    )


def test_oldtoonsworld_adult_policy_rejects_unattended_upload() -> None:
    assert not asyncio.run(
        _tracker().get_additional_checks(_meta(keywords=["adult"]))
    )


def test_oldtoonsworld_adult_policy_can_be_confirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        otw_module.cli_ui, "ask_yes_no", lambda *_args, **_kwargs: True
    )
    meta = _meta(keywords=["adult"], unattended=False)
    assert asyncio.run(_tracker().get_additional_checks(meta))


def test_oldtoonsworld_reality_policy_can_be_declined(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        otw_module.cli_ui, "ask_yes_no", lambda *_args, **_kwargs: False
    )
    meta = _meta(keywords=["game show"], unattended=False)
    assert not asyncio.run(_tracker().get_additional_checks(meta))


def test_oldtoonsworld_restricted_group_needs_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        otw_module.cli_ui, "ask_yes_no", lambda *_args, **_kwargs: True
    )
    meta = _meta(type="ENCODE", tag="EVO", unattended=False)
    assert asyncio.run(_tracker().get_additional_checks(meta))


def test_oldtoonsworld_genre_list_and_string_normalization() -> None:
    tracker = _tracker()
    assert tracker._normalized_genres([" Animation ", "", "Family"]) == [
        "Animation",
        "Family",
    ]
    assert tracker._normalized_genres("Animation, Family") == [
        "Animation",
        "Family",
    ]
    assert tracker._genre_policy_passes(_meta(), ["Family"])


def test_oldtoonsworld_string_values_cover_empty_and_scalar() -> None:
    assert OldToonsWorld._string_values(None) == []
    assert OldToonsWorld._string_values("adult") == ["adult"]


def test_oldtoonsworld_type_mapping_modes_and_disc_variants() -> None:
    tracker = _tracker()
    assert (
        asyncio.run(tracker.get_type_id(_meta(), mapping_only=True))["WEBDL"]
        == "4"
    )
    assert (
        asyncio.run(tracker.get_type_id(_meta(), reverse=True))["4"] == "WEBDL"
    )
    assert asyncio.run(tracker.get_type_id(_meta(is_disc="BDMV"))) == {
        "type_id": "1"
    }
    assert asyncio.run(tracker.get_type_id(_meta(is_disc="DVD"))) == {
        "type_id": "7"
    }
    assert asyncio.run(
        tracker.get_type_id(_meta(type="UNKNOWN"), type="ENCODE")
    ) == {"type_id": "3"}


def test_oldtoonsworld_dvd_name_details() -> None:
    meta = _meta(
        name="Example AKA DVD AAC 2.0",
        is_disc="DVD",
        source="DVD",
        resolution="480p",
        video_codec="MPEG-2",
        audio="AAC 2.0",
    )
    assert asyncio.run(_tracker().get_name(meta)) == {
        "name": "Example 480p DVD MPEG-2 AAC 2.0"
    }


def test_oldtoonsworld_tv_year_prefers_tmdb_and_fallbacks() -> None:
    tracker = _tracker()
    direct = _meta(
        category="TV", name="Example S01", aka="", title="Example", year=2024
    )
    assert asyncio.run(tracker.get_name(direct))["name"] == "Example 2024 S01"

    fallback = _meta(
        category="TV",
        name="Example S01",
        aka="",
        title="Example",
        year=None,
        imdb_info={"year": "2023"},
        tvdb_episode_data={"series_year": "2022"},
    )
    assert (
        asyncio.run(tracker.get_name(fallback))["name"] == "Example 2022 S01"
    )

    no_year = _meta(
        category="TV",
        name="Example S01",
        aka="",
        title="Example",
        year=None,
        imdb_info={},
        tvdb_episode_data={},
    )
    assert asyncio.run(tracker.get_name(no_year))["name"] == "Example S01"


def test_oldtoonsworld_tv_year_skipped_when_search_year_present() -> None:
    meta = _meta(
        category="TV",
        name="Example S01",
        aka="",
        title="Example",
        search_year="2025",
    )
    assert asyncio.run(_tracker().get_name(meta))["name"] == "Example S01"
