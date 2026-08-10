# ruff: noqa: S101
from pathlib import Path

import pytest

from src.languages import LanguagesManager
from src.meta import Meta


@pytest.mark.asyncio
async def test_debug_does_not_invent_missing_audio_languages(tmp_path: Path) -> None:
    release_dir = tmp_path / "tmp" / "mandarin-release"
    release_dir.mkdir(parents=True)
    (release_dir / "MEDIAINFO.txt").write_text(
        "General\nFormat : MPEG-4\n\nAudio\nFormat : AAC\n\nText\nLanguage : Chinese\n",
        encoding="utf-8",
    )
    meta = Meta(
        base_dir=str(tmp_path),
        uuid="mandarin-release",
        category="TV",
        unattended=True,
        debug=True,
        tracker_status={},
    )

    await LanguagesManager().process_desc_language(meta)

    assert meta.audio_languages == []
    assert meta.unattended_audio_skip is True
