from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.capybarabr import CapybaraBR


def _tracker() -> CapybaraBR:
    return CapybaraBR({"TRACKERS": {"CAPYBARABR": {}}})


@pytest.mark.asyncio
async def test_capybarabr_game_platform_ids_cover_playstation_and_console() -> (
    None
):
    tracker = _tracker()
    assert await tracker.get_type_id(
        Meta(category="GAME", type="GAME", platform="PlayStation 5")
    ) == {"type_id": "48"}
    assert await tracker.get_type_id(
        Meta(category="GAME", type="GAME", platform="Switch")
    ) == {"type_id": "50"}


def test_capybarabr_game_language_tags_cover_multi_and_fallback() -> None:
    assert (
        CapybaraBR._game_language_tag(
            Meta(languages=["Portuguese", "English"], language="Portuguese")
        )
        == "[MULTI]"
    )
    assert (
        CapybaraBR._game_language_tag(
            Meta(languages=["Spanish"], language="Spanish")
        )
        == "[SPANISH]"
    )
    assert CapybaraBR._is_multilingual_portuguese(["Portuguese", "French"])


def test_capybarabr_preserves_source_group_when_adding_audio_tag() -> None:
    meta = Meta(path="Movie-OLD.DUAL-GROUP.mkv", uuid="", tag="-GROUP")
    assert CapybaraBR._source_group(meta) == "OLD"
    assert (
        CapybaraBR._insert_audio_tag("Movie-GROUP", " DUAL", meta)
        == "Movie-OLD DUAL-GROUP"
    )


_NUREMBERG = "Nuremberg.2025.BluRay.1080p.x264-CYBER.Dual.Tontom.mkv"
_NUREMBERG_NAMED = (
    "Nuremberg.2025.1080p.BluRay.DD5.1.x264-CYBER.DUAL-Tontom.mkv"
)


def _allow_portuguese(
    tracker: CapybaraBR, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        tracker.common,
        "check_portuguese_video_requirements",
        AsyncMock(return_value=True),
    )


@pytest.mark.asyncio
async def test_capybarabr_rejects_a_filename_without_audio_channels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    _allow_portuguese(tracker, monkeypatch)
    meta = Meta(
        category="MOVIE",
        unattended=True,
        filelist=[_NUREMBERG],
        tag="-CYBER.Dual.Tontom",
    )
    assert not await tracker.get_additional_checks(meta)


@pytest.mark.asyncio
async def test_capybarabr_rejects_sidecar_subs_when_they_are_already_embedded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    _allow_portuguese(tracker, monkeypatch)
    subtitle = _NUREMBERG_NAMED.replace(".mkv", ".srt")
    meta = Meta(
        category="MOVIE",
        unattended=True,
        filelist=[_NUREMBERG_NAMED, subtitle],
        subtitle_files=[subtitle],
        mediainfo={
            "media": {
                "track": [
                    {
                        "@type": "Text",
                        "Language": "Portuguese",
                        "Format": "PGS",
                    }
                ]
            }
        },
        tag="-CYBER.DUAL-Tontom",
    )
    assert not await tracker.get_additional_checks(meta)


@pytest.mark.asyncio
async def test_capybarabr_allows_a_named_mkv_without_sidecar_subs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    _allow_portuguese(tracker, monkeypatch)
    meta = Meta(
        category="MOVIE",
        unattended=True,
        filelist=[_NUREMBERG_NAMED],
        tag="-CYBER.DUAL-Tontom",
    )
    assert await tracker.get_additional_checks(meta)


@pytest.mark.asyncio
async def test_capybarabr_still_marks_a_plain_group_as_dual() -> None:
    meta = Meta(
        category="MOVIE",
        name="Movie 2025 1080p BluRay DD5.1 x264-GROUP",
        title="Movie",
        aka="",
        original_language="en",
        audio_languages=["Portuguese", "English"],
        tag="-GROUP",
    )
    result = await _tracker().get_name(meta)
    assert result["name"] == "Movie 2025 1080p BluRay DD5.1 x264 DUAL-GROUP"


@pytest.mark.asyncio
async def test_capybarabr_keeps_dual_inside_the_existing_group_name() -> None:
    expected = "Nuremberg 2025 1080p BluRay DD5.1 x264-CYBER.Dual.Tontom"
    shared = {
        "category": "MOVIE",
        "name": expected,
        "title": "Nuremberg",
        "aka": "",
        "original_language": "en",
        "audio_languages": ["Portuguese", "English"],
        "tag": "-CYBER.Dual.Tontom",
    }
    folder = Meta(
        path="Nuremberg.2025.BluRay.1080p.x264.Dual - By Tontom",
        uuid="",
        **shared,
    )
    filed = Meta(
        path="Nuremberg.2025.BluRay.1080p.x264.Dual - By Tontom",
        uuid="Nuremberg.2025.BluRay.1080p.x264-CYBER.Dual.Tontom.mkv",
        **shared,
    )
    tracker = _tracker()
    assert (await tracker.get_name(folder))["name"] == expected
    assert (await tracker.get_name(filed))["name"] == expected


def test_capybarabr_missing_group_tag_adds_nogroup() -> None:
    assert not CapybaraBR._valid_group_tag(None)
    assert CapybaraBR._ensure_nogroup("Movie-UNKNOWN", None) == "Movie-NoGroup"


@pytest.mark.asyncio
async def test_capybarabr_audiobook_requires_narrator() -> None:
    meta = Meta(category="BOOK", audiobook=True, narrator="")
    assert not await _tracker().get_additional_checks(meta)


@pytest.mark.asyncio
async def test_capybarabr_bluray_remux_encode_settings_require_bdinfo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker.common, "has_bdinfo", lambda _text: False)
    tracker.common.check_portuguese_video_requirements = AsyncMock(
        return_value=True
    )  # type: ignore[method-assign]
    meta = Meta(
        category="MOVIE",
        type="REMUX",
        source="BluRay",
        has_encode_settings=True,
        description="",
        description_link_content="",
        description_file_content="",
    )
    assert not await tracker.get_additional_checks(meta)
    tracker.common.check_portuguese_video_requirements.assert_not_awaited()


def test_capybarabr_keeps_groups_that_only_start_with_an_audio_marker() -> (
    None
):
    meta = Meta(path="Movie-2025.mkv", uuid="", tag="-DUALITY")
    assert CapybaraBR._strip_leading_audio_mark("DUALITY") == "DUALITY"
    assert CapybaraBR._strip_leading_audio_mark("MULTIVERSE") == "MULTIVERSE"
    assert CapybaraBR._strip_leading_audio_mark("DUAL-GROUP") == "GROUP"
    assert CapybaraBR._strip_leading_audio_mark("MULTI.ENG") == "ENG"
    assert CapybaraBR._insert_audio_tag("Movie-DUALITY", " DUAL", meta) == (
        "Movie DUAL-DUALITY"
    )


def test_capybarabr_book_name_keeps_title_before_author() -> None:
    meta = Meta(
        title="O Estrangeiro",
        author="Albert Camus",
        year=2019,
        category="BOOK",
    )
    assert _tracker()._book_name(meta) == "O Estrangeiro - Albert Camus [2019]"


def test_capybarabr_book_name_drops_a_duplicated_author_prefix() -> None:
    meta = Meta(
        title="Albert Camus - O Estrangeiro",
        author="Albert Camus",
        year=2019,
        category="BOOK",
    )
    assert _tracker()._book_name(meta) == "O Estrangeiro - Albert Camus [2019]"
