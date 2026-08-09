import asyncio
from typing import Any

from src.meta import Meta
from src.trackers.UNIT3D.luminarr import Luminarr


def _tracker() -> Luminarr:
    return Luminarr({"DEFAULT": {}, "TRACKERS": {"LUMINARR": {}}})


def _movie_meta(**kwargs: Any) -> Meta:
    base: dict[str, Any] = {
        "category": "MOVIE",
        "tmdb": 12,
        "mediainfo": {"media": {"track": []}},
        "audio_languages": ["English"],
        "subtitle_languages": ["English"],
        "original_language": "English",
        "resolution": "1080p",
        "container": "mkv",
        "valid_mi_settings": True,
        "filelist": ["Example.Movie.2024.1080p.mkv"],
        "name": "Example Movie",
        "screens": 3,
        "unattended": True,
        "unattended_confirm": False,
    }
    base.update(kwargs)
    return Meta(**base)


def _tv_meta(**kwargs: Any) -> Meta:
    base: dict[str, Any] = {
        "category": "TV",
        "tmdb": 12,
        "mediainfo": {"media": {"track": []}},
        "audio_languages": ["English"],
        "subtitle_languages": ["English"],
        "original_language": "English",
        "resolution": "1080p",
        "container": "mkv",
        "valid_mi_settings": True,
        "filelist": ["Example.Show.S01E01.1080p.mkv"],
        "name": "Example Show",
        "tv_pack": False,
        "screens": 3,
        "imdb_info": {"status": "Ended"},
        "unattended": True,
        "unattended_confirm": False,
    }
    base.update(kwargs)
    return Meta(**base)


def test_luminarr_rejects_malformed_filelist():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(filelist=1))) is False


def test_luminarr_requires_tmdb_identifier():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(tmdb=0, tmdb_id=None, imdb_id=0))) is False


def test_luminarr_accepts_valid_movie_metadata():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta())) is True


def test_luminarr_rejects_bootleg_markers_in_release_name():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(name="Example Movie Cam"))) is False


def test_luminarr_accepts_release_with_none_name_when_no_bootleg_marker_present():
    assert (
        asyncio.run(
            _tracker().get_additional_checks(
                _movie_meta(
                    name=None,
                )
            )
        )
        is True
    )


def test_luminarr_rejects_low_resolution_unless_confirmed():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(resolution="480p"))) is False


def test_luminarr_rejects_archive_files_for_non_disc():
    assert (
        asyncio.run(
            _tracker().get_additional_checks(
                _movie_meta(
                    filelist=["Example.Movie.2024.1080p.mkv", "Example.Movie.2024.part01.rar"],
                )
            )
        )
        is False
    )


def test_luminarr_rejects_extra_file_types_for_non_disc():
    assert (
        asyncio.run(
            _tracker().get_additional_checks(
                _movie_meta(
                    filelist=["Example.Movie.2024.1080p.mkv", "Example.Movie.screenshot.nfo"],
                )
            )
        )
        is False
    )


def test_luminarr_rejects_non_discs_without_three_screenshots():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(screens=2))) is False


def test_luminarr_treats_missing_screenshot_count_as_zero():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(screens=None))) is False


def test_luminarr_rejects_pornography_metadata():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(adult_media=True))) is False


def test_luminarr_rejects_original_audio_missing_for_foreign_audio_release():
    assert (
        asyncio.run(
            _tracker().get_additional_checks(
                _movie_meta(
                    audio_languages=["Spanish"],
                    original_language="English",
                )
            )
        )
        is False
    )


def test_luminarr_accepts_english_subtitle_alias():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(subtitle_languages=["eng"]))) is True


def test_luminarr_rejects_nested_single_file_for_movie():
    assert (
        asyncio.run(
            _tracker().get_additional_checks(
                _movie_meta(
                    filelist=["Example.Movie/Example.Movie.2024.1080p.mkv"],
                    keep_folder=True,
                )
            )
        )
        is False
    )


def test_luminarr_rejects_multi_files_without_common_top_folder():
    assert (
        asyncio.run(
            _tracker().get_additional_checks(
                _movie_meta(
                    filelist=["Example.Movie.2024.1080p_part1.mkv", "Example.Movie.2024.1080p_part2.mkv"],
                )
            )
        )
        is False
    )


def test_luminarr_rejects_bd_release_without_bdinfo():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(is_disc="BDMV", filelist=["BDMV"], bdinfo={}, valid_mi_settings=False))) is False


def test_luminarr_accepts_bd_release_when_bdinfo_is_present():
    assert (
        asyncio.run(
            _tracker().get_additional_checks(
                _movie_meta(is_disc="BDMV", filelist=["BDMV"], bdinfo={"disc_title": "sample"}, valid_mi_settings=False)
            )
        )
        is True
    )


def test_luminarr_rejects_tv_uploads_with_multiple_seasons():
    assert (
        asyncio.run(
            _tracker().get_additional_checks(
                _tv_meta(
                    filelist=["Example.Show.S01E01.mkv", "Example.Show.S02E01.mkv"],
                )
            )
        )
        is False
    )


def test_luminarr_rejects_ongoing_tv_pack_without_confirmation():
    assert (
        asyncio.run(
            _tracker().get_additional_checks(
                _tv_meta(
                    tv_pack=True,
                    filelist=["Example.Show.S01E01.mkv", "Example.Show.S01E02.mkv"],
                    imdb_info={"status": "Returning Series"},
                )
            )
        )
        is False
    )


def test_luminarr_accepts_ended_tv_season_pack():
    assert (
        asyncio.run(
            _tracker().get_additional_checks(
                _tv_meta(
                    tv_pack=True,
                    filelist=["Example.Show.S01/Example.Show.S01E01.mkv", "Example.Show.S01/Example.Show.S01E02.mkv"],
                    imdb_info={"status": "Ended"},
                )
            )
        )
        is True
    )
