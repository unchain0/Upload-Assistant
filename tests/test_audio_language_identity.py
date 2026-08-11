# ruff: noqa: S101
import pytest

from src.audio import AudioManager
from src.meta import Meta


@pytest.mark.asyncio
async def test_display_name_and_iso_code_are_the_same_original_audio_language() -> None:
    meta = Meta(original_language="Malay", trackers=["PEERGARDEN"], type="WEBDL")
    mediainfo = {
        "media": {
            "track": [
                {
                    "@type": "Audio",
                    "Format": "AAC",
                    "Format_AdditionalFeatures": "LC",
                    "Channels": "2",
                    "Language": "ms",
                }
            ]
        }
    }

    await AudioManager({"DEFAULT": {}, "TRACKERS": {}}).get_audio_v2(mediainfo, meta, None)

    assert meta.bloated is False
