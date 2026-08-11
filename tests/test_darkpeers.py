# ruff: noqa: S101
"""Regression tests for DarkPeers-specific BOOK and MUSIC title rules."""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

from src.meta import Meta
from src.trackers.UNIT3D.darkpeers import DarkPeers


def test_darkpeers_rejects_malformed_filelists_for_all_categories():
    tracker = DarkPeers({"DEFAULT": {"tmdb_api": "test-key"}, "TRACKERS": {"DARKPEERS": {}}})

    for category in ("MOVIE", "TV", "BOOK", "GAME"):
        assert asyncio.run(tracker.get_additional_checks(Meta(category=category, filelist=1))) is False


def _name(meta: Meta) -> str:
    config = {"DEFAULT": {"tmdb_api": "test-key"}, "TRACKERS": {"DARKPEERS": {}}}
    return asyncio.run(DarkPeers(config).get_name(meta))["name"]


def test_darkpeers_rejects_malformed_video_filelist():
    tracker = DarkPeers({"DEFAULT": {"tmdb_api": "test-key"}, "TRACKERS": {"DARKPEERS": {}}})

    assert tracker.validate_video_files(Meta(filelist=1)) is False


def test_darkpeers_rejects_tagged_video_filename_renamed_with_spaces():
    tracker = DarkPeers({"DEFAULT": {"tmdb_api": "test-key"}, "TRACKERS": {"DARKPEERS": {}}})
    renamed = "Oh Boy Was I Wrong About Her S01E01 Beginning with a Summer Day REPACK 1080p CR WEB-DL DDP2.0 H.264-Kitsune.mkv"
    original = "Oh.Boy.Was.I.Wrong.About.Her.S01E01.Beginning.with.a.Summer.Day.REPACK.1080p.CR.WEB-DL.DDP2.0.H.264-Kitsune.mkv"

    assert tracker.validate_video_files(Meta(tag="-Kitsune", filelist=[renamed])) is False
    assert tracker.validate_video_files(Meta(tag="-Kitsune", filelist=[original])) is True


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
            "tracks": [{"codec": "MP3", "bitrate": 320000, "bitrate_mode": "CBR"}],
        },
    )

    assert _name(meta) == "Eskila - Tequetom (2026) - WEB MP3 320 CBR Single"


def test_darkpeers_accepts_numbered_scene_music_filenames():
    pattern = DarkPeers._AUDIO_TRACK_PATTERN

    assert pattern.match("01-simon_and_garfunkel-the_sound_of_silence-repack-remastered")
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
    )

    assert _name(meta) == "Liu Cixin - The Three-Body Problem 2008 Revised Edition EPUB 9780765377067 Retail"


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

    assert _name(meta) == "Eric Evans - Domain-Driven Design Reference 2014 PDF 9781457501197"


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

    assert _name(Meta(**base, manual_source="OVERDRIVE")) == "Author - Recording 2026 Overdrive MP3 64 9780123456472"
    assert _name(Meta(**base, manual_source="RETAIL")) == "Author - Recording 2026 MP3 64 9780123456472 Retail"


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

    assert _name(meta) == "Example AKA Original 2026 Director's Cut IMAX Hybrid PROPER 1080p Criterion Collection AMZN WEB-DL DD+ 5.1 Atmos HDR10+ H.264-GROUP"


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

    assert _name(meta) == "Example 2026 Extended Hybrid REPACK 2160p Criterion Collection USA UHD Blu-ray DV HDR10+ HEVC TrueHD 7.1 Atmos-GROUP"


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

    assert _name(meta) == "Daily Show 2026-08-11 1080p AMZN WEB-DL DD+ 2.0 H.264-GROUP"


def test_darkpeers_normalizes_htm_ebook_format_to_html():
    meta = Meta(category="BOOK", author="Author", title="Book", year=2026, type="HTM", isbn="978-0-123456-47-2", source="RETAIL")

    assert _name(meta) == "Author - Book 2026 HTML 9780123456472 Retail"


def test_darkpeers_scene_name_is_never_retagged_with_derived_audio():
    meta = Meta(
        category="MOVIE",
        scene_name="Original.Release.2026-GRP",
        audio_languages=["English", "French"],
        original_language="English",
        language_checked=True,
    )

    assert _name(meta) == "Original.Release.2026-GRP"


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
    adapter = DarkPeers({"DEFAULT": {"tmdb_api": "test-key"}, "TRACKERS": {"DARKPEERS": {}}})

    assert asyncio.run(adapter.get_additional_checks(Meta(**values, unattended=True))) is False

    adapter.common.prompt_user_for_confirmation = AsyncMock(return_value=True)
    assert asyncio.run(adapter.get_additional_checks(Meta(**values))) is True
    adapter.common.prompt_user_for_confirmation.assert_awaited_once_with("Do these audiobook edition details match the files?", Meta(**values))


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
    adapter = DarkPeers({"DEFAULT": {"tmdb_api": "test-key"}, "TRACKERS": {"DARKPEERS": {}}})
    adapter.common.prompt_user_for_confirmation = AsyncMock(return_value=True)

    assert asyncio.run(adapter.get_additional_checks(Meta(**base))) is False
    assert asyncio.run(adapter.get_additional_checks(Meta(**base, isbn="9780061452988"))) is False
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
    adapter = DarkPeers({"DEFAULT": {"tmdb_api": "test-key"}, "TRACKERS": {"DARKPEERS": {}}})
    adapter.common.prompt_user_for_confirmation = AsyncMock(return_value=True)

    assert asyncio.run(adapter.get_additional_checks(meta)) is True
    description = asyncio.run(adapter.get_description(meta))["description"]
    assert meta.isbn == "9780061452987"
    assert "[tr][td][b]ISBN[/b][/td][td]9780061452987[/td][/tr]" in description


