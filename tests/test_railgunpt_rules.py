# ruff: noqa: S101

import asyncio
from typing import Any

import pytest

from src.meta import Meta
from src.trackers.NEXUSPHP.railgunpt import RailgunPT


def _tracker() -> RailgunPT:
    return RailgunPT({"DEFAULT": {"tmdb_api": "test"}, "TRACKERS": {"RAILGUNPT": {}}})


def _movie_meta(**kwargs: Any) -> Meta:
    values: dict[str, Any] = {
        "category": "MOVIE",
        "filelist": ["Example.Movie.2024.1080p.BluRay.x264-GRP.mkv"],
        "name": "Example Movie 2024 1080p BluRay DD 5.1 x264-GRP",
        "resolution": "1080p",
        "source": "BluRay",
        "source_size": 1024**3,
        "type": "ENCODE",
        "video_codec": "AVC",
        "video_encode": "x264",
        "year": 2024,
    }
    values.update(kwargs)
    return Meta(**values)


def _tv_meta(**kwargs: Any) -> Meta:
    values: dict[str, Any] = {
        "category": "TV",
        "filelist": ["Example.Show.S01E01.1080p.HDTV.x264-GRP.mkv"],
        "name": "Example Show S01E01 1080p HDTV DD 5.1 x264-GRP",
        "resolution": "1080p",
        "source": "HDTV",
        "source_size": 1024**3,
        "type": "HDTV",
        "video_codec": "AVC",
        "video_encode": "x264",
    }
    values.update(kwargs)
    return Meta(**values)


def _check(meta: Meta) -> bool:
    return asyncio.run(_tracker().get_additional_checks(meta))


def test_railgunpt_accepts_compliant_movie_and_tv_uploads():
    assert _check(_movie_meta()) is True
    assert _check(_tv_meta()) is True


def test_railgunpt_rejects_unsupported_and_sensitive_content():
    assert _check(_movie_meta(category="BOOK")) is False
    assert _check(_movie_meta(adult_media=True)) is False
    assert _check(_movie_meta(keywords=["Political"])) is False
    assert _check(_movie_meta(keywords="Political")) is False


def test_railgunpt_normalizes_scalar_category_metadata():
    assert _tracker().get_category(_movie_meta(genres="Documentary")) == 404
    assert _tracker().get_category(_movie_meta(keywords="Animation")) == 405


def test_railgunpt_enforces_video_minimum_size():
    assert _check(_movie_meta(source_size=100 * 1024 * 1024)) is True
    assert _check(_movie_meta(source_size=100 * 1024 * 1024 - 1)) is False
    assert _check(_movie_meta(source_size=0)) is True


@pytest.mark.parametrize("marker", ["CAM", "TC", "TS", "SCR", "DVDSCR", "R5", "HalfCD"])
def test_railgunpt_rejects_low_quality_source_markers(marker: str):
    assert _check(_movie_meta(name=f"Example Movie 2024 {marker} 1080p BluRay x264-GRP")) is False


def test_railgunpt_enforces_sd_source_rules_and_rejects_sd_upscales():
    assert _check(_movie_meta(name="Example Movie 2024 576p BluRay x264-GRP", resolution="576p")) is True
    assert _check(_movie_meta(name="Example Movie 2024 576p WEB-DL x264-GRP", resolution="576p", source="WEB-DL", type="WEBDL")) is False
    assert _check(_movie_meta(name="Example Movie 2024 UPSCALE 576p BluRay x264-GRP", resolution="576p")) is False
    assert _check(_movie_meta(name="Example Movie 2024 360p BluRay x264-GRP", resolution="360p")) is False


def test_railgunpt_rejects_archives_spam_realvideo_and_individual_samples():
    assert _check(_movie_meta(filelist=["release.rar"])) is False
    assert _check(_movie_meta(filelist=["downloaded from tracker.url"])) is False
    assert _check(_movie_meta(filelist=["movie.rmvb"], video_codec="RealVideo")) is False
    assert _check(_movie_meta(filelist=["sample.mkv"])) is False


def test_railgunpt_allows_permitted_archived_attachments_and_main_samples():
    assert _check(_movie_meta(filelist=["movie.mkv", "subtitles.rar"])) is True
    assert _check(_movie_meta(filelist=["movie.mkv", "sample.mkv"])) is True


@pytest.mark.parametrize(
    "name",
    [
        "Example Movie 1080p BluRay x264-GRP",
        "Example Movie 2024 BluRay x264-GRP",
        "Example Movie 2024 1080p x264-GRP",
        "Example Movie 2024 1080p BluRay-GRP",
    ],
)
def test_railgunpt_requires_descriptive_movie_title(name: str):
    assert _check(_movie_meta(name=name)) is False


def test_railgunpt_rejects_video_tokens_embedded_in_words():
    assert _check(_movie_meta(name="Example Movie 2024 1080p NotBluRayish x264codec-GRP")) is False


def test_railgunpt_requires_tv_season_episode_or_pack_token():
    assert _check(_tv_meta(name="Example Show 1080p HDTV x264-GRP")) is False
    assert _check(_tv_meta(name="Example Show S01 1080p HDTV x264-GRP", tv_pack=True)) is True


def test_railgunpt_enforces_pack_consistency():
    files = ["Show.S01E01.1080p.HDTV.x264-GRP.mkv", "Show.S01E02.720p.HDTV.x264-GRP.mkv"]
    assert _check(_tv_meta(name="Example Show S01 1080p HDTV x264-GRP", tv_pack=True, filelist=files)) is False

    files = ["Show.S01E01.1080p.HDTV.x264-GRP.mkv", "Show.S01E02.1080p.WEB-DL.x265-GRP.mkv"]
    assert _check(_tv_meta(name="Example Show S01 1080p HDTV x264-GRP", tv_pack=True, filelist=files)) is False


def test_railgunpt_requires_official_boxset_marker_for_multi_movie_uploads():
    files = ["Movie.One.2020.1080p.BluRay.x264.mkv", "Movie.Two.2022.1080p.BluRay.x264.mkv"]
    assert _check(_movie_meta(filelist=files)) is False
    assert _check(_movie_meta(name="Example Collection 2024 1080p BluRay x264-GRP", filelist=files)) is True


def test_railgunpt_allows_multi_file_disc_layout_without_collection_marker():
    files = ["BDMV/STREAM/00001.m2ts", "BDMV/STREAM/00002.m2ts"]
    assert _check(_movie_meta(is_disc="BDMV", filelist=files)) is True
    assert _check(_movie_meta(is_disc="unknown", filelist=files)) is False


def test_railgunpt_rejects_invalid_filelist_metadata():
    assert _check(_movie_meta(filelist=1)) is False
