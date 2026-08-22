from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.NEXUSPHP import NEXUSPHP
from tests.test_railgunpt_rules import _movie_meta, _tracker, _tv_meta


@pytest.mark.asyncio
async def test_railgunpt_superclass_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    meta = _movie_meta()
    load = AsyncMock(return_value=None)
    technical = AsyncMock(return_value={"x": "y"})
    search = AsyncMock(return_value=[{"name": "existing"}])
    monkeypatch.setattr(NEXUSPHP, "load_localized_data", load)
    monkeypatch.setattr(NEXUSPHP, "get_technical_info", technical)
    monkeypatch.setattr(NEXUSPHP, "search_existing", search)

    await tracker.load_localized_data(meta)
    assert await tracker.get_technical_info(meta) == {"x": "y"}
    assert await tracker.search_existing(meta) == [{"name": "existing"}]
    load.assert_awaited_once()
    technical.assert_awaited_once()
    search.assert_awaited_once()


def test_railgunpt_metadata_title_and_sd_edges() -> None:
    tracker = _tracker()
    assert tracker._metadata_values(object()) == []
    assert not tracker._title_contains_token("Title", "")
    assert tracker._valid_sd_release(
        _movie_meta(type="ENCODE", source="HDTV"), 480
    )
    assert tracker._sd_is_dvd_exception("dvd", "", "")
    assert tracker._sd_is_dvd_exception("", "dvdrip", "")
    assert tracker._valid_sd_release(
        _movie_meta(type="DVDRIP", source="", is_disc=""), 480
    )
    assert tracker._title_resolution_allowed(
        _movie_meta(resolution=None), "Movie"
    )
    assert not tracker._title_resolution_allowed(
        _movie_meta(resolution="1080p"), "Movie BluRay x264"
    )
    assert tracker._title_movie_year_allowed(_movie_meta(year=None), "Movie")
    assert not tracker._title_movie_year_allowed(
        _movie_meta(year=2024), "Movie 1080p BluRay x264"
    )


def test_railgunpt_music_root_guard_edges(tmp_path: Path) -> None:
    tracker = _tracker()
    source_file = tmp_path / "source.bin"
    source_file.write_bytes(b"x")
    assert tracker._resolved_music_audio([], source_file) is None
    assert tracker._absolute_music_audio_paths([Path("cover.jpg")]) is None
    assert tracker._absolute_music_audio_paths([Path("track.flac")]) is None

    payload_root = tmp_path / "payload"
    payload_root.mkdir()
    outside = tmp_path / "outside" / "track.flac"
    assert not tracker._music_root_contains_audio(payload_root, [outside])


def test_railgunpt_pack_and_category_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        tracker, "_validate_pack_consistency", lambda _paths: True
    )
    assert tracker._tv_pack_allowed(
        _tv_meta(tv_pack=True), [Path("a.mkv"), Path("b.mkv")]
    )
    assert tracker._animation_category_id(Meta(anime=True), "") == 405
    assert tracker.get_category(Meta(category="OTHER")) == 401


def test_railgunpt_type_codec_resolution_mappings() -> None:
    tracker = _tracker()
    assert tracker._bdmv_type_id(Meta(resolution="2160p")) == 2
    assert tracker._disc_type_id(Meta(is_disc="BDMV", resolution="1080p")) == 1
    assert tracker._disc_type_id(Meta(is_disc="DVD")) == 6
    assert tracker._file_type_id(Meta(type="REMUX")) == 3
    assert tracker._file_type_id(Meta(type="UNKNOWN")) == 7
    assert tracker.get_type(Meta(category="MOVIE", type="REMUX")) == 3

    assert tracker._video_codec_rules()
    assert tracker.get_codec(Meta(video_codec="HEVC")) == 2
    assert tracker.get_codec(Meta(video_codec="AVC")) == 1
    assert tracker.get_codec(Meta(video_codec="MPEG-2")) == 4
    assert tracker.get_codec(Meta(video_codec="VC-1")) == 3
    assert tracker.get_codec(Meta(video_codec="XviD")) == 5
    assert tracker.get_codec(Meta(video_codec="UNKNOWN")) == 6

    assert tracker._resolution_value(Meta(resolution=None)) == ""
    assert tracker.get_resolution(Meta(resolution="1080i")) == 2
    assert tracker.get_resolution(Meta(resolution="720p")) == 3
    assert tracker.get_resolution(Meta(resolution="576p", sd=1)) == 4
    assert tracker.get_resolution(Meta(resolution="2160p")) == 1
    assert tracker.get_resolution(Meta(resolution="576p")) == 5


def test_railgunpt_audio_checkbox_and_anonymous_edges() -> None:
    tracker = _tracker()
    assert tracker._music_audio_codec_values(Meta(music_release=None)) == []
    assert tracker.get_audio_codec(Meta(audio="UNKNOWN")) == 9
    assert tracker._has_chinese_language(["Mandarin"])

    meta = Meta(
        exclusive=True,
        audio_languages=["Chinese"],
        subtitle_languages=["Mandarin"],
        hdr="HDR10",
    )
    assert tracker._checkbox_options(meta)
    assert tracker.get_checkboxes(meta) == ["1", "5", "6", "7"]
    assert tracker.get_anonymous(Meta(anon=1))
