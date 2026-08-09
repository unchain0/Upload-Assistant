# ruff: noqa: S101
"""Regression tests for Zenith-specific names."""

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import patch

from src.meta import Meta
from src.trackers.UNIT3D.znth import Zenith


def _tracker() -> Zenith:
    return Zenith({"DEFAULT": {}, "TRACKERS": {"ZENITH": {}}})


def _movie_meta(**kwargs: Any) -> Meta:
    base: dict[str, Any] = {
        "category": "MOVIE",
        "filelist": ["Movie.2024.mkv"],
        "name": "Example Movie 2024 1080p WEB-DL H.264 DD 5.1",
        "screens": 3,
        "unattended": True,
        "unattended_confirm": False,
        "imdb_info": {"status": ""},
    }
    base.update(kwargs)
    return Meta(**base)


def _tv_meta(**kwargs: Any) -> Meta:
    base: dict[str, Any] = {
        "category": "TV",
        "filelist": ["Show.S01E01.mkv"],
        "name": "Example TV S01 1080p AMZN WEB-DL DD+ 5.1",
        "tv_pack": False,
        "screens": 3,
        "imdb_info": {"status": "Ended"},
        "unattended": True,
        "unattended_confirm": False,
    }
    base.update(kwargs)
    return Meta(**base)


def test_zenith_rejects_malformed_filelist_and_screenshot_count():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(filelist=1))) is False
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(screens=None))) is False
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(screens=float("inf")))) is False


def test_zenith_rejects_software_without_dedicated_category():
    assert asyncio.run(_tracker().get_additional_checks(Meta(category="GAME", software=True))) is False


def _book_meta(**kwargs: Any) -> Meta:
    base: dict[str, Any] = {
        "category": "BOOK",
        "book_language_iso": "ENG",
        "filelist": ["Example Book/Example Book.epub"],
        "isbn": "9780000000000",
        "isdir": True,
        "keep_folder": True,
        "path": "Example Book",
        "type": "EPUB",
        "format": "EPUB",
        "title": "Example Book",
        "name": "Example Book",
        "year": 2020,
        "unattended": True,
        "unattended_confirm": False,
    }
    base.update(kwargs)
    return Meta(**base)


def _audiobook_meta(**kwargs: Any) -> Meta:
    expected = "宮沢 賢治 - 宮沢賢治童話全集 (2016) JPN {パンローリング} [WEB] M4B AAC 63kbps"
    base: dict[str, Any] = {
        "category": "BOOK",
        "audiobook": True,
        "author": "宮沢 賢治",
        "title": "宮沢賢治童話全集",
        "year": 2016,
        "book_language_iso": "JPN",
        "asin": "B07ZHYPJK1",
        "narrator": "パンローリング",
        "source": "WEB",
        "type": "M4B",
        "format": "M4B",
        "audiobook_bitrate": 63,
        "name": expected,
        "path": expected,
        "filelist": [f"{expected}/{expected}.m4b"],
        "mediainfo": {"media": {"track": [{"@type": "Audio", "Language": "jpn"}]}},
        "isdir": True,
        "keep_folder": True,
        "unattended": True,
        "unattended_confirm": False,
    }
    base.update(kwargs)
    return Meta(**base)


def test_zenith_rejects_single_file_book_without_directory():
    meta = _audiobook_meta(path="宮沢賢治童話全集 [B07ZHYPJK1].m4b", filelist=["宮沢賢治童話全集 [B07ZHYPJK1].m4b"], isdir=False)

    assert asyncio.run(_tracker().get_additional_checks(meta)) is False


def test_zenith_rejects_single_file_book_when_torrent_would_flatten_directory():
    assert asyncio.run(_tracker().get_additional_checks(_audiobook_meta(keep_folder=False))) is False


def test_zenith_rejects_improper_single_file_audiobook_name():
    expected_folder = _audiobook_meta().path
    meta = _audiobook_meta(filelist=[f"{expected_folder}/宮沢賢治童話全集 [B07ZHYPJK1].m4b"])

    assert asyncio.run(_tracker().get_additional_checks(meta)) is False


