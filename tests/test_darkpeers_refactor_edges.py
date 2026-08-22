from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.domain_models.release import Meta
from tests.test_darkpeers_branch_edges import _book_meta, _tracker


@pytest.mark.asyncio
async def test_darkpeers_refactor_video_dispatch_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        tracker, "validate_video_quality", AsyncMock(return_value=True)
    )
    assert await tracker._video_payload_checks(Meta(is_disc="BDMV"))
    monkeypatch.setattr(tracker, "validate_music", lambda _meta: True)
    assert await tracker._category_checks(Meta(category="MUSIC"), "MUSIC")
    assert await tracker._category_checks(Meta(category="OTHER"), "OTHER")
    monkeypatch.setattr(
        tracker, "_confirm_or_skip", AsyncMock(return_value=True)
    )
    assert await tracker.validate_video_resolution(Meta(resolution="360p"))
    assert not tracker._renamed_tagged_video_path(Path("readme.txt"), "grp")
    invalid = Meta()
    invalid.filelist = "bad"  # type: ignore[assignment]
    assert tracker._video_file_items(invalid) is None
    assert not tracker.validate_video_files(invalid)
    assert not tracker._is_single_tv_season(Meta(episode="E01"))


@pytest.mark.asyncio
async def test_darkpeers_refactor_book_validation_edges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    unsupported = _book_meta(type="XYZ")
    assert not tracker._book_format_allowed(unsupported, "XYZ")
    audiobook = _book_meta(audiobook=True, isbn="", book_isbn="")
    assert not tracker._audiobook_isbn_allowed(audiobook, False, None)
    lossy = _book_meta(audiobook=True, audiobook_bitrate="bad")
    assert not await tracker._lossy_audiobook_bitrate_allowed(lossy)
    monkeypatch.setattr(
        tracker, "_book_format_allowed", lambda _meta, _format: False
    )
    assert await tracker._book_identity_phase(_book_meta()) is None
    assert tracker._invalid_audiobook_numbering([Path("01 Chapter.mp3")]) == ""
    assert (
        tracker._invalid_audiobook_numbering(
            [Path("01 Chapter.mp3"), Path("02 Chapter.mp3")]
        )
        == ""
    )
    audio_file = tmp_path / "book.m4b"
    audio_file.write_text("x")
    file_meta = _book_meta(audiobook=True, path=str(audio_file))
    assert not tracker._audiobook_file_layout_allowed(
        file_meta, audio_file, "M4B"
    )
    missing_path = tmp_path / "missing.epub"
    assert tracker._ebook_file_layout_allowed(_book_meta(), missing_path)
    assert tracker._normalized_isbn("") == ""


def test_darkpeers_refactor_book_media_helpers() -> None:
    tracker = _tracker()
    assert tracker._raw_book_tracks(Meta(mediainfo={"media": []})) == []
    assert tracker._book_audio_track("bad") is None
    assert tracker._book_track_codec_text({}) == ""
    assert tracker._book_audio_alias("OTHER", "") == "OTHER"


def test_darkpeers_refactor_music_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    meta = Meta(filelist=["Track.mp3", "cover.jpg"])
    assert tracker._filelist_music_paths(meta) == ["Track.mp3"]
    assert tracker._music_path_invalid("a" * 181 + ".mp3", Path())
    assert not tracker._music_filename_numbered("artist.album.01.flac", False)
    monkeypatch.setattr(
        tracker, "_music_validation_errors", lambda _release: ["invalid"]
    )
    assert not tracker._music_release_valid(tracker._music_release(Meta()))
    monkeypatch.setattr(
        tracker, "_music_release_valid", lambda _release: False
    )
    assert not tracker.validate_music(Meta())
    monkeypatch.setattr(tracker, "_music_release_valid", lambda _release: True)
    monkeypatch.setattr(tracker, "_music_root_allowed", lambda *_args: False)
    assert not tracker.validate_music(Meta(path="", title="Album"))
    assert tracker._first_music_track({"tracks": []}) == {}
    assert (
        tracker._lossless_music_detail(
            {
                "fields": {
                    "nfo_bit_depth": {"value": None},
                    "nfo_sample_rate": {"value": None},
                }
            },
            {},
        )
        == ""
    )
    assert (
        tracker._lossless_music_detail(
            {"nfo_bit_depth": "bad", "nfo_sample_rate": "bad"}, {}
        )
        == ""
    )
    assert tracker._lossy_music_bitrate(Meta(), {}) == ""
    assert tracker._lossy_music_bitrate(Meta(audio_bitrate="bad"), {}) == ""
    assert tracker._music_format_details(Meta(), {}, {}, "OTHER") == []
    assert tracker._render_music_title("Artist", "Album", "") == (
        "Artist - Album"
    )


