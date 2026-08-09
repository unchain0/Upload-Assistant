# ruff: noqa: S101

import asyncio
from typing import Any

import pytest

from src.dupe_checking import DupeChecker
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


def test_luminarr_rejects_primary_mp3_audio():
    mediainfo = {
        "media": {
            "track": [
                {"@type": "General"},
                {"@type": "Video", "Format": "AVC"},
                {"@type": "Audio", "Format": "MPEG Audio", "Format_Profile": "Layer 3", "Title": "English"},
            ]
        }
    }

    tracker = _tracker()
    meta = _tv_meta(type="HDTV", mediainfo=mediainfo)

    assert tracker._invalid_audio_reason(meta) == "MP3 is permitted only for supplementary audio tracks (for example, commentary) under rule 6.2.5.3."
    assert asyncio.run(tracker.get_additional_checks(meta)) is False


def test_luminarr_allows_mp3_for_commentary_only():
    mediainfo = {
        "media": {
            "track": [
                {"@type": "General"},
                {"@type": "Video", "Format": "AVC"},
                {"@type": "Audio", "Format": "AAC", "Title": "English"},
                {"@type": "Audio", "Format": "MPEG Audio", "CodecID": "A_MPEG/L3", "Title": "Director Commentary"},
            ]
        }
    }

    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(type="WEBDL", mediainfo=mediainfo))) is True


def test_luminarr_rejects_primary_vorbis_audio():
    mediainfo = {"media": {"track": [{"@type": "Audio", "Format": "Vorbis", "Title": "English"}]}}

    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(type="WEBDL", mediainfo=mediainfo))) is False


def test_luminarr_allows_primary_mp2_for_hdtv():
    mediainfo = {"media": {"track": [{"@type": "Audio", "Format": "MPEG Audio", "Format_Profile": "Layer 2", "Title": "English"}]}}

    assert asyncio.run(_tracker().get_additional_checks(_tv_meta(type="HDTV", mediainfo=mediainfo))) is True


def test_luminarr_rejects_primary_mp2_for_webdl():
    mediainfo = {"media": {"track": [{"@type": "Audio", "Format": "MPEG Audio", "Format_Profile": "Layer 2", "Title": "English"}]}}

    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(type="WEBDL", mediainfo=mediainfo))) is False


def test_luminarr_rejects_tagged_video_filename_renamed_with_spaces():
    renamed = "Oh Boy Was I Wrong About Her S01E01 REPACK 1080p CR WEB-DL DDP2.0 H.264-Kitsune.mkv"
    original = "Oh.Boy.Was.I.Wrong.About.Her.S01E01.REPACK.1080p.CR.WEB-DL.DDP2.0.H.264-Kitsune.mkv"

    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(tag="-Kitsune", filelist=[renamed]))) is False
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(tag="-Kitsune", filelist=[original]))) is True


def test_luminarr_rejects_bootleg_markers_in_release_name():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(name="Example Movie Cam"))) is False


def test_luminarr_allows_tc_as_trailing_release_group():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(name="Example Movie 2024-TC"))) is True
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(name="Example Movie TC 2024-GRP"))) is False
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(name="Example Movie TC 2024-TC"))) is False


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


def test_luminarr_accepts_english_audio_without_english_subtitles():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(subtitle_languages=["Serbian", "Croatian"]))) is True


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


def test_luminarr_disc_types_do_not_require_mediainfo_encoding_settings():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(is_disc="DVD", filelist=["VIDEO_TS"], valid_mi_settings=False))) is True


@pytest.mark.asyncio
async def test_luminarr_keeps_existing_same_episode_and_resolution_as_dupe():
    meta = _tv_meta(
        name="My Adventures with Superman S03E09 1080p AMZN WEB-DL Portuguese Multi DD+ 5.1 H.264-C76",
        uuid="My.Adventures.with.Superman.S03E09.1080p.AMZN.WEB-DL.DDP5.1.H.264.DUAL-C76",
        season="S03",
        episode="E09",
        source="AMZN",
        type="WEBDL",
        resolution="1080p",
    )
    tiered: dict[str, Any] = {
        "name": "My Adventures with Superman S03E09 1080p AMZN WEB-DL DDP5.1 H.264-NTb",
        "size": 1,
        "files": ["My.Adventures.with.Superman.S03E09.1080p.AMZN.WEB-DL.DDP5.1.H.264-NTb.mkv"],
        "id": 123,
        "res": "1080p",
        "type": "WEB-DL",
    }

    result = await DupeChecker({"DEFAULT": {}}).filter_dupes([tiered], meta, "LUMINARR")

    assert len(result) == 1
    assert result[0].get("name") == tiered["name"]
    assert meta.get("LUMINARR_matched_reason") == "luminarr_same_episode_resolution"


@pytest.mark.asyncio
async def test_luminarr_does_not_block_different_episode_or_resolution():
    meta = _tv_meta(
        name="My Adventures with Superman S03E09 1080p AMZN WEB-DL Portuguese Multi DD+ 5.1 H.264-C76",
        uuid="My.Adventures.with.Superman.S03E09.1080p.AMZN.WEB-DL.DDP5.1.H.264.DUAL-C76",
        season="S03",
        episode="E09",
        source="AMZN",
        type="WEBDL",
        resolution="1080p",
    )
    candidates: list[dict[str, Any]] = [
        {"name": "My Adventures with Superman S03E08 1080p AMZN WEB-DL DDP5.1 H.264-NTb", "size": 1},
        {"name": "My Adventures with Superman S03E09 2160p AMZN WEB-DL DDP5.1 H.265-NTb", "size": 1},
    ]

    assert await DupeChecker({"DEFAULT": {}}).filter_dupes(candidates, meta, "LUMINARR") == []


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
