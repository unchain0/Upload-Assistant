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
        screens=3,
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


def test_capybarabr_book_name_drops_a_trailing_author() -> None:
    meta = Meta(
        title="Diário Estoico: 366 Lições Sobre Sabedoria - Ryan Holiday",
        author="Ryan Holiday",
        year=2021,
        category="BOOK",
    )
    assert _tracker()._book_name(meta) == (
        "Diário Estoico: 366 Lições sobre Sabedoria - Ryan Holiday [2021]"
    )


def test_capybarabr_book_name_strips_a_truncated_author_fragment() -> None:
    meta = Meta(
        title="Grokking Algorithms, Second Edition - Aditya",
        author="Aditya Y. Bhargava",
        year=2024,
        category="BOOK",
    )
    assert _tracker()._book_name(meta) == (
        "Grokking Algorithms, Second Edition - Aditya Y. Bhargava [2024]"
    )


def test_capybarabr_book_name_uses_the_full_author_name() -> None:
    meta = Meta(
        title="Aditya Y Bhargava - Grokking Algorithms",
        author="Aditya Y. Bhargava",
        year=2024,
        category="BOOK",
    )
    assert _tracker()._book_name(meta) == (
        "Grokking Algorithms - Aditya Y. Bhargava [2024]"
    )


def test_capybarabr_book_name_keeps_a_single_word_author_once() -> None:
    meta = Meta(
        title="Meditações - Marco Aurélio",
        author="Marco Aurélio",
        year=2020,
        category="BOOK",
    )
    assert _tracker()._book_name(meta) == "Meditações - Marco Aurélio [2020]"


def test_capybarabr_book_name_strips_every_repeated_author_segment() -> None:
    meta = Meta(
        title="Ryan Holiday - Ryan Holiday - Ryan Holiday - Ryan Holiday - O Obstáculo é o Caminho",
        author="Ryan Holiday",
        year=2014,
        category="BOOK",
    )
    assert _tracker()._book_name(meta) == (
        "O Obstáculo É o Caminho - Ryan Holiday [2014]"
    )


def _video_tracker(*, api_key: str = "test-key") -> CapybaraBR:
    tracker = _tracker()
    tracker.api_key = api_key
    return tracker


def _video_meta(**values: object) -> Meta:
    defaults: dict[str, object] = {
        "category": "TV",
        "dual_audio": False,
        "path": "Show.S03.1080p.AMZN.WEB-DL.DDP5.1.H.264-FLUX.mkv",
        "name": "Show.S03.1080p.AMZN.WEB-DL.DDP5.1.H.264-FLUX",
    }
    defaults.update(values)
    return Meta(**defaults)


@pytest.mark.asyncio
async def test_capybarabr_skips_mono_when_a_dual_release_exists() -> None:
    tracker = _video_tracker()
    tracker.search_existing = AsyncMock(  # type: ignore[method-assign]
        return_value=[{"name": "Show.S03.1080p.BluRay.DUAL-FLUX"}]
    )
    assert await tracker._dual_release_allowed(_video_meta()) is False


@pytest.mark.asyncio
async def test_capybarabr_allows_dual_when_a_dual_release_exists() -> None:
    tracker = _video_tracker()
    tracker.search_existing = AsyncMock(  # type: ignore[method-assign]
        return_value=[{"name": "Show.S03.1080p.BluRay.DUAL-FLUX"}]
    )
    meta = _video_meta(dual_audio=True)
    assert await tracker._dual_release_allowed(meta) is True
    tracker.search_existing.assert_not_awaited()


@pytest.mark.asyncio
async def test_capybarabr_allows_release_without_a_dual_match() -> None:
    tracker = _video_tracker()
    tracker.search_existing = AsyncMock(  # type: ignore[method-assign]
        return_value=[{"name": "Show.S03.1080p.BluRay-GROUP"}]
    )
    assert await tracker._dual_release_allowed(_video_meta()) is True


@pytest.mark.asyncio
async def test_capybarabr_fails_open_when_the_search_raises() -> None:
    tracker = _video_tracker()
    tracker.search_existing = AsyncMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("search down")
    )
    assert await tracker._dual_release_allowed(_video_meta()) is True


@pytest.mark.asyncio
async def test_capybarabr_fails_open_without_an_api_key() -> None:
    tracker = _video_tracker(api_key="")
    tracker.search_existing = AsyncMock()  # type: ignore[method-assign]
    assert await tracker._dual_release_allowed(_video_meta()) is True
    tracker.search_existing.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name",
    [
        "Show.S03.1080p.BluRay.Dual.Audio-FLUX",
        "Show.S03.1080p.BluRay.DUAL-FLUX",
        "Show.S03.1080p.BluRay.MULTI-FLUX",
    ],
)
async def test_capybarabr_detects_dual_and_multi_separators(name: str) -> None:
    tracker = _video_tracker()
    tracker.search_existing = AsyncMock(  # type: ignore[method-assign]
        return_value=[{"name": name}]
    )
    assert await tracker._dual_release_allowed(_video_meta()) is False


def _video_meta(**values: object) -> Meta:
    defaults: dict[str, object] = {
        "category": "MOVIE",
        "type": "WEBDL",
        "resolution": "1080p",
        "screens": 3,
        "name": "Movie.2024.1080p.WEB-DL.DDP5.1.H.264-GROUP",
        "filelist": ["Movie.2024.1080p.WEB-DL.DDP5.1.H.264-GROUP.mkv"],
    }
    defaults.update(values)
    return Meta(**defaults)


