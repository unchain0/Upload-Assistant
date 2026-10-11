import asyncio

from src.domain_models.release import Meta
from src.integrations.trackers.registry import TrackerSetup, tracker_class_map
from src.integrations.trackers.UNIT3D.retromoviesclub import RetroMoviesClub


def _tracker() -> RetroMoviesClub:
    return RetroMoviesClub(
        {"DEFAULT": {}, "TRACKERS": {"RETROMOVIESCLUB": {}}}
    )


def test_retromoviesclub_is_registered_with_full_tracker_name():
    assert tracker_class_map["RETROMOVIESCLUB"] is RetroMoviesClub
    assert RetroMoviesClub.display_name == "RetroMoviesClub"


def test_retromoviesclub_filters_non_movie_categories():
    meta = Meta(category="TV", trackers=["RETROMOVIESCLUB"])
    setup = TrackerSetup(
        {"TRACKERS": {"RETROMOVIESCLUB": {"api_key": "token"}}}
    )

    setup.filter_unsupported_trackers(meta)

    assert meta.trackers == []
    assert meta.tracker_status["RETROMOVIESCLUB"] == {
        "upload": False,
        "skipped": True,
        "skip_reason": "Category 'TV' is not supported by this tracker",
        "status_message": "Category 'TV' is not supported by this tracker",
    }


def test_retromoviesclub_accepts_movies_released_in_2000_or_earlier():
    assert (
        asyncio.run(
            _tracker().get_additional_checks(Meta(category="MOVIE", year=2000))
        )
        is True
    )
    assert (
        asyncio.run(
            _tracker().get_additional_checks(Meta(category="MOVIE", year=2001))
        )
        is False
    )


def test_retromoviesclub_mapping_modes_and_fallbacks():
    tracker = _tracker()
    meta = Meta(category="MOVIE", resolution="1080p")

    category_mapping = asyncio.run(
        tracker.get_category_id(meta, mapping_only=True)
    )
    assert category_mapping == {"MOVIE": "1"}
    assert asyncio.run(tracker.get_category_id(meta, reverse=True)) == {
        "1": "MOVIE"
    }
    assert asyncio.run(tracker.get_category_id(meta, category="TV")) == {
        "category_id": "0"
    }

    type_mapping = asyncio.run(tracker.get_type_id(meta, mapping_only=True))
    assert type_mapping["BDMV"] == "1"
    assert asyncio.run(tracker.get_type_id(meta, reverse=True))["1"] == "BDMV"

    resolution_mapping = asyncio.run(
        tracker.get_resolution_id(meta, mapping_only=True)
    )
    assert resolution_mapping["2160p"] == "2"
    assert (
        asyncio.run(tracker.get_resolution_id(meta, reverse=True))["2"]
        == "2160p"
    )
    assert asyncio.run(tracker.get_resolution_id(meta, resolution="720p")) == {
        "resolution_id": "5"
    }
    assert asyncio.run(tracker.get_resolution_id(Meta(resolution="360p"))) == {
        "resolution_id": "11"
    }


def test_retromoviesclub_uses_tracker_specific_type_ids():
    tracker = _tracker()

    assert asyncio.run(
        tracker.get_type_id(Meta(category="MOVIE", is_disc="BDMV"))
    ) == {"type_id": "1"}
    assert asyncio.run(
        tracker.get_type_id(
            Meta(category="MOVIE", type="REMUX", source="BluRay")
        )
    ) == {"type_id": "2"}
    assert asyncio.run(
        tracker.get_type_id(
            Meta(category="MOVIE", type="REMUX", source="PAL DVD")
        )
    ) == {"type_id": "4"}
    assert asyncio.run(
        tracker.get_type_id(Meta(category="MOVIE", type="WEBDL"))
    ) == {"type_id": "7"}


def test_retromoviesclub_sanitizes_upload_name_and_removes_aka():
    meta = Meta(name="Le Fabuleux Amélie [Amélie] (2001)!", aka="[Amélie]")

    assert asyncio.run(_tracker().get_name(meta)) == {
        "name": "Le Fabuleux Amlie 2001"
    }


def test_retromoviesclub_includes_mod_queue_flag():
    data = asyncio.run(
        RetroMoviesClub(
            {"DEFAULT": {}, "TRACKERS": {"RETROMOVIESCLUB": {"modq": True}}}
        ).get_additional_data(Meta())
    )

    assert data == {"mod_queue_opt_in": "1"}
