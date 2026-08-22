import asyncio

from src.delivery.cli.arguments import Args
from src.domain_models.release import Meta
from src.engines.upload_safety_policy import (
    blocks_automatic_upload,
    content_paths_with_spaces,
    invalid_release_group_tag,
)
from src.services.tracker_status_service import TrackerStatusManager


def test_single_file_with_spaces_is_blocked() -> None:
    path = "/data/daily/To Philly with Love 2026 1080p WEB-DL BONE.mkv"
    meta = Meta(path=path, filelist=[path])

    assert content_paths_with_spaces(meta) == [
        "To Philly with Love 2026 1080p WEB-DL BONE.mkv"
    ]
    assert blocks_automatic_upload(meta) is True


def test_spaces_outside_torrent_content_are_ignored() -> None:
    path = "/data/My Media/Movie.2026.1080p.WEB-DL-GROUP.mkv"
    meta = Meta(path=path, filelist=[path])

    assert content_paths_with_spaces(meta) == []


def test_windows_spaces_outside_torrent_content_are_ignored() -> None:
    path = r"C:\data\My Media\Movie.2026.1080p.WEB-DL-GROUP.mkv"
    meta = Meta(path=path, filelist=[path])

    assert content_paths_with_spaces(meta) == []


def test_windows_single_file_with_spaces_is_blocked() -> None:
    path = r"C:\data\daily\Movie With Spaces.mkv"
    meta = Meta(path=path, filelist=[path])

    assert content_paths_with_spaces(meta) == ["Movie With Spaces.mkv"]


def test_nested_content_folder_with_spaces_is_blocked() -> None:
    meta = Meta(
        path="Release", filelist=["Release/Season 01/Episode.S01E01.mkv"]
    )

    assert content_paths_with_spaces(meta) == ["Season 01"]


def test_allow_spaces_is_an_explicit_override() -> None:
    meta = Meta(
        path="Movie With Spaces.mkv",
        filelist=["Movie With Spaces.mkv"],
        allow_spaces=True,
    )

    assert blocks_automatic_upload(meta) is False


def test_audiobook_cover_is_global_upload_safety_invariant() -> None:
    reason = TrackerStatusManager._unsafe_release_reason(
        Meta(category="BOOK", audiobook=True, artwork_path="")
    )
    assert reason is not None
    assert "Audiobook cover is missing or invalid" in reason


def test_global_guard_refuses_audiobook_without_cover() -> None:
    meta = Meta(
        category="BOOK",
        audiobook=True,
        trackers=["YUSCENE", "PEERGARDEN"],
        unattended=True,
    )

    successful = asyncio.run(
        TrackerStatusManager({}).process_all_trackers(meta)
    )

    assert successful == 0
    assert set(meta.tracker_status) == {"YUSCENE", "PEERGARDEN"}
    assert all(
        status["skipped"] is True and status["upload"] is False
        for status in meta.tracker_status.values()
    )
    assert all(
        "Audiobook cover is missing or invalid" in status["skip_reason"]
        for status in meta.tracker_status.values()
    )


def test_invalid_release_group_tag_detects_episode_syntax() -> None:
    assert invalid_release_group_tag(Meta(tag="-_S01E05_")) == "S01E05"
    assert invalid_release_group_tag(Meta(tag="-Gecko")) is None


def test_global_guard_refuses_episode_syntax_release_group() -> None:
    meta = Meta(
        path="Release.mkv",
        filelist=["Release.mkv"],
        trackers=["YUSCENE", "LUMINARR"],
        tag="-_S01E05_",
        unattended=True,
    )

    successful = asyncio.run(
        TrackerStatusManager({}).process_all_trackers(meta)
    )

    assert successful == 0
    assert set(meta.tracker_status) == {"YUSCENE", "LUMINARR"}
    assert all(
        status["skipped"] is True and status["upload"] is False
        for status in meta.tracker_status.values()
    )
    assert all(
        "Release group 'S01E05' matches season/episode syntax"
        in status["skip_reason"]
        for status in meta.tracker_status.values()
    )


def test_allow_spaces_cli_option_sets_explicit_override() -> None:
    meta, _parser, _unused = Args({"DEFAULT": {"screens": 3}}).parse(
        ["--allow-spaces", "Movie With Spaces.mkv"], Meta()
    )

    assert meta.allow_spaces is True


def test_global_guard_skips_every_tracker_before_processing() -> None:
    path = "/data/daily/To Philly with Love.mkv"
    meta = Meta(
        path=path,
        filelist=[path],
        trackers=["LUMINARR", "ZENITH"],
        name="To Philly with Love",
        unattended=True,
    )

    successful = asyncio.run(
        TrackerStatusManager({}).process_all_trackers(meta)
    )

    assert successful == 0
    assert set(meta.tracker_status) == {"LUMINARR", "ZENITH"}
    assert all(
        status["skipped"] is True and status["upload"] is False
        for status in meta.tracker_status.values()
    )
    assert all(
        "--allow-spaces" in status["skip_reason"]
        for status in meta.tracker_status.values()
    )
