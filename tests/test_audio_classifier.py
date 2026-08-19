"""Tests for audio category classification (BOOK/audiobook vs MUSIC vs ambiguous)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.domain_models.processing import ItemProcessingError
from src.domain_models.release import Meta
from src.services.audio_classification_service import AudioCategoryResult, detect_audio_category
from src.services.preparation_helpers import detect_disc_and_category


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

    with patch("src.services.audio_classification_service._inspect_audio_file", return_value=_podcast_audio_metadata(genre)):
        result = asyncio.run(detect_audio_category(Meta(), track))

    assert result.category == "PODCAST"
    assert result.is_audiobook is False
    assert any("podcast metadata genre" in evidence for evidence in result.evidence)


def test_podcast_detection_flows_into_prep_category(tmp_path):
    track = tmp_path / "Example Show - 2026-08-18 - Episode.mp3"
    track.write_bytes(b"not tagged")
    meta = Meta(path=str(track))
    prep = SimpleNamespace(disc_info_manager=SimpleNamespace(get_disc=AsyncMock(return_value=("", str(track), {}, []))))

    with patch("src.services.audio_classification_service._inspect_audio_file", return_value=_podcast_audio_metadata("podcast")):
        asyncio.run(detect_disc_and_category(prep, meta))

    assert meta.category == "PODCAST"
    assert meta.audiobook is False


def test_legacy_book_fallback_does_not_claim_shared_mp3_extension(tmp_path):
    track = tmp_path / "spoken-or-music.mp3"
    track.write_bytes(b"unknown")
    meta = Meta(path=str(track))
    prep = SimpleNamespace(disc_info_manager=SimpleNamespace(get_disc=AsyncMock(return_value=("", str(track), {}, []))))

    with patch("src.services.audio_classification_service.detect_audio_category", new=AsyncMock(return_value=AudioCategoryResult(category="NONE"))):
        asyncio.run(detect_disc_and_category(prep, meta))

    assert meta.category != "BOOK"
    assert meta.audiobook is False


def test_dated_single_long_track_is_music(tmp_path):
    track = tmp_path / "The Bruenigs - 2026-08-10 - Summers End.mp3"
    track.write_bytes(b"not tagged")

    result = asyncio.run(detect_audio_category(Meta(), track))

    assert result.category == "MUSIC"
    assert "dated artist/title music release name" in result.evidence


def test_inspect_audio_file_reads_easy_technical_and_vendor_tags(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from src.services import audio_classification_service as classifier

    class Tags(dict):
        def __iter__(self):
            return iter(self.keys())

    easy = SimpleNamespace(
        tags=Tags(
            genre=[" Spoken Word "],
            title=["Example Title"],
            artist=["Example Artist"],
            album=["Example Album"],
            albumartist=["Example Album Artist"],
        )
    )
    full = SimpleNamespace(
        info=SimpleNamespace(channels=1, bitrate=64000, sample_rate=22050, length=1800.0),
        tags=Tags(
            CHAP001="Chapter 1",
            narrator="Example Narrator",
            author="Example Author",
            publisher="Example Publisher",
            isbn="9780000000000",
            asin="B000000000",
            musicbrainz_albumid="musicbrainz-id",
            discogs_release_id="discogs-id",
            catalognumber="CAT-001",
            genre="Spoken Word",
            chapter01="Opening",
        ),
    )

    def fake_file(_path, *, easy=False):
        return globals_easy if easy else globals_full

    globals_easy = easy
    globals_full = full
    monkeypatch.setattr(classifier.mutagen, "File", fake_file)
    track = tmp_path / "track.mp3"
    track.write_bytes(b"audio")

    result = classifier._inspect_audio_file(track)

    assert result["genres"] == ["Spoken Word"]
    assert result["title"] == "Example Title"
    assert result["artist"] == "Example Artist"
    assert result["album"] == "Example Album"
    assert result["albumartist"] == "Example Album Artist"
    assert result["channels"] == 1
    assert result["bitrate"] == 64000
    assert result["sample_rate"] == 22050
    assert result["length"] == 1800.0
    assert result["narrator"] == "Example Narrator"
    assert result["author"] == "Example Author"
    assert result["publisher"] == "Example Publisher"
    assert result["isbn"] == "9780000000000"
    assert result["asin"] == "B000000000"
    assert result["has_chapters"] is True
    assert result["has_musicbrainz"] is True
    assert result["has_discogs"] is True
    assert result["has_catalog_no"] is True
    assert "chapter01=Opening".lower() in result["raw_tag_text"]


def test_audio_classifier_none_video_and_ebook_only_paths(tmp_path):
    missing = asyncio.run(detect_audio_category(Meta(), tmp_path / "missing"))
    assert missing.category == "NONE"

    video = tmp_path / "video.mkv"
    video.write_bytes(b"video")
    result = asyncio.run(detect_audio_category(Meta(), video))
    assert result.category == "NONE"
    assert result.evidence == ["Contains video files"]

    empty = tmp_path / "readme.txt"
    empty.write_text("not media", encoding="utf-8")
    assert asyncio.run(detect_audio_category(Meta(), empty)).category == "NONE"

    ebook = tmp_path / "book.epub"
    ebook.write_bytes(b"ebook")
    result = asyncio.run(detect_audio_category(Meta(), ebook))
    assert result.category == "BOOK"
    assert result.is_audiobook is False


def test_audio_classifier_accumulates_all_audiobook_evidence(tmp_path):
    track = tmp_path / "Chapter 01.mp3"
    track.write_bytes(b"audio")
    metadata = {
        "channels": 1,
        "bitrate": 64000,
        "sample_rate": 22050,
        "length": 1800.0,
        "genres": ["spoken   word"],
        "title": "Chapter One",
        "artist": "",
        "album": "Book",
        "albumartist": "",
        "narrator": "Narrator",
        "author": "Author",
        "publisher": "Publisher",
        "isbn": "9780000000000",
        "asin": "B000000000",
        "has_chapters": True,
        "has_musicbrainz": False,
        "has_discogs": False,
        "has_catalog_no": False,
        "raw_tag_text": "chapter01",
    }
    with patch("src.services.audio_classification_service._inspect_audio_file", return_value=metadata):
        result = asyncio.run(detect_audio_category(Meta(), track))

    assert result.category == "BOOK"
    assert result.is_audiobook is True
    evidence = " | ".join(result.evidence)
    for expected in ("spoken-word", "chapter", "narrator", "author", "ISBN", "mono", "low bitrate", "low sample", "long individual"):
        assert expected.casefold() in evidence.casefold()


def test_audio_classifier_accumulates_music_metadata_evidence(tmp_path):
    track = tmp_path / "01 - Example Song.flac"
    track.write_bytes(b"audio")
    metadata = {
        "channels": 2,
        "bitrate": 900000,
        "sample_rate": 44100,
        "length": 240.0,
        "genres": ["rock"],
        "title": "Example Song",
        "artist": "Artist",
        "album": "Album",
        "albumartist": "Artist",
        "narrator": "",
        "author": "",
        "publisher": "",
        "isbn": "",
        "asin": "",
        "has_chapters": False,
        "has_musicbrainz": True,
        "has_discogs": True,
        "has_catalog_no": True,
        "raw_tag_text": "musicbrainz discogs catalognumber",
    }
    with patch("src.services.audio_classification_service._inspect_audio_file", return_value=metadata):
        result = asyncio.run(detect_audio_category(Meta(), track))

    assert result.category == "MUSIC"
    evidence = " | ".join(result.evidence)
    for expected in ("numbered song", "recognized music genre", "MusicBrainz", "Discogs", "catalogue"):
        assert expected.casefold() in evidence.casefold()


def test_audio_classifier_returns_ambiguous_without_metadata(tmp_path):
    track = tmp_path / "unknown.mp3"
    track.write_bytes(b"audio")
    metadata = _podcast_audio_metadata("")
    with patch("src.services.audio_classification_service._inspect_audio_file", return_value=metadata):
        result = asyncio.run(detect_audio_category(Meta(), track))
    assert result.category == "AMBIGUOUS"
    assert result.confidence == 0.0


def test_inspect_audio_file_uses_full_tag_genre_when_easy_tags_have_none(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from src.services import audio_classification_service as classifier

    easy = SimpleNamespace(tags={})
    full = SimpleNamespace(info=None, tags={"genre": "Jazz"})
    calls = iter((easy, full))
    monkeypatch.setattr(classifier.mutagen, "File", lambda *_args, **_kwargs: next(calls))
    track = tmp_path / "track.mp3"
    track.write_bytes(b"audio")

    assert classifier._inspect_audio_file(track)["genres"] == ["Jazz"]
