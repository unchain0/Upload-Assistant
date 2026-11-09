from __future__ import annotations

import asyncio

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D import torrenteros as torrenteros_module
from src.integrations.trackers.UNIT3D.torrenteros import Torrenteros


def _tracker() -> Torrenteros:
    return Torrenteros({"DEFAULT": {}, "TRACKERS": {"TORRENTEROS": {}}})


def test_torrenteros_disc_audio_prompt_adds_selected_suffix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        torrenteros_module.cli_ui, "ask_string", lambda *_args, **_kwargs: "2"
    )
    meta = Meta(
        name_notag="Movie 1080p",
        tag="-GROUP",
        is_disc="BDMV",
        audio_languages=["Spanish"],
        subtitle_languages=[],
        unattended=False,
        unattended_confirm=False,
    )
    assert tracker.build_name(meta) == "Movie 1080p Latino-GROUP"


def test_torrenteros_disc_subtitle_prompt_defaults_to_castellano(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        torrenteros_module.cli_ui,
        "ask_string",
        lambda *_args, **_kwargs: "invalid",
    )
    meta = Meta(
        name_notag="Movie 1080p",
        tag="",
        is_disc="BDMV",
        audio_languages=[],
        subtitle_languages=["Spanish"],
        unattended=False,
        unattended_confirm=False,
    )
    assert tracker.build_name(meta) == "Movie 1080p Castellano Subs"


def test_torrenteros_file_audio_and_subtitle_detection() -> None:
    tracker = _tracker()
    audio_meta = Meta(
        name_notag="Movie 1080p",
        tag="",
        is_disc="",
        mediainfo={
            "media": {"track": [{"@type": "Audio", "Language": "es-MX"}]}
        },
    )
    assert tracker.build_name(audio_meta) == "Movie 1080p Latino"

    subtitle_meta = Meta(
        name_notag="Movie 1080p",
        tag="",
        is_disc="",
        mediainfo={"media": {"track": [{"@type": "Text", "Language": "spa"}]}},
    )
    assert tracker.build_name(subtitle_meta) == "Movie 1080p Castellano Subs"


def test_torrenteros_media_tracks_rejects_non_list_payload() -> None:
    assert (
        Torrenteros._media_tracks(Meta(mediainfo={"media": {"track": "bad"}}))
        == []
    )


def test_torrenteros_additional_checks_accept_spanish_audio() -> None:
    tracker = _tracker()
    meta = Meta(
        language_checked=True,
        audio_languages=["Spanish"],
        subtitle_languages=[],
    )
    assert asyncio.run(tracker.get_additional_checks(meta))


def test_torrenteros_additional_checks_subtitle_only_attended(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        torrenteros_module.cli_ui, "ask_yes_no", lambda *_args, **_kwargs: True
    )
    meta = Meta(
        language_checked=True,
        audio_languages=[],
        subtitle_languages=["Spanish"],
        unattended=False,
        unattended_confirm=False,
    )
    assert asyncio.run(tracker.get_additional_checks(meta))


def test_torrenteros_additional_checks_processes_missing_language_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()

    async def process(meta: Meta, **_kwargs: object) -> None:
        meta.audio_languages = ["Spanish"]
        meta.language_checked = True

    monkeypatch.setattr(
        torrenteros_module.languages_manager, "process_desc_language", process
    )
    meta = Meta(
        language_checked=False, audio_languages=[], subtitle_languages=[]
    )
    assert asyncio.run(tracker.get_additional_checks(meta))
