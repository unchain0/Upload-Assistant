import asyncio

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.homiehelpdesk import HomieHelpDesk


def _tracker() -> HomieHelpDesk:
    return HomieHelpDesk({"DEFAULT": {}, "TRACKERS": {"HOMIEHELPDESK": {}}})


def test_homiehelpdesk_music_upload_uses_musicbrainz_release_id() -> None:
    meta = Meta(
        category="MUSIC",
        music_release={
            "external_ids": {
                "musicbrainz_release": (
                    "c0d17e85-3a36-4dc8-9a88-c188a5e78b0d"
                )
            }
        },
    )

    assert asyncio.run(_tracker().get_additional_data(meta)) == {
        "music_exists_on_musicbrainz": "1",
        "musicbrainz": "c0d17e85-3a36-4dc8-9a88-c188a5e78b0d",
    }


def test_homiehelpdesk_music_upload_uses_discogs_fallback() -> None:
    meta = Meta(
        category="MUSIC",
        music_release={
            "external_ids": {
                "discogs_master_url": (
                    "https://www.discogs.com/master/28700-Example"
                )
            }
        },
    )

    assert asyncio.run(_tracker().get_additional_data(meta)) == {
        "music_exists_on_discogs": "1",
        "discogs": "https://www.discogs.com/master/28700-Example",
    }


def test_homiehelpdesk_requires_music_external_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "src.integrations.trackers.UNIT3D.homiehelpdesk.logger.info",
        lambda *_args, **_kwargs: None,
    )
    meta = Meta(
        category="MUSIC",
        music_release={"external_ids": {"musicbrainz_release": "invalid"}},
    )

    assert not asyncio.run(_tracker().get_additional_checks(meta))


def test_homiehelpdesk_music_type_uses_analyzed_format() -> None:
    meta = Meta(
        category="MUSIC",
        music_release={"fields": {"format": {"value": "FLAC"}}},
    )

    assert asyncio.run(_tracker().get_type_id(meta)) == {"type_id": "7"}
