from __future__ import annotations

import asyncio
import copy
from unittest.mock import AsyncMock

import pytest

from data.example_config import config as example_config
from src.domain_models.release import Meta
from src.integrations.trackers import description_builder
from src.integrations.trackers.UNIT3D.homiehelpdesk import HomieHelpDesk
from src.integrations.trackers.UNIT3D.itatorrents import ItaTorrents
from src.integrations.trackers.UNIT3D.peergarden import PeerGarden
from src.integrations.trackers.UNIT3D.rastastugan import Rastastugan
from src.integrations.trackers.UNIT3D.retromoviesclub import RetroMoviesClub
from src.integrations.trackers.UNIT3D.samaritano import Samaritano
from src.integrations.trackers.UNIT3D.utopia import Utopia


def _config() -> dict:
    config = copy.deepcopy(example_config)
    config.setdefault("DEFAULT", {})["tmdb_api"] = "0123456789abcdef0123456789abcdef"
    trackers = config.setdefault("TRACKERS", {})
    for tracker in ("HOMIEHELPDESK", "PEERGARDEN", "RASTASTUGAN", "SAMARITANO", "ITATORRENTS", "RETROMOVIESCLUB", "UTOPIA"):
        values = trackers.setdefault(tracker, {})
        values.setdefault("api_key", "test-key")
        values.setdefault("announce_url", "https://tracker.invalid/announce")
    return config


def test_homiehelpdesk_book_category_variants_and_unknown_book_type() -> None:
    tracker = HomieHelpDesk(_config())
    cases = (("comic", "11"), ("manga", "9"), ("magazine", "12"))
    for attribute, expected in cases:
        meta = Meta(category="BOOK")
        setattr(meta, attribute, True)
        assert asyncio.run(tracker.get_category_id(meta)) == {"category_id": expected}

    assert asyncio.run(tracker.get_type_id(Meta(category="BOOK", type="UNKNOWN"))) == {"type_id": "23"}


def test_peergarden_game_platform_type_variants() -> None:
    tracker = PeerGarden(_config())
    cases = (
        (Meta(category="GAME", type="GAME", console_game=True, platform=""), "32"),
        (Meta(category="GAME", type="GAME", platform="Android"), "24"),
        (Meta(category="GAME", type="GAME", platform="iOS"), "25"),
        (Meta(category="GAME", type="GAME", platform="MacOS"), "12"),
        (Meta(category="GAME", type="GAME", platform="Windows"), "13"),
    )
    for meta, expected in cases:
        assert asyncio.run(tracker.get_type_id(meta)) == {"type_id": expected}


def test_rastastugan_game_platform_type_variants() -> None:
    tracker = Rastastugan(_config())
    cases = (("MacOS", "9"), ("Linux", "18"), ("Windows PC", "10"), ("", "11"), ("Unknown", "19"))
    for platform, expected in cases:
        meta = Meta(category="GAME", type="GAME", platform=platform, console_game=platform == "")
        assert asyncio.run(tracker.get_type_id(meta)) == {"type_id": expected}


def test_samaritano_book_and_game_variants() -> None:
    tracker = Samaritano(_config())
    assert asyncio.run(tracker.get_category_id(Meta(category="BOOK", comic=True))) == {"category_id": "7"}

    cases = (("PlayStation 5", "52"), ("Xbox", "53"), ("Nintendo Switch", "54"), ("Android", "55"), ("Emulator ROM", "51"))
    for platform, expected in cases:
        meta = Meta(category="GAME", type="GAME", platform=platform)
        assert asyncio.run(tracker.get_type_id(meta)) == {"type_id": expected}


def test_retromoviesclub_dynamic_type_variants() -> None:
    tracker = RetroMoviesClub(_config())
    cases = (
        (Meta(category="MOVIE", type="ENCODE", source="BluRay"), "5"),
        (Meta(category="MOVIE", type="DVDRIP", source="DVD"), "6"),
        (Meta(category="MOVIE", type="HDTV", source="UHDTV"), "9"),
        (Meta(category="MOVIE", type="HDTV", source="HDTV"), "10"),
        (Meta(category="TV", type="UNKNOWN", source="", sd=1), "11"),
        (Meta(category="MOVIE", type="UNKNOWN", source=""), "0"),
    )
    for meta, expected in cases:
        assert asyncio.run(tracker.get_type_id(meta)) == {"type_id": expected}


