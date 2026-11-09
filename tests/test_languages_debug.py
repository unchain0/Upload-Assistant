from pathlib import Path

import pytest

from src.domain_models.release import Meta
from src.integrations.media.language_adapter import LanguagesManager


@pytest.mark.asyncio
async def test_debug_does_not_invent_missing_audio_languages(
    tmp_path: Path,
) -> None:
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


@pytest.mark.asyncio
async def test_single_untagged_audio_uses_explicitly_confirmed_language(
    tmp_path: Path,
) -> None:
    release_dir = tmp_path / "tmp" / "mandarin-release"
    release_dir.mkdir(parents=True)
    mediainfo = "General\nFormat : MPEG-4\n\nVideo\nFormat : AVC\n\nAudio\nFormat : AAC\n"
    for filename in ("MEDIAINFO.txt", "MEDIAINFO_CLEANPATH.txt"):
        (release_dir / filename).write_text(mediainfo, encoding="utf-8")
    (release_dir / "MediaInfo.json").write_text("{}", encoding="utf-8")
    meta = Meta(
        base_dir=str(tmp_path),
        uuid="mandarin-release",
        path="The.Rap.of.China.2026.S09E06.1080p.WEB-DL.H264.AAC-PTerWEB.mp4",
        category="TV",
        original_language="zh",
        manual_language="zh",
        mediainfo={
            "media": {
                "track": [
                    {"@type": "General"},
                    {"@type": "Audio", "Format": "AAC"},
                ]
            }
        },
    )

    inferred = await LanguagesManager().apply_confirmed_single_audio_language(
        meta
    )

    assert inferred is True
    assert meta.mediainfo["media"]["track"][1]["Language"] == "Chinese"
    assert "Language                                : Chinese" in (
        release_dir / "MEDIAINFO_CLEANPATH.txt"
    ).read_text(encoding="utf-8")
    assert '"Language": "Chinese"' in (
        release_dir / "MediaInfo.json"
    ).read_text(encoding="utf-8")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "release_name",
    ["Show.S01.DUAL-GROUP", "Show.S01.MULTI-GROUP", "Show.S01.DUBBED-GROUP"],
)
async def test_ambiguous_release_does_not_infer_audio_language(
    tmp_path: Path, release_name: str
) -> None:
    meta = Meta(
        base_dir=str(tmp_path),
        uuid=release_name,
        category="TV",
        original_language="zh",
        manual_language="zh",
        mediainfo={"media": {"track": [{"@type": "Audio", "Format": "AAC"}]}},
    )

    assert (
        await LanguagesManager().apply_confirmed_single_audio_language(meta)
        is False
    )
    assert "Language" not in meta.mediainfo["media"]["track"][0]


@pytest.mark.asyncio
async def test_tmdb_original_language_does_not_fill_an_untagged_audio_track(
    tmp_path: Path,
) -> None:
    meta = Meta(
        base_dir=str(tmp_path),
        uuid="malayalam-release",
        category="TV",
        original_language="ml",
        mediainfo={"media": {"track": [{"@type": "Audio", "Format": "AAC"}]}},
    )

    assert (
        await LanguagesManager().apply_confirmed_single_audio_language(meta)
        is False
    )
    assert "Language" not in meta.mediainfo["media"]["track"][0]
