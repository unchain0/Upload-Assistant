"""Regression tests for DarkPeers-specific BOOK and MUSIC title rules."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.darkpeers import DarkPeers


def test_darkpeers_rejects_malformed_filelists_for_all_categories():
    tracker = DarkPeers(
        {"DEFAULT": {"tmdb_api": "test-key"}, "TRACKERS": {"DARKPEERS": {}}}
    )

    for category in ("MOVIE", "TV", "BOOK", "GAME"):
        assert (
            asyncio.run(
                tracker.get_additional_checks(
                    Meta(category=category, filelist=1)
                )
            )
            is False
        )


def _name(meta: Meta) -> str:
    config = {
        "DEFAULT": {"tmdb_api": "test-key"},
        "TRACKERS": {"DARKPEERS": {}},
    }
    return asyncio.run(DarkPeers(config).get_name(meta))["name"]


def _audio(meta: Meta) -> str:
    config = {
        "DEFAULT": {"tmdb_api": "test-key"},
        "TRACKERS": {"DARKPEERS": {}},
    }
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
            "tracks": [
                {"codec": "FLAC", "bit_depth": 16, "sample_rate": 44100}
            ],
        },
    )

    assert _name(meta) == "Taylor Swift - Red (2012) - WEB FLAC 16-44.1"


def test_darkpeers_music_single_name_is_disambiguated():
    meta = Meta(
        category="MUSIC",
        music_release={
            "fields": {
                "artist": {"value": "Eskila"},
                "album": {"value": "Tequetom"},
                "release_year": {"value": 2026},
                "media": {"value": "WEB"},
                "release_type": {"value": "Single"},
            },
            "tracks": [
                {"codec": "MP3", "bitrate": 320000, "bitrate_mode": "CBR"}
            ],
        },
    )

    assert _name(meta) == "Eskila - Tequetom (2026) - WEB MP3 320 CBR Single"


def test_darkpeers_accepts_numbered_scene_music_filenames():
    pattern = DarkPeers._AUDIO_TRACK_PATTERN

    assert pattern.match(
        "01-simon_and_garfunkel-the_sound_of_silence-repack-remastered"
    )
    assert pattern.match("01 - The Sound of Silence")
    assert pattern.match("01. After Hours & Josh Heuston - Into You")
    assert pattern.match("Ye-The Life of Pablo-01-Ultralight Beam")
    assert not pattern.match("simon_and_garfunkel-the_sound_of_silence")


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
    spectrogram = {
        "web_url": "https://example.com/page",
        "raw_url": "https://example.com/spectrogram.png",
    }

    for category in ("MOVIE", "TV", "BOOK", "GAME"):
        meta = Meta(
            category=category,
            audio_spectrogram=True,
            spectrograms_images=[spectrogram],
        )
        description = asyncio.run(adapter.get_description(meta))["description"]
        assert "Audio Spectrogram" not in description
        assert "spectrogram.png" not in description

    music = Meta(
        category="MUSIC",
        audio_spectrogram=True,
        spectrograms_images=[spectrogram],
    )
    music_description = asyncio.run(adapter.get_description(music))[
        "description"
    ]
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
    )

    assert (
        _name(meta)
        == "Liu Cixin - The Three-Body Problem 2008 Revised Edition EPUB 9780765377067 Retail"
    )


def test_darkpeers_ebook_name_does_not_repeat_author_from_extracted_title():
    meta = Meta(
        category="BOOK",
        author="Eric Evans",
        title="Eric Evans -\u00a0Domain-Driven Design Reference",
        year=2014,
        type="PDF",
        isbn="9781457501197",
        source="OTHER",
    )

    assert (
        _name(meta)
        == "Eric Evans - Domain-Driven Design Reference 2014 PDF 9781457501197"
    )


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

    assert (
        _name(meta)
        == "Ernest Cline - Ready Player One 2011 MP3 64 9780307887436-GROUP"
    )


def test_darkpeers_audiobook_name_includes_explicit_source_or_retail_marker():
    base = {
        "category": "BOOK",
        "audiobook": True,
        "author": "Author",
        "title": "Recording",
        "year": 2026,
        "type": "MP3",
        "audiobook_bitrate": 64,
        "isbn": "978-0-123456-47-2",
    }

    assert (
        _name(Meta(**base, manual_source="OVERDRIVE"))
        == "Author - Recording 2026 Overdrive MP3 64 9780123456472"
    )
    assert (
        _name(Meta(**base, manual_source="RETAIL"))
        == "Author - Recording 2026 MP3 64 9780123456472 Retail"
    )


def test_darkpeers_video_web_name_follows_documented_order():
    meta = Meta(
        category="MOVIE",
        type="WEBDL",
        title="Example",
        aka="AKA Original",
        year=2026,
        edition="Director's Cut IMAX Criterion Collection Hybrid",
        repack="PROPER",
        resolution="1080p",
        service="AMZN",
        audio="DD+ 5.1 Atmos",
        audio_languages=["English"],
        original_language="English",
        language_checked=True,
        hdr="HDR10+",
        video_encode="H.264",
        tag="-GROUP",
    )

    assert (
        _name(meta)
        == "Example AKA Original 2026 Director's Cut IMAX Hybrid PROPER 1080p Criterion Collection AMZN WEB-DL DD+ 5.1 Atmos HDR10+ H.264-GROUP"
    )


def test_darkpeers_full_disc_name_follows_documented_order():
    meta = Meta(
        category="MOVIE",
        type="DISC",
        is_disc="BDMV",
        title="Example",
        year=2026,
        edition="Extended Criterion Collection Hybrid",
        repack="REPACK",
        resolution="2160p",
        region="USA",
        source="BluRay",
        uhd="UHD",
        hdr="DV HDR10+",
        video_codec="HEVC",
        audio="TrueHD 7.1 Atmos",
        audio_languages=["English"],
        original_language="English",
        language_checked=True,
        tag="-GROUP",
    )

    assert (
        _name(meta)
        == "Example 2026 Extended Hybrid REPACK 2160p Criterion Collection USA UHD Blu-ray DV HDR10+ HEVC TrueHD 7.1 Atmos-GROUP"
    )


def test_darkpeers_daily_tv_name_uses_iso_date_without_year_or_season():
    meta = Meta(
        category="TV",
        type="WEBDL",
        title="Daily Show",
        year=2026,
        season="S01",
        episode="E01",
        manual_date="2026-08-11",
        resolution="1080p",
        service="AMZN",
        audio="DD+ 2.0",
        audio_languages=["English"],
        original_language="English",
        language_checked=True,
        video_encode="H.264",
        tag="-GROUP",
    )

    assert (
        _name(meta)
        == "Daily Show 2026-08-11 1080p AMZN WEB-DL DD+ 2.0 H.264-GROUP"
    )


def test_darkpeers_normalizes_htm_ebook_format_to_html():
    meta = Meta(
        category="BOOK",
        author="Author",
        title="Book",
        year=2026,
        type="HTM",
        isbn="978-0-123456-47-2",
        source="RETAIL",
    )

    assert _name(meta) == "Author - Book 2026 HTML 9780123456472 Retail"


def test_darkpeers_scene_name_is_normalized_when_not_treated_as_scene():
    meta = Meta(
        category="MOVIE",
        scene_name="Original.Release.2026-GRP",
        audio_languages=["English", "French"],
        original_language="English",
        language_checked=True,
    )

    assert _name(meta) == "Original Release 2026-GRP"


def test_darkpeers_preserves_scene_name_dots_when_scene_mode_is_enabled():
    meta = Meta(
        category="TV",
        scene_name="Dr.Seuss's.Red.Fish.Blue.Fish.S03E04.1080p.WEB.h264-DOLORES",
        scene=True,
        language_checked=True,
        audio_languages=["English"],
        original_language="English",
    )

    assert (
        _name(meta)
        == "Dr.Seuss's.Red.Fish.Blue.Fish.S03E04.1080p.WEB.h264-DOLORES"
    )


def test_darkpeers_uses_metadata_name_when_scene_name_is_set_but_scene_mode_disabled():
    meta = Meta(
        category="TV",
        scene_name="Dr.Seusss.Red.Fish.Blue.Fish.S03E04.1080p.WEB.h264-DOLORES",
        scene=False,
        tag="-DOLORES",
        type="WEBDL",
        title="Dr. Seuss's Red Fish, Blue Fish",
        year=2025,
        no_year=True,
        season="S03",
        episode="E04",
        resolution="1080p",
        service="AMZN",
        video_encode="H.264",
        audio="DD+ 5.1",
        audio_languages=["English"],
        original_language="English",
        language_checked=True,
    )

    assert (
        _name(meta)
        == "Dr. Seuss's Red Fish, Blue Fish S03E04 1080p AMZN WEB-DL DD+ 5.1 H.264-DOLORES"
    )


def test_darkpeers_prefers_normalized_scene_name_when_scene_name_set_and_name_is_whitespace():
    meta = Meta(
        category="TV",
        scene_name="Movie.2026-3L",
        name="   ",
        tag="-3L",
        audio_languages=["English"],
        original_language="English",
        language_checked=True,
    )

    assert _name(meta) == "Movie 2026-3L"


def test_darkpeers_preserves_leading_digit_release_group_without_double_append():
    meta = Meta(
        category="TV",
        scene_name="Movie.2026-3L",
        name="   ",
        tag="3L",
        audio_languages=["English"],
        original_language="English",
        language_checked=True,
    )

    assert _name(meta) == "Movie 2026-3L"


def test_darkpeers_normalizes_scene_names_without_changing_codec_or_audio_channels():
    meta = Meta(
        category="TV",
        scene_name="Show.Name.S03E04.DD+5.1.WEB.h.264-GROUP_NAME",
        language_checked=True,
        audio_languages=["English"],
        original_language="English",
    )

    assert _name(meta) == "Show Name S03E04 DD+5.1 WEB h.264-GROUP_NAME"


def test_darkpeers_requires_attended_audiobook_edition_verification():
    values = {
        "category": "BOOK",
        "audiobook": True,
        "author": "Mary Stewart",
        "title": "The Gabriel Hounds",
        "narrator": "Davina Porter",
        "publisher": "Recorded Books",
        "year": 1991,
        "type": "MP3",
        "isbn": "9781664616110",
        "audiobook_duration": 39875,
        "audiobook_duration_formatted": "11h 04m 35s",
        "audiobook_bitrate": 64,
    }
    adapter = DarkPeers(
        {"DEFAULT": {"tmdb_api": "test-key"}, "TRACKERS": {"DARKPEERS": {}}}
    )

    assert (
        asyncio.run(
            adapter.get_additional_checks(Meta(**values, unattended=True))
        )
        is False
    )

    adapter.common.prompt_user_for_confirmation = AsyncMock(return_value=True)
    assert asyncio.run(adapter.get_additional_checks(Meta(**values))) is True
    adapter.common.prompt_user_for_confirmation.assert_awaited_once_with(
        "Do these audiobook edition details match the files?", Meta(**values)
    )


def test_darkpeers_requires_audiobook_year_and_runtime_before_verification():
    base = {
        "category": "BOOK",
        "audiobook": True,
        "author": "Mary Stewart",
        "narrator": "Davina Porter",
        "publisher": "Recorded Books",
        "type": "MP3",
        "isbn": "9781664616110",
        "unattended": True,
    }

    assert _additional_checks(Meta(**base, audiobook_duration=39875)) is False
    assert _additional_checks(Meta(**base, year=1991)) is False


def test_darkpeers_rejects_audiobook_without_valid_isbn_even_with_asin():
    base = {
        "category": "BOOK",
        "audiobook": True,
        "author": "Kim Harrison",
        "narrator": "Gigi Bermingham",
        "publisher": "Harper Audio",
        "year": 2008,
        "type": "MP3",
        "audiobook_duration": 59365,
        "asin": "B0012JQ8JO",
    }
    adapter = DarkPeers(
        {"DEFAULT": {"tmdb_api": "test-key"}, "TRACKERS": {"DARKPEERS": {}}}
    )
    adapter.common.prompt_user_for_confirmation = AsyncMock(return_value=True)

    assert asyncio.run(adapter.get_additional_checks(Meta(**base))) is False
    assert (
        asyncio.run(
            adapter.get_additional_checks(Meta(**base, isbn="9780061452988"))
        )
        is False
    )
    adapter.common.prompt_user_for_confirmation.assert_not_awaited()


def test_darkpeers_validated_audiobook_isbn_is_rendered_in_description():
    meta = Meta(
        category="BOOK",
        audiobook=True,
        author="Kim Harrison",
        title="The Outlaw Demon Wails",
        narrator="Gigi Bermingham",
        publisher="Harper Audio",
        year=2008,
        type="MP3",
        audiobook_duration=59365,
        audiobook_duration_formatted="16h 29m 25s",
        audiobook_bitrate=64,
        isbn="978-0-06-145298-7",
    )
    adapter = DarkPeers(
        {"DEFAULT": {"tmdb_api": "test-key"}, "TRACKERS": {"DARKPEERS": {}}}
    )
    adapter.common.prompt_user_for_confirmation = AsyncMock(return_value=True)

    assert asyncio.run(adapter.get_additional_checks(meta)) is True
    description = asyncio.run(adapter.get_description(meta))["description"]
    assert meta.isbn == "9780061452987"
    assert "[tr][td][b]ISBN[/b][/td][td]9780061452987[/td][/tr]" in description


def test_darkpeers_book_name_never_uses_publisher_as_author():
    meta = Meta(
        category="BOOK",
        publisher="Publisher Name",
        title="Book Title",
        year=2026,
        type="EPUB",
        isbn="978-0-123456-47-2",
    )

    assert _name(meta) == "Book Title 2026 EPUB 9780123456472"


def test_darkpeers_book_name_preserves_alphanumeric_asin():
    meta = Meta(
        category="BOOK",
        author="Author",
        title="Book Title",
        year=2026,
        type="EPUB",
        asin="B01N5AX3TQ",
    )

    assert _name(meta) == "Author - Book Title 2026 EPUB B01N5AX3TQ"


def test_darkpeers_rejects_asin_as_individual_ebook_identifier():
    meta = Meta(
        category="BOOK",
        unattended=True,
        author="Author",
        publisher="Publisher",
        title="Book Title",
        year=2026,
        type="EPUB",
        asin="B01N5AX3TQ",
        source="WEB",
    )

    assert _additional_checks(meta) is False


def test_darkpeers_rejects_ebook_without_identifier_even_with_multiple_files():
    meta = Meta(
        category="BOOK",
        unattended=True,
        author="David Bohm",
        publisher="Routledge",
        title="Wholeness and the Implicate Order",
        year=1980,
        type="AZW3",
        filelist=["book.azw3", "cover.jpg"],
    )

    assert _additional_checks(meta) is False


def test_darkpeers_required_book_fields_cannot_be_bypassed_by_unattended_confirm():
    meta = Meta(
        category="BOOK",
        unattended=True,
        unattended_confirm=True,
        author="David Bohm",
        publisher="Routledge",
        title="Wholeness and the Implicate Order",
        year=1980,
        type="AZW3",
        source="RETAIL",
    )

    assert _additional_checks(meta) is False


def test_darkpeers_normalizes_valid_ebook_isbn_before_building_title():
    meta = Meta(
        category="BOOK",
        unattended=True,
        author="David Bohm",
        publisher="Routledge",
        title="Wholeness and the Implicate Order",
        year=1980,
        type="AZW3",
        isbn="978-0-415-28979-5",
        source="RETAIL",
    )

    assert _additional_checks(meta) is True
    assert (
        _name(meta)
        == "David Bohm - Wholeness and the Implicate Order 1980 AZW3 9780415289795 Retail"
    )


def test_darkpeers_rejects_generic_web_as_ebook_provenance():
    meta = Meta(
        category="BOOK",
        unattended=True,
        author="Author",
        publisher="Publisher",
        title="Book",
        year=2026,
        type="EPUB",
        isbn="978-0-123456-47-2",
        source="WEB",
    )

    assert _additional_checks(meta) is False


def test_darkpeers_accepts_explicit_non_retail_born_digital_ebook():
    meta = Meta(
        category="BOOK",
        unattended=True,
        author="Eric Evans",
        publisher="Domain Language, Inc.",
        title="Domain-Driven Design Reference",
        year=2015,
        type="PDF",
        isbn="978-1-4575-0119-7",
        source="OTHER",
        page_count=59,
    )

    assert _additional_checks(meta) is True
    assert (
        _name(meta)
        == "Eric Evans - Domain-Driven Design Reference 2015 PDF 9781457501197"
    )


def test_darkpeers_rejects_retail_ocr_contradiction():
    meta = Meta(
        category="BOOK",
        unattended=True,
        author="Author",
        publisher="Publisher",
        title="Book",
        year=2026,
        type="EPUB",
        isbn="978-0-123456-47-2",
        source="RETAIL",
        ocr=True,
    )

    assert _additional_checks(meta) is False


def test_darkpeers_scan_ocr_name_is_explicit():
    meta = Meta(
        category="BOOK",
        author="Author",
        title="Book",
        year=2026,
        type="PDF",
        isbn="978-0-123456-47-2",
        source="SCAN",
        ocr=True,
    )

    assert _name(meta) == "Author - Book 2026 PDF 9780123456472 Scan OCR"


def test_darkpeers_rejects_invalid_ebook_isbn_instead_of_rendering_it():
    meta = Meta(
        category="BOOK",
        unattended=True,
        author="David Bohm",
        publisher="Routledge",
        title="Wholeness and the Implicate Order",
        year=1980,
        type="AZW3",
        isbn="9780415289794",
    )

    assert _additional_checks(meta) is False


def test_darkpeers_allows_explicit_multi_file_collection_without_isbn():
    meta = Meta(
        category="BOOK",
        unattended=True,
        author="Author",
        publisher="Publisher",
        title="Author Collection",
        year=2026,
        type="EPUB",
        source="RETAIL",
        filelist=[
            "Author - Author Collection - Book One.epub",
            "Author - Author Collection - Book Two.epub",
            "Author - Author Collection - Book Three.epub",
            "Author - Author Collection - Book Four.epub",
            "Author - Author Collection - Book Five.epub",
        ],
    )

    assert _additional_checks(meta) is True


def test_darkpeers_lossy_audiobook_requires_bitrate_and_minimum():
    base = {
        "category": "BOOK",
        "audiobook": True,
        "author": "Author",
        "publisher": "Publisher",
        "narrator": "Narrator",
        "title": "Book",
        "year": 2026,
        "type": "MP3",
        "isbn": "9780061452987",
        "audiobook_duration": 3600,
    }
    adapter = DarkPeers(
        {"DEFAULT": {"tmdb_api": "test-key"}, "TRACKERS": {"DARKPEERS": {}}}
    )
    adapter.common.prompt_user_for_confirmation = AsyncMock(return_value=True)

    assert asyncio.run(adapter.get_additional_checks(Meta(**base))) is False
    assert (
        asyncio.run(
            adapter.get_additional_checks(Meta(**base, audiobook_bitrate=63))
        )
        is False
    )
    assert (
        asyncio.run(
            adapter.get_additional_checks(Meta(**base, audiobook_bitrate=64))
        )
        is True
    )


def test_darkpeers_book_description_includes_required_technical_fields():
    meta = Meta(
        category="BOOK",
        author="Author",
        publisher="Publisher",
        title="Book",
        year=2026,
        type="PDF",
        isbn="9780061452987",
        source="SCAN",
        book_language="French",
        book_series="Series",
        book_series_index="2",
        page_count=320,
    )
    description = asyncio.run(
        DarkPeers(
            {
                "DEFAULT": {"tmdb_api": "test-key"},
                "TRACKERS": {"DARKPEERS": {}},
            }
        ).get_description(meta)
    )["description"]

    for expected in ("French", "Series #2", "SCAN", "320"):
        assert expected in description


def test_darkpeers_allows_unnumbered_official_single_filename(tmp_path):
    root = tmp_path / "Artist - Song Title (2026) - WEB FLAC Single"
    root.mkdir()
    track = root / "Song Title.flac"
    track.touch()
    meta = Meta(
        category="MUSIC",
        path=str(root),
        music_release={
            "root": str(root),
            "fields": {
                "artist": {"value": "Artist"},
                "album": {"value": "Song Title"},
                "release_year": {"value": "2026"},
                "release_type": {"value": "Single"},
                "media": {"value": "WEB"},
            },
            "tracks": [
                {
                    "path": str(track),
                    "relative_path": "Song Title.flac",
                    "format": "FLAC",
                    "codec": "FLAC",
                    "title": "Song Title",
                    "track_number": 1,
                }
            ],
        },
    )

    assert (
        DarkPeers(
            {
                "DEFAULT": {"tmdb_api": "test-key"},
                "TRACKERS": {"DARKPEERS": {}},
            }
        ).validate_music(meta)
        is True
    )


def test_darkpeers_rejects_small_collection_without_isbn():
    meta = Meta(
        category="BOOK",
        unattended=True,
        author="Author",
        publisher="Publisher",
        title="Author Collection",
        year=2026,
        type="EPUB",
        source="WEB",
        filelist=[
            "Author - Author Collection - One.epub",
            "Author - Author Collection - Two.epub",
        ],
    )

    assert _additional_checks(meta) is False


def test_darkpeers_requires_exact_single_file_m4b_name():
    adapter = DarkPeers(
        {"DEFAULT": {"tmdb_api": "test-key"}, "TRACKERS": {"DARKPEERS": {}}}
    )
    values = {
        "category": "BOOK",
        "audiobook": True,
        "author": "Author",
        "title": "Book",
        "year": 2026,
        "type": "M4B",
    }

    assert (
        adapter._validate_book_file_layout(
            Meta(**values, filelist=["Author - Book - 2026.m4b"]), "M4B"
        )
        is True
    )
    assert (
        adapter._validate_book_file_layout(
            Meta(**values, filelist=["Book.m4b"]), "M4B"
        )
        is False
    )


def test_darkpeers_replaces_generic_dual_audio_with_rule_matrix_label():
    meta = Meta(
        category="MOVIE",
        name="Anime 2026 1080p WEB-DL Dual-Audio-TEAM",
        language_checked=True,
        original_language="Japanese",
        audio_languages=["Japanese", "French"],
    )

    assert _name(meta) == "Anime 2026 1080p WEB-DL French MULTi-TEAM"


def test_darkpeers_keeps_dual_audio_for_original_non_english_with_english_only_pair():
    assert (
        _audio(
            Meta(
                category="MOVIE",
                language_checked=True,
                original_language="Japanese",
                audio_languages=["Japanese", "en-US"],
            )
        )
        == "Dual-Audio"
    )


def test_darkpeers_preserves_detected_original_scene_name():
    meta = Meta(
        category="MOVIE",
        name="Generated Name",
        scene=True,
        scene_name="Original.Release.2026-GRP",
        language_checked=True,
    )

    assert _name(meta) == "Original.Release.2026-GRP"


def test_darkpeers_ignores_absolute_scene_name_when_building_upload_title():
    meta = Meta(
        category="MOVIE",
        name="Full Contact AKA Hap do Ko Fei 1992 480p BluRay Dual-Audio AAC 1.0 x264-gazer",
        scene=True,
        scene_name="/home/seedbox/data/torrents/Full.Contact.1992.OAR.BDRip.x264-GAZER/full.contact.1992.oar.bdrip.x264-gazer.mkv",
        language_checked=True,
    )

    assert (
        _name(meta)
        == "Full Contact AKA Hap do Ko Fei 1992 480p BluRay Dual-Audio AAC 1.0 x264-gazer"
    )


def test_darkpeers_rejects_local_path_as_generated_video_title():
    meta = Meta(
        category="MOVIE",
        name="/home/seedbox/data/torrents/full.contact.1992.oar.bdrip.x264-gazer.mkv",
        audio_languages=["English"],
        resolution="480p",
        screens=3,
    )

    assert _additional_checks(meta) is False


def test_darkpeers_tv_name_omits_year_without_an_exact_title_match():
    meta = Meta(
        category="TV",
        title="BLACK TORCH",
        year=2026,
        name="BLACK TORCH 2026 S01E05 1080p CR WEB-DL DD+ 2.0 H.264-AnoZu",
    )
    adapter = DarkPeers(
        {"DEFAULT": {"tmdb_api": "test-key"}, "TRACKERS": {"DARKPEERS": {}}}
    )
    adapter._tv_title_needs_year = AsyncMock(return_value=False)

    assert (
        asyncio.run(adapter.get_name(meta))["name"]
        == "BLACK TORCH S01E05 1080p CR WEB-DL DD+ 2.0 H.264-AnoZu"
    )


def test_darkpeers_tv_name_keeps_year_for_an_exact_title_match():
    meta = Meta(
        category="TV",
        title="The Flash",
        year=2014,
        name="The Flash 2014 S01E01 1080p WEB-DL",
    )
    adapter = DarkPeers(
        {"DEFAULT": {"tmdb_api": "test-key"}, "TRACKERS": {"DARKPEERS": {}}}
    )
    adapter._tv_title_needs_year = AsyncMock(return_value=True)

    assert (
        asyncio.run(adapter.get_name(meta))["name"]
        == "The Flash 2014 S01E01 1080p WEB-DL"
    )


def test_darkpeers_tv_year_rule_preserves_aka():
    meta = Meta(
        category="TV",
        title="Localized Title",
        year=2020,
        aka="AKA Original Title",
        name="Localized Title 2020 AKA Original Title S01E01 1080p WEB-DL",
    )
    adapter = DarkPeers(
        {"DEFAULT": {"tmdb_api": "test-key"}, "TRACKERS": {"DARKPEERS": {}}}
    )
    adapter._tv_title_needs_year = AsyncMock(return_value=False)

    assert (
        asyncio.run(adapter.get_name(meta))["name"]
        == "Localized Title AKA Original Title S01E01 1080p WEB-DL"
    )


def test_darkpeers_tv_year_rule_detects_a_distinct_exact_tmdb_title():
    meta = Meta(category="TV", title="The Flash", tmdb_id=60735)
    adapter = DarkPeers(
        {"DEFAULT": {"tmdb_api": "test-key"}, "TRACKERS": {"DARKPEERS": {}}}
    )
    response = Mock()
    response.json.return_value = {
        "results": [
            {"id": 60735, "name": "The Flash", "original_name": "The Flash"},
            {"id": 236, "name": "The Flash", "original_name": "The Flash"},
        ]
    }

    with patch(
        "src.integrations.trackers.UNIT3D.darkpeers.httpx.AsyncClient.get",
        new=AsyncMock(return_value=response),
    ):
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
    assert (
        _audio(
            Meta(
                category="MOVIE",
                language_checked=True,
                original_language="English",
                audio_languages=["en-US", "Portuguese"],
            )
        )
        == "Portuguese MULTi"
    )


def test_darkpeers_keeps_existing_multi_label_if_audio_stays_multi():
    meta = Meta(
        category="MOVIE",
        language_checked=True,
        audio_languages=["english", "Portuguese"],
        name="Closer to God 2014 1080p WEB-DL Portuguese MULTi AAC 2.0 H.265-nitrato",
    )

    assert (
        _name(meta)
        == "Closer to God 2014 1080p WEB-DL Portuguese MULTi AAC 2.0 H.265-nitrato"
    )


def test_darkpeers_tv_year_rule_does_not_count_the_only_tmdb_result_as_a_duplicate():
    meta = Meta(category="TV", title="BLACK TORCH")
    adapter = DarkPeers(
        {"DEFAULT": {"tmdb_api": "test-key"}, "TRACKERS": {"DARKPEERS": {}}}
    )
    response = Mock()
    response.json.return_value = {
        "results": [
            {
                "id": 279807,
                "name": "BLACK TORCH",
                "original_name": "BLACK TORCH",
            }
        ]
    }

    with patch(
        "src.integrations.trackers.UNIT3D.darkpeers.httpx.AsyncClient.get",
        new=AsyncMock(return_value=response),
    ):
        assert asyncio.run(adapter._tv_title_needs_year(meta)) is False


def _additional_checks(meta: Meta) -> bool:
    config = {
        "DEFAULT": {"tmdb_api": "test-key", "thumbnail_size": "350"},
        "TRACKERS": {"DARKPEERS": {}},
    }
    return asyncio.run(DarkPeers(config).get_additional_checks(meta))


def _additional_checks_with_config(
    meta: Meta, tracker_config: dict[str, Any]
) -> bool:
    config = {
        "DEFAULT": {"tmdb_api": "test-key", "thumbnail_size": "350"},
        "TRACKERS": {"DARKPEERS": tracker_config},
    }
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


def test_darkpeers_movie_tv_require_between_three_and_five_screens():
    assert (
        _additional_checks(
            Meta(
                category="MOVIE",
                audio_languages=["English"],
                resolution="1080p",
                screens=2,
            )
        )
        is False
    )
    assert (
        _additional_checks(
            Meta(
                category="TV",
                type="WEBDL",
                audio_languages=["English"],
                resolution="1080p",
                screens=3,
            )
        )
        is True
    )
    assert (
        _additional_checks(
            Meta(
                category="TV",
                type="WEBDL",
                audio_languages=["English"],
                resolution="1080p",
                screens=5,
            )
        )
        is True
    )
    assert (
        _additional_checks(
            Meta(
                category="MOVIE",
                type="WEBDL",
                audio_languages=["English"],
                resolution="1080p",
                screens=6,
            )
        )
        is False
    )


def test_darkpeers_movie_tv_invalid_screens_value_is_treated_as_missing():
    assert (
        _additional_checks(
            Meta(
                category="MOVIE",
                audio_languages=["English"],
                resolution="1080p",
                screens="many",
            )
        )
        is False
    )


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


def test_darkpeers_rejects_webdl_when_video_bitrate_is_missing():
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


def test_darkpeers_accepts_webdl_bitrate_when_configured_higher_quality_is_not_required():
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

    assert (
        _additional_checks_with_config(
            meta,
            {
                "webl_min_video_kbps": {
                    "1080p": 1000,
                }
            },
        )
        is True
    )


def test_darkpeers_allows_480p_webdl_when_video_bitrate_threshold_is_unset():
    low_res = Meta(
        category="MOVIE",
        unattended=True,
        language_checked=True,
        audio_languages=["English"],
        filelist=["Movie.mkv"],
        type="WEBDL",
        resolution="480p",
        screens=3,
        audio_bitrate=160,
    )

    assert _additional_checks(low_res) is True


def test_darkpeers_requires_movie_tv_payload_for_content_checks():
    assert (
        _additional_checks(
            Meta(
                category="MOVIE",
                unattended=True,
                language_checked=True,
                audio_languages=["English"],
                resolution="1080p",
                screens=3,
            )
        )
        is False
    )


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
    base = {
        "category": "MOVIE",
        "unattended": True,
        "language_checked": True,
        "audio_languages": ["English"],
        "filelist": ["Movie.mkv"],
        "resolution": "1080p",
    }

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
    unsupported = Meta(
        category="MOVIE",
        unattended=True,
        audio_languages=["English"],
        resolution="1440p",
        screens=3,
    )

    assert _additional_checks(unsupported) is False


def test_darkpeers_rejects_multi_season_and_video_archives():
    seasons = Meta(
        category="TV",
        unattended=True,
        audio_languages=["English"],
        resolution="1080p",
        screens=3,
        filelist=["Show.S01E01.mkv", "Show.S02E01.mkv"],
    )
    archive = Meta(
        category="MOVIE",
        unattended=True,
        audio_languages=["English"],
        resolution="1080p",
        screens=3,
        filelist=["Movie.part01.rar"],
    )

    assert _additional_checks(seasons) is False
    assert _additional_checks(archive) is False


def test_darkpeers_tv_scope_ignores_parent_directory_and_detects_episode_season():
    meta = Meta(
        category="TV",
        name="Show S01",
        path="C:/media/Complete Series/Show S01",
        filelist=["Show.S01E01.mkv"],
    )
    adapter = DarkPeers(
        {"DEFAULT": {"tmdb_api": "test-key"}, "TRACKERS": {"DARKPEERS": {}}}
    )

    assert adapter.validate_tv_scope(meta) is True
    assert adapter._is_single_tv_season(meta) is True


def test_darkpeers_confirmed_folder_check_continues_to_evo_validation():
    meta = Meta(
        category="MOVIE",
        type="ENCODE",
        tag="-EVO",
        keep_folder=True,
        audio_languages=["English"],
        resolution="1080p",
        screens=3,
    )
    adapter = DarkPeers(
        {"DEFAULT": {"tmdb_api": "test-key"}, "TRACKERS": {"DARKPEERS": {}}}
    )
    adapter._confirm_or_skip = AsyncMock(return_value=True)

    assert asyncio.run(adapter.get_additional_checks(meta)) is False


def test_darkpeers_book_language_is_unrestricted_but_author_is_required_unattended():
    portuguese = Meta(
        category="BOOK",
        unattended=True,
        author="Autor",
        publisher="Editora",
        title="Livro",
        year=2026,
        type="EPUB",
        isbn="978-0-123456-47-2",
        book_language="Portuguese",
        source="RETAIL",
    )
    no_author = Meta(
        category="BOOK",
        unattended=True,
        publisher="Editora",
        type="EPUB",
        isbn="978-0-123456-47-2",
    )

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
    iso = Meta(
        category="GAME",
        unattended=True,
        scene=True,
        scene_nfo_file="release.nfo",
        filelist=["release.iso"],
        description="Installation instructions",
    )

    assert _additional_checks(valid_game) is True
    assert _additional_checks(iso) is False
