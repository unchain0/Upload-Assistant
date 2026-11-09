from __future__ import annotations

from pathlib import Path
from types import TracebackType
from typing import Any, Self
from unittest.mock import AsyncMock

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D import darkpeers as darkpeers_module
from src.integrations.trackers.UNIT3D.darkpeers import DarkPeers


def _config(*, tmdb_api: str = "test-key") -> dict[str, Any]:
    return {"DEFAULT": {"tmdb_api": tmdb_api}, "TRACKERS": {"DARKPEERS": {}}}


def _tracker(*, tmdb_api: str = "test-key") -> DarkPeers:
    return DarkPeers(_config(tmdb_api=tmdb_api))


def _book_meta(**values: object) -> Meta:
    state: dict[str, object] = {
        "category": "BOOK",
        "author": "Author",
        "book_author": "",
        "title": "Book",
        "book_title": "",
        "year": 2026,
        "type": "EPUB",
        "format": "",
        "isbn": "9780765377067",
        "book_isbn": "",
        "publisher": "Publisher",
        "book_publisher": "",
        "audiobook": False,
        "comic": False,
        "narrator": "",
        "audiobook_duration": 0,
        "audiobook_duration_formatted": "",
        "audiobook_bitrate": 0,
        "manual_source": "",
        "source": "RETAIL",
        "ocr": False,
        "filelist": [],
        "path": "",
        "unattended": False,
        "asin": "",
        "book_asin": "",
    }
    state.update(values)
    return Meta(state)


@pytest.mark.asyncio
async def test_darkpeers_keep_folder_confirmation_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        tracker, "validate_video_languages", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        tracker, "validate_video_resolution", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        tracker, "validate_video_quality", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(tracker, "validate_video_files", lambda _meta: True)
    monkeypatch.setattr(tracker, "validate_video_content", lambda _meta: True)
    monkeypatch.setattr(
        tracker, "validate_video_screenshots", lambda _meta: True
    )
    monkeypatch.setattr(
        tracker, "_confirm_or_skip", AsyncMock(return_value=False)
    )
    meta = Meta(
        category="MOVIE",
        name="Movie 2026 1080p WEB-DL",
        filelist=["video.mkv"],
        keep_folder=True,
        is_disc="",
        screens=3,
        type="WEBDL",
        tag="",
    )
    assert not await tracker.get_additional_checks(meta)


def test_darkpeers_config_min_bitrate_guards() -> None:
    tracker = _tracker()
    tracker.tracker_config["invalid"] = "bad"
    assert tracker._config_min_bitrate("invalid") == {}

    tracker.tracker_config["mixed"] = {
        "Good": "100",
        "Bad": "x",
        123: 100,
        "Negative": -1,
    }
    assert tracker._config_min_bitrate("mixed") == {"good": 100}


@pytest.mark.asyncio
async def test_darkpeers_video_quality_none_and_low_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker, "_min_webl_bitrate", lambda *_args: None)
    assert await tracker.validate_video_quality(
        Meta(type="WEBDL", resolution="1080p")
    )

    def minimum(kind: str, _resolution: str) -> int:
        return 1000 if kind == "audio" else 100

    monkeypatch.setattr(tracker, "_min_webl_bitrate", minimum)
    meta = Meta(
        type="WEBDL", resolution="1080p", video_bitrate=5000, audio_bitrate=500
    )
    assert not await tracker.validate_video_quality(meta)


def test_darkpeers_rejects_renamed_tagged_video() -> None:
    meta = Meta(tag="-GROUP", filelist=["Movie Name-GROUP.mkv"])
    assert not _tracker().validate_video_files(meta)


def test_darkpeers_video_content_records_suffixless_file() -> None:
    meta = Meta(filelist=["video.mkv", "README"])
    assert not _tracker().validate_video_content(meta)


def test_darkpeers_tv_scope_rejects_complete_series_name() -> None:
    meta = Meta(name="Example Complete Series", path="", filelist=[])
    assert not _tracker().validate_tv_scope(meta)


@pytest.mark.asyncio
async def test_darkpeers_book_missing_identifier_after_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker, "_book_identifier", lambda _meta: "")
    assert not await tracker.validate_book(_book_meta())


@pytest.mark.asyncio
async def test_darkpeers_book_missing_publisher() -> None:
    assert not await _tracker().validate_book(_book_meta(publisher=""))


@pytest.mark.asyncio
async def test_darkpeers_audiobook_missing_narrator_and_runtime() -> None:
    tracker = _tracker()
    missing_narrator = _book_meta(
        audiobook=True, type="M4B", narrator="", audiobook_duration=3600
    )
    assert not await tracker.validate_book(missing_narrator)

    missing_runtime = _book_meta(
        audiobook=True, type="M4B", narrator="Narrator", audiobook_duration=0
    )
    assert not await tracker.validate_book(missing_runtime)


@pytest.mark.asyncio
async def test_darkpeers_audiobook_declined_edition_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        tracker.common,
        "prompt_user_for_confirmation",
        AsyncMock(return_value=False),
    )
    meta = _book_meta(
        audiobook=True,
        type="M4B",
        narrator="Narrator",
        audiobook_duration=3600,
    )
    assert not await tracker.validate_book(meta)


