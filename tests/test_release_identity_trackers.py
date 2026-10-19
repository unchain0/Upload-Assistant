"""Identity, DigitalCore, MidnightScene, and hyphenated-group decisions."""

import asyncio

from src.domain_models.release import Meta
from src.domain_models.release_identity import (
    digitalcore_staff_skip_reason,
    normalize_dc_episode_numbering,
    order_release_videos,
    preserve_failed_tv_category,
    unattended_identity_skip_reason,
)
from src.integrations.filesystem.tags import get_tag
from src.integrations.trackers.digitalcore import DigitalCore
from src.integrations.trackers.UNIT3D.midnightscene import MidnightScene
from src.services.release_naming_service import NameManager


def _videos(*names: str) -> Meta:
    return Meta(category="TV", unattended=True, filelist=list(names))


def _digitalcore() -> DigitalCore:
    return DigitalCore(
        {"DEFAULT": {}, "TRACKERS": {"DIGITALCORE": {"api_key": ""}}}
    )


def _midnight() -> MidnightScene:
    return MidnightScene(
        {"DEFAULT": {}, "TRACKERS": {"MIDNIGHTSCENE": {}}}
    )


def test_main_episode_stays_ahead_of_alphabetically_first_extra() -> None:
    ordered = order_release_videos(
        [
            "A.Extra.mkv",
            "Show.Pitch.Pilot.mkv",
            "Show.S01E02.mkv",
            "Show.S01E01.mkv",
        ]
    )

    assert ordered[0] == "Show.S01E01.mkv"
    assert ordered[-1] in {"A.Extra.mkv", "Show.Pitch.Pilot.mkv"}


def test_ova_does_not_identify_a_release_that_has_s01e01() -> None:
    ordered = order_release_videos(["Show.OVA.mkv", "Show.S01E01.mkv"])

    assert ordered[0] == "Show.S01E01.mkv"
    meta = _videos("Show.OVA.mkv", "Show.S01E01.mkv")
    meta.tmdb_id = 10
    assert unattended_identity_skip_reason(meta) is not None
    meta.filelist = ordered
    assert unattended_identity_skip_reason(meta) is None


def test_unattended_movie_of_episodes_is_skipped() -> None:
    meta = Meta(
        category="MOVIE",
        unattended=True,
        tmdb_id=10,
        filelist=["Show.S01E01.mkv", "Show.S01E02.mkv"],
    )

    reason = unattended_identity_skip_reason(meta)

    assert reason is not None
    assert "movie" in reason


def test_unattended_tv_without_database_ids_is_skipped() -> None:
    meta = _videos("Show.S01E01.mkv")

    reason = unattended_identity_skip_reason(meta)

    assert reason is not None
    assert "TMDB" in reason


def test_books_and_attended_runs_are_not_identity_skipped() -> None:
    book = Meta(category="BOOK", unattended=True, filelist=["Extra.mkv"])
    attended = _videos("A.Extra.mkv", "Show.S01E01.mkv")
    attended.unattended = False

    assert unattended_identity_skip_reason(book) is None
    assert unattended_identity_skip_reason(attended) is None


def test_failed_tmdb_lookup_keeps_an_existing_tv_category() -> None:
    assert preserve_failed_tv_category("TV", "MOVIE", None) == "TV"
    assert preserve_failed_tv_category("TV", "MOVIE", 0) == "TV"
    assert preserve_failed_tv_category("TV", "MOVIE", 338377) == "MOVIE"


def test_digitalcore_rejects_tv_without_imdb() -> None:
    meta = Meta(
        category="TV",
        filelist=["Show.S01E01.mkv"],
        audio_languages=["en"],
    )

    assert digitalcore_staff_skip_reason(meta) is not None
    assert asyncio.run(_digitalcore().get_additional_checks(meta)) is False


