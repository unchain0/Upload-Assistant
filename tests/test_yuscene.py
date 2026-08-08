import asyncio
from typing import Any

from src.meta import Meta
from src.trackers.UNIT3D.yuscene import YUSCENE


def _tracker() -> YUSCENE:
    return YUSCENE({"DEFAULT": {}, "TRACKERS": {"YUSCENE": {}}})


def _movie_meta(**kwargs: Any) -> Meta:
    base: dict[str, Any] = {
        "category": "MOVIE",
        "mediainfo": {"media": {"track": []}},
        "filelist": ["Movie.2024.mkv"],
        "name": "Example Movie 2024",
        "screens": 3,
        "unattended": True,
        "unattended_confirm": False,
    }
    base.update(kwargs)
    return Meta(**base)


def _tv_meta(**kwargs: Any) -> Meta:
    base: dict[str, Any] = {
        "category": "TV",
        "mediainfo": {"media": {"track": []}},
        "filelist": ["Show.S01E01.mkv"],
        "name": "Example Series",
        "season": 1,
        "tv_pack": False,
        "screens": 3,
        "unattended": True,
        "unattended_confirm": False,
    }
    base.update(kwargs)
    return Meta(**base)


def test_yuscene_blocks_adult_keywords_when_unattended():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(keywords=["Porn"], unattended=True, unattended_confirm=False))) is False


def test_yuscene_blocks_adult_media_flag():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(adult_media=True, unattended=True, unattended_confirm=False))) is False


def test_yuscene_accepts_non_adult_movie_in_unattended_mode():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta())) is True


def test_yuscene_blocks_archive_files_for_non_games():
    assert (
        asyncio.run(
            _tracker().get_additional_checks(
                _movie_meta(
                    filelist=["Example.Movie.2024.rar"],
                )
            )
        )
        is False
    )


def test_yuscene_blocks_extra_files_in_movie_uploads():
    assert (
        asyncio.run(
            _tracker().get_additional_checks(
                _movie_meta(
                    filelist=["Example.Movie.2024.mkv", "Example.Movie.2024.nfo"],
                )
            )
        )
        is False
    )


def test_yuscene_requires_mediainfo_for_movie():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(mediainfo={}))) is False


def test_yuscene_blocks_tv_pack_when_series_still_ongoing():
    assert (
        asyncio.run(
            _tracker().get_additional_checks(
                _tv_meta(
                    tv_pack=True,
                    imdb_info={"status": "Returning Series"},
                )
            )
        )
        is False
    )


def test_yuscene_does_not_classify_in_development_series_as_ended():
    assert asyncio.run(_tracker().get_additional_checks(_tv_meta(tv_pack=True, imdb_info={"status": "In Development"}))) is False


def test_yuscene_blocks_title_chars_for_movie():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(name="Example.Movie 2024"))) is False


def test_yuscene_allows_movies_without_forbidden_title_chars():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(name="Example Movie 2024"))) is True


def test_yuscene_blocks_other_tracker_mentions():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(name="Example Movie 2024 yify"))) is False


def test_yuscene_does_not_match_ambiguous_tracker_alias_inside_names():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(name="Katniss Everdeen 2024"))) is True


def test_yuscene_allows_urls_in_description_when_tracker_reference_check_targets_title_only():
    assert (
        asyncio.run(
            _tracker().get_additional_checks(
                _movie_meta(
                    description="Screens: https://i.imgur.com/example1.jpg\nhttps://www.imdb.com/title/tt1234567/",
                )
            )
        )
        is True
    )


def test_yuscene_ignores_tracker_tokens_in_legitimate_url_paths():
    assert YUSCENE._contains_other_tracker_mention("https://images.example/kat/rutracker/screen.png") == ""


def test_yuscene_tracker_detection_handles_malformed_urls():
    assert YUSCENE._contains_other_tracker_mention("https://[") == ""


def test_yuscene_detects_protocol_relative_tracker_domain_with_trailing_dot():
    assert YUSCENE._contains_other_tracker_mention("//rutracker.net./release") == "rutracker.net"


def test_yuscene_blocks_disallowed_tracker_domains_in_title():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(name="Example Movie 2024 from rutracker.net"))) is False


def test_yuscene_blocks_low_screenshot_count():
    assert (
        asyncio.run(
            _tracker().get_additional_checks(
                _movie_meta(
                    screens=2,
                )
            )
        )
        is False
    )


def test_yuscene_allows_movie_with_three_screenshots():
    assert (
        asyncio.run(
            _tracker().get_additional_checks(
                _movie_meta(
                    screens=3,
                    filelist=["Example.Movie.2024.mkv"],
                )
            )
        )
        is True
    )