def test_darkpeers_book_name_never_uses_publisher_as_author():
    meta = Meta(category="BOOK", publisher="Publisher Name", title="Book Title", year=2026, type="EPUB", isbn="978-0-123456-47-2")

    assert _name(meta) == "Book Title 2026 EPUB 9780123456472"


def test_darkpeers_book_name_preserves_alphanumeric_asin():
    meta = Meta(category="BOOK", author="Author", title="Book Title", year=2026, type="EPUB", asin="B01N5AX3TQ")

    assert _name(meta) == "Author - Book Title 2026 EPUB B01N5AX3TQ"


def test_darkpeers_rejects_asin_as_individual_ebook_identifier():
    meta = Meta(category="BOOK", unattended=True, author="Author", publisher="Publisher", title="Book Title", year=2026, type="EPUB", asin="B01N5AX3TQ", source="WEB")

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
    assert _name(meta) == "David Bohm - Wholeness and the Implicate Order 1980 AZW3 9780415289795 Retail"


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
    assert _name(meta) == "Eric Evans - Domain-Driven Design Reference 2015 PDF 9781457501197"


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
    meta = Meta(category="BOOK", author="Author", title="Book", year=2026, type="PDF", isbn="978-0-123456-47-2", source="SCAN", ocr=True)

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
    adapter = DarkPeers({"DEFAULT": {"tmdb_api": "test-key"}, "TRACKERS": {"DARKPEERS": {}}})
    adapter.common.prompt_user_for_confirmation = AsyncMock(return_value=True)

    assert asyncio.run(adapter.get_additional_checks(Meta(**base))) is False
    assert asyncio.run(adapter.get_additional_checks(Meta(**base, audiobook_bitrate=63))) is False
    assert asyncio.run(adapter.get_additional_checks(Meta(**base, audiobook_bitrate=64))) is True


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
    description = asyncio.run(DarkPeers({"DEFAULT": {"tmdb_api": "test-key"}, "TRACKERS": {"DARKPEERS": {}}}).get_description(meta))["description"]

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

    assert DarkPeers({"DEFAULT": {"tmdb_api": "test-key"}, "TRACKERS": {"DARKPEERS": {}}}).validate_music(meta) is True


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
        filelist=["Author - Author Collection - One.epub", "Author - Author Collection - Two.epub"],
    )

    assert _additional_checks(meta) is False


def test_darkpeers_requires_exact_single_file_m4b_name():
    adapter = DarkPeers({"DEFAULT": {"tmdb_api": "test-key"}, "TRACKERS": {"DARKPEERS": {}}})
    values = {
        "category": "BOOK",
        "audiobook": True,
        "author": "Author",
        "title": "Book",
        "year": 2026,
        "type": "M4B",
    }

    assert adapter._validate_book_file_layout(Meta(**values, filelist=["Author - Book - 2026.m4b"]), "M4B") is True
    assert adapter._validate_book_file_layout(Meta(**values, filelist=["Book.m4b"]), "M4B") is False


def test_darkpeers_replaces_generic_dual_audio_with_rule_matrix_label():
    meta = Meta(category="MOVIE", name="Anime 2026 1080p WEB-DL Dual-Audio-TEAM", language_checked=True, original_language="Japanese", audio_languages=["Japanese", "French"])

    assert _name(meta) == "Anime 2026 1080p WEB-DL French MULTi-TEAM"


def test_darkpeers_replaces_language_multi_with_dual_audio_for_original_and_english():
    meta = Meta(
        category="TV",
        name="Tomb Raider King AKA Dogul Wang S01E03 1080p CR WEB-DL Korean MULTi DD+ 2.0 H.264-AnoZu",
        language_checked=True,
        original_language="Korean",
        audio_languages=["Korean", "English"],
    )

    assert _name(meta) == "Tomb Raider King AKA Dogul Wang S01E03 1080p CR WEB-DL Dual-Audio DD+ 2.0 H.264-AnoZu"