def test_itatorrents_hybrid_and_manual_date_naming(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = ItaTorrents(_config())
    monkeypatch.setattr(tracker, "get_dubs", AsyncMock(return_value="ITA"))
    meta = Meta(
        category="TV",
        title="Example",
        year=2025,
        search_year="2025",
        manual_date="2025-01-01",
        season="S01",
        episode="E02",
        type="WEBDL",
        source="WEB",
        resolution="1080p",
        edition="Hybrid Director's Cut",
        audio="DDP 5.1",
        video_codec="H.264",
        tag="-GROUP",
    )

    name = asyncio.run(tracker.get_name(meta))["name"]
    assert "Hybrid" not in name
    assert "S01" not in name and "E02" not in name


def test_itatorrents_language_requirement_rejection() -> None:
    tracker = ItaTorrents(_config())
    tracker.common.check_language_requirements = AsyncMock(return_value=False)  # type: ignore[method-assign]

    assert not asyncio.run(tracker.get_additional_checks(Meta()))


def test_utopia_description_transforms_and_restores_packed_images(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = Utopia(_config())
    original = [{"raw_url": "https://full.invalid/one", "img_url": "https://medium.invalid/one"}]
    packed = [{"raw_url": "https://full.invalid/two", "img_url": "https://medium.invalid/two"}]
    meta = Meta(image_list=original.copy())
    meta["new_images_pack"] = packed.copy()
    monkeypatch.setattr(meta, "to_dict", lambda: {**Meta.to_dict(meta), "new_images_pack": meta["new_images_pack"]})

    seen: dict[str, object] = {}

    class Builder:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def general_description_generator(self, current: Meta, **_kwargs) -> str:
            seen["image_list"] = current.image_list.copy()
            seen["packed"] = list(current["new_images_pack"])
            return "description"

    monkeypatch.setattr(description_builder, "DescriptionBuilder", Builder)
    result = asyncio.run(tracker.get_description(meta))

    assert result == {"description": "description"}
    assert seen["packed"] != packed
    assert meta.image_list == original
    assert meta["new_images_pack"] == packed


def test_utopia_name_strips_dual_audio_marker() -> None:
    tracker = Utopia(_config())
    meta = Meta(
        category="MOVIE",
        type="REMUX",
        title="Example",
        year=2025,
        audio="Dual-Audio  TrueHD Atmos 7.1",
        resolution="2160p",
        source="BluRay",
        video_codec="HEVC",
        video_encode="HEVC",
        tag="-GROUP",
    )

    name = asyncio.run(tracker.get_name(meta))["name"]
    assert "Dual-Audio" not in name
    assert "TrueHD Atmos 7.1" in name


def test_homiehelpdesk_additional_checks_and_music_identifiers() -> None:
    tracker = HomieHelpDesk(_config())
    assert not asyncio.run(tracker.get_additional_checks(Meta(type="DVDRIP")))
    assert not asyncio.run(tracker.get_additional_checks(Meta(category="MUSIC", music_release={})))
    assert asyncio.run(tracker.get_additional_checks(Meta(category="MOVIE", type="WEBDL")))

    mbid = "12345678-1234-1234-1234-123456789abc"
    mb_meta = Meta(music_release={"external_ids": {"musicbrainz_release": mbid}})
    assert tracker._music_upload_data(mb_meta) == {"music_exists_on_musicbrainz": "1", "musicbrainz": mbid}

    discogs_url = "https://www.discogs.com/release/12345-example"
    discogs_meta = Meta(music_discogs_enabled=True, music_release={"external_ids": {"discogs_release_url": discogs_url}})
    assert tracker._music_upload_data(discogs_meta) == {"music_exists_on_discogs": "1", "discogs": discogs_url}


def test_peergarden_rejects_software_uploads() -> None:
    assert not asyncio.run(PeerGarden(_config()).get_additional_checks(Meta(software=True)))


def test_rastastugan_language_and_book_fallbacks() -> None:
    tracker = Rastastugan(_config())
    tracker.common.check_language_requirements = AsyncMock(return_value=True)  # type: ignore[method-assign]
    assert asyncio.run(tracker.get_additional_checks(Meta()))
    assert asyncio.run(tracker.get_type_id(Meta(category="BOOK", type="UNKNOWN"))) == {"type_id": "19"}
    assert asyncio.run(tracker.get_type_id(Meta(category="GAME", type="MAC", platform="Unknown"))) == {"type_id": "9"}


def test_samaritano_invalid_filelist_and_book_are_deterministic() -> None:
    tracker = Samaritano(_config())
    assert not asyncio.run(tracker.get_additional_checks(Meta(filelist="bad")))
    assert asyncio.run(tracker.get_additional_checks(Meta(category="BOOK", filelist=[])))


def test_samaritano_movie_validation_paths() -> None:
    tracker = Samaritano(_config())
    assert not asyncio.run(tracker.get_additional_checks(Meta(category="MOVIE", filelist=["a.mkv", "b.mkv"])))

    tracker.common.check_portuguese_video_requirements = AsyncMock(return_value=True)  # type: ignore[method-assign]
    assert asyncio.run(tracker.get_additional_checks(Meta(category="MOVIE", filelist=["a.mkv"])))


def test_samaritano_tv_pack_validation_paths() -> None:
    tracker = Samaritano(_config())
    tracker.common.extract_tv_seasons = lambda _files: {1, 2}  # type: ignore[method-assign]
    tracker.common.count_tv_episodes = lambda _files: 2  # type: ignore[method-assign]
    assert not asyncio.run(tracker.get_additional_checks(Meta(category="TV", tv_pack=True, filelist=["a.mkv"])))

    tracker.common.extract_tv_seasons = lambda _files: {1}  # type: ignore[method-assign]
    tracker.common.is_tv_series_ended = lambda *_args, **_kwargs: False  # type: ignore[method-assign]
    assert not asyncio.run(tracker.get_additional_checks(Meta(category="TV", tv_pack=True, filelist=["a.mkv"])))


def test_samaritano_tv_pack_success_and_episode_rejection() -> None:
    tracker = Samaritano(_config())
    tracker.common.extract_tv_seasons = lambda _files: {1}  # type: ignore[method-assign]
    tracker.common.count_tv_episodes = lambda _files: 1  # type: ignore[method-assign]
    tracker.common.is_tv_series_ended = lambda *_args, **_kwargs: True  # type: ignore[method-assign]
    tracker.common.check_portuguese_video_requirements = AsyncMock(return_value=True)  # type: ignore[method-assign]
    assert asyncio.run(tracker.get_additional_checks(Meta(category="TV", tv_pack=True, filelist=["a.mkv"])))

    tracker.common.count_tv_episodes = lambda _files: 2  # type: ignore[method-assign]
    assert not asyncio.run(tracker.get_additional_checks(Meta(category="TV", tv_pack=False, filelist=["a.mkv"])))


def test_itatorrents_language_requirement_success() -> None:
    tracker = ItaTorrents(_config())
    tracker.common.check_language_requirements = AsyncMock(return_value=True)  # type: ignore[method-assign]
    assert asyncio.run(tracker.get_additional_checks(Meta()))


def test_retromoviesclub_remaining_type_and_policy_paths() -> None:
    tracker = RetroMoviesClub(_config())
    cases = (
        (Meta(category="MOVIE", type="REMUX", source="BluRay"), "2"),
        (Meta(category="MOVIE", type="REMUX", source="DVD"), "4"),
        (Meta(category="MOVIE", type="WEBDL", source="WEB"), "7"),
        (Meta(category="MOVIE", type="WEBRIP", source="WEB"), "8"),
    )
    for meta, expected in cases:
        assert asyncio.run(tracker.get_type_id(meta)) == {"type_id": expected}

    assert not asyncio.run(tracker.get_additional_checks(Meta(category="TV", year=1990)))
    assert not asyncio.run(tracker.get_additional_checks(Meta(category="MOVIE", year=2001)))
    assert asyncio.run(tracker.get_additional_checks(Meta(category="MOVIE", year=2000)))
    assert asyncio.run(tracker.get_name(Meta(name="Title AKA 1990", aka="AKA"))) == {"name": "Title 1990"}


def test_utopia_encode_hdtv_and_other_category_naming() -> None:
    tracker = Utopia(_config())
    encode = Meta(category="MOVIE", type="ENCODE", title="Encode", year=2025, video_encode="x265", video_codec="HEVC", resolution="1080p")
    assert "x265" in asyncio.run(tracker.get_name(encode))["name"]

    hdtv = Meta(category="TV", type="HDTV", title="Show", season="S01", episode="E01", year=2025, video_encode="x264", resolution="1080p")
    assert "x264" in asyncio.run(tracker.get_name(hdtv))["name"]

    other = Meta(category="MUSIC", type="WEB", name="Existing Name", tag="-GROUP")
    assert asyncio.run(tracker.get_name(other))["name"] == "Existing Name-GROUP"


def test_peergarden_accepts_non_software_uploads() -> None:
    assert asyncio.run(PeerGarden(_config()).get_additional_checks(Meta(software=False)))


def test_samaritano_software_name_category_type_and_default_policy() -> None:
    tracker = Samaritano(_config())
    software = Meta(category="GAME", software=True, platform="Windows", name="Tool")
    assert asyncio.run(tracker.get_name(software)) == {"name": "Tool"}
    assert asyncio.run(tracker.get_category_id(software)) == {"category_id": "9"}
    assert asyncio.run(tracker.get_type_id(software)) == {"type_id": "50"}

    tracker.common.check_portuguese_video_requirements = AsyncMock(return_value=True)  # type: ignore[method-assign]
    assert asyncio.run(tracker.get_additional_checks(Meta(category="GAME", filelist=[])))


def test_retromoviesclub_dvd_disc_type() -> None:
    tracker = RetroMoviesClub(_config())
    assert asyncio.run(tracker.get_type_id(Meta(category="MOVIE", is_disc="DVD", source="DVD"))) == {"type_id": "3"}