@pytest.mark.asyncio
async def test_darkpeers_refactor_game_confirm_and_misc_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    assert not tracker._game_scene_metadata_allowed(Meta(scene=False))
    assert not tracker._game_scene_metadata_allowed(
        Meta(scene=True, scene_nfo_file="")
    )
    assert not tracker._game_scene_payload_allowed(
        Meta(filelist=["release.iso"], scene=True, scene_nfo_file="x.nfo")
    )
    assert not tracker._game_scene_payload_allowed(
        Meta(filelist=["readme.nfo"], scene=True, scene_nfo_file="x.nfo")
    )
    unattended = Meta(unattended=True, unattended_confirm=True)
    assert await tracker._confirm_or_skip("message", unattended)
    attended = Meta(unattended=False)
    monkeypatch.setattr(
        tracker.common,
        "prompt_user_for_confirmation",
        AsyncMock(return_value=True),
    )
    assert await tracker._confirm_or_skip("message", attended)
    data = await tracker.get_additional_data(Meta())
    assert data == {"mod_queue_opt_in": "0"}
    assert tracker._single_foreign_audio_label("spanish", "") == "SKIPPED"
    assert (
        tracker._other_audio_multi_label({"spanish", "french", "german"})
        == "SKIPPED"
    )


@pytest.mark.asyncio
async def test_darkpeers_refactor_video_name_helper_edges() -> None:
    tracker = _tracker()
    assert tracker._normalize_aka_year_order("", "Title", "AKA", "2026") == ""
    assert tracker._video_season_episode(Meta(no_season=True)) == ("", False)
    assert tracker._ds4k_flag("REMUX", True, "Movie DS4K") == ""
    assert tracker._dvdrip_video_source("NTSC DVD") == "NTSC DVDRip"
    assert tracker._dvdrip_video_source("PAL DVD") == "PAL DVDRip"
    assert tracker._dvdrip_video_source("DVD") == "DVDRip"
    assert tracker._disc_video_source(Meta(), "HDTV") == "HDTV"
    assert tracker._remux_video_source(Meta(), "BluRay") == "BluRay"
    assert tracker._apply_dub_element("Title 1080p", "Dubbed") == (
        "Title 1080p"
    )
    assert (
        tracker._tv_result_id_if_match({"name": "Show", "id": ""}, "show")
        == ""
    )
    assert (
        tracker._tv_result_id_if_match({"name": "Other", "id": "1"}, "show")
        == ""
    )
    assert not await tracker._tv_title_needs_year(Meta(title=""))


@pytest.mark.asyncio
async def test_darkpeers_refactor_category_and_type_id_edges() -> None:
    tracker = _tracker()
    mapping = await tracker.get_category_id(Meta(), mapping_only=True)
    assert mapping["MOVIE"] == "1"
    reverse = await tracker.get_category_id(Meta(), reverse=True)
    assert reverse["1"] == "MOVIE"
    assert await tracker.get_category_id(Meta(category="TV"), category="") == {
        "category_id": "2"
    }
    assert tracker._book_meta_type(Meta(audiobook=True), "") == "AUDIOBOOK"
    assert tracker._book_meta_type(Meta(), "") == "EBOOK"
    assert tracker._game_meta_type(Meta(console_game=True)) == "CONSOLE"
    type_mapping = await tracker.get_type_id(Meta(), mapping_only=True)
    assert type_mapping["DISC"] == "1"
    type_reverse = await tracker.get_type_id(Meta(), reverse=True)
    assert type_reverse["1"] == "DISC"