def test_zenith_rejects_audiobook_when_audio_track_language_disagrees():
    meta = _audiobook_meta(mediainfo={"media": {"track": [{"@type": "Audio", "Language": "en"}]}})

    assert asyncio.run(_tracker().get_additional_checks(meta)) is False


def test_zenith_accepts_compliant_single_file_audiobook_layout():
    assert asyncio.run(_tracker().get_additional_checks(_audiobook_meta())) is True


def test_zenith_supports_music_and_uses_its_music_naming_guide():
    meta = Meta(
        category="MUSIC",
        tag="-FiVE0",
        music_release={
            "fields": {
                "artist": {"value": "Salem"},
                "album": {"value": "King Night"},
                "release_year": {"value": "2010"},
                "media": {"value": "WEB"},
                "format": {"value": "FLAC"},
                "release_type": {"value": "Single"},
            },
            "tracks": [{"codec": "FLAC", "bit_depth": 24, "sample_rate": 44100}],
        },
    )

    tracker = Zenith({"DEFAULT": {}, "TRACKERS": {"ZENITH": {}}})

    assert "MUSIC" in tracker.supported_categories
    assert asyncio.run(tracker.get_name(meta))["name"] == "Salem - King Night (2010) - [WEB FLAC 24bit-44.1kHz Single]-FiVE0"


def test_zenith_music_name_omits_calculated_lossless_bitrate():
    meta = Meta(
        category="MUSIC",
        music_release={
            "fields": {
                "artist": {"value": "Kanye West"},
                "album": {"value": "808s & Heartbreak"},
                "release_year": {"value": "2008"},
                "media": {"value": "CD"},
                "format": {"value": "FLAC"},
            },
            "tracks": [{"codec": "FLAC", "bit_depth": 16, "sample_rate": 44100, "bitrate": 737000}],
        },
    )

    name = asyncio.run(Zenith({"DEFAULT": {}, "TRACKERS": {"ZENITH": {}}}).get_name(meta))["name"]

    assert name == "Kanye West - 808s & Heartbreak (2008) - [CD FLAC 16bit-44.1kHz]"


def test_zenith_accepts_numbered_scene_music_filenames():
    files = [
        "01-simon_and_garfunkel-the_sound_of_silence_(electric_version)-repack-remastered.flac",
        "02-simon_and_garfunkel-leaves_that_are_green-repack-remastered.flac",
    ]

    assert Zenith._validate_music_track_naming(files) == ""
    assert Zenith._validate_music_track_naming(["01. After Hours & Josh Heuston - Into You.flac"]) == ""
    assert Zenith._validate_music_track_naming(["Ye-The Life of Pablo-01-Ultralight Beam.flac"]) == ""
    assert Zenith._validate_music_track_naming(["simon_and_garfunkel-the_sound_of_silence.flac"]) == ""


def test_zenith_validates_torrent_relative_music_path_length(tmp_path: Path):
    root: Path = tmp_path / ("Long Lidarr Library Prefix " * 4) / "Sweet Trip - Album (2021) - WEB FLAC"
    track: Path = root / "03. The Weight of Comfort, This Rain is Comfort, This Rain is You.flac"

    assert Zenith._validate_music_track_naming([str(track)], root) == ""


def test_zenith_music_additional_data_sends_valid_external_ids():
    meta = Meta(
        category="MUSIC",
        music_release={
            "external_ids": {
                "musicbrainz_release": "c0d17e85-3a36-4dc8-9a88-c188a5e78b0d",
                "musicbrainz_release_group": "3bdb2b21-f6f5-3f8b-a1e0-067f8bb71940",
                "discogs_release": "1791341",
                "discogs_master": "28700",
            }
        },
    )

    data = asyncio.run(Zenith({"DEFAULT": {}, "TRACKERS": {"ZENITH": {}}}).get_additional_data(meta))

    assert data == {
        "exists_on_musicbrainz": "1",
        "musicbrainz_release_id": "c0d17e85-3a36-4dc8-9a88-c188a5e78b0d",
        "musicbrainz_release_group_id": "3bdb2b21-f6f5-3f8b-a1e0-067f8bb71940",
        "exists_on_discogs": "1",
        "discogs_release_id": "1791341",
        "discogs_master_id": "28700",
    }