@pytest.mark.asyncio
async def test_darkpeers_pdf_requires_page_count() -> None:
    assert not await _tracker().validate_book(_book_meta(type="PDF"))


def test_darkpeers_audiobook_multifile_numbering_rejection() -> None:
    meta = _book_meta(
        audiobook=True,
        type="MP3",
        filelist=["intro.mp3", "02 - Chapter.mp3"],
        path="/path/that/does/not/exist",
    )
    assert not _tracker()._validate_book_file_layout(meta, "MP3")


def test_darkpeers_ebook_filename_must_contain_author_and_title(
    tmp_path: Path,
) -> None:
    meta = _book_meta(path=str(tmp_path), filelist=["Wrong.epub"])
    assert not _tracker()._validate_book_file_layout(meta, "EPUB")


def test_darkpeers_book_format_aliases_from_codec() -> None:
    tracker = _tracker()
    m4a_alac = _book_meta(
        audiobook=True,
        type="M4A",
        mediainfo={"media": {"track": [{"@type": "Audio", "Format": "ALAC"}]}},
    )
    m4a_aac = _book_meta(
        audiobook=True,
        type="M4A",
        mediainfo={"media": {"track": [{"@type": "Audio", "Format": "AAC"}]}},
    )
    ogg_opus = _book_meta(
        audiobook=True,
        type="OGG",
        mediainfo={"media": {"track": [{"@type": "Audio", "Format": "Opus"}]}},
    )
    ogg_vorbis = _book_meta(
        audiobook=True,
        type="OGG",
        mediainfo={
            "media": {"track": [{"@type": "Audio", "Format": "Vorbis"}]}
        },
    )
    assert tracker._book_format(m4a_alac) == "ALAC"
    assert tracker._book_format(m4a_aac) == "AAC"
    assert tracker._book_format(ogg_opus) == "OPUS"
    assert tracker._book_format(ogg_vorbis) == "VORBIS"


def _music_meta(
    root: Path,
    relative_path: str,
    *,
    album: str = "Album",
    release_type: str = "Album",
) -> Meta:
    return Meta(
        category="MUSIC",
        title=album,
        path=str(root),
        filelist=[relative_path],
        music_release={
            "fields": {
                "artist": {"value": "Artist"},
                "album": {"value": album},
                "release_year": {"value": 2026},
                "release_type": {"value": release_type},
                "media": {"value": "WEB"},
            },
            "tracks": [
                {
                    "path": relative_path,
                    "relative_path": relative_path,
                    "format": "FLAC",
                    "codec": "FLAC",
                    "title": "Track",
                    "track_number": 1,
                }
            ],
        },
    )


def test_darkpeers_music_path_policy_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        darkpeers_module.MusicValidator, "validate", lambda _self, _release: []
    )
    tracker = _tracker()

    root_file = tmp_path / "music.flac"
    root_file.write_bytes(b"x")
    assert not tracker.validate_music(
        _music_meta(root_file, "01 - Track.flac")
    )

    wrong_folder = tmp_path / "Wrong Folder"
    wrong_folder.mkdir()
    assert not tracker.validate_music(
        _music_meta(wrong_folder, "01 - Track.flac", album="Album")
    )

    nonexistent = tmp_path / "missing"
    assert not tracker.validate_music(
        _music_meta(nonexistent, " 01 - Track.flac")
    )
    assert not tracker.validate_music(
        _music_meta(nonexistent, "01 - Bad?.flac")
    )
    assert not tracker.validate_music(_music_meta(nonexistent, "Track.flac"))


@pytest.mark.asyncio
async def test_darkpeers_game_requires_instructions() -> None:
    meta = Meta(
        filelist=["release.rar"],
        scene=True,
        scene_nfo_file="release.nfo",
        repack="",
        description="No useful prose",
        description_file_content="",
        description_link_content="",
        description_nfo_content="",
    )
    assert not await _tracker().validate_game(meta)


@pytest.mark.asyncio
async def test_darkpeers_audio_label_matrix_remaining_branches() -> None:
    tracker = _tracker()
    assert (
        await tracker.get_audio(
            Meta(
                language_checked=True,
                is_disc="",
                original_language="Japanese",
                audio_languages=["English"],
            )
        )
        == "Dubbed"
    )
    assert (
        await tracker.get_audio(
            Meta(
                language_checked=True,
                is_disc="",
                original_language="Japanese",
                audio_languages=["Swedish"],
            )
        )
        == "Swedish Dubbed"
    )
    assert (
        await tracker.get_audio(
            Meta(
                language_checked=True,
                is_disc="",
                original_language="Japanese",
                audio_languages=["Japanese", "French", "German"],
            )
        )
        == "MULTi"
    )
    assert (
        await tracker.get_audio(
            Meta(
                language_checked=True,
                is_disc="",
                original_language="Japanese",
                audio_languages=["English", "Swedish", "Danish"],
            )
        )
        == "MULTi"
    )
    assert (
        await tracker.get_audio(
            Meta(
                language_checked=True,
                is_disc="",
                original_language="Japanese",
                audio_languages=["Swedish", "French"],
            )
        )
        == "French MULTi"
    )


