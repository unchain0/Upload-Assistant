from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from src.domain_models.release import Meta
from src.integrations.trackers.vultu import Vultu


def _tracker() -> Vultu:
    return Vultu({"DEFAULT": {}, "TRACKERS": {"VULTU": {}}})


def test_music_work_uses_valid_musicbrainz_release_group() -> None:
    meta = Meta(
        category="MUSIC",
        music_release={
            "external_ids": {
                "musicbrainz_release_group": "01234567-89ab-cdef-0123-456789abcdef"
            }
        },
    )

    assert asyncio.run(_tracker().get_work(meta)) == (
        "musicbrainz:release-group:01234567-89ab-cdef-0123-456789abcdef"
    )


def test_music_upload_uses_lame_preset_and_bbcode_notes() -> None:
    tracker = _tracker()
    tracker.get_notes = AsyncMock(return_value="[b]Synthetic notes[/b]")  # type: ignore[method-assign]
    tracker.get_mediainfo = AsyncMock(return_value="")  # type: ignore[method-assign]
    meta = Meta(
        category="MUSIC",
        name="Synthetic Artist - Synthetic Album FLAC",
        tag="-GROUP",
        music_release={
            "tracks": [
                {
                    "encoder_settings": "-V 0",
                    "bitrate_mode": "VBR",
                    "bitrate": 230000,
                }
            ]
        },
    )

    data = asyncio.run(
        tracker.get_data(meta, "musicbrainz:release:release-id")
    )

    assert data["name"].endswith("FLAC")
    assert data["notes"] == "[b]Synthetic notes[/b]"
    assert data["notes_format"] == "bbcode"


def test_music_upload_requires_an_external_album_id() -> None:
    assert not asyncio.run(
        _tracker().get_additional_checks(Meta(category="MUSIC"))
    )