def test_digitalcore_rejects_fansub_crc_and_loose_s1() -> None:
    crc = Meta(
        category="TV",
        imdb_id=1,
        audio_languages=["en"],
        filelist=["Okay-Subs Show S01E01 [ABCD1234].mkv"],
    )
    loose = Meta(
        category="TV",
        imdb_id=1,
        audio_languages=["en"],
        filelist=["Show.S1E01.mkv"],
    )

    assert "CRC" in str(digitalcore_staff_skip_reason(crc))
    assert "SxxExx" in str(digitalcore_staff_skip_reason(loose))


def test_digitalcore_rejects_episode_pack_classified_as_movie() -> None:
    meta = Meta(
        category="MOVIE",
        imdb_id=1,
        audio_languages=["en"],
        filelist=["Show.S01E01.mkv", "Show.S01E02.mkv"],
    )

    reason = digitalcore_staff_skip_reason(meta)

    assert reason is not None
    assert "movie" in reason


def test_digitalcore_requires_a_season_pack_for_several_episodes() -> None:
    meta = Meta(
        category="TV",
        imdb_id=1,
        audio_languages=["en"],
        tv_pack=False,
        filelist=["Show.S01E01.mkv", "Show.S01E02.mkv"],
    )

    reason = digitalcore_staff_skip_reason(meta)

    assert reason is not None
    assert "season pack" in reason


def test_digitalcore_subtitle_rule_keeps_english_audio_and_rejects_japanese() -> (
    None
):
    english = Meta(
        category="MOVIE",
        imdb_id=1,
        audio_languages=["en"],
        filelist=["Movie.mkv"],
    )
    japanese = Meta(
        category="TV",
        imdb_id=1,
        audio_languages=["ja"],
        filelist=["Show.S01E01.mkv"],
    )

    assert digitalcore_staff_skip_reason(english) is None
    assert asyncio.run(_digitalcore().get_additional_checks(english)) is True
    assert digitalcore_staff_skip_reason(japanese) is not None
    assert asyncio.run(_digitalcore().get_additional_checks(japanese)) is False


def test_digitalcore_pads_episode_numbers_and_keeps_v2() -> None:
    padded = normalize_dc_episode_numbering("Show.S1E1.1080p")
    versioned = normalize_dc_episode_numbering("Show.S3E1v2.1080p")
    meta = Meta(category="TV", basename_no_ext="Show.S3E1v2.1080p")

    assert padded == "Show.S01E01.1080p"
    assert versioned == "Show.S03E01v2.1080p"
    assert "S03E01v2" in asyncio.run(_digitalcore().get_name(meta))
    assert digitalcore_staff_skip_reason(
        Meta(
            category="TV",
            imdb_id=1,
            audio_languages=["en"],
            filelist=["Show.S03E01v2.mkv"],
        )
    ) is None


def test_midnightscene_requires_english_for_tv_and_allows_music_and_bdmv() -> (
    None
):
    tracker = _midnight()
    japanese = Meta(
        category="TV",
        resolution="1080p",
        screens=3,
        audio_languages=["ja"],
        filelist=["Show.S01E01.mkv"],
    )
    music = Meta(category="MUSIC", filelist=["Album.flac"])
    disc = Meta(
        category="TV",
        is_disc="BDMV",
        resolution="1080p",
        screens=3,
        audio_languages=["ja"],
        filelist=["Show.mkv"],
    )

    assert asyncio.run(tracker.get_additional_checks(japanese)) is False
    assert asyncio.run(tracker.get_additional_checks(music)) is True
    assert asyncio.run(tracker.get_additional_checks(disc)) is True


def test_hyphenated_release_group_survives_the_generated_name() -> None:
    filename = "Movie 2026 1080p WEB-DL HEVC x265 H.264-JPN-LCD.mkv"
    meta = Meta(
        category="MOVIE",
        title="Movie",
        year=2026,
        type="WEBDL",
        resolution="1080p",
        audio="AAC 2.0",
        video_encode="H.265",
    )
    meta.tag = asyncio.run(get_tag(filename, meta))
    _unused, name, _clean, _missing = asyncio.run(
        NameManager({}).get_name(meta)
    )

    assert meta.tag == "-JPN-LCD"
    assert name.endswith("-JPN-LCD")
