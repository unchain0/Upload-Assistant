from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D import UNIT3D
from src.integrations.trackers.UNIT3D.znth import (
    Zenith,
    _normalized_tracker_names,
    _uses_zenith_music_layout,
    prepare_zenith_music_layout,
)
from tests.test_znth import (
    _audiobook_meta,
    _book_meta,
    _movie_meta,
    _tracker,
    _tv_meta,
)


def test_zenith_layout_and_tracker_guard_branches() -> None:
    movie = Meta(category="MOVIE", trackers=["ZENITH"])
    prepare_zenith_music_layout(movie)
    assert not _uses_zenith_music_layout(movie)
    assert _normalized_tracker_names(None) == set()


def test_zenith_author_guard_branches() -> None:
    tracker = _tracker()
    assert tracker._normalize_author("") == set()
    assert tracker._split_authors("") == []
    assert tracker._split_author_candidate("   ") == []
    assert tracker._split_comma_author("Smith, John") == ["Smith, John"]
    assert tracker._looks_like_last_first("J T")
    assert not tracker._is_banned_author("")


def test_zenith_music_release_error_for_root_and_missing_tracks() -> None:
    tracker = _tracker()
    file_root = Meta(
        category="MUSIC",
        path="track.flac",
        artist="Artist",
        title="Album",
        music_release={},
    )
    assert (
        tracker._music_release_error(file_root, ["track.flac"])
        == "music uploads must be inside a directory"
    )

    missing_tracks = Meta(
        category="MUSIC",
        path="Artist - Album",
        artist="Artist",
        title="Album",
        music_release={
            "fields": {
                "artist": {"value": "Artist"},
                "album": {"value": "Album"},
            },
            "tracks": [],
        },
    )
    assert (
        tracker._music_release_error(missing_tracks, [])
        == "music metadata does not contain any audio tracks"
    )


def test_zenith_music_path_and_filename_final_branches() -> None:
    tracker = _tracker()
    path = Path("Track One.flac")
    assert tracker._torrent_music_path(path, None) == path
    assert tracker._invalid_music_filename(path, 2)


def test_zenith_audiobook_layout_remaining_branches() -> None:
    tracker = _tracker()
    meta = _audiobook_meta()
    assert (
        tracker._audiobook_layout_error(meta, [], meta.path)
        == "audiobook does not contain a supported audio file"
    )

    multi = [
        Path("01. First - Book (2020).mp3"),
        Path("02. Second - Book (2020).mp3"),
    ]
    multi_meta = _book_meta(title="Book", year=2020, path="Book")
    assert (
        tracker._audiobook_layout_error(
            multi_meta, [str(path) for path in multi], "Book"
        )
        == ""
    )

    m4b = [
        Path("01. First - Book (2020).m4b"),
        Path("02. Second - Book (2020).mp3"),
    ]
    assert "M4B audiobooks" in tracker._multi_audiobook_layout_error(
        multi_meta, m4b
    )

    invalid = [Path("bad name.mp3"), Path("02. Second - Book (2020).mp3")]
    assert "bad name.mp3" in tracker._multi_audiobook_layout_error(
        multi_meta, invalid
    )


def test_zenith_audiobook_language_without_audio_track() -> None:
    assert (
        _tracker()._audiobook_language_error(
            _audiobook_meta(mediainfo={"media": {"track": []}})
        )
        == "MediaInfo does not contain an audio track"
    )


@pytest.mark.asyncio
async def test_zenith_video_storage_and_tv_scope_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    movie = _movie_meta(is_disc="")
    assert tracker._video_storage_policy(
        movie, movie.filelist, tracker._collect_video_paths(movie.filelist)
    )

    monkeypatch.setattr(
        tracker.common, "extract_tv_seasons", lambda _files: {1, 2}
    )
    assert not await tracker._tv_scope_policy(
        _tv_meta(), ["S01E01.mkv", "S02E01.mkv"]
    )

    monkeypatch.setattr(
        tracker.common, "extract_tv_seasons", lambda _files: {1}
    )
    monkeypatch.setattr(tracker.common, "count_tv_episodes", lambda _files: 2)
    assert not tracker._tv_episode_policy(
        _tv_meta(tv_pack=False), ["S01E01.mkv", "S01E02.mkv"]
    )