def test_capybarabr_blocks_compressed_media() -> None:
    for name in ("release.rar", "release.zip", "release.7z", "release.r00", "release.001"):
        meta = _video_meta(filelist=["Movie.2024.mkv", name])
        assert CapybaraBR._compressed_media(meta) is True
    book = _video_meta(category="BOOK", filelist=["book.epub", "book.cbz"])
    assert CapybaraBR._compressed_media(book) is False


def test_capybarabr_sidecar_matches_video_stem_and_language_suffix() -> None:
    meta = _video_meta(
        filelist=["Show.S01E01.1080p.mkv"],
        subtitle_files=["Show.S01E01.1080p.pt-BR.srt"],
    )
    assert CapybaraBR._orphan_sidecar_name(meta) == ""
    meta = _video_meta(
        filelist=["Show.S01E01.1080p.mkv"],
        subtitle_files=["Show.S01E01.1080p.eng.ass"],
    )
    assert CapybaraBR._orphan_sidecar_name(meta) == ""


def test_capybarabr_orphan_sidecar_is_reported() -> None:
    meta = _video_meta(
        filelist=["Show.S01E01.1080p.mkv"],
        subtitle_files=["Show.S01E02.1080p.srt"],
    )
    assert CapybaraBR._orphan_sidecar_name(meta) == "Show.S01E02.1080p.srt"
    assert "no matching video" in CapybaraBR._filename_skip_reason(meta)


def test_capybarabr_sidecar_without_videos_fails_open() -> None:
    meta = _video_meta(filelist=["Show.S01E01.srt"], subtitle_files=[])
    assert CapybaraBR._orphan_sidecar_name(meta) == ""


def test_capybarabr_screenshot_minimum_for_movies_and_shows() -> None:
    tracker = _tracker()
    assert tracker._screenshot_allowed(_video_meta(screens=3)) is True
    assert tracker._screenshot_allowed(_video_meta(screens=2)) is False
    assert tracker._screenshot_allowed(_video_meta(category="BOOK", screens=0)) is True
    assert tracker._screenshot_allowed(_video_meta(screens=None)) is True


def test_capybarabr_compressed_media_is_allowed_only_for_games_and_programs() -> None:
    tracker = _tracker()
    assert tracker._payload_skip_reason(
        _video_meta(category="PROGRAMS", filelist=["crack.rar"])
    ) == ""
    assert tracker._payload_skip_reason(
        _video_meta(category="GAME", filelist=["crack.zip"])
    ) == ""
    assert "compressed archives" in tracker._payload_skip_reason(
        _video_meta(category="BOOK", filelist=["book.rar"])
    )


def test_capybarabr_blocks_nfo_sample_txt_and_executables() -> None:
    tracker = _tracker()
    for name in ("release.nfo", "sample.mkv", "notes.txt", "setup.exe", "run.sh"):
        assert "not allowed" in tracker._payload_skip_reason(
            _video_meta(filelist=["Movie.mkv", name])
        )


def test_capybarabr_blocks_multipart_releases_but_allows_full_disc() -> None:
    tracker = _tracker()
    for name in ("Movie.part1.mkv", "Show.CD2.mkv", "Movie.Disc1.mkv"):
        assert "split into parts" in tracker._payload_skip_reason(
            _video_meta(filelist=[name])
        )
    assert "authored Blu-ray" in tracker._payload_skip_reason(
        _video_meta(is_disc="BDMV", filelist=["Movie.Disc1.m2ts"])
    )


def test_capybarabr_blocks_extras_outside_a_full_disc() -> None:
    tracker = _tracker()
    assert "bonus discs" in tracker._payload_skip_reason(
        _video_meta(filelist=["Movie.mkv", "Extras/featurette.mkv"])
    )
    assert tracker._payload_skip_reason(
        _video_meta(filelist=["Movie.mkv", "Extras/deleted.scenes.mkv"])
    ) != ""


def test_capybarabr_blocks_forbidden_sources_and_gpu_encodes() -> None:
    tracker = _tracker()
    for name in ("Movie.2024.HDTS.mkv", "Movie.2024.CAM.mkv", "Movie.2024.DCP.mkv"):
        assert "CAM, TS, HDTS and DCP" in tracker._payload_skip_reason(
            _video_meta(filelist=[name])
        )
    assert "GPU encodes" in tracker._payload_skip_reason(
        _video_meta(video_encode="NVENC")
    )
    assert "AV1" in tracker._payload_skip_reason(_video_meta(video_codec="AV1"))


def test_capybarabr_blocks_authored_discs_and_low_bitrate() -> None:
    tracker = _tracker()
    assert "authored Blu-ray" in tracker._payload_skip_reason(
        _video_meta(is_disc="BDMV", filelist=["BDMV/index.bdmv"])
    )
    assert "BRRip" in tracker._payload_skip_reason(
        _video_meta(resolution="1080p", video_bitrate=4000)
    )
    assert "BRRip" in tracker._payload_skip_reason(
        _video_meta(resolution="720p", video_bitrate=4000)
    )
    assert tracker._payload_skip_reason(
        _video_meta(resolution="1080p", video_bitrate=9000)
    ) == ""
    assert tracker._payload_skip_reason(
        _video_meta(resolution="1080p", video_bitrate=7000, anime=True)
    ) == ""
    assert tracker._payload_skip_reason(
        _video_meta(resolution="720p", video_bitrate=4000, anime=True)
    ) == ""
    assert "BRRip" in tracker._payload_skip_reason(
        _video_meta(resolution="1080p", video_bitrate=5000, anime=True)
    )
