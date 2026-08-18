# ruff: noqa: S101
"""Tests for audio category classification (BOOK/audiobook vs MUSIC vs ambiguous)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.audio_classifier import AudioCategoryResult, detect_audio_category
from src.exceptions import ItemProcessingError
from src.meta import Meta
from src.prep_helpers import detect_disc_and_category


def test_m4b_audiobook_detected_as_book(tmp_path):
    m4b_file = tmp_path / "testbook.m4b"
    m4b_file.write_bytes(b"dummy m4b content")
    meta = Meta(path=str(tmp_path))
    prep = SimpleNamespace(disc_info_manager=SimpleNamespace(get_disc=AsyncMock(return_value=("", str(tmp_path), {}, []))))

    asyncio.run(detect_disc_and_category(prep, meta))

    assert meta.category == "BOOK"
    assert meta.audiobook is True


def test_mp3_audiobook_with_part_filenames_detected_as_book(tmp_path):
    p1 = tmp_path / "Book-Part01.mp3"
    p2 = tmp_path / "Book-Part02.mp3"
    p3 = tmp_path / "Book-Part03.mp3"
    p1.write_bytes(b"dummy mp3 content 1")
    p2.write_bytes(b"dummy mp3 content 2")
    p3.write_bytes(b"dummy mp3 content 3")

    meta = Meta(path=str(tmp_path))
    prep = SimpleNamespace(disc_info_manager=SimpleNamespace(get_disc=AsyncMock(return_value=("", str(tmp_path), {}, []))))

    asyncio.run(detect_disc_and_category(prep, meta))

    assert meta.category == "BOOK"
    assert meta.audiobook is True


def test_mixed_ebook_and_audiobook_folder_detected_as_book(tmp_path):
    epub = tmp_path / "Book.epub"
    mp3 = tmp_path / "Book-Part01.mp3"
    epub.write_bytes(b"dummy epub")
    mp3.write_bytes(b"dummy mp3")

    meta = Meta(path=str(tmp_path))
    prep = SimpleNamespace(disc_info_manager=SimpleNamespace(get_disc=AsyncMock(return_value=("", str(tmp_path), {}, []))))

    asyncio.run(detect_disc_and_category(prep, meta))

    assert meta.category == "BOOK"
    assert meta.audiobook is True


def test_explicit_book_category_override_takes_priority(tmp_path):
    mp3 = tmp_path / "audio.mp3"
    mp3.write_bytes(b"dummy mp3")

    meta = Meta(path=str(tmp_path), manual_category="book")
    prep = SimpleNamespace(disc_info_manager=SimpleNamespace(get_disc=AsyncMock(return_value=("", str(tmp_path), {}, []))))

    asyncio.run(detect_disc_and_category(prep, meta))

    assert meta.category == "BOOK"
    assert meta.audiobook is True


def test_explicit_music_category_override_takes_priority(tmp_path):
    m4b = tmp_path / "book.m4b"
    m4b.write_bytes(b"dummy m4b")

    meta = Meta(path=str(tmp_path), manual_category="music")
    prep = SimpleNamespace(disc_info_manager=SimpleNamespace(get_disc=AsyncMock(return_value=("", str(tmp_path), {}, []))))

    asyncio.run(detect_disc_and_category(prep, meta))

    assert meta.category == "MUSIC"
    assert meta.audiobook is False


def test_ambiguous_audio_folder_prompts_in_interactive_mode(tmp_path):
    t1 = tmp_path / "track_alpha.mp3"
    t2 = tmp_path / "track_beta.mp3"
    t1.write_bytes(b"dummy mp3 1")
    t2.write_bytes(b"dummy mp3 2")

    meta = Meta(path=str(tmp_path), unattended=False)
    prep = SimpleNamespace(disc_info_manager=SimpleNamespace(get_disc=AsyncMock(return_value=("", str(tmp_path), {}, []))))

    with patch("cli_ui.ask_choice", return_value="2. Audiobook"):
        asyncio.run(detect_disc_and_category(prep, meta))

    assert meta.category == "BOOK"
    assert meta.audiobook is True


def test_ambiguous_audio_prompt_does_not_hide_unexpected_errors(tmp_path):
    t1 = tmp_path / "track_alpha.mp3"
    t2 = tmp_path / "track_beta.mp3"
    t1.write_bytes(b"dummy mp3 1")
    t2.write_bytes(b"dummy mp3 2")

    meta = Meta(path=str(tmp_path), unattended=False)
    prep = SimpleNamespace(disc_info_manager=SimpleNamespace(get_disc=AsyncMock(return_value=("", str(tmp_path), {}, []))))

    with patch("cli_ui.ask_choice", side_effect=RuntimeError("unexpected prompt failure")), pytest.raises(RuntimeError, match="unexpected prompt failure"):
        asyncio.run(detect_disc_and_category(prep, meta))


def test_ambiguous_audio_folder_fails_in_unattended_mode(tmp_path):
    t1 = tmp_path / "track_alpha.mp3"
    t2 = tmp_path / "track_beta.mp3"
    t1.write_bytes(b"dummy mp3 1")
    t2.write_bytes(b"dummy mp3 2")

    meta = Meta(path=str(tmp_path), unattended=True, unattended_confirm=False)
    prep = SimpleNamespace(disc_info_manager=SimpleNamespace(get_disc=AsyncMock(return_value=("", str(tmp_path), {}, []))))

    with pytest.raises(ItemProcessingError, match="Could not determine if release is MUSIC, PODCAST, or BOOK"):
        asyncio.run(detect_disc_and_category(prep, meta))


def test_untagged_flac_release_in_lidarr_is_music(tmp_path):
    release = tmp_path / "lidarr" / "Sweet Trip - A Tiny House (2021) - WEB FLAC"
    release.mkdir(parents=True)
    (release / "track.flac").write_bytes(b"not tagged")

    result = asyncio.run(detect_audio_category(Meta(), release))

    assert result.category == "MUSIC"
    assert "Lidarr library path" in result.evidence


def test_structured_untagged_flac_release_name_is_music(tmp_path):
    release = tmp_path / "Ross From Friends - Tread (2021) [FLAC]"
    release.mkdir()
    (release / "track.flac").write_bytes(b"not tagged")

    result = asyncio.run(detect_audio_category(Meta(), release))

    assert result.category == "MUSIC"
    assert "structured artist/album music release name" in result.evidence


def test_scene_single_without_tags_is_music(tmp_path):
    release = tmp_path / "Eskila-Tequetom-(7490926)-SINGLE-WEB-2026-LiBi"
    release.mkdir()
    (release / "01-eskila-tequetom.mp3").write_bytes(b"not tagged")

    result = asyncio.run(detect_audio_category(Meta(), release))

    assert result.category == "MUSIC"
    assert "scene music release name" in result.evidence


def _podcast_audio_metadata(genre: str) -> dict[str, object]:
    return {
        "channels": 2,
        "bitrate": 192000,
        "sample_rate": 44100,
        "length": 600.0,
        "genres": [genre],
        "title": "Episode",
        "artist": "Example Show",
        "album": "Example Show",
        "albumartist": "",
        "narrator": "",
        "author": "",
        "publisher": "",
        "isbn": "",
        "asin": "",
        "has_chapters": False,
        "has_musicbrainz": False,
        "has_discogs": False,
        "has_catalog_no": False,
        "raw_tag_text": "",
    }


@pytest.mark.parametrize("genre", ["podcast", "News & Politics"])
def test_podcast_genres_are_not_misclassified_as_audiobooks(tmp_path, genre):
    track = tmp_path / "Example Show - 2026-08-18 - Episode.mp3"
    track.write_bytes(b"not tagged")

    with patch("src.audio_classifier._inspect_audio_file", return_value=_podcast_audio_metadata(genre)):
        result = asyncio.run(detect_audio_category(Meta(), track))

    assert result.category == "PODCAST"
    assert result.is_audiobook is False
    assert any("podcast metadata genre" in evidence for evidence in result.evidence)


def test_podcast_detection_flows_into_prep_category(tmp_path):
    track = tmp_path / "Example Show - 2026-08-18 - Episode.mp3"
    track.write_bytes(b"not tagged")
    meta = Meta(path=str(track))
    prep = SimpleNamespace(disc_info_manager=SimpleNamespace(get_disc=AsyncMock(return_value=("", str(track), {}, []))))

    with patch("src.audio_classifier._inspect_audio_file", return_value=_podcast_audio_metadata("podcast")):
        asyncio.run(detect_disc_and_category(prep, meta))

    assert meta.category == "PODCAST"
    assert meta.audiobook is False


def test_legacy_book_fallback_does_not_claim_shared_mp3_extension(tmp_path):
    track = tmp_path / "spoken-or-music.mp3"
    track.write_bytes(b"unknown")
    meta = Meta(path=str(track))
    prep = SimpleNamespace(disc_info_manager=SimpleNamespace(get_disc=AsyncMock(return_value=("", str(track), {}, []))))

    with patch("src.audio_classifier.detect_audio_category", new=AsyncMock(return_value=AudioCategoryResult(category="NONE"))):
        asyncio.run(detect_disc_and_category(prep, meta))

    assert meta.category != "BOOK"
    assert meta.audiobook is False


def test_dated_single_long_track_is_music(tmp_path):
    track = tmp_path / "The Bruenigs - 2026-08-10 - Summers End.mp3"
    track.write_bytes(b"not tagged")

    result = asyncio.run(detect_audio_category(Meta(), track))

    assert result.category == "MUSIC"
    assert "dated artist/title music release name" in result.evidence