def test_darkpeers_applies_dub_matrix_to_existing_title_element():
    cases = [
        (["Korean", "English", "French"], "Korean MULTi", "MULTi", "Korean"),
        (["English"], "Korean MULTi", "Dubbed", "Korean"),
        (["English", "French"], "Dual-Audio", "French MULTi", "English"),
        (["Korean"], "Dual-Audio", "", "Korean"),
    ]

    for languages, existing, expected, original in cases:
        meta = Meta(
            category="TV",
            name=f"Example S01E01 1080p WEB-DL {existing} DD+ 2.0 H.264-GROUP",
            language_checked=True,
            original_language=original,
            audio_languages=languages,
        )
        expected_element = f" {expected}" if expected else ""
        assert _name(meta) == f"Example S01E01 1080p WEB-DL{expected_element} DD+ 2.0 H.264-GROUP"


def test_darkpeers_preserves_detected_original_scene_name():
    meta = Meta(category="MOVIE", name="Generated Name", scene=True, scene_name="Original.Release.2026-GRP", language_checked=True)

    assert _name(meta) == "Original.Release.2026-GRP"


def test_darkpeers_ignores_absolute_scene_name_when_building_upload_title():
    meta = Meta(
        category="MOVIE",
        name="Full Contact AKA Hap do Ko Fei 1992 480p BluRay Dual-Audio AAC 1.0 x264-gazer",
        scene=True,
        scene_name="/home/seedbox/data/torrents/Full.Contact.1992.OAR.BDRip.x264-GAZER/full.contact.1992.oar.bdrip.x264-gazer.mkv",
        language_checked=True,
    )

    assert _name(meta) == "Full Contact AKA Hap do Ko Fei 1992 480p BluRay Dual-Audio AAC 1.0 x264-gazer"


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


def test_darkpeers_evo_webdl_allowed_and_non_webdl_blocked():
    evo_webdl = Meta(category="MOVIE", type="WEBDL", tag="-EVO", audio_languages=["English"], resolution="1080p", screens=3)
    evo_encode = Meta(category="MOVIE", type="ENCODE", tag="-EVO", audio_languages=["English"], resolution="1080p", screens=3)
    evo_remux = Meta(category="MOVIE", type="REMUX", tag="EVO", audio_languages=["English"], resolution="1080p", screens=3)

    assert _additional_checks(evo_webdl) is True
    assert _additional_checks(evo_encode) is False
    assert _additional_checks(evo_remux) is False


def test_darkpeers_hdt_remux_allowed_and_non_remux_blocked():
    hdt_remux = Meta(category="MOVIE", type="REMUX", tag="-HDT", audio_languages=["English"], resolution="1080p", screens=3)
    hdt_webdl = Meta(category="MOVIE", type="WEBDL", tag="-HDT", audio_languages=["English"], resolution="1080p", screens=3)
    hdt_encode = Meta(category="MOVIE", type="ENCODE", tag="HDT", audio_languages=["English"], resolution="1080p", screens=3)

    assert _additional_checks(hdt_remux) is True
    assert _additional_checks(hdt_webdl) is False
    assert _additional_checks(hdt_encode) is False


def test_darkpeers_movie_tv_require_between_three_and_five_screens():
    assert _additional_checks(Meta(category="MOVIE", audio_languages=["English"], resolution="1080p", screens=2)) is False
    assert _additional_checks(Meta(category="TV", type="WEBDL", audio_languages=["English"], resolution="1080p", screens=3)) is True
    assert _additional_checks(Meta(category="TV", type="WEBDL", audio_languages=["English"], resolution="1080p", screens=5)) is True
    assert _additional_checks(Meta(category="MOVIE", type="WEBDL", audio_languages=["English"], resolution="1080p", screens=6)) is False


def test_darkpeers_movie_tv_invalid_screens_value_is_treated_as_missing():
    assert _additional_checks(Meta(category="MOVIE", audio_languages=["English"], resolution="1080p", screens="many")) is False


def test_darkpeers_hardcoded_subs_blocked_in_interactive_and_unattended():
    subs_interactive = Meta(category="MOVIE", type="WEBDL", tag="-GRP", hardcoded_subs=True, unattended=False, audio_languages=["English"], resolution="1080p", screens=3)
    subs_unattended = Meta(category="MOVIE", type="WEBDL", tag="-GRP", hardcoded_subs=True, unattended=True, audio_languages=["English"], resolution="1080p", screens=3)
    no_subs_unattended = Meta(category="MOVIE", type="WEBDL", tag="-GRP", hardcoded_subs=False, unattended=True, audio_languages=["English"], resolution="1080p", screens=3)

    assert _additional_checks(subs_interactive) is False
    assert _additional_checks(subs_unattended) is False
    assert _additional_checks(no_subs_unattended) is True


def test_darkpeers_video_language_rule_requires_original_audio_with_accepted_subtitles():
    original_with_subtitles = Meta(
        category="MOVIE", unattended=True, audio_languages=["jpn"], subtitle_languages=["Swedish"], original_language="Japanese", resolution="1080p", screens=3
    )
    foreign_dub_with_subtitles = Meta(
        category="MOVIE", unattended=True, audio_languages=["Spanish"], subtitle_languages=["English"], original_language="Japanese", resolution="1080p", screens=3
    )

    assert _additional_checks(original_with_subtitles) is True
    assert _additional_checks(foreign_dub_with_subtitles) is False


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