def test_zenith_music_additional_data_omits_invalid_or_disabled_external_ids():
    meta = Meta(
        category="MUSIC",
        music_discogs_enabled=False,
        music_release={"external_ids": {"musicbrainz_release": "invalid", "discogs_release": "not-a-number"}},
    )

    assert asyncio.run(Zenith({"DEFAULT": {}, "TRACKERS": {"ZENITH": {}}}).get_additional_data(meta)) == {}


def test_zenith_music_type_id_comes_from_the_analyzed_codec():
    meta = Meta(category="MUSIC", music_release={"fields": {"format": {"value": "FLAC"}}})

    type_data = asyncio.run(Zenith({"DEFAULT": {}, "TRACKERS": {"ZENITH": {}}}).get_type_id(meta))

    assert type_data == {"type_id": "7"}


def test_zenith_rejects_movie_uploads_with_less_than_three_screenshots():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(screens=2))) is False


def test_zenith_rejects_movie_with_invalid_video_container_extension():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(filelist=["Movie.2024.avi"]))) is False


def test_zenith_accepts_dvdrip_with_avi_container():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(type="DVDRIP", filelist=["Movie.2024.avi"]))) is True


def test_zenith_rejects_tagged_video_filename_renamed_with_spaces():
    renamed = "KAIJU GIRL CARAMELISE S01E01 REPACK 1080p CR WEB-DL DDP2.0 H.264-Kitsune.mkv"
    original = "KAIJU.GIRL.CARAMELISE.S01E01.REPACK.1080p.CR.WEB-DL.DDP2.0.H.264-Kitsune.mkv"

    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(tag="-Kitsune", filelist=[renamed]))) is False
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(tag="-Kitsune", filelist=[original]))) is True


def test_zenith_allows_hdtv_release_with_ts_container():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(type="HDTV", filelist=["Show.S01E01.ts"]))) is True


def test_zenith_allows_encoded_hdtv_release_with_mkv_container():
    meta = _tv_meta(
        type="HDTV",
        video_encode="x264",
        filelist=["Treasure.And.Dirt.S01E03.1080p.HDTV.H264-DARKFLiX.mkv"],
        imdb_info={"status": "Continuing"},
    )

    assert asyncio.run(_tracker().get_additional_checks(meta)) is True


def test_zenith_rejects_sdtv_release_with_mkv_container():
    assert asyncio.run(_tracker().get_additional_checks(_tv_meta(type="SDTV", filelist=["Show.S01E01.mkv"]))) is False


def test_zenith_rejects_archive_files_in_movie_upload():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(filelist=["Movie.2024.part01.rar"]))) is False


def test_zenith_rejects_ongoing_tv_pack():
    assert (
        asyncio.run(
            _tracker().get_additional_checks(
                _tv_meta(
                    tv_pack=True,
                    filelist=["Show.S01E01.mkv", "Show.S01E02.mkv"],
                    imdb_info={"status": "Returning Series"},
                )
            )
        )
        is False
    )


def test_zenith_handles_unknown_tv_pack_status_by_confirmation_policy():
    files = ["Show.S01E01.mkv", "Show.S01E02.mkv"]
    assert asyncio.run(_tracker().get_additional_checks(_tv_meta(tv_pack=True, filelist=files, imdb_info={}))) is False
    assert (
        asyncio.run(
            _tracker().get_additional_checks(
                _tv_meta(tv_pack=True, filelist=files, imdb_info={}, unattended_confirm=True)
            )
        )
        is True
    )


def test_zenith_handles_none_name_in_movie_checks():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(name=None))) is False


def test_zenith_confirmed_unattended_adult_upload_does_not_prompt():
    with patch("src.trackers.common.cli_ui.ask_yes_no", side_effect=AssertionError("prompt should not run")):
        assert asyncio.run(_tracker().get_additional_checks(_movie_meta(adult_media=True, unattended=True, unattended_confirm=True))) is True


