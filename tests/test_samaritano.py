import asyncio
from typing import Any

import pytest

from src.meta import Meta
from src.trackers.UNIT3D.capybarabr import CapybaraBR
from src.trackers.UNIT3D.samaritano import Samaritano


def test_samaritano_rejects_malformed_filelist() -> None:
    meta = Meta(category="MOVIE", filelist=1, unattended=True, unattended_confirm=False)

    assert asyncio.run(Samaritano({"TRACKERS": {}}).get_additional_checks(meta)) is False  # noqa: S101


@pytest.mark.parametrize(
    "tracker_class",
    [CapybaraBR, Samaritano],
)
@pytest.mark.parametrize(
    ("audio_languages", "expected_tag"),
    [
        (["Japanese", "English"], ""),
        (["Portuguese", "Portuguese"], ""),
        (["Portuguese", "English"], "DUAL"),
        (["Portuguese", "English", "Japanese"], "MULTI"),
    ],
)
def test_brazilian_trackers_audio_tags_require_portuguese(tracker_class: type[CapybaraBR] | type[Samaritano], audio_languages: list[str], expected_tag: str) -> None:
    meta = Meta(
        category="TV",
        name="Example Show 2026 WEB-DL - GROUP",
        title="Example Show",
        year=2026,
        tag="-GROUP",
        audio_languages=audio_languages,
        dual_audio=True,
    )

    name = asyncio.run(tracker_class({"TRACKERS": {}}).get_name(meta))["name"]

    assert (" DUAL-" in name) == (expected_tag == "DUAL")  # noqa: S101
    assert (" MULTI-" in name) == (expected_tag == "MULTI")  # noqa: S101


def test_capybarabr_formats_dvdrips_with_resolution_before_audio_and_codec() -> None:
    meta = Meta(
        category="MOVIE",
        name="Example Movie 2001 DVD x264 DVDRip DD 2.0-DDOS",
        title="Example Movie",
        year=2001,
        type="DVDRIP",
        resolution="480p",
        audio="DD 2.0",
        video_encode="x264",
        tag="-DDOS",
    )

    name = asyncio.run(CapybaraBR({"TRACKERS": {}}).get_name(meta))["name"]

    assert name == "Example Movie 2001 480p DVDRip DD2.0 x264-DDOS"  # noqa: S101


def _movie_meta(**kwargs: Any) -> Meta:
    base: dict[str, Any] = {
        "category": "MOVIE",
        "filelist": ["Example.Movie.2024.mkv"],
        "unattended": True,
        "unattended_confirm": False,
        "audio_languages": ["Portuguese"],
        "name": "Example Movie 2024",
        "imdb_info": {"status": ""},
    }
    base.update(kwargs)
    return Meta(**base)


def _tv_meta(**kwargs: Any) -> Meta:
    base: dict[str, Any] = {
        "category": "TV",
        "filelist": ["Show.S01E01.mkv"],
        "tv_pack": False,
        "unattended": True,
        "unattended_confirm": False,
        "audio_languages": ["Portuguese"],
        "imdb_info": {"status": "Ended"},
    }
    base.update(kwargs)
    return Meta(**base)


def test_samaritano_rejects_movie_with_multiple_video_files():
    assert (
        asyncio.run(
            Samaritano({"TRACKERS": {}}).get_additional_checks(
                _movie_meta(
                    filelist=["Example.Movie.2024.mkv", "Example.Movie.Extra.2024.mkv"],
                )
            )
        )
        is False
    )


def test_samaritano_accepts_single_movie_video_upload():
    assert asyncio.run(Samaritano({"TRACKERS": {}}).get_additional_checks(_movie_meta())) is True


def test_samaritano_rejects_tv_pack_for_non_ended_series():
    assert (
        asyncio.run(
            Samaritano({"TRACKERS": {}}).get_additional_checks(
                _tv_meta(
                    tv_pack=True,
                    filelist=["Show.S01E01.mkv", "Show.S01E02.mkv"],
                    imdb_info={"status": "Returning Series"},
                )
            )
        )
        is False
    )


def test_samaritano_rejects_tv_uploads_with_multiple_seasons():
    assert (
        asyncio.run(
            Samaritano({"TRACKERS": {}}).get_additional_checks(
                _tv_meta(
                    tv_pack=True,
                    filelist=["Show.S01E01.mkv", "Show.S02E01.mkv"],
                    imdb_info={"status": "Ended"},
                )
            )
        )
        is False
    )


def test_samaritano_rejects_tv_uploads_with_multiple_episodes_without_pack():
    assert (
        asyncio.run(
            Samaritano({"TRACKERS": {}}).get_additional_checks(
                _tv_meta(
                    filelist=["Show.S01E01.mkv", "Show.S01E02.mkv", "Show.S01E03.mkv"],
                )
            )
        )
        is False
    )


def test_samaritano_accepts_tv_pack_for_ended_series():
    assert (
        asyncio.run(
            Samaritano({"TRACKERS": {}}).get_additional_checks(
                _tv_meta(
                    tv_pack=True,
                    filelist=["Show.S01E01.mkv", "Show.S01E02.mkv", "Show.S01E03.mkv"],
                    imdb_info={"status": "Ended"},
                )
            )
        )
        is True
    )
