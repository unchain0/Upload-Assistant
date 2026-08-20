from __future__ import annotations

import asyncio

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.blutopia import Blutopia


def _tracker() -> Blutopia:
    return Blutopia({"DEFAULT": {}, "TRACKERS": {"BLUTOPIA": {}}})


def _meta(**values: object) -> Meta:
    state: dict[str, object] = {
        "category": "MOVIE",
        "name": "Local 2020 1080p WEB-DL H264-GROUP",
        "title": "Local",
        "aka": "",
        "year": 2020,
        "type": "WEBDL",
        "container": "mkv",
        "hdr": "",
        "is_disc": "",
        "tag": "-GROUP",
        "unattended": True,
        "unattended_confirm": False,
        "valid_mi_settings": True,
        "imdb_info": {},
        "webdv": False,
        "tracker_status": {"BLUTOPIA": {}},
        "edition": "",
        "episode_title": "",
        "resolution": "1080p",
        "no_aka": False,
    }
    state.update(values)
    return Meta(state)


def test_blutopia_hdtv_allows_transport_stream_and_reaches_success() -> None:
    meta = _meta(type="HDTV", container="ts")
    assert asyncio.run(_tracker().get_additional_checks(meta))


def test_blutopia_derived_dolby_vision_confirmation_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    declined = _meta(type="REMUX", hdr="DV HDR", unattended=False)
    monkeypatch.setattr("src.integrations.trackers.UNIT3D.blutopia.cli_ui.ask_yes_no", lambda *_args, **_kwargs: False)
    assert not asyncio.run(tracker.get_additional_checks(declined))

    answers = iter((True, True))
    monkeypatch.setattr("src.integrations.trackers.UNIT3D.blutopia.cli_ui.ask_yes_no", lambda *_args, **_kwargs: next(answers))
    accepted = _meta(type="REMUX", hdr="DV HDR", unattended=False)
    assert asyncio.run(tracker.get_additional_checks(accepted))
    assert accepted.tracker_status["BLUTOPIA"]["other"] is True


def test_blutopia_raw_only_group_confirmation_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    monkeypatch.setattr("src.integrations.trackers.UNIT3D.blutopia.cli_ui.ask_yes_no", lambda *_args, **_kwargs: True)
    assert asyncio.run(tracker.get_additional_checks(_meta(type="ENCODE", tag="AOC", unattended=False)))

    monkeypatch.setattr("src.integrations.trackers.UNIT3D.blutopia.cli_ui.ask_yes_no", lambda *_args, **_kwargs: False)
    assert not asyncio.run(tracker.get_additional_checks(_meta(type="ENCODE", tag="AOC", unattended=False)))
    assert not asyncio.run(tracker.get_additional_checks(_meta(type="ENCODE", tag="AOC", unattended=True, unattended_confirm=False)))


def test_blutopia_name_uses_imdb_title_aka_and_derived_layer_marker() -> None:
    tracker = _tracker()
    meta = _meta(
        name="Local AKA 2020 1080p WEB-DL H264-GROUP",
        aka="AKA",
        imdb_info={"title": "Canonical", "aka": "Alternate", "year": "2021"},
        tracker_status={"BLUTOPIA": {"other": True}},
    )
    assert asyncio.run(tracker.get_name(meta)) == {"name": "Canonical AKA Alternate 2021 1080p DVP5/DVP8 WEB-DL H264-GROUP"}


def test_blutopia_other_flag_forces_fanres_category() -> None:
    tracker = _tracker()
    meta = _meta(tracker_status={"BLUTOPIA": {"other": True}})
    assert asyncio.run(tracker.get_category_id(meta)) == {"category_id": "3"}