@pytest.mark.asyncio
async def test_zenith_deferred_audiobook_and_book_identity_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    deferred = _audiobook_meta()
    assert await tracker._audiobook_policy(deferred, deferred.filelist, True)

    monkeypatch.setattr(tracker, "_book_author_policy", lambda _meta: True)
    monkeypatch.setattr(tracker, "_book_work_policy", lambda _meta: False)
    assert not tracker._book_identity_policy(_book_meta())

    banned = _book_meta(author="J.R.R. Tolkien")
    assert not _tracker()._book_author_policy(banned)


def test_zenith_movie_episode_token_and_misc_book_name() -> None:
    tracker = _tracker()
    assert not tracker._video_episode_title_policy(
        _movie_meta(), "MOVIE", "Movie S01E01 1080p WEB-DL"
    )
    misc = Meta(category="BOOK", comic=True, name="Comic Name")
    assert tracker._book_release_name(misc) == "Comic Name"


def test_zenith_ebook_series_edition_and_source_branches() -> None:
    tracker = _tracker()
    meta = _book_meta(book_series="Series", book_series_index="2")
    assert tracker._ebook_series(meta) == "Series #2"
    assert tracker._ebook_edition(_book_meta(edition="")) == ""
    assert tracker._ebook_edition(_book_meta(edition="First Edition")) == ""
    assert (
        tracker._ebook_source(_book_meta(manual_source="RETAIL"), "EPUB")
        == "RETAIL"
    )
    assert (
        tracker._declared_ebook_source(_book_meta(manual_source="SCAN"))
        == "SCAN"
    )


def test_zenith_canonical_year_and_empty_aka_branches() -> None:
    tracker = _tracker()
    meta = _movie_meta(year=2020, search_year=2019, imdb_info={"year": "2021"})
    assert tracker._release_year(meta) == "2020"
    assert (
        tracker._normalize_aka_year_order(
            "Movie 2020", "Movie", "AKA ", "2020"
        )
        == "Movie 2020"
    )


def test_zenith_music_bitrate_invalid_value_is_ignored() -> None:
    assert (
        Zenith._music_bitrate_label({"bitrate": "not-a-number"}, "MP3") == ""
    )


@pytest.mark.asyncio
async def test_zenith_additional_files_adds_audiobook_cover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        UNIT3D, "get_additional_files", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        tracker,
        "get_image_file",
        AsyncMock(return_value=("cover.jpg", b"cover", "image/jpeg")),
    )
    meta = _audiobook_meta(artwork_path="cover.jpg")
    files = await tracker.get_additional_files(meta)
    assert files["torrent-cover"] == ("cover.jpg", b"cover", "image/jpeg")
    tracker.get_image_file.assert_awaited_once_with(
        "cover.jpg", max_size=3 * 1024 * 1024
    )


@pytest.mark.asyncio
async def test_zenith_audiobook_cover_above_three_mib_is_not_uploaded(
    tmp_path: Path,
) -> None:
    tracker = _tracker()
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"\xff\xd8\xff" + b"x" * (3 * 1024 * 1024))
    meta = _audiobook_meta(artwork_path=str(cover))

    files = await tracker.get_additional_files(meta)

    assert "torrent-cover" not in files


def test_zenith_torrent_music_path_preserves_existing_root_component() -> None:
    root = Path("Artist - Album")
    path = root / "01 - Track.flac"
    assert Zenith._torrent_music_path(path, root) == path


def test_zenith_video_storage_rejects_unsupported_disc_structure() -> None:
    meta = _movie_meta(is_disc="ISO")
    assert not _tracker()._video_storage_policy(
        meta, meta.filelist, _tracker()._collect_video_paths(meta.filelist)
    )


def test_zenith_book_identity_rejects_banned_author() -> None:
    assert not _tracker()._book_identity_policy(
        _book_meta(author="J.R.R. Tolkien")
    )


def test_zenith_tracker_name_values_support_scalar_tuple_and_set() -> None:
    from src.integrations.trackers.UNIT3D.znth import _tracker_name_values

    assert _tracker_name_values("ZENITH") == ["ZENITH"]
    assert _tracker_name_values(("ZENITH", "PEERGARDEN")) == [
        "ZENITH",
        "PEERGARDEN",
    ]
    assert set(_tracker_name_values({"ZENITH", "PEERGARDEN"})) == {
        "ZENITH",
        "PEERGARDEN",
    }
