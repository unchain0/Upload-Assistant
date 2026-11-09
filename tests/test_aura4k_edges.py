from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D import aura4k as aura_module
from src.integrations.trackers.UNIT3D.aura4k import Aura4K


def _tracker() -> Aura4K:
    return Aura4K({"DEFAULT": {}, "TRACKERS": {"AURA4K": {}}})


def _meta(**values: object) -> Meta:
    state: dict[str, object] = {
        "resolution": "2160p",
        "type": "WEBDL",
        "is_disc": "",
        "category": "MOVIE",
        "mediainfo": {"media": {"track": []}},
        "unattended": True,
        "unattended_confirm": False,
        "language_checked": True,
        "audio_languages": ["English"],
        "name": "Movie 2160p WEB-DL",
    }
    state.update(values)
    return Meta(state)


def test_aura4k_rejects_unsupported_type() -> None:
    assert not asyncio.run(
        _tracker().get_additional_checks(_meta(type="WEBRIP"))
    )


def test_aura4k_rejects_language_policy_failure() -> None:
    tracker = _tracker()
    tracker.common.check_language_requirements = AsyncMock(return_value=False)  # type: ignore[method-assign]
    assert not asyncio.run(tracker.get_additional_checks(_meta()))


def test_aura4k_accepts_valid_encoded_bitrate() -> None:
    tracker = _tracker()
    tracker.common.check_language_requirements = AsyncMock(return_value=True)  # type: ignore[method-assign]
    meta = _meta(
        mediainfo={
            "media": {
                "track": [
                    {
                        "@type": "Video",
                        "Encoded_Library_Settings": "rc=crf",
                        "BitRate": "20000000",
                    }
                ]
            }
        }
    )
    assert asyncio.run(tracker.get_additional_checks(meta))


def test_aura4k_media_tracks_reject_non_list_payload() -> None:
    assert (
        Aura4K._media_tracks(_meta(mediainfo={"media": {"track": "bad"}}))
        == []
    )


def test_aura4k_missing_bitrate_rejects_unattended_upload() -> None:
    assert not _tracker()._confirm_missing_bitrate(_meta())


def test_aura4k_missing_bitrate_can_be_confirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        aura_module.cli_ui, "ask_yes_no", lambda *_args, **_kwargs: True
    )
    assert _tracker()._confirm_missing_bitrate(_meta(unattended=False))


def test_aura4k_foreign_language_name(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        aura_module.languages_manager,
        "has_english_language",
        AsyncMock(return_value=False),
    )
    meta = _meta(audio_languages=["French"], name="Movie 2160p WEB-DL")
    assert asyncio.run(tracker.get_name(meta)) == {
        "name": "Movie FRENCH 2160p WEB-DL"
    }


def test_aura4k_bdmv_keeps_foreign_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        aura_module.languages_manager,
        "has_english_language",
        AsyncMock(return_value=False),
    )
    meta = _meta(is_disc="BDMV", audio_languages=["French"])
    assert asyncio.run(tracker.get_name(meta)) == {
        "name": "Movie 2160p WEB-DL"
    }


def test_aura4k_mapping_modes_cover_fallbacks() -> None:
    tracker = _tracker()
    assert (
        asyncio.run(tracker.get_type_id(_meta(), mapping_only=True))["WEBDL"]
        == "4"
    )
    assert (
        asyncio.run(tracker.get_resolution_id(_meta(), reverse=True))["2"]
        == "2160p"
    )
    assert asyncio.run(
        tracker.get_type_id(_meta(type="UNKNOWN"), type="")
    ) == {"type_id": "0"}
    assert asyncio.run(
        tracker.get_resolution_id(_meta(resolution="1080p"), resolution="")
    ) == {"resolution_id": "10"}
