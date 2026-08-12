"""Regression tests for DarkPeers-specific BOOK and MUSIC title rules."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from src.meta import Meta
from src.trackers.UNIT3D.darkpeers import DarkPeers


def _name(meta: Meta) -> str:
    config = {"DEFAULT": {"tmdb_api": "test-key"}, "TRACKERS": {"DARKPEERS": {}}}
    return asyncio.run(DarkPeers(config).get_name(meta))["name"]


def _audio(meta: Meta) -> str:
    config = {"DEFAULT": {"tmdb_api": "test-key"}, "TRACKERS": {"DARKPEERS": {}}}
    return asyncio.run(DarkPeers(config).get_audio(meta))


def test_darkpeers_music_name_uses_required_folder_style():
    meta = Meta(
        category="MUSIC",
        music_release={
            "fields": {
                "artist": {"value": "Taylor Swift"},
                "album": {"value": "Red"},
                "release_year": {"value": "2012"},
                "media": {"value": "WEB"},
            },
            "tracks": [{"codec": "FLAC", "bit_depth": 16, "sample_rate": 44100}],
        },
    )

    assert _name(meta) == "Taylor Swift - Red (2012) - WEB FLAC 16-44.1"


def test_darkpeers_only_includes_audio_spectrograms_for_music():
    config = {
        "DEFAULT": {"tmdb_api": "test-key"},
        "TRACKERS": {
            "DARKPEERS": {
                "add_audio_spectrogram": True,
                "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
            }
        },
    }
    adapter = DarkPeers(config)
    spectrogram = {"web_url": "https://example.com/page", "raw_url": "https://example.com/spectrogram.png"}

    for category in ("MOVIE", "TV", "BOOK", "GAME"):
        meta = Meta(category=category, audio_spectrogram=True, spectrograms_images=[spectrogram])
        description = asyncio.run(adapter.get_description(meta))["description"]
        assert "Audio Spectrogram" not in description
        assert "spectrogram.png" not in description

    music = Meta(category="MUSIC", audio_spectrogram=True, spectrograms_images=[spectrogram])
    music_description = asyncio.run(adapter.get_description(music))["description"]
    assert "[h2]Audio Spectrogram[/h2]" in music_description
    assert "spectrogram.png" in music_description


def test_darkpeers_ebook_name_includes_book_elements():
    meta = Meta(
        category="BOOK",
        author="Liu Cixin",
        title="The Three-Body Problem",
        edition="Revised Edition",
        year=2008,
        type="EPUB",
        isbn="978-0765377067",
        source="RETAIL",
        ocr=True,
    )

    assert _name(meta) == "Liu Cixin - The Three-Body Problem 2008 Revised Edition EPUB 9780765377067 Retail OCR"


def test_darkpeers_audiobook_name_includes_format_bitrate_isbn_and_tag():
    meta = Meta(
        category="BOOK",
        audiobook=True,
        author="Ernest Cline",
        title="Ready Player One",
        year=2011,
        type="MP3",
        audiobook_bitrate=64,
        isbn="978-0-307-88743-6",
        tag="GROUP",
    )

    assert _name(meta) == "Ernest Cline - Ready Player One 2011 MP3 64 9780307887436-GROUP"


def test_darkpeers_book_name_never_uses_publisher_as_author():
    meta = Meta(category="BOOK", publisher="Publisher Name", title="Book Title", year=2026, type="EPUB", isbn="978-0-123456-47-2")

    assert _name(meta) == "Book Title 2026 EPUB 9780123456472"


def test_darkpeers_book_name_preserves_alphanumeric_asin():
    meta = Meta(category="BOOK", author="Author", title="Book Title", year=2026, type="EPUB", asin="B01N5AX3TQ")

    assert _name(meta) == "Author - Book Title 2026 EPUB B01N5AX3TQ"


def test_darkpeers_replaces_generic_dual_audio_with_rule_matrix_label():
    meta = Meta(category="MOVIE", name="Anime 2026 1080p WEB-DL Dual-Audio-TEAM", language_checked=True, original_language="Japanese", audio_languages=["Japanese", "French"])

    assert _name(meta) == "Anime 2026 1080p WEB-DL French MULTi-TEAM"


def test_darkpeers_keeps_dual_audio_for_original_non_english_with_english_only_pair():
    assert _audio(Meta(category="MOVIE", language_checked=True, original_language="Japanese", audio_languages=["Japanese", "en-US"])) == "Dual-Audio"


def test_darkpeers_preserves_detected_original_scene_name():
    meta = Meta(category="MOVIE", name="Generated Name", scene=True, scene_name="Original.Release.2026-GRP", language_checked=True)

    assert _name(meta) == "Original.Release.2026-GRP"


def test_darkpeers_tv_name_omits_year_without_an_exact_title_match():
    meta = Meta(category="TV", title="BLACK TORCH", year=2026, name="BLACK TORCH 2026 S01E05 1080p CR WEB-DL DD+ 2.0 H.264-AnoZu")
    adapter = DarkPeers({"DEFAULT": {"tmdb_api": "test-key"}, "TRACKERS": {"DARKPEERS": {}}})
    adapter._tv_title_needs_year = AsyncMock(return_value=False)

    assert asyncio.run(adapter.get_name(meta))["name"] == "BLACK TORCH S01E05 1080p CR WEB-DL DD+ 2.0 H.264-AnoZu"


def test_darkpeers_tv_name_keeps_year_for_an_exact_title_match():
    meta = Meta(category="TV", title="The Flash", year=2014, name="The Flash 2014 S01E01 1080p WEB-DL")
    adapter = DarkPeers({"DEFAULT": {"tmdb_api": "test-key"}, "TRACKERS": {"DARKPEERS": {}}})
    adapter._tv_title_needs_year = AsyncMock(return_value=True)

    assert asyncio.run(adapter.get_name(meta))["name"] == "The Flash 2014 S01E01 1080p WEB-DL"


def test_darkpeers_tv_year_rule_preserves_aka():
    meta = Meta(category="TV", title="Localized Title", year=2020, aka="AKA Original Title", name="Localized Title 2020 AKA Original Title S01E01 1080p WEB-DL")
    adapter = DarkPeers({"DEFAULT": {"tmdb_api": "test-key"}, "TRACKERS": {"DARKPEERS": {}}})
    adapter._tv_title_needs_year = AsyncMock(return_value=False)

    assert asyncio.run(adapter.get_name(meta))["name"] == "Localized Title AKA Original Title S01E01 1080p WEB-DL"


def test_darkpeers_tv_year_rule_detects_a_distinct_exact_tmdb_title():
    meta = Meta(category="TV", title="The Flash", tmdb_id=60735)
    adapter = DarkPeers({"DEFAULT": {"tmdb_api": "test-key"}, "TRACKERS": {"DARKPEERS": {}}})
    response = Mock()
    response.json.return_value = {
        "results": [
            {"id": 60735, "name": "The Flash", "original_name": "The Flash"},
            {"id": 236, "name": "The Flash", "original_name": "The Flash"},
        ]
    }

    with patch("src.trackers.UNIT3D.darkpeers.httpx.AsyncClient.get", new=AsyncMock(return_value=response)):
        assert asyncio.run(adapter._tv_title_needs_year(meta)) is True


def test_darkpeers_moves_year_to_end_of_aka_for_movie():
    assert (
        _name(
            Meta(
                category="MOVIE",
                title="The Flash",
                aka="AKA A Sombra",
                year=2014,
                name="The Flash 2014 AKA A Sombra 1080p WEB-DL AAC 2.0 H.264",
                language_checked=True,
            )
        )
        == "The Flash AKA A Sombra 2014 1080p WEB-DL AAC 2.0 H.264"
    )


def test_darkpeers_uses_manual_year_when_reordering_aka_year():
    assert (
        _name(
            Meta(
                category="MOVIE",
                title="The Flash",
                aka="AKA O Brilho",
                year=2014,
                manual_year=2015,
                name="The Flash 2015 AKA O Brilho 1080p WEB-DL AAC 2.0 H.264",
                language_checked=True,
            )
        )
        == "The Flash AKA O Brilho 2015 1080p WEB-DL AAC 2.0 H.264"
    )


def test_darkpeers_does_not_reorder_aka_year_without_both_tokens():
    assert (
        _name(
            Meta(
                category="MOVIE",
                title="The Flash",
                year=2014,
                name="The Flash 2014 1080p WEB-DL AAC 2.0 H.264",
                language_checked=True,
            )
        )
        == "The Flash 2014 1080p WEB-DL AAC 2.0 H.264"
    )


def test_darkpeers_treats_english_plus_one_other_as_language_multi():
    assert _audio(Meta(category="MOVIE", language_checked=True, original_language="English", audio_languages=["en-US", "Portuguese"])) == "Portuguese MULTi"


def test_darkpeers_keeps_existing_multi_label_if_audio_stays_multi():
    meta = Meta(
        category="MOVIE",
        language_checked=True,
        audio_languages=["english", "Portuguese"],
        name="Closer to God 2014 1080p WEB-DL Portuguese MULTi AAC 2.0 H.265-nitrato",
    )

    assert _name(meta) == "Closer to God 2014 1080p WEB-DL Portuguese MULTi AAC 2.0 H.265-nitrato"


def test_darkpeers_tv_year_rule_does_not_count_the_only_tmdb_result_as_a_duplicate():
    meta = Meta(category="TV", title="BLACK TORCH")
    adapter = DarkPeers({"DEFAULT": {"tmdb_api": "test-key"}, "TRACKERS": {"DARKPEERS": {}}})
    response = Mock()
    response.json.return_value = {"results": [{"id": 279807, "name": "BLACK TORCH", "original_name": "BLACK TORCH"}]}

    with patch("src.trackers.UNIT3D.darkpeers.httpx.AsyncClient.get", new=AsyncMock(return_value=response)):
        assert asyncio.run(adapter._tv_title_needs_year(meta)) is False


def _additional_checks(meta: Meta) -> bool:
    config = {"DEFAULT": {"tmdb_api": "test-key", "thumbnail_size": "350"}, "TRACKERS": {"DARKPEERS": {}}}
    return asyncio.run(DarkPeers(config).get_additional_checks(meta))


def _additional_checks_with_config(meta: Meta, tracker_config: dict[str, Any]) -> bool:
    config = {"DEFAULT": {"tmdb_api": "test-key", "thumbnail_size": "350"}, "TRACKERS": {"DARKPEERS": tracker_config}}
    return asyncio.run(DarkPeers(config).get_additional_checks(meta))


def test_darkpeers_evo_webdl_allowed_and_non_webdl_blocked():
    evo_webdl = Meta(
        category="MOVIE",
        type="WEBDL",
        tag="-EVO",
        language_checked=True,
        audio_languages=["English"],
        filelist=["Movie.mkv"],
        video_bitrate=3500,
        audio_bitrate=160,
        resolution="1080p",
        screens=3,
    )
    evo_encode = Meta(
        category="MOVIE",
        type="ENCODE",
        tag="-EVO",
        language_checked=True,
        audio_languages=["English"],
        filelist=["Movie.mkv"],
        video_bitrate=3500,
        audio_bitrate=160,
        resolution="1080p",
        screens=3,
    )
    evo_remux = Meta(
        category="MOVIE",
        type="REMUX",
        tag="EVO",
        language_checked=True,
        audio_languages=["English"],
        filelist=["Movie.mkv"],
        video_bitrate=3500,
        audio_bitrate=160,
        resolution="1080p",
        screens=3,
    )

    assert _additional_checks(evo_webdl) is True
    assert _additional_checks(evo_encode) is False
    assert _additional_checks(evo_remux) is False


def test_darkpeers_hdt_remux_allowed_and_non_remux_blocked():
    hdt_remux = Meta(
        category="MOVIE",
        type="REMUX",
        tag="-HDT",
        language_checked=True,
        audio_languages=["English"],
        filelist=["Movie.mkv"],
        resolution="1080p",
        screens=3,
    )
    hdt_webdl = Meta(
        category="MOVIE",
        type="WEBDL",
        tag="-HDT",
        language_checked=True,
        audio_languages=["English"],
        filelist=["Movie.mkv"],
        video_bitrate=3500,
        audio_bitrate=160,
        resolution="1080p",
        screens=3,
    )
    hdt_encode = Meta(
        category="MOVIE",
        type="ENCODE",
        tag="HDT",
        language_checked=True,
        audio_languages=["English"],
        filelist=["Movie.mkv"],
        video_bitrate=3500,
        audio_bitrate=160,
        resolution="1080p",
        screens=3,
    )

    assert _additional_checks(hdt_remux) is True
    assert _additional_checks(hdt_webdl) is False
    assert _additional_checks(hdt_encode) is False


def test_darkpeers_hardcoded_subs_blocked_in_interactive_and_unattended():
    subs_interactive = Meta(
        category="MOVIE",
        type="WEBDL",
        tag="-GRP",
        hardcoded_subs=True,
        unattended=False,
        language_checked=True,
        audio_languages=["English"],
        filelist=["Movie.mkv"],
        video_bitrate=3500,
        audio_bitrate=160,
        resolution="1080p",
        screens=3,
    )
    subs_unattended = Meta(
        category="MOVIE",
        type="WEBDL",
        tag="-GRP",
        hardcoded_subs=True,
        unattended=True,
        language_checked=True,
        audio_languages=["English"],
        filelist=["Movie.mkv"],
        video_bitrate=3500,
        audio_bitrate=160,
        resolution="1080p",
        screens=3,
    )
    no_subs_unattended = Meta(
        category="MOVIE",
        type="WEBDL",
        tag="-GRP",
        hardcoded_subs=False,
        unattended=True,
        language_checked=True,
        audio_languages=["English"],
        filelist=["Movie.mkv"],
        video_bitrate=3500,
        audio_bitrate=160,
        resolution="1080p",
        screens=3,
    )

    assert _additional_checks(subs_interactive) is False
    assert _additional_checks(subs_unattended) is False
    assert _additional_checks(no_subs_unattended) is True


def test_darkpeers_video_language_rule_requires_original_audio_with_accepted_subtitles():
    original_with_subtitles = Meta(
        category="MOVIE",
        unattended=True,
        language_checked=True,
        audio_languages=["jpn"],
        subtitle_languages=["Swedish"],
        filelist=["Movie.mkv"],
        original_language="Japanese",
        resolution="1080p",
        screens=3,
        video_bitrate=3500,
        audio_bitrate=160,
    )
    foreign_dub_with_subtitles = Meta(
        category="MOVIE",
        unattended=True,
        language_checked=True,
        audio_languages=["Spanish"],
        subtitle_languages=["English"],
        filelist=["Movie.mkv"],
        original_language="Japanese",
        resolution="1080p",
        screens=3,
    )

    assert _additional_checks(original_with_subtitles) is True
    assert _additional_checks(foreign_dub_with_subtitles) is False


def test_darkpeers_rejects_low_bitrate_webl_for_1080p():
    low = Meta(
        category="MOVIE",
        unattended=True,
        language_checked=True,
        audio_languages=["English"],
        filelist=["Movie.mkv"],
        type="WEBDL",
        resolution="1080p",
        screens=3,
        video_bitrate=1500,
    )

    assert _additional_checks(low) is False


def test_darkpeers_rejects_webl_when_video_bitrate_is_missing():
    missing = Meta(
        category="MOVIE",
        unattended=True,
        language_checked=True,
        audio_languages=["English"],
        filelist=["Movie.mkv"],
        type="WEBDL",
        resolution="1080p",
        screens=3,
        audio_bitrate=160,
    )

    assert _additional_checks(missing) is False


def test_darkpeers_accepts_webl_bitrate_when_configured_higher_quality_is_not_required():
    meta = Meta(
        category="MOVIE",
        unattended=True,
        language_checked=True,
        audio_languages=["English"],
        filelist=["Movie.mkv"],
        type="WEBDL",
        resolution="1080p",
        screens=3,
        video_bitrate=1500,
        audio_bitrate=160,
    )

    assert _additional_checks_with_config(
        meta,
        {
            "webl_min_video_kbps": {
                "1080p": 1000,
            }
        },
    ) is True


def test_darkpeers_requires_movie_tv_payload_for_content_checks():
    assert _additional_checks(Meta(category="MOVIE", unattended=True, language_checked=True, audio_languages=["English"], resolution="1080p", screens=3)) is False


def test_darkpeers_rejects_movie_tv_payload_with_unsupported_file_types():
    payload_invalid = Meta(
        category="MOVIE",
        unattended=True,
        language_checked=True,
        audio_languages=["English"],
        filelist=["Movie.mkv", "readme.txt"],
        resolution="1080p",
        screens=3,
    )

    assert _additional_checks(payload_invalid) is False


def test_darkpeers_rejects_movie_tv_payload_with_non_video_only_files():
    payload_video_like = Meta(
        category="MOVIE",
        unattended=True,
        language_checked=True,
        audio_languages=["English"],
        filelist=["cover.jpg", "scan.nfo"],
        resolution="1080p",
        screens=3,
    )

    assert _additional_checks(payload_video_like) is False


def test_darkpeers_enforces_screenshot_count_rules_movie_tv():
    base = dict(
        category="MOVIE",
        unattended=True,
        language_checked=True,
        audio_languages=["English"],
        filelist=["Movie.mkv"],
        resolution="1080p",
    )

    assert _additional_checks(Meta(**base, screens=2)) is False
    assert _additional_checks(Meta(**base, screens=3)) is True
    assert _additional_checks(Meta(**base, screens=5)) is True
    assert _additional_checks(Meta(**base, screens=6)) is False


def test_darkpeers_rejects_invalid_screenshot_count_value():
    payload_invalid_screens = Meta(
        category="MOVIE",
        unattended=True,
        language_checked=True,
        audio_languages=["English"],
        filelist=["Movie.mkv"],
        resolution="1080p",
        screens="not-a-number",
    )

    assert _additional_checks(payload_invalid_screens) is False


def test_darkpeers_rejects_unsupported_resolution():
    unsupported = Meta(category="MOVIE", unattended=True, audio_languages=["English"], resolution="1440p", screens=3)

    assert _additional_checks(unsupported) is False


def test_darkpeers_rejects_multi_season_and_video_archives():
    seasons = Meta(category="TV", unattended=True, audio_languages=["English"], resolution="1080p", screens=3, filelist=["Show.S01E01.mkv", "Show.S02E01.mkv"])
    archive = Meta(category="MOVIE", unattended=True, audio_languages=["English"], resolution="1080p", screens=3, filelist=["Movie.part01.rar"])

    assert _additional_checks(seasons) is False
    assert _additional_checks(archive) is False


def test_darkpeers_tv_scope_ignores_parent_directory_and_detects_episode_season():
    meta = Meta(category="TV", name="Show S01", path="C:/media/Complete Series/Show S01", filelist=["Show.S01E01.mkv"])
    adapter = DarkPeers({"DEFAULT": {"tmdb_api": "test-key"}, "TRACKERS": {"DARKPEERS": {}}})

    assert adapter.validate_tv_scope(meta) is True
    assert adapter._is_single_tv_season(meta) is True


def test_darkpeers_confirmed_folder_check_continues_to_evo_validation():
    meta = Meta(category="MOVIE", type="ENCODE", tag="-EVO", keep_folder=True, audio_languages=["English"], resolution="1080p", screens=3)
    adapter = DarkPeers({"DEFAULT": {"tmdb_api": "test-key"}, "TRACKERS": {"DARKPEERS": {}}})
    adapter._confirm_or_skip = AsyncMock(return_value=True)

    assert asyncio.run(adapter.get_additional_checks(meta)) is False


def test_darkpeers_book_language_is_unrestricted_but_author_is_required_unattended():
    portuguese = Meta(category="BOOK", unattended=True, author="Autor", publisher="Editora", type="EPUB", isbn="978-0-123456-47-2", book_language="Portuguese")
    no_author = Meta(category="BOOK", unattended=True, publisher="Editora", type="EPUB", isbn="978-0-123456-47-2")

    assert _additional_checks(portuguese) is True
    assert _additional_checks(no_author) is False


def test_darkpeers_game_requires_scene_rars_nfo_and_instructions():
    valid_game = Meta(
        category="GAME",
        unattended=True,
        scene=True,
        scene_nfo_file="release.nfo",
        filelist=["release.r00", "release.rar"],
        description="Installation instructions: mount and install.",
    )
    iso = Meta(category="GAME", unattended=True, scene=True, scene_nfo_file="release.nfo", filelist=["release.iso"], description="Installation instructions")

    assert _additional_checks(valid_game) is True
    assert _additional_checks(iso) is False