def test_darkpeers_small_name_helpers_cover_guard_branches() -> None:
    assert DarkPeers._normalize_scene_name("") == ""
    assert DarkPeers._ensure_group_tag("Movie-H264", "-H264") == "Movie-H264"
    assert not DarkPeers._has_group_in_name("Movie-123")
    assert not DarkPeers._has_group_in_name("Movie-H264")
    assert not DarkPeers._has_group_in_name("Movie-h2640")
    assert (
        DarkPeers._normalize_aka_year_order(
            "Movie 2020 AKA", " ", "AKA", "2020"
        )
        == "Movie 2020 AKA"
    )
    assert (
        DarkPeers._normalize_aka_year_order(
            "Movie 2020 AKA", "Movie", "AKA", " "
        )
        == "Movie 2020 AKA"
    )


@pytest.mark.asyncio
async def test_darkpeers_video_name_other_resolution() -> None:
    meta = Meta(
        category="MOVIE",
        type="WEBDL",
        title="Movie",
        no_aka=True,
        aka="",
        manual_year=0,
        year=2025,
        no_year=False,
        manual_date="",
        no_season=False,
        season="",
        episode="",
        manual_edition="",
        edition="",
        webdv=False,
        repack="",
        resolution="OTHER",
        source="WEB",
        service="Netflix",
        three_d="",
        basename_no_ext="",
        path="",
        name="Movie 2025",
        video_encode="x264",
        video_codec="H264",
        region="",
        uhd="",
        dvd_size="",
        hdr="",
        audio="AAC 2.0",
        tag="-GROUP",
    )
    result = await _tracker()._video_name(meta)
    assert "OTHER" not in result


def test_darkpeers_video_source_dvd_size() -> None:
    meta = Meta(source="PAL DVD", dvd_size="DVD9", uhd="")
    assert DarkPeers._video_source(meta, "DISC") == "PAL DVD9"


def test_darkpeers_dub_element_insertion_and_blank_audio() -> None:
    assert (
        DarkPeers._apply_dub_element("Movie AAC 2.0 x264", "")
        == "Movie AAC 2.0 x264"
    )
    assert (
        DarkPeers._apply_dub_element("Movie AAC 2.0 x264", "Dubbed")
        == "Movie Dubbed AAC 2.0 x264"
    )


@pytest.mark.asyncio
async def test_darkpeers_tv_year_no_api_key_is_needed() -> None:
    tracker = _tracker()
    tracker.config["DEFAULT"]["tmdb_api"] = ""
    assert await tracker._tv_title_needs_year(Meta(title="Example"))


class _Response:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {
            "results": [
                "bad",
                {"id": 1, "name": "Example", "original_name": "Example"},
                {"id": 2, "name": "Example", "original_name": "Other"},
            ]
        }


class _Client:
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        return None

    async def get(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()


@pytest.mark.asyncio
async def test_darkpeers_tv_year_skips_non_mapping_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(darkpeers_module.httpx, "AsyncClient", _Client)
    meta = Meta(title="Example", tmdb_id=1)
    assert await _tracker()._tv_title_needs_year(meta)


@pytest.mark.asyncio
async def test_darkpeers_explicit_book_type_mapping_branches() -> None:
    tracker = _tracker()
    meta = Meta(category="BOOK", type="", audiobook=False, comic=False)
    assert await tracker.get_type_id(meta, type="CBR") == {"type_id": "17"}
    assert await tracker.get_type_id(meta, type="EPUB") == {"type_id": "18"}
    assert await tracker.get_type_id(meta, type="M4B") == {"type_id": "15"}
    assert await tracker.get_type_id(
        Meta(category="BOOK", type="CBR", audiobook=False, comic=True)
    ) == {"type_id": "17"}


@pytest.mark.asyncio
async def test_darkpeers_video_name_dvd_source_clears_resolution() -> None:
    meta = Meta(
        category="MOVIE",
        type="REMUX",
        title="Movie",
        no_aka=True,
        aka="",
        manual_year=0,
        year=2025,
        no_year=False,
        manual_date="",
        no_season=False,
        season="",
        episode="",
        manual_edition="",
        edition="",
        webdv=False,
        repack="",
        resolution="480p",
        source="DVD",
        service="",
        three_d="",
        basename_no_ext="",
        path="",
        name="Movie 2025",
        video_encode="x264",
        video_codec="MPEG-2",
        region="",
        uhd="",
        dvd_size="",
        hdr="",
        audio="AAC 2.0",
        tag="-GROUP",
    )
    result = await _tracker()._video_name(meta)
    assert "480p" not in result


def test_darkpeers_book_format_wav_pcm_alias() -> None:
    meta = _book_meta(
        audiobook=True,
        type="WAV",
        mediainfo={"media": {"track": [{"@type": "Audio", "Format": "PCM"}]}},
    )
    assert _tracker()._book_format(meta) == "PCM"
