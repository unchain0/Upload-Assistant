from src.domain_models.release import Meta
from src.integrations.trackers.common import Common
from src.services.tracker_status_service import TrackerStatusManager


def test_tmdb_ended_status_identifies_completed_episode() -> None:
    meta = Meta(
        category="TV",
        tv_pack=False,
        episode_int=2,
        series_status="Ended",
        imdb_info={},
    )

    assert Common.is_completed_tv_episode(meta) is True


def test_completed_episode_blocks_passed_trackers_but_preserves_dupes() -> (
    None
):
    meta = Meta(
        category="TV", tv_pack=False, episode_int=2, series_status="Ended"
    )
    passed = {
        "banned": False,
        "skipped": False,
        "dupe": False,
        "upload": False,
        "other": False,
    }
    dupe = {
        "banned": False,
        "skipped": False,
        "dupe": True,
        "upload": False,
        "other": False,
    }
    results = [
        ("ZENITH", passed, None, None),
        ("SAMARITANO", dupe, None, None),
    ]

    blocked = TrackerStatusManager._block_completed_episode_uploads(
        meta, results
    )

    assert blocked is True
    assert passed["skipped"] is True
    assert dupe["dupe"] is True
    assert dupe["skipped"] is False


def test_ongoing_series_episode_remains_eligible() -> None:
    meta = Meta(
        category="TV",
        tv_pack=False,
        episode_int=2,
        series_status="Returning Series",
    )

    assert Common.is_completed_tv_episode(meta) is False