def test_zenith_does_not_accept_source_hints_inside_unrelated_words():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(name="Example Movie 2024 1080p WEDDING"))) is False


def test_zenith_rejects_single_episode_for_ended_tv_series():
    assert asyncio.run(_tracker().get_additional_checks(_tv_meta(filelist=["Show.S01E01.mkv"]))) is False


def test_zenith_allows_tv_pack_for_ended_series():
    assert (
        asyncio.run(
            _tracker().get_additional_checks(
                _tv_meta(
                    tv_pack=True,
                    filelist=["Show.S01E01.mkv", "Show.S01E02.mkv"],
                    imdb_info={"status": "Ended"},
                )
            )
        )
        is True
    )


def test_zenith_rejects_music_with_invalid_track_structure():
    meta = _compliant_music_meta(filelist=["01.My-Track.flac", "02 - Other Track.flac"], personalrelease=True)
    meta.music_release["tracks"].append(
        {
            "relative_path": "02 - Other Track.flac",
            "artist": "Artist",
            "album": "Album",
            "title": "Other Track",
            "track_number": 2,
        }
    )

    assert (
        asyncio.run(_tracker().get_additional_checks(meta))
        is False
    )


def test_zenith_allows_music_with_valid_track_structure():
    assert (
        asyncio.run(
            _tracker().get_additional_checks(
                _compliant_music_meta(filelist=["Disc1/01 - My Track.flac"], personalrelease=True)
            )
        )
        is True
    )


def _compliant_music_meta(**kwargs: Any) -> Meta:
    values: dict[str, Any] = {
        "category": "MUSIC",
        "path": "/library/Artist - Album (2024) - [WEB FLAC]",
        "filelist": ["01 - My Track.flac"],
        "unattended": True,
        "unattended_confirm": False,
        "music_release": {
            "fields": {
                "artist": {"value": "Artist"},
                "album": {"value": "Album"},
            },
            "tracks": [
                {
                    "relative_path": "01 - My Track.flac",
                    "artist": "Artist",
                    "album": "Album",
                    "title": "My Track",
                    "track_number": 1,
                }
            ],
        },
    }
    values.update(kwargs)
    return Meta(**values)


def test_zenith_preserves_non_original_music_filenames():
    meta = _compliant_music_meta(filelist=["Artist-Album-01-My.Track.flac"])

    assert asyncio.run(_tracker().get_additional_checks(meta)) is True


def test_zenith_rejects_music_directory_without_artist_and_album():
    meta = _compliant_music_meta(path="/library/Unsorted Download")

    assert asyncio.run(_tracker().get_additional_checks(meta)) is False


def test_zenith_accepts_verified_artist_alias_in_music_directory():
    meta = _compliant_music_meta(path="/library/Ye - Album (2024) - [WEB FLAC]")
    meta.music_release["conflicts"] = {"artist": ["Artist", "Ye"]}

    assert asyncio.run(_tracker().get_additional_checks(meta)) is True


def test_zenith_rejects_music_track_missing_required_tag():
    meta = _compliant_music_meta()
    meta.music_release["tracks"][0]["track_number"] = None

    assert asyncio.run(_tracker().get_additional_checks(meta)) is False


def test_zenith_counts_release_folder_in_music_path_limit():
    folder = f"Artist - Album - {'x' * 165}"
    meta = _compliant_music_meta(path=f"/library/{folder}")

    assert asyncio.run(_tracker().get_additional_checks(meta)) is False


def test_zenith_allows_unnumbered_filename_for_original_single_track_release():
    meta = _compliant_music_meta(filelist=["Alive.flac"], personalrelease=True)

    assert asyncio.run(_tracker().get_additional_checks(meta)) is True


def test_zenith_rejects_book_with_invalid_language_code():
    assert asyncio.run(_tracker().get_additional_checks(_book_meta(book_language_iso="en", title="Valid Title", name="Valid Title"))) is False


def test_zenith_rejects_banned_book_work():
    assert (
        asyncio.run(
            _tracker().get_additional_checks(
                _book_meta(title="Four Against Darkness Expanded Edition", name="Four Against Darkness Expanded Edition")
            )
        )
        is False
    )
